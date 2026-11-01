"""One bot run: read feeds, pick the strongest stories, post them to the English (and Arabic) channel,
remember them, and send the evening and weekly posts when they are due."""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import requests

from newsbot.banner import download_photo
from newsbot.editions import arabic_edition, english_edition
from newsbot.feeds import collect_articles
from newsbot.images import fetch_og_image
from newsbot.models import Article, Config, Settings, Story
from newsbot.publish import Target, render_post, send_story, story_tags, translate_story
from newsbot.quality import first_sentence
from newsbot.scheduled import ScheduledOutcome, send_scheduled
from newsbot.selector import Selection, select_stories
from newsbot.state import (
    DUPLICATE,
    FAILED,
    POSTED,
    SEEDED,
    BotState,
    PostedRecord,
    StateCorruptError,
    load_state,
    save_state,
    set_aside,
)
from newsbot.telegram import FatalTelegramError, TelegramClient, TelegramError, UncertainDeliveryError
from newsbot.translate import GeminiTranslator, TranslationError

log = logging.getLogger(__name__)

Out = Callable[[str], None]


@dataclass(frozen=True)
class RunResult:
    posted: int = 0
    would_post: int = 0  # dry run only
    unconfirmed: int = 0  # sent, but Telegram gave no clear answer; remembered as posted
    duplicates: int = 0
    failed_posts: int = 0
    arabic_posted: int = 0
    feeds_failed: int = 0
    feeds_total: int = 0
    digest_sent: bool = False
    weekly_sent: bool = False
    fatal_error: str | None = None


@dataclass(frozen=True)
class _Run:
    """Everything a run needs to post."""

    config: Config
    session: requests.Session
    client: TelegramClient
    english: Target
    arabic: Target | None
    translator: GeminiTranslator | None
    state_path: str | Path
    now: datetime
    sleep: Callable[[float], None]


@dataclass(frozen=True)
class _PostOutcome:
    state: BotState
    posted: int = 0
    unconfirmed: int = 0
    failed: int = 0
    arabic_posted: int = 0
    fatal_error: str | None = None  # English setup problem: nothing else is sent this run
    arabic_error: str | None = None  # Arabic setup problem: English carries on, the run is still red


def run_once(
    *,
    config: Config,
    session: requests.Session,
    client: TelegramClient | None,
    chat_id: str,
    state_path: str | Path,
    now: datetime,
    dry_run: bool = False,
    force_digest: bool = False,
    force_weekly: bool = False,
    arabic_chat_id: str | None = None,
    translator: GeminiTranslator | None = None,
    banners_dir: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: Out = print,
) -> RunResult:
    settings = config.settings
    state, broken_state = _load_state(state_path, now, settings, dry_run)
    feeds = collect_articles(session, config, now)
    base = RunResult(feeds_failed=len(feeds.failed), feeds_total=feeds.total)
    if feeds.total and len(feeds.failed) == feeds.total:
        # A broken state file stays where it is until a run can re-learn the stories.
        return replace(
            base, fatal_error="All news feeds failed. Check the internet connection or the feed URLs."
        )
    if broken_state and not dry_run:
        return _recover_state(state_path, broken_state, feeds.articles, now, settings, base)

    selection = select_stories(feeds.articles, state, config, now)
    for article in selection.duplicates:
        log.info("Skipping repeat story: %s (%s)", article.title, article.source)
        state = _remember(state, article, DUPLICATE, now)
    _log_selection(selection)
    stories = [_with_image(session, s, settings.request_timeout_seconds) for s in selection.to_post]
    english = Target(chat_id, english_edition(config))
    arabic = _arabic_target(config, arabic_chat_id, translator)
    if dry_run:
        _show_dry_run(
            stories,
            state,
            config,
            english,
            arabic,
            now,
            (force_digest, force_weekly),
            banners_dir,
            session,
            out,
        )
        return replace(base, would_post=len(stories), duplicates=len(selection.duplicates))

    if client is None:
        raise ValueError("A Telegram client is required unless dry_run is True")
    run = _Run(config, session, client, english, arabic, translator, state_path, now, sleep)
    outcome = _post_all(run, stories, state)
    scheduled = ScheduledOutcome(state=outcome.state)
    if outcome.fatal_error is None:
        scheduled = send_scheduled(
            client, english, arabic, outcome.state, config, now, force_digest, force_weekly, out
        )
    save_state(state_path, scheduled.state)
    return replace(
        base,
        posted=outcome.posted,
        unconfirmed=outcome.unconfirmed,
        duplicates=len(selection.duplicates),
        failed_posts=outcome.failed,
        arabic_posted=outcome.arabic_posted,
        digest_sent=scheduled.digest_sent,
        weekly_sent=scheduled.weekly_sent,
        fatal_error=outcome.fatal_error or scheduled.fatal_error or outcome.arabic_error,
    )


