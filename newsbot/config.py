"""Load and validate config.yaml, failing early with a message a person can act on."""

import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import yaml

from newsbot.models import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_MODEL_RE,
    ArabicSettings,
    Config,
    DigestSettings,
    FeedSource,
    Filters,
    LanguagePicker,
    PostStyle,
    Settings,
    Topic,
    WeeklySettings,
)


class ConfigError(Exception):
    """config.yaml is missing or has a mistake."""


# name -> (allowed types, minimum, maximum)
_NumberRules = dict[str, tuple[tuple[type, ...], float, float]]
_SETTING_RULES: _NumberRules = {
    "max_posts_per_run": ((int,), 1, 50),
    "max_age_hours": ((int,), 1, 24 * 14),
    "summary_max_chars": ((int,), 0, 900),
    "post_delay_seconds": ((int, float), 0, 60),
    "min_score": ((int,), 0, 50),
    "top_story_min_sources": ((int,), 2, 50),
    "same_story_similarity": ((int, float), 0.2, 1),
    "state_retention_days": ((int,), 1, 90),
    "request_timeout_seconds": ((int,), 1, 120),
    "topic_gap_minutes": ((int,), 0, 24 * 60),
}
_DIGEST_NUMBER_RULES: _NumberRules = {
    "local_hour": ((int,), 0, 23),
    "utc_offset_hours": ((int, float), -12, 14),
    "size": ((int,), 1, 10),
    "min_stories": ((int,), 1, 10),
}
_FILTER_KEYS = ("skip_tags", "skip_url_parts", "skip_titles", "strong_words", "blocked")
_STYLE_TEXTS = (
    "channel_signature",
    "top_story_label",
    "brand_name",
    "read_button",
    "share_button",
    "source_label",
)
_ARABIC_TEXTS = (
    "channel_signature",
    "top_story_label",
    "read_button",
    "share_button",
    "source_label",
    "digest_title",
    "weekly_title",
    "poll_question",
    "model",
)
_WEEKLY_NUMBER_RULES: _NumberRules = {
    "local_hour": ((int,), 0, 23),
    "size": ((int,), 1, 10),
    "min_stories": ((int,), 1, 10),
    "poll_options": ((int,), 2, 10),
}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_SUITS = ("spade", "heart", "diamond", "club")
_TOPIC_KEYS = (
    "name",
    "hashtag",
    "emoji",
    "big_names",
    "min_score",
    "feeds",
    "suit",
    "arabic_name",
    "arabic_hashtag",
)
_ARABIC_HASHTAG_RE = re.compile(r"^\w{1,64}$")
_HASHTAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MAX_EMOJI_CHARS = 16


def load_config(path: str | Path) -> Config:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc
    return parse_config(data)


def parse_config(data: Any) -> Config:
    if not isinstance(data, Mapping):
        raise ConfigError("The config must be a mapping with 'settings' and 'topics'.")
    return Config(
        settings=_parse_settings(data.get("settings") or {}),
        topics=_parse_topics(data.get("topics")),
        filters=_parse_filters(data.get("filters") or {}),
        style=_parse_style(data.get("style") or {}),
        digest=_parse_digest(data.get("digest") or {}),
        arabic=_parse_arabic(data.get("arabic") or {}),
        weekly=_parse_weekly(data.get("weekly") or {}),
        language_picker=LanguagePicker(
            **_section(
                data.get("language_picker") or {},
                "language_picker",
                texts=("text", "english_button", "arabic_button", "banner_subtitle", "banner_subtitle_ar"),
            )
        ),
    )


def _parse_settings(raw: Any) -> Settings:
    _require_mapping(raw, "settings")
    _reject_unknown(raw, _SETTING_RULES, "setting")
    settings = Settings(**{name: _check_number(name, value, _SETTING_RULES) for name, value in raw.items()})
    if settings.state_retention_days * 24 <= settings.max_age_hours:
        raise ConfigError(
            "state_retention_days must cover more time than max_age_hours, "
            "or old stories could be posted twice."
        )
    return settings


