"""Plain data types shared by the whole bot. All are immutable."""

import re
from dataclasses import dataclass, field
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
    emoji: str = ""
    big_names: re.Pattern[str] | None = None  # well-known names that make a story matter more
    min_score: int | None = None  # overrides settings.min_score for this topic


@dataclass(frozen=True)
class Settings:
    max_posts_per_run: int = 3
    max_age_hours: int = 12
    summary_max_chars: int = 200
    post_delay_seconds: float = 3
    min_score: int = 2
    top_story_min_sources: int = 3
    same_story_similarity: float = 0.5
    state_retention_days: int = 7
    request_timeout_seconds: int = 15


@dataclass(frozen=True)
class Filters:
    """Rules that recognise filler (lists, guides, deals, reviews) and words of real news."""

    skip_tags: frozenset[str] = frozenset()  # lower-case feed categories
    skip_url_parts: tuple[str, ...] = ()  # lower-case pieces of a link's path
    skip_titles: tuple[re.Pattern[str], ...] = ()
    strong_words: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class PostStyle:
    channel_signature: str = ""
    top_story_label: str = "TOP STORY"


@dataclass(frozen=True)
class DigestSettings:
    enabled: bool = False
    local_hour: int = 21
    utc_offset_hours: float = 3
    size: int = 5
    min_stories: int = 3
    title: str = "Today's top stories"


@dataclass(frozen=True)
class Config:
    settings: Settings
    topics: tuple[Topic, ...]
    filters: Filters = field(default_factory=Filters)
    style: PostStyle = field(default_factory=PostStyle)
    digest: DigestSettings = field(default_factory=DigestSettings)

    def topic(self, name: str) -> Topic | None:
        return next((t for t in self.topics if t.name == name), None)


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
    emoji: str = ""
    tags: tuple[str, ...] = ()  # the feed's own categories for this article


@dataclass(frozen=True)
class Story:
    """One piece of news, maybe reported by several sites. `lead` is the version that gets posted."""

    lead: Article
    others: tuple[Article, ...] = ()
    score: int = 0

    @property
    def sources(self) -> int:
        return len({a.source for a in (self.lead, *self.others)})