def _arabic_target(config: Config, chat_id: str | None, translator: GeminiTranslator | None) -> Target | None:
    if config.arabic.enabled and chat_id and translator is not None:
        return Target(chat_id, arabic_edition(config, chat_id))
    return None


def _log_selection(selection: Selection) -> None:
    log.info(
        "%d chosen for this run, %d strong stories waiting, %d weak stories skipped for now, "
        "%d filler skipped, %d blocked (18+ and other unwanted subjects), %d repeats skipped",
        len(selection.to_post),
        selection.waiting,
        selection.weak,
        selection.filler,
        selection.blocked,
        len(selection.duplicates),
    )


def _load_state(
    state_path: str | Path, now: datetime, settings: Settings, dry_run: bool
) -> tuple[BotState, StateCorruptError | None]:
    """The saved state, plus the error when the file was broken (then this run must not post)."""
    try:
        return load_state(state_path).pruned(now, settings.state_retention_days), None
    except StateCorruptError as exc:
        if dry_run:
            log.warning("The posted-stories file could not be read (%s); previewing as if it were empty", exc)
        return BotState.empty(), exc


def _recover_state(
    state_path: str | Path,
    error: StateCorruptError,
    articles: Iterable[Article],
    now: datetime,
    settings: Settings,
    base: RunResult,
) -> RunResult:
    """Keep the broken file for inspection and re-learn current stories, posting none of them."""
    backup = set_aside(state_path)
    save_state(state_path, _seed(BotState.empty(), articles, now, settings))
    problem = (
        f"The posted-stories file could not be read ({error}). It was moved to {backup}. "
        "This run posted nothing and only re-learned the current stories, so none are posted twice."
    )
    return replace(base, fatal_error=problem)


def _seed(state: BotState, articles: Iterable[Article], now: datetime, settings: Settings) -> BotState:
    """Mark every current story as known without posting it."""
    cutoff = now - timedelta(hours=settings.max_age_hours)
    for article in articles:
        if article.published >= cutoff and not state.has(article.key):
            state = _remember(state, article, SEEDED, now)
    return state


def _post_all(run: _Run, stories: list[Story], state: BotState) -> _PostOutcome:
    counts = {"posted": 0, "unconfirmed": 0, "failed": 0, "arabic": 0}
    arabic, arabic_problem = run.arabic, None
    for index, story in enumerate(stories):
        if index:
            run.sleep(run.config.settings.post_delay_seconds)
        photo = (
            download_photo(run.session, story.lead.image_url, run.config.settings.request_timeout_seconds)
            if run.config.style.banner
            else None
        )
        tags = story_tags(story, run.config)
        state, result, fatal = _post_english(run, story, photo, tags, state)
        if fatal:
            return _PostOutcome(
                state, counts["posted"], counts["unconfirmed"], counts["failed"], counts["arabic"], fatal
            )
        counts[result] += 1
        save_state(run.state_path, state)  # after every post, so a crash cannot cause a repeat
        if result in ("posted", "unconfirmed") and arabic is not None:
            state, sent, stop = _post_arabic(run, arabic, story, photo, tags, state)
            counts["arabic"] += sent
            if stop:
                arabic, arabic_problem = None, stop if stop.startswith("Arabic channel") else arabic_problem
            save_state(run.state_path, state)
    return _PostOutcome(
        state,
        counts["posted"],
        counts["unconfirmed"],
        counts["failed"],
        counts["arabic"],
        arabic_error=arabic_problem,
    )


def _post_english(
    run: _Run, story: Story, photo: bytes | None, tags: list[str], state: BotState
) -> tuple[BotState, str, str | None]:
    """Returns the new state, what happened ("posted", "unconfirmed" or "failed"), and a setup error."""
    lead = story.lead
    try:
        kind = send_story(
            run.client, run.english, story, render_post(story, run.english.edition, run.config, photo, tags)
        )
    except FatalTelegramError as exc:
        return state, "failed", str(exc)
    except UncertainDeliveryError as exc:
        log.warning("%s. It may already be in the channel, so it will not be sent again.", exc)
        return _remember_story(state, story, POSTED, run.now), "unconfirmed", None
    except TelegramError as exc:
        if exc.retryable:
            log.warning("Could not post %s: %s (will try again next run)", lead.url, exc)
            return state, "failed", None
        log.warning("Telegram rejected %s: %s (will not try again)", lead.url, exc)
        # Only the lead is given up on; another site's version of the story can still be posted.
        return _remember(state, lead, FAILED, run.now), "failed", None
    log.info(
        "Posted (%s) [%s] score %d, %d site(s): %s", kind, lead.topic, story.score, story.sources, lead.title
    )
    return _remember_story(state, story, POSTED, run.now), "posted", None


