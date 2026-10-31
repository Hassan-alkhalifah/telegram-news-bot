"""One bot run: read feeds, pick the strongest stories, post them, remember them, maybe send the digest."""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import requests

from newsbot.digest import build_digest, date_label, digest_day_if_due, digest_entries
from newsbot.feeds import collect_articles
from newsbot.formatter import build_caption
from newsbot.images import fetch_og_image
from newsbot.models import Article, Config, Settings, Story
from newsbot.poster import post_article
from newsbot.quality import first_sentence
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

log = logging.getLogger(__name__)

Out = Callable[[str], None]


@dataclass(frozen=True)
class RunResult:
    posted: int = 0
    would_post: int = 0  # dry run only
    unconfirmed: int = 0  # sent, but Telegram gave no clear answer; remembered as posted
    duplicates: int = 0
    failed_posts: int = 0
    feeds_failed: int = 0
    feeds_total: int = 0
    digest_sent: bool = False
    fatal_error: str | None = None


@dataclass(frozen=True)
class _PostOutcome:
    state: BotState
    posted: int = 0
    unconfirmed: int = 0
    failed: int = 0
    fatal_error: str | None = None


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
    if dry_run:
        _show_dry_run(stories, state, config, now, force_digest, out)
        return replace(base, would_post=len(stories), duplicates=len(selection.duplicates))

    if client is None:
        raise ValueError("A Telegram client is required unless dry_run is True")
    outcome = _post_all(client, chat_id, stories, state, state_path, now, config, sleep)
    state, digest_sent, digest_error = outcome.state, False, None
    if outcome.fatal_error is None:
        state, digest_sent, digest_error = _send_digest_if_due(
            client, chat_id, state, config, now, force_digest, out
        )
    save_state(state_path, state)
    return replace(
        base,
        posted=outcome.posted,
        unconfirmed=outcome.unconfirmed,
        duplicates=len(selection.duplicates),
        failed_posts=outcome.failed,
        digest_sent=digest_sent,
        fatal_error=outcome.fatal_error or digest_error,
    )


def _log_selection(selection: Selection) -> None:
    log.info(
        "%d chosen for this run, %d strong stories waiting, %d weak stories skipped for now, "
        "%d filler skipped, %d repeats skipped",
        len(selection.to_post),
        selection.waiting,
        selection.weak,
        selection.filler,
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


def _post_all(
    client: TelegramClient,
    chat_id: str,
    stories: list[Story],
    state: BotState,
    state_path: str | Path,
    now: datetime,
    config: Config,
    sleep: Callable[[float], None],
) -> _PostOutcome:
    settings = config.settings
    posted = unconfirmed = failed = 0
    for index, story in enumerate(stories):
        if index:
            sleep(settings.post_delay_seconds)
        lead = story.lead
        caption = build_caption(story, config.style, settings.top_story_min_sources)
        try:
            kind = post_article(client, chat_id, lead, caption)
            log.info(
                "Posted (%s) [%s] score %d, %d site(s): %s",
                kind,
                lead.topic,
                story.score,
                story.sources,
                lead.title,
            )
            state, posted = _remember_story(state, story, POSTED, now), posted + 1
        except FatalTelegramError as exc:
            return _PostOutcome(state, posted, unconfirmed, failed, fatal_error=str(exc))
        except UncertainDeliveryError as exc:
            log.warning("%s. It may already be in the channel, so it will not be sent again.", exc)
            state, unconfirmed = _remember_story(state, story, POSTED, now), unconfirmed + 1
        except TelegramError as exc:
            failed += 1
            if exc.retryable:
                log.warning("Could not post %s: %s (will try again next run)", lead.url, exc)
                continue
            log.warning("Telegram rejected %s: %s (will not try again)", lead.url, exc)
            # Only the lead is given up on; another site's version of the story can still be posted.
            state = _remember(state, lead, FAILED, now)
        save_state(state_path, state)  # after every post, so a crash cannot cause a repeat
    return _PostOutcome(state, posted, unconfirmed, failed)


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


def _send_digest_if_due(
    client: TelegramClient | None,
    chat_id: str,
    state: BotState,
    config: Config,
    now: datetime,
    force: bool,
    out: Out,
) -> tuple[BotState, bool, str | None]:
    """Returns the new state, whether a digest was sent, and a fatal setup error if one happened.

    With no client (dry run) the digest is only printed.
    """
    settings = config.digest
    day = digest_day_if_due(state, settings, now, force)
    if day is None:
        return state, False, None
    entries = digest_entries(state, config, settings, now)
    needed = 1 if force else settings.min_stories
    if len(entries) < needed:
        log.info("Digest skipped for %s: only %d story(ies) posted in the last 24 hours", day, len(entries))
        # A forced digest with nothing to list must not use up the scheduled evening digest.
        keep_day_open = client is None or force
        return (state if keep_day_open else state.with_digest_day(day)), False, None
    text = build_digest(entries, settings.title, date_label(now, settings), config.style)
    if client is None:
        out(f"----- DIGEST for {day}\n{text}\n")
        return state, False, None
    try:
        client.send_message(chat_id, text, preview_url=entries[0].url)
    except FatalTelegramError as exc:
        return state, False, str(exc)
    except UncertainDeliveryError as exc:
        log.warning("%s. The digest may already be in the channel, so it is not sent again today.", exc)
        return state.with_digest_day(day), True, None
    except TelegramError as exc:
        if exc.retryable:
            log.warning("Could not send the digest: %s (will try again next run)", exc)
            return state, False, None
        log.warning("Telegram rejected the digest: %s (no digest today)", exc)
        return state.with_digest_day(day), False, None
    log.info("Digest sent for %s with %d stories", day, len(entries))
    return state.with_digest_day(day), True, None


def _show_dry_run(
    stories: list[Story], state: BotState, config: Config, now: datetime, force_digest: bool, out: Out
) -> None:
    """Print the posts and the digest exactly as a real run would send them. Nothing is sent or saved."""
    for story in stories:
        out(_preview(story, config))
    _send_digest_if_due(None, "", state, config, now, force_digest, out)


def _with_image(session: requests.Session, story: Story, timeout: float) -> Story:
    if story.lead.image_url:
        return story
    image = fetch_og_image(session, story.lead.url, timeout)
    return replace(story, lead=replace(story.lead, image_url=image)) if image else story


def _preview(story: Story, config: Config) -> str:
    lead = story.lead
    when = lead.published.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"----- [{lead.topic}] {lead.source} | {when} | score {story.score}, {story.sources} site(s)\n"
        f"image: {lead.image_url or '(none, will use link preview)'}\n"
        f"{build_caption(story, config.style, config.settings.top_story_min_sources)}\n"
    )
