"""Download RSS/Atom feeds and turn their entries into Articles."""

import calendar
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests

from newsbot.images import image_from_entry
from newsbot.models import Article, Config, FeedSource, Topic
from newsbot.text import clean_html, truncate, url_key

log = logging.getLogger(__name__)

_MAX_TITLE_CHARS = 250
_DATE_FIELDS = ("published_parsed", "updated_parsed", "created_parsed")


class FeedError(Exception):
    """A feed could not be downloaded or read."""


@dataclass(frozen=True)
class FeedResults:
    articles: tuple[Article, ...]
    failed: tuple[str, ...]  # "Topic/Source" labels
    total: int


def fetch_feed(session: requests.Session, url: str, timeout: float) -> bytes:
    try:
        response = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise FeedError(f"network error: {exc}") from exc
    if response.status_code != 200:
        raise FeedError(f"HTTP {response.status_code}")
    return response.content


def parse_feed(
    content: bytes, topic: Topic, source: FeedSource, summary_max_chars: int, now: datetime
) -> list[Article]:
    parsed = feedparser.parse(content)
    if not parsed.entries:
        # No version means this was not RSS/Atom at all, e.g. an HTML "access denied" page.
        if not parsed.get("version"):
            reason = (
                parsed.get("bozo_exception")
                or "the page is not an RSS or Atom feed; the site may be blocking the bot"
            )
            raise FeedError(f"{source.name}: could not read the feed ({reason})")
        return []
    articles = []
    for entry in parsed.entries:
        try:
            article = _to_article(entry, topic, source, summary_max_chars, now)
        except (ValueError, TypeError, AttributeError, AssertionError) as exc:
            # One strange story (broken link, odd field types) must not hide the rest of the feed.
            log.warning(
                "Skipped a story from %s that could not be read (%s: %s)",
                source.name,
                type(exc).__name__,
                exc,
            )
            continue
        if article is not None:
            articles.append(article)
    return articles


def _to_article(
    entry: Mapping[str, Any], topic: Topic, source: FeedSource, summary_max_chars: int, now: datetime
) -> Article | None:
    link = str(entry.get("link") or "").strip()
    title = clean_html(entry.get("title"))
    published = _published(entry, now)
    if not link.startswith(("http://", "https://")) or not title or published is None:
        return None
    summary = clean_html(_summary_html(entry))
    if summary.casefold() == title.casefold():
        summary = ""
    return Article(
        key=url_key(link),
        url=link,
        title=truncate(title, _MAX_TITLE_CHARS),
        summary=truncate(summary, summary_max_chars),
        image_url=image_from_entry(entry, link),
        published=published,
        topic=topic.name,
        hashtag=topic.hashtag,
        source=source.name,
    )


def _summary_html(entry: Mapping[str, Any]) -> str:
    summary = entry.get("summary") or ""
    if summary.strip():
        return summary
    contents = entry.get("content") or []
    return str(contents[0].get("value", "")) if contents else ""


def _published(entry: Mapping[str, Any], now: datetime) -> datetime | None:
    """Entry time in UTC (feedparser already converts to UTC). Future times become `now`."""
    for field in _DATE_FIELDS:
        value = entry.get(field)
        if value:
            return min(datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc), now)
    return None


def collect_articles(session: requests.Session, config: Config, now: datetime) -> FeedResults:
    """Read every feed in the config. One broken feed never stops the others."""
    settings = config.settings
    articles: list[Article] = []
    failed: list[str] = []
    total = 0
    for topic in config.topics:
        for source in topic.feeds:
            total += 1
            label = f"{topic.name}/{source.name}"
            try:
                content = fetch_feed(session, source.url, settings.request_timeout_seconds)
                found = parse_feed(content, topic, source, settings.summary_max_chars, now)
            except FeedError as exc:
                log.warning("Feed failed: %s (%s)", label, exc)
                failed.append(label)
                continue
            except Exception:  # last resort: a bug or surprise in one feed must not stop the others
                log.exception("Feed failed with an unexpected error: %s", label)
                failed.append(label)
                continue
            log.info("Feed ok: %s, %d stories", label, len(found))
            articles.extend(found)
    return FeedResults(articles=tuple(articles), failed=tuple(failed), total=total)
