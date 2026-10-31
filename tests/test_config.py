from pathlib import Path

import pytest

from newsbot.config import ConfigError, load_config, parse_config
from newsbot.models import Settings

ROOT = Path(__file__).resolve().parent.parent


def valid_data(**settings):
    return {
        "settings": {"max_posts_per_run": 2, **settings},
        "topics": [
            {"name": "AI", "hashtag": "AI", "feeds": [{"name": "Verge", "url": "https://example.com/rss"}]}
        ],
    }


def test_repo_config_loads():
    config = load_config(ROOT / "config.yaml")
    assert [t.name for t in config.topics] == ["AI", "Games", "Anime", "Tech"]
    assert all(len(t.feeds) == 3 for t in config.topics)
    assert config.settings.max_posts_per_run == 4


def test_missing_settings_use_defaults():
    config = parse_config({"topics": valid_data()["topics"]})
    assert config.settings == Settings()


def test_hash_prefix_on_hashtag_is_removed():
    data = valid_data()
    data["topics"][0]["hashtag"] = "#AI"
    assert parse_config(data).topics[0].hashtag == "AI"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.pop("topics"), "topics"),
        (lambda d: d["topics"][0].update(feeds=None), "no feeds"),
        (lambda d: d["topics"][0]["feeds"][0].update(url="ftp://x"), "http"),
        (lambda d: d["topics"][0]["feeds"][0].update(name=""), "name"),
        (lambda d: d["topics"][0].update(hashtag="Video Games"), "hashtag"),
        (lambda d: d["topics"][0].update(name=""), "name"),
        (lambda d: d["settings"].update(max_posts_per_run=0), "max_posts_per_run"),
        (lambda d: d["settings"].update(max_posts_per_run="four"), "max_posts_per_run"),
        (lambda d: d["settings"].update(max_posts_per_run=True), "max_posts_per_run"),
        (lambda d: d["settings"].update(max_post_per_run=3), "Unknown setting"),
        (lambda d: d["settings"].update(duplicate_title_similarity=1.5), "duplicate_title_similarity"),
        (lambda d: d["settings"].update(state_retention_days=1, max_age_hours=48), "state_retention_days"),
        (lambda d: d["topics"].append(dict(d["topics"][0])), "Duplicate topic"),
        (lambda d: d.update(settings=[1, 2]), "settings"),
    ],
)
def test_invalid_config_is_rejected(mutate, message):
    data = valid_data()
    mutate(data)
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_top_level_must_be_a_mapping():
    with pytest.raises(ConfigError, match="mapping"):
        parse_config(["not", "a", "mapping"])


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_broken_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("topics: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(path)
