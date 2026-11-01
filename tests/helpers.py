"""Shared builders for tests."""

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from newsbot.models import Article, Config, FeedSource, PostStyle, Settings, Story, Topic
from newsbot.text import url_key

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
TOKEN = "123456789:" + "A" * 35
API = f"https://api.telegram.org/bot{TOKEN}"
ROOT = Path(__file__).resolve().parent.parent
LIVE_STORIES = Path(__file__).resolve().parent / "data" / "live_stories_2026-10-31.json"


def unique_title(tag: str) -> str:
    """A title whose words share nothing with other tags, so it never looks like a duplicate."""
    return " ".join(f"{tag}{n}" for n in range(5))


def make_article(**overrides) -> Article:
    values = {
        "url": "https://example.com/a",
        "title": unique_title("base"),
        "summary": "Summary text.",
        "image_url": "https://example.com/a.jpg",
        "published": NOW - timedelta(hours=1),
        "topic": "AI",
        "hashtag": "AI",
        "source": "Example",
    }
    values.update(overrides)
    values.setdefault("key", url_key(values["url"]))
    return Article(**values)


def make_story(lead: Article, *others: Article, score: int = 0) -> Story:
    return Story(lead=lead, others=tuple(others), score=score)


def make_config(
    topics=("AI", "Games"),
    *,
    filters=None,
    style=None,
    digest=None,
    arabic=None,
    weekly=None,
    topic_extra=None,
    **settings,
):
    """A small config. min_score defaults to 0 so tests about other things are not filtered out."""
    topic_extra = topic_extra or {}
    built = tuple(
        Topic(
            name=name,
            hashtag=name,
            feeds=(FeedSource(name=f"{name} Feed", url=f"https://{name.lower()}.example.com/rss"),),
            **topic_extra.get(name, {}),
        )
        for name in topics
    )
    # Banners are off unless a test asks for them: other tests check the plain photo link.
    style = style if style is not None else PostStyle(banner=False)
    pairs = (
        ("filters", filters),
        ("style", style),
        ("digest", digest),
        ("arabic", arabic),
        ("weekly", weekly),
    )
    extra = {k: v for k, v in pairs if v is not None}
    return Config(
        settings=Settings(**{"post_delay_seconds": 0, "min_score": 0, **settings}), topics=built, **extra
    )


def live_articles() -> list[Article]:
    """211 real stories from 32 news feeds, saved on 2026-10-31 (titles, summaries, tags, links)."""
    rows = json.loads(LIVE_STORIES.read_text(encoding="utf-8"))
    return [
        Article(
            key=url_key(row["link"]),
            url=row["link"],
            title=row["title"],
            summary=row.get("summary", ""),
            image_url=None,
            published=NOW - timedelta(hours=1),
            topic=row["topic"],
            hashtag=row["topic"],
            source=row["source"],
            tags=tuple(row.get("tags", [])),
        )
        for row in rows
    ]


def live_article(title_start: str) -> Article:
    matches = [a for a in live_articles() if a.title.startswith(title_start)]
    assert len(matches) == 1, f"expected one live story starting with {title_start!r}, found {len(matches)}"
    return matches[0]


def rfc822(dt: datetime) -> str:
    return format_datetime(dt)


def rss_item(title, link, pub="Sat, 31 Oct 2026 11:00:00 +0000", description="", extra="") -> str:
    parts = ["<item>"]
    if title is not None:
        parts.append(f"<title>{title}</title>")
    if link is not None:
        parts.append(f"<link>{link}</link>")
    if pub is not None:
        parts.append(f"<pubDate>{pub}</pubDate>")
    if description:
        parts.append(f"<description><![CDATA[{description}]]></description>")
    parts.append(extra)
    parts.append("</item>")
    return "".join(parts)


def rss(*items: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>Test feed</title><link>https://example.com</link>"
        + "".join(items)
        + "</channel></rss>"
    ).encode("utf-8")


def refused(url: str = "https://example.com") -> Exception:
    """A connection error where nothing reached the server (safe to retry)."""
    import requests
    from urllib3.exceptions import MaxRetryError, NewConnectionError

    return requests.ConnectionError(MaxRetryError(None, url, NewConnectionError(None, "Connection refused")))


def reset() -> Exception:
    """A connection that broke after the request was sent (the server may have acted on it)."""
    import requests
    from urllib3.exceptions import ProtocolError

    return requests.ConnectionError(
        ProtocolError("Connection aborted.", ConnectionResetError("reset by peer"))
    )


GEMINI_KEY = "test-gemini-key-not-real"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"


def gemini_answer(*translations: str) -> dict:
    """A successful generateContent answer carrying the translations as JSON."""
    import json

    text = json.dumps({"translations": list(translations)}, ensure_ascii=False)
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]}


def gemini_error(code: int, status: str, message: str = "error", *details: dict) -> dict:
    """An error body in Google's API format (as seen live for an invalid key)."""
    return {"error": {"code": code, "message": message, "status": status, "details": list(details)}}
