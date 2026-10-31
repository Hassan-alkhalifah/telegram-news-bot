"""Remember which stories were already posted, in data/posted.json."""

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

log = logging.getLogger(__name__)

STATE_VERSION = 1
POSTED = "posted"  # sent (or probably sent) to the channel
DUPLICATE = "duplicate"  # same story as one already posted
FAILED = "failed"  # Telegram rejected it for good; never try again
SEEDED = "seeded"  # learned after the state file was lost; never post


class StateCorruptError(Exception):
    """The state file exists but cannot be read."""


@dataclass(frozen=True)
class PostedRecord:
    at: datetime
    title: str
    topic: str
    status: str  # one of the four statuses above
    url: str = ""
    score: int = 0  # how strong the story was (see config.yaml, min_score)
    sources: int = 1  # how many sites reported it
    summary: str = ""  # first sentence; helps spot the same news reworded on a later day
    title_ar: str = ""  # the Arabic title, when the story was also posted to the Arabic channel


def _frozen(mapping: Mapping | None = None) -> Mapping:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True)
class BotState:
    records: Mapping[str, PostedRecord] = field(default_factory=_frozen)
    topic_last_posted: Mapping[str, datetime] = field(default_factory=_frozen)
    digest_day: str = ""  # local date (YYYY-MM-DD) of the last evening digest
    weekly_day: str = ""  # local date of the last weekly top 10

    @classmethod
    def empty(cls) -> "BotState":
        return cls()

    def has(self, key: str) -> bool:
        return key in self.records

    def with_record(self, key: str, record: PostedRecord) -> "BotState":
        """A new state with this record added. The current state is left unchanged."""
        topic_times = dict(self.topic_last_posted)
        if record.status == POSTED:
            topic_times[record.topic] = max(record.at, topic_times.get(record.topic, record.at))
        return replace(
            self, records=_frozen({**self.records, key: record}), topic_last_posted=_frozen(topic_times)
        )

    def with_digest_day(self, day: str) -> "BotState":
        return replace(self, digest_day=day)

    def with_weekly_day(self, day: str) -> "BotState":
        return replace(self, weekly_day=day)

    def pruned(self, now: datetime, retention_days: int) -> "BotState":
        cutoff = now - timedelta(days=retention_days)
        kept = {k: r for k, r in self.records.items() if r.at >= cutoff}
        return replace(self, records=_frozen(kept))

    def known_stories(self) -> tuple[tuple[str, PostedRecord], ...]:
        """(key, record) for every version (each site's wording) of every story already posted."""
        return tuple((k, r) for k, r in self.records.items() if r.status in (POSTED, DUPLICATE))

    def recent_titles(self) -> tuple[str, ...]:
        return tuple(r.title for _, r in self.known_stories())


def _to_dict(state: BotState) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "posted": {
            key: {
                "at": r.at.isoformat(),
                "title": r.title,
                "topic": r.topic,
                "status": r.status,
                "url": r.url,
                "score": r.score,
                "sources": r.sources,
                "summary": r.summary,
                "title_ar": r.title_ar,
            }
            for key, r in sorted(state.records.items())
        },
        "topic_last_posted": {topic: at.isoformat() for topic, at in sorted(state.topic_last_posted.items())},
        "digest_day": state.digest_day,
        "weekly_day": state.weekly_day,
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_dict(data: Mapping[str, Any]) -> BotState:
    records = {
        str(key): PostedRecord(
            at=_parse_time(item["at"]),
            title=str(item["title"]),
            topic=str(item["topic"]),
            status=str(item["status"]),
            url=str(item.get("url", "")),
            score=int(item.get("score", 0)),
            sources=int(item.get("sources", 1)),
            summary=str(item.get("summary", "")),
            title_ar=str(item.get("title_ar", "")),
        )
        for key, item in data.get("posted", {}).items()
    }
    topic_times = {str(t): _parse_time(at) for t, at in data.get("topic_last_posted", {}).items()}
    return BotState(
        records=_frozen(records),
        topic_last_posted=_frozen(topic_times),
        digest_day=str(data.get("digest_day", "")),
        weekly_day=str(data.get("weekly_day", "")),
    )


def load_state(path: str | Path) -> BotState:
    """Read the state file. A missing file means a first run; a broken one raises StateCorruptError."""
    state_path = Path(path)
    if not state_path.exists():
        return BotState.empty()
    try:
        return _from_dict(json.loads(state_path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise StateCorruptError(f"{state_path}: {type(exc).__name__}: {exc}") from exc


def set_aside(path: str | Path) -> Path:
    """Move a broken state file to <name>.corrupt so it can be inspected later."""
    state_path = Path(path)
    backup = state_path.with_name(state_path.name + ".corrupt")
    os.replace(state_path, backup)
    return backup


def save_state(path: str | Path, state: BotState) -> None:
    """Write the state atomically so a crash mid-write cannot leave a half-written file."""
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.with_name(state_path.name + ".tmp")
    text = json.dumps(_to_dict(state), ensure_ascii=False, indent=1) + "\n"
    temp_path.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp_path, state_path)
