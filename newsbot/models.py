"""Plain data types shared by the whole bot. All are immutable."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str


@dataclass(frozen=True)
class Topic:
    name: str
    hashtag: str  # without the leading "#"
    feeds: tuple[FeedSource, ...]


@dataclass(frozen=True)
class Settings:
    max_posts_per_run: int = 4
    max_age_hours: int = 12
    summary_max_chars: int = 280
    post_delay_seconds: float = 3
    duplicate_title_similarity: float = 0.6
    state_retention_days: int = 7
    request_timeout_seconds: int = 15


@dataclass(frozen=True)
class Config:
    settings: Settings
    topics: tuple[Topic, ...]


@dataclass(frozen=True)
class Article:
    key: str  # normalised link, used to remember what was posted
    url: str
    title: str
    summary: str
    image_url: str | None
    published: datetime  # timezone-aware, UTC
    topic: str
    hashtag: str
    source: str
