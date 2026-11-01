"""Plain data types shared by the whole bot. All are immutable."""

import re
from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"  # free tier; Google recommends it for new projects
GEMINI_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")


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
    names: tuple[str, ...] = ()  # the big names as written in config.yaml (for name hashtags)
    suit: str = "spade"  # card suit on the banner badge: spade, heart, diamond or club
    arabic_name: str = ""
    arabic_hashtag: str = ""


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
    blocked: tuple[re.Pattern[str], ...] = ()  # 18+ and other unwanted subjects; never posted


@dataclass(frozen=True)
class PostStyle:
    channel_signature: str = ""
    top_story_label: str = "TOP STORY"
    brand_name: str = "SAV NEWS"
    banner: bool = True
    name_hashtags: int = 2
    read_button: str = "Read full story"
    share_button: str = "Share"
    source_label: str = "Source"


@dataclass(frozen=True)
class ArabicSettings:
    enabled: bool = False
    channel_signature: str = ""
    top_story_label: str = "خبر بارز"
    read_button: str = "اقرأ الخبر كاملاً"
    share_button: str = "مشاركة"
    source_label: str = "المصدر"
    digest_title: str = "أهم أخبار اليوم"
    weekly_title: str = "أهم أخبار الأسبوع"
    poll_question: str = "ما أهم خبر هذا الأسبوع؟"
    model: str = DEFAULT_GEMINI_MODEL  # the Gemini model that translates


@dataclass(frozen=True)
class WeeklySettings:
    enabled: bool = False
    weekday: int = 4  # Monday is 0, Friday is 4 (local time, see digest.utc_offset_hours)
    local_hour: int = 20
    size: int = 10
    min_stories: int = 3
    title: str = "Top 10 of the week"
    poll: bool = True
    poll_question: str = "Which was the biggest news this week?"
    poll_options: int = 5


@dataclass(frozen=True)
class LanguagePicker:
    text: str = "Choose your language."
    english_button: str = "English"
    arabic_button: str = "العربية"
    banner_subtitle: str = "AI, games, anime and tech news"
    banner_subtitle_ar: str = "أخبار الذكاء الاصطناعي " "والألعاب والأنمي والتقنية"


@dataclass(frozen=True)
class Edition:
    """How posts look in one channel: its language, labels and signature."""

    language: str  # "en" or "ar"
    signature: str
    top_story_label: str
    read_button: str
    share_button: str
    source_label: str
    digest_title: str
    weekly_title: str
    poll_question: str

    @property
    def rtl(self) -> bool:
        return self.language == "ar"


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
    arabic: ArabicSettings = field(default_factory=ArabicSettings)
    weekly: WeeklySettings = field(default_factory=WeeklySettings)
    language_picker: LanguagePicker = field(default_factory=LanguagePicker)

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