def _parse_filters(raw: Any) -> Filters:
    _require_mapping(raw, "filters")
    _reject_unknown(raw, _FILTER_KEYS, "filters key")
    return Filters(
        skip_tags=frozenset(t.casefold() for t in _text_list(raw.get("skip_tags"), "skip_tags")),
        skip_url_parts=tuple(p.casefold() for p in _text_list(raw.get("skip_url_parts"), "skip_url_parts")),
        skip_titles=tuple(
            _pattern(p, "skip_titles") for p in _text_list(raw.get("skip_titles"), "skip_titles")
        ),
        strong_words=tuple(
            _pattern(p, "strong_words") for p in _text_list(raw.get("strong_words"), "strong_words")
        ),
        blocked=tuple(_pattern(p, "blocked") for p in _text_list(raw.get("blocked"), "blocked")),
    )


def _parse_style(raw: Any) -> PostStyle:
    values = _section(
        raw, "style", texts=_STYLE_TEXTS, bools=("banner",), numbers={"name_hashtags": ((int,), 0, 5)}
    )
    return PostStyle(**values)


def _parse_weekly(raw: Any) -> WeeklySettings:
    _require_mapping(raw, "weekly")
    weekday = raw.get("weekday")
    rest = {k: v for k, v in raw.items() if k != "weekday"}
    values = _section(
        rest,
        "weekly",
        texts=("title", "poll_question"),
        bools=("enabled", "poll"),
        numbers=_WEEKLY_NUMBER_RULES,
    )
    if weekday is not None:
        values["weekday"] = _weekday(weekday)
    return WeeklySettings(**values)


def _weekday(value: Any) -> int:
    if isinstance(value, str) and value.strip().casefold() in _WEEKDAYS:
        return _WEEKDAYS.index(value.strip().casefold())
    raise ConfigError(f"Weekly 'weekday' must be a day name like friday, got {value!r}.")


def _section(
    raw: Any,
    where: str,
    *,
    texts: Collection[str] = (),
    bools: Collection[str] = (),
    numbers: _NumberRules | None = None,
) -> dict[str, Any]:
    """Checked values of a flat config section; keys left out keep the dataclass defaults."""
    _require_mapping(raw, where)
    numbers = numbers or {}
    _reject_unknown(raw, (*texts, *bools, *numbers), f"{where} key")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key in texts:
            values[key] = _text(value, f"{where} {key}")
        elif key in bools:
            if not isinstance(value, bool):
                raise ConfigError(f"'{where}' {key} must be true or false, got {value!r}.")
            values[key] = value
        else:
            values[key] = _check_number(key, value, numbers)
    return values


def _parse_arabic(raw: Any) -> ArabicSettings:
    values = _section(raw, "arabic", texts=_ARABIC_TEXTS, bools=("enabled",))
    model = values.get("model", DEFAULT_GEMINI_MODEL).strip()
    if not GEMINI_MODEL_RE.fullmatch(model):
        raise ConfigError(
            f"'arabic' model must be a Gemini model name like {DEFAULT_GEMINI_MODEL}, got {model!r}."
        )
    return ArabicSettings(**{**values, "model": model})


def _parse_digest(raw: Any) -> DigestSettings:
    _require_mapping(raw, "digest")
    _reject_unknown(raw, (*_DIGEST_NUMBER_RULES, "enabled", "title"), "digest key")
    numbers = {
        k: _check_number(k, v, _DIGEST_NUMBER_RULES) for k, v in raw.items() if k in _DIGEST_NUMBER_RULES
    }
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("Digest 'enabled' must be true or false.")
    title = _text(raw.get("title", DigestSettings().title), "title")
    if not title.strip():
        raise ConfigError("Digest 'title' cannot be empty.")
    return DigestSettings(enabled=enabled, title=title, **numbers)


