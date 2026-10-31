"""Load and validate config.yaml, failing early with a message a person can act on."""

import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import yaml

from newsbot.models import Config, DigestSettings, FeedSource, Filters, PostStyle, Settings, Topic


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
}
_DIGEST_NUMBER_RULES: _NumberRules = {
    "local_hour": ((int,), 0, 23),
    "utc_offset_hours": ((int, float), -12, 14),
    "size": ((int,), 1, 10),
    "min_stories": ((int,), 1, 10),
}
_FILTER_KEYS = ("skip_tags", "skip_url_parts", "skip_titles", "strong_words")
_STYLE_KEYS = ("channel_signature", "top_story_label")
_TOPIC_KEYS = ("name", "hashtag", "emoji", "big_names", "min_score", "feeds")
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
    )


def _parse_style(raw: Any) -> PostStyle:
    _require_mapping(raw, "style")
    _reject_unknown(raw, _STYLE_KEYS, "style key")
    defaults = PostStyle()
    return PostStyle(
        channel_signature=_text(
            raw.get("channel_signature", defaults.channel_signature), "channel_signature"
        ),
        top_story_label=_text(raw.get("top_story_label", defaults.top_story_label), "top_story_label"),
    )


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
    return Topic(
        name=name,
        hashtag=hashtag,
        feeds=tuple(_parse_feed(f, name) for f in feeds),
        emoji=emoji,
        big_names=_names_pattern(raw.get("big_names"), name),
        min_score=min_score,
    )


def _parse_feed(raw: Any, topic: str) -> FeedSource:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"Topic '{topic}': each feed needs 'name' and 'url'.")
    name = _required_text(raw.get("name"), f"Topic '{topic}': a feed is missing its name.")
    url = _required_text(raw.get("url"), f"Topic '{topic}': feed '{name}' is missing its url.")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Topic '{topic}': feed '{name}' url must start with http:// or https://.")
    return FeedSource(name=name, url=url)


def _names_pattern(raw: Any, topic: str) -> re.Pattern[str] | None:
    names = _text_list(raw, f"Topic '{topic}' big_names")
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