def _post_arabic(
    run: _Run, target: Target, story: Story, photo: bytes | None, tags: list[str], state: BotState
) -> tuple[BotState, int, str | None]:
    """Best effort. Returns the new state, 1 if posted, and why Arabic must stop for this run (if so).

    A stop message starting with "Arabic channel" is a setup problem that turns the run red.
    """
    if run.translator is None:  # only happens if the Arabic channel was set up without a translator
        return state, 0, None
    try:
        arabic_story = translate_story(run.translator, story, run.config)
        send_story(
            run.client,
            target,
            arabic_story,
            render_post(arabic_story, target.edition, run.config, photo, tags),
        )
    except TranslationError as exc:
        log.warning("Arabic version skipped: %s", exc)
        if exc.setup_problem:
            return state, 0, f"Arabic channel: {exc}"
        return state, 0, (str(exc) if exc.pauses_run else None)
    except FatalTelegramError as exc:
        return state, 0, f"Arabic channel: {exc}"
    except UncertainDeliveryError as exc:
        log.warning("Arabic: %s. It may already be in the channel.", exc)
        return state, 0, None
    except TelegramError as exc:
        log.warning("Could not post the Arabic version of %s: %s", story.lead.url, exc)
        return state, 0, None
    except Exception:  # a surprise in the Arabic step must never undo or repeat the English post
        log.exception("Arabic version of %s failed unexpectedly", story.lead.url)
        return state, 0, None
    record = state.records[story.lead.key]
    log.info("Posted in Arabic: %s", arabic_story.lead.title)
    return state.with_record(story.lead.key, replace(record, title_ar=arabic_story.lead.title)), 1, None


def _remember_story(state: BotState, story: Story, status: str, now: datetime) -> BotState:
    """The lead gets `status`; the other sites' versions are remembered as repeats of it."""
    state = _remember(state, story.lead, status, now, score=story.score, sources=story.sources)
    for other in story.others:
        state = _remember(state, other, DUPLICATE, now)
    return state


def _remember(
    state: BotState, article: Article, status: str, now: datetime, score: int = 0, sources: int = 1
) -> BotState:
    record = PostedRecord(
        at=now,
        title=article.title,
        topic=article.topic,
        status=status,
        url=article.url,
        score=score,
        sources=sources,
        summary=first_sentence(article.summary),
    )
    return state.with_record(article.key, record)


def _show_dry_run(
    stories: list[Story],
    state: BotState,
    config: Config,
    english: Target,
    arabic: Target | None,
    now: datetime,
    forced: tuple[bool, bool],
    banners_dir: Path | None,
    session: requests.Session,
    out: Out,
) -> None:
    """Print the posts and scheduled posts exactly as a real run would send them. Nothing is sent or saved.

    Arabic versions are not translated in a dry run, so previews never use Gemini's free limit.
    """
    for number, story in enumerate(stories, start=1):
        photo = download_photo(session, story.lead.image_url, config.settings.request_timeout_seconds)
        post = render_post(
            story, english.edition, config, photo if config.style.banner else None, story_tags(story, config)
        )
        out(_preview(story, post.caption))
        if banners_dir is not None and post.banner:
            banners_dir.mkdir(parents=True, exist_ok=True)
            (banners_dir / f"banner_{number}.jpg").write_bytes(post.banner)
        if arabic is not None:
            out(f"(also translated into Arabic and posted to {arabic.chat_id})\n")
    send_scheduled(None, english, arabic, state, config, now, forced[0], forced[1], out)


def _with_image(session: requests.Session, story: Story, timeout: float) -> Story:
    if story.lead.image_url:
        return story
    image = fetch_og_image(session, story.lead.url, timeout)
    return replace(story, lead=replace(story.lead, image_url=image)) if image else story


def _preview(story: Story, caption: str) -> str:
    lead = story.lead
    when = lead.published.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"----- [{lead.topic}] {lead.source} | {when} | score {story.score}, {story.sources} site(s)\n"
        f"image: {lead.image_url or '(none, banner uses the brand background)'}\n"
        f"{caption}\n"
    )
