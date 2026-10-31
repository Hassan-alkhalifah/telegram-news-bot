"""Shared builders for tests."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from newsbot.models import Article, Config, FeedSource, Settings, Topic
from newsbot.text import url_key

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
TOKEN = "123456789:" + "A" * 35
API = f"https://api.telegram.org/bot{TOKEN}"


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


def make_config(topics=("AI", "Games"), **settings) -> Config:
    built = tuple(
        Topic(
            name=name,
            hashtag=name,
            feeds=(FeedSource(name=f"{name} Feed", url=f"https://{name.lower()}.example.com/rss"),),
        )
        for name in topics
    )
    return Config(settings=Settings(**{"post_delay_seconds": 0, **settings}), topics=built)


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