def _parse_topics(raw: Any) -> tuple[Topic, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'topics' must list at least one topic.")
    topics = tuple(_parse_topic(item, index) for index, item in enumerate(raw, start=1))
    names = [t.name.casefold() for t in topics]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate topic name(s): {', '.join(duplicates)}.")
    return topics


def _parse_topic(raw: Any, index: int) -> Topic:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"Topic #{index} must have 'name', 'hashtag' and 'feeds'.")
    _reject_unknown(raw, _TOPIC_KEYS, f"key in topic #{index}")
    name = _required_text(raw.get("name"), f"Topic #{index} needs a name.")
    hashtag = str(raw.get("hashtag") or name).strip().lstrip("#")
    if not _HASHTAG_RE.match(hashtag):
        raise ConfigError(
            f"Topic '{name}': hashtag '{hashtag}' must start with a letter and use only "
            "letters, numbers and _ (no spaces)."
        )
    feeds = raw.get("feeds")
    if not isinstance(feeds, list) or not feeds:
        raise ConfigError(f"Topic '{name}' has no feeds. Add a feed or remove the topic.")
    emoji = _text(raw.get("emoji", ""), f"Topic '{name}' emoji").strip()
    if len(emoji) > _MAX_EMOJI_CHARS:
        raise ConfigError(f"Topic '{name}': emoji is too long (at most {_MAX_EMOJI_CHARS} characters).")
    min_score = raw.get("min_score")
    if min_score is not None:
        min_score = _check_number(f"Topic '{name}' min_score", min_score, {})
    names = _text_list(raw.get("big_names"), f"Topic '{name}' big_names")
    return Topic(
        name=name,
        hashtag=hashtag,
        feeds=tuple(_parse_feed(f, name) for f in feeds),
        emoji=emoji,
        big_names=_names_pattern(names),
        min_score=min_score,
        names=tuple(names),
        suit=_suit(raw.get("suit", "spade"), name),
        arabic_name=_text(raw.get("arabic_name", ""), f"Topic '{name}' arabic_name").strip(),
        arabic_hashtag=_arabic_hashtag(raw.get("arabic_hashtag", ""), name),
    )


def _suit(value: Any, topic: str) -> str:
    suit = str(value).strip().casefold()
    if suit not in _SUITS:
        raise ConfigError(f"Topic '{topic}': suit must be one of {', '.join(_SUITS)}, got {value!r}.")
    return suit


def _arabic_hashtag(value: Any, topic: str) -> str:
    tag = _text(value, f"Topic '{topic}' arabic_hashtag").strip().lstrip("#")
    if tag and not _ARABIC_HASHTAG_RE.match(tag):
        raise ConfigError(
            f"Topic '{topic}': arabic_hashtag '{tag}' must be one word (use _ instead of spaces)."
        )
    return tag


def _parse_feed(raw: Any, topic: str) -> FeedSource:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"Topic '{topic}': each feed needs 'name' and 'url'.")
    name = _required_text(raw.get("name"), f"Topic '{topic}': a feed is missing its name.")
    url = _required_text(raw.get("url"), f"Topic '{topic}': feed '{name}' is missing its url.")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Topic '{topic}': feed '{name}' url must start with http:// or https://.")
    return FeedSource(name=name, url=url)


def _names_pattern(names: list[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    # Look-arounds instead of \b, so names that end in a dot or digit ("Kaiju No. 8") still work.
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def _pattern(value: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(value, re.IGNORECASE)
    except re.error as exc:
        raise ConfigError(
            f"'{where}': the pattern {value!r} is not a valid regular expression ({exc})."
        ) from exc


def _text_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"'{where}' must be a list of text lines.")
    return [v.strip() for v in value]


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"'{where}' must be text, got {value!r}.")
    return value


def _check_number(name: str, value: Any, rules: _NumberRules) -> Any:
    types, low, high = rules.get(name, ((int,), 0, 50))
    whole_only = types == (int,)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (whole_only and not isinstance(value, int))
    ):
        kind = "a whole number" if whole_only else "a number"
        raise ConfigError(f"Setting '{name}' must be {kind}, got {value!r}.")
    if not low <= value <= high:
        raise ConfigError(f"Setting '{name}' must be between {low} and {high}, got {value}.")
    return value


def _require_mapping(raw: Any, where: str) -> None:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"'{where}' must be a list of 'name: value' lines.")


def _reject_unknown(raw: Mapping, allowed: Collection[str], what: str) -> None:
    unknown = sorted(str(k) for k in set(raw) - set(allowed))
    if unknown:
        raise ConfigError(f"Unknown {what}(s): {', '.join(unknown)}. Check the spelling.")


def _required_text(value: Any, message: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ConfigError(message)
    return text
