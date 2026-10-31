"""Build the Telegram post text (HTML) for one story."""

import html

from newsbot.models import Article, PostStyle, Story
from newsbot.text import truncate, utf16_len

CAPTION_LIMIT = 1024  # Telegram photo caption limit, counted after HTML tags are removed
_MIN_SUMMARY = 40
_GAP = "\n\n"
_SEPARATOR = "  |  "


def build_caption(
    story: Story, style: PostStyle, top_story_min_sources: int, limit: int = CAPTION_LIMIT
) -> str:
    """Topic line, bold headline, short summary, then source, link, hashtag and channel name."""
    lead = story.lead
    header = _header(lead, style, story.sources >= top_story_min_sources)
    tag_line = f"#{lead.hashtag}" + (
        f"{_SEPARATOR}{style.channel_signature}" if style.channel_signature else ""
    )
    source_line_plain = f"{lead.source}  -  Read more"
    fixed = utf16_len(header) + 1 + utf16_len(_GAP) + utf16_len(source_line_plain) + 1 + utf16_len(tag_line)
    title, summary = _fit(lead.title, lead.summary, fixed, limit)

    parts = [f"{_escape(header)}\n<b>{_escape(title)}</b>"]
    if summary:
        parts.append(_escape(summary))
    parts.append(
        f'{_escape(lead.source)}  -  <a href="{html.escape(lead.url, quote=True)}">Read more</a>\n'
        f"{_escape(tag_line)}"
    )
    return _GAP.join(parts)


def _header(lead: Article, style: PostStyle, top_story: bool) -> str:
    label = f"{lead.emoji} {lead.topic.upper()}".strip()
    if top_story and style.top_story_label:
        label += f"{_SEPARATOR}{style.top_story_label}"
    return label


def _fit(title: str, summary: str, fixed: int, limit: int) -> tuple[str, str]:
    """Shorten the summary first, then the title, until the visible text fits."""
    room_for_summary = limit - fixed - utf16_len(title) - utf16_len(_GAP)
    if summary and utf16_len(summary) > room_for_summary:
        summary = truncate(summary, room_for_summary) if room_for_summary >= _MIN_SUMMARY else ""
    if utf16_len(title) > limit - fixed:
        title = truncate(title, limit - fixed)
    return title, summary


def _escape(text: str) -> str:
    return html.escape(text, quote=False)
