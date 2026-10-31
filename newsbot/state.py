"""Remember which stories were already posted, in data/posted.json."""

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
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


def _frozen(mapping: Mapping | None = None) -> Mapping:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True)
class BotState:
    records: Mapping[str, PostedRecord] = field(default_factory=_frozen)
    topic_last_posted: Mapping[str, datetime] = field(default_factory=_frozen)

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
        return BotState(
            records=_frozen({**self.records, key: record}), topic_last_posted=_frozen(topic_times)
        )

    def pruned(self, now: datetime, retention_days: int) -> "BotState":
        cutoff = now - timedelta(days=retention_days)
        kept = {k: r for k, r in self.records.items() if r.at >= cutoff}
        return BotState(records=_frozen(kept), topic_last_posted=self.topic_last_posted)

    def recent_titles(self) -> tuple[str, ...]:
        return tuple(r.title for r in self.records.values() if r.status == POSTED)


def _to_dict(state: BotState) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "posted": {
            key: {"at": r.at.isoformat(), "title": r.title, "topic": r.topic, "status": r.status}
            for key, r in sorted(state.records.items())
        },
        "topic_last_posted": {topic: at.isoformat() for topic, at in sorted(state.topic_last_posted.items())},
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
        )
        for key, item in data.get("posted", {}).items()
    }
    topic_times = {str(t): _parse_time(at) for t, at in data.get("topic_last_posted", {}).items()}
    return BotState(records=_frozen(records), topic_last_posted=_frozen(topic_times))


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
