"""The evening digest: one post listing the day's strongest stories."""

import html
from dataclasses import dataclass
from datetime import datetime, timedelta

from newsbot.models import Config, DigestSettings, PostStyle
from newsbot.state import POSTED, BotState, PostedRecord

_LOOKBACK = timedelta(hours=24)


@dataclass(frozen=True)
class DigestEntry:
    title: str
    url: str
    emoji: str
    hashtag: str
    sources: int


def local_time(now: datetime, settings: DigestSettings) -> datetime:
    return now + timedelta(hours=settings.utc_offset_hours)


def digest_day_if_due(
    state: BotState, settings: DigestSettings, now: datetime, force: bool = False
) -> str | None:
    """The local date to send a digest for, or None when it is not time yet or it was already sent."""
    local = local_time(now, settings)
    day = local.date().isoformat()
    if force:
        return day
    if not settings.enabled or local.hour < settings.local_hour or state.digest_day == day:
        return None
    return day


def digest_entries(
    state: BotState, config: Config, settings: DigestSettings, now: datetime
) -> list[DigestEntry]:
    """Stories posted in the 24 hours before this run, strongest first.

    Posts from this run itself are left out, so the digest never repeats what went out a moment ago.
    """
    since = now - _LOOKBACK
    records = [r for r in state.records.values() if r.status == POSTED and r.url and since <= r.at < now]
    records.sort(key=lambda r: (r.score, r.at), reverse=True)
    return [_entry(r, config) for r in records[: settings.size]]


def _entry(record: PostedRecord, config: Config) -> DigestEntry:
    topic = config.topic(record.topic)
    return DigestEntry(
        title=record.title,
        url=record.url,
        emoji=topic.emoji if topic else "",
        hashtag=topic.hashtag if topic else record.topic,
        sources=record.sources,
    )


def build_digest(entries: list[DigestEntry], title: str, date_label: str, style: PostStyle) -> str:
    lines = [
        f"{n}. "
        + (f"{_escape(e.emoji)} " if e.emoji else "")
        + f'<a href="{html.escape(e.url, quote=True)}">{_escape(e.title)}</a>'
        for n, e in enumerate(entries, start=1)
    ]
    text = f"<b>{_escape(title)}</b>\n{_escape(date_label)}\n\n" + "\n".join(lines)
    if style.channel_signature:
        text += f"\n\n{_escape(style.channel_signature)}"
    return text


def date_label(now: datetime, settings: DigestSettings) -> str:
    local = local_time(now, settings)
    return f"{local.day} {local:%b %Y}"


def _escape(text: str) -> str:
    return html.escape(text, quote=False)
