"""Load and validate config.yaml, failing early with a message a person can act on."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from newsbot.models import Config, FeedSource, Settings, Topic


class ConfigError(Exception):
    """config.yaml is missing or has a mistake."""


# name -> (allowed types, minimum, maximum)
_SETTING_RULES: dict[str, tuple[tuple[type, ...], float, float]] = {
    "max_posts_per_run": ((int,), 1, 50),
    "max_age_hours": ((int,), 1, 24 * 14),
    "summary_max_chars": ((int,), 0, 900),
    "post_delay_seconds": ((int, float), 0, 60),
    "duplicate_title_similarity": ((int, float), 0.1, 1),
    "state_retention_days": ((int,), 1, 90),
    "request_timeout_seconds": ((int,), 1, 120),
}

_HASHTAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


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
    settings = _parse_settings(data.get("settings") or {})
    topics = _parse_topics(data.get("topics"))
    return Config(settings=settings, topics=topics)


def _parse_settings(raw: Any) -> Settings:
    if not isinstance(raw, Mapping):
        raise ConfigError("'settings' must be a list of 'name: value' lines.")
    unknown = sorted(set(raw) - set(_SETTING_RULES))
    if unknown:
        raise ConfigError(f"Unknown setting(s): {', '.join(map(str, unknown))}. Check the spelling.")
    values = {name: _check_setting(name, value) for name, value in raw.items()}
    settings = Settings(**values)
    if settings.state_retention_days * 24 <= settings.max_age_hours:
        raise ConfigError(
            "state_retention_days must cover more time than max_age_hours, "
            "or old stories could be posted twice."
        )
    return settings


def _check_setting(name: str, value: Any) -> Any:
    types, low, high = _SETTING_RULES[name]
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
    return Topic(name=name, hashtag=hashtag, feeds=tuple(_parse_feed(f, name) for f in feeds))


def _parse_feed(raw: Any, topic: str) -> FeedSource:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"Topic '{topic}': each feed needs 'name' and 'url'.")
    name = _required_text(raw.get("name"), f"Topic '{topic}': a feed is missing its name.")
    url = _required_text(raw.get("url"), f"Topic '{topic}': feed '{name}' is missing its url.")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Topic '{topic}': feed '{name}' url must start with http:// or https://.")
    return FeedSource(name=name, url=url)


def _required_text(value: Any, message: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ConfigError(message)
    return text
