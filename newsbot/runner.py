"""One bot run: read feeds, pick stories, post them, remember them."""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import requests

from newsbot.feeds import collect_articles
from newsbot.formatter import build_caption
from newsbot.images import fetch_og_image
from newsbot.models import Article, Config, Settings
from newsbot.poster import post_article
from newsbot.selector import select_articles
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


@dataclass(frozen=True)
class RunResult:
    posted: int = 0
    would_post: int = 0  # dry run only
    unconfirmed: int = 0  # sent, but Telegram gave no clear answer; remembered as posted
    duplicates: int = 0
    failed_posts: int = 0
    feeds_failed: int = 0
    feeds_total: int = 0
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
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
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

    selection = select_articles(feeds.articles, state, config, now)
    for article in selection.duplicates:
        log.info("Skipping repeat story: %s (%s)", article.title, article.source)
        state = _remember(state, article, DUPLICATE, now)
    log.info(
        "%d new stories: %d chosen for this run, %d waiting, %d repeats skipped",
        len(selection.to_post) + selection.waiting,
        len(selection.to_post),
        selection.waiting,
        len(selection.duplicates),
    )

    articles = [_with_image(session, a, settings.request_timeout_seconds) for a in selection.to_post]
    if dry_run:
        for article in articles:
            out(_preview(article))
        return replace(base, would_post=len(articles), duplicates=len(selection.duplicates))

    if client is None:
        raise ValueError("A Telegram client is required unless dry_run is True")
    outcome = _post_all(client, chat_id, articles, state, state_path, now, settings, sleep)
    save_state(state_path, outcome.state)
    return replace(
        base,
        posted=outcome.posted,
        unconfirmed=outcome.unconfirmed,
        duplicates=len(selection.duplicates),
        failed_posts=outcome.failed,
        fatal_error=outcome.fatal_error,
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
    articles: list[Article],
    state: BotState,
    state_path: str | Path,
    now: datetime,
    settings: Settings,
    sleep: Callable[[float], None],
) -> _PostOutcome:
    posted = unconfirmed = failed = 0
    for index, article in enumerate(articles):
        if index:
            sleep(settings.post_delay_seconds)
        try:
            kind = post_article(client, chat_id, article, build_caption(article))
            log.info("Posted (%s) [%s] %s", kind, article.topic, article.title)
            state, posted = _remember(state, article, POSTED, now), posted + 1
        except FatalTelegramError as exc:
            return _PostOutcome(state, posted, unconfirmed, failed, fatal_error=str(exc))
        except UncertainDeliveryError as exc:
            log.warning("%s. It may already be in the channel, so it will not be sent again.", exc)
            state, unconfirmed = _remember(state, article, POSTED, now), unconfirmed + 1
        except TelegramError as exc:
            failed += 1
            if exc.retryable:
                log.warning("Could not post %s: %s (will try again next run)", article.url, exc)
                continue
            log.warning("Telegram rejected %s: %s (will not try again)", article.url, exc)
            state = _remember(state, article, FAILED, now)
        save_state(state_path, state)  # after every post, so a crash cannot cause a repeat
    return _PostOutcome(state, posted, unconfirmed, failed)


def _remember(state: BotState, article: Article, status: str, now: datetime) -> BotState:
    return state.with_record(article.key, PostedRecord(now, article.title, article.topic, status))


def _with_image(session: requests.Session, article: Article, timeout: float) -> Article:
    if article.image_url:
        return article
    image = fetch_og_image(session, article.url, timeout)
    return replace(article, image_url=image) if image else article


def _preview(article: Article) -> str:
    when = article.published.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"----- [{article.topic}] {article.source} | {when}\n"
        f"image: {article.image_url or '(none, will use link preview)'}\n"
        f"{build_caption(article)}\n"
    )
