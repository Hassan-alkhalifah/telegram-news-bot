"""Build the Telegram post text (HTML) for one article."""

import html

from newsbot.models import Article
from newsbot.text import truncate, utf16_len

CAPTION_LIMIT = 1024  # Telegram photo caption limit, counted after HTML tags are removed
_MIN_SUMMARY = 40
_GAP = "\n\n"


def build_caption(article: Article, limit: int = CAPTION_LIMIT) -> str:
    footer = f"#{article.hashtag} | Source: {article.source}\nRead more"
    title, summary = _fit(article.title, article.summary, footer, limit)
    parts = [f"<b>{_escape(title)}</b>"]
    if summary:
        parts.append(_escape(summary))
    parts.append(
        f"#{_escape(article.hashtag)} | Source: {_escape(article.source)}\n"
        f'<a href="{html.escape(article.url, quote=True)}">Read more</a>'
    )
    return _GAP.join(parts)


def _fit(title: str, summary: str, footer: str, limit: int) -> tuple[str, str]:
    """Shorten the summary first, then the title, until the visible text fits."""
    fixed = utf16_len(footer) + utf16_len(_GAP)
    room_for_summary = limit - fixed - utf16_len(title) - utf16_len(_GAP)
    if summary and utf16_len(summary) > room_for_summary:
        summary = truncate(summary, room_for_summary) if room_for_summary >= _MIN_SUMMARY else ""
    if utf16_len(title) > limit - fixed:
        title = truncate(title, limit - fixed)
    return title, summary


def _escape(text: str) -> str:
    return html.escape(text, quote=False)
