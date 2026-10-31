"""The evening digest and the weekly top 10: posts that list the strongest stories already posted."""

import html
from dataclasses import dataclass
from datetime import datetime, timedelta

from newsbot.models import Config, DigestSettings, WeeklySettings
from newsbot.state import POSTED, BotState, PostedRecord
from newsbot.text import truncate

DAY = timedelta(hours=24)
WEEK = timedelta(days=7)
_POLL_OPTION_LIMIT = 100  # Telegram's limit per poll answer


@dataclass(frozen=True)
class DigestEntry:
    title: str
    url: str
    emoji: str
    hashtag: str
    sources: int


def local_time(now: datetime, utc_offset_hours: float) -> datetime:
    return now + timedelta(hours=utc_offset_hours)


def digest_day_if_due(
    state: BotState, settings: DigestSettings, now: datetime, force: bool = False
) -> str | None:
    """The local date to send a digest for, or None when it is not time yet or it was already sent."""
    local = local_time(now, settings.utc_offset_hours)
    day = local.date().isoformat()
    if force:
        return day
    if not settings.enabled or local.hour < settings.local_hour or state.digest_day == day:
        return None
    return day


def weekly_day_if_due(
    state: BotState, settings: WeeklySettings, utc_offset_hours: float, now: datetime, force: bool = False
) -> str | None:
    """The local date of this week's top 10, or None when it is not the day and hour yet, or already sent."""
    local = local_time(now, utc_offset_hours)
    day = local.date().isoformat()
    if force:
        return day
    if (
        not settings.enabled
        or local.weekday() != settings.weekday
        or local.hour < settings.local_hour
        or state.weekly_day == day
    ):
        return None
    return day


def digest_entries(
    state: BotState,
    config: Config,
    now: datetime,
    lookback: timedelta = DAY,
    size: int = 5,
    arabic: bool = False,
) -> list[DigestEntry]:
    """Stories posted before this run within `lookback`, strongest first.

    Posts from this run itself are left out, so a digest never repeats what went out a moment ago.
    The Arabic list uses Arabic titles and skips stories that were not posted in Arabic.
    """
    since = now - lookback
    records = [r for r in state.records.values() if r.status == POSTED and r.url and since <= r.at < now]
    if arabic:
        records = [r for r in records if r.title_ar]
    records.sort(key=lambda r: (r.score, r.at), reverse=True)
    return [_entry(r, config, arabic) for r in records[:size]]


def _entry(record: PostedRecord, config: Config, arabic: bool) -> DigestEntry:
    topic = config.topic(record.topic)
    return DigestEntry(
        title=record.title_ar if arabic else record.title,
        url=record.url,
        emoji=topic.emoji if topic else "",
        hashtag=topic.hashtag if topic else record.topic,
        sources=record.sources,
    )


def build_digest(entries: list[DigestEntry], title: str, date_label: str, signature: str) -> str:
    lines = [
        f"{n}. "
        + (f"{_escape(e.emoji)} " if e.emoji else "")
        + f'<a href="{html.escape(e.url, quote=True)}">{_escape(e.title)}</a>'
        for n, e in enumerate(entries, start=1)
    ]
    text = f"<b>{_escape(title)}</b>\n{_escape(date_label)}\n\n" + "\n".join(lines)
    if signature:
        text += f"\n\n{_escape(signature)}"
    return text


def poll_options(entries: list[DigestEntry], count: int) -> list[str]:
    """Poll answers from the top stories: short enough for Telegram and never repeated."""
    options: list[str] = []
    for entry in entries:
        option = truncate(entry.title, _POLL_OPTION_LIMIT)
        if option and option not in options:
            options.append(option)
        if len(options) == count:
            break
    return options


def date_label(now: datetime, utc_offset_hours: float) -> str:
    local = local_time(now, utc_offset_hours)
    return f"{local.day} {local:%b %Y}"


def _escape(text: str) -> str:
    return html.escape(text, quote=False)
