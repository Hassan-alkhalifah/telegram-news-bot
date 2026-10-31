"""Build the Telegram post text (HTML), the buttons under it, and the name hashtags."""

import html
import re
from collections.abc import Sequence
from urllib.parse import quote

from newsbot.editions import topic_hashtag, topic_label
from newsbot.models import Article, Edition, Story, Topic
from newsbot.text import truncate, utf16_len

CAPTION_LIMIT = 1024  # Telegram photo caption limit, counted after HTML tags are removed
_MIN_SUMMARY = 40
_GAP = "\n\n"
_SEPARATOR = "  |  "
_NOT_HASHTAG_RE = re.compile(r"[^\w]")


def build_caption(
    story: Story,
    edition: Edition,
    topic: Topic | None,
    top_story_min_sources: int,
    name_tags: Sequence[str] = (),
    limit: int = CAPTION_LIMIT,
) -> str:
    """Topic line, bold headline, short summary, source, hashtags and the channel name."""
    lead = story.lead
    header = _header(story, edition, topic, top_story_min_sources)
    tags = " ".join(f"#{tag}" for tag in (topic_hashtag(topic, lead.hashtag, edition), *name_tags))
    tag_line = tags + (f"{_SEPARATOR}{edition.signature}" if edition.signature else "")
    source_line = f"{edition.source_label}: {lead.source}"
    fixed = utf16_len(header) + 1 + utf16_len(_GAP) + utf16_len(source_line) + 1 + utf16_len(tag_line)
    title, summary = _fit(lead.title, lead.summary, fixed, limit)

    parts = [f"{_escape(header)}\n<b>{_escape(title)}</b>"]
    if summary:
        parts.append(_escape(summary))
    parts.append(f"{_escape(source_line)}\n{_escape(tag_line)}")
    return _GAP.join(parts)


def build_buttons(article: Article, edition: Edition) -> dict:
    """Two buttons under the post: open the story, and share it in a chat."""
    share = f"https://t.me/share/url?url={quote(article.url, safe='')}&text={quote(article.title, safe='')}"
    return {
        "inline_keyboard": [
            [{"text": edition.read_button, "url": article.url}, {"text": edition.share_button, "url": share}]
        ]
    }


def name_hashtags(story: Story, topic: Topic | None, limit: int) -> list[str]:
    """Hashtags for the topic's big names in the story, in the order they appear: #Nintendo #Switch2."""
    if topic is None or limit <= 0:
        return []
    text = " ".join(a.title for a in (story.lead, *story.others))
    hits = []
    for name in topic.names:
        match = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
        if match:
            hits.append((match.start(), -len(name), name))
    found: list[str] = []
    for _, _, name in sorted(hits):
        tag = _NOT_HASHTAG_RE.sub("", name)
        taken = {t.casefold() for t in found} | {topic.hashtag.casefold()}
        if tag and not tag.isdigit() and tag.casefold() not in taken:
            found.append(tag)
    return found[:limit]


def _header(story: Story, edition: Edition, topic: Topic | None, top_story_min_sources: int) -> str:
    lead = story.lead
    label = f"{lead.emoji} {topic_label(topic, lead.topic, edition)}".strip()
    if story.sources >= top_story_min_sources and edition.top_story_label:
        label += f"{_SEPARATOR}{edition.top_story_label}"
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
