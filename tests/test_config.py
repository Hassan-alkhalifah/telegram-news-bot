from pathlib import Path

import pytest

from newsbot.config import ConfigError, load_config, parse_config
from newsbot.models import DigestSettings, Filters, PostStyle, Settings

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
    assert [len(t.feeds) for t in config.topics] == [6, 10, 4, 8]
    assert config.settings.max_posts_per_run == 3
    assert config.style.channel_signature == "@sav_news_channel"
    assert config.digest.enabled and config.digest.local_hour == 21 and config.digest.size == 5
    assert config.topic("Anime").min_score == 1
    assert config.topic("AI").big_names.search("big news about OpenAI today")
    assert all(t.emoji for t in config.topics)
    assert "features" in config.filters.skip_tags
    assert config.filters.skip_titles and config.filters.strong_words and config.filters.skip_url_parts


def test_missing_sections_use_defaults():
    config = parse_config({"topics": valid_data()["topics"]})
    assert config.settings == Settings()
    assert config.filters == Filters()
    assert config.style == PostStyle()
    assert config.digest == DigestSettings()
    assert config.topics[0].big_names is None and config.topics[0].min_score is None


def test_patterns_ignore_case_and_words_are_whole():
    data = valid_data()
    data["filters"] = {"skip_titles": ["^poll:"], "strong_words": ["\\blaunch"]}
    data["topics"][0]["big_names"] = ["EA", "Re:Zero"]
    config = parse_config(data)
    assert config.filters.skip_titles[0].search("POLL: favourite game")
    assert config.topics[0].big_names.search("Re:Zero season 4") and config.topics[0].big_names.search(
        "EA Sports"
    )
    assert not config.topics[0].big_names.search("great idea")


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
        (lambda d: d["settings"].update(same_story_similarity=1.5), "same_story_similarity"),
        (lambda d: d.update(filters={"skip_titles": ["(unclosed"]}), "skip_titles"),
        (lambda d: d.update(filters={"skip_titles": "not a list"}), "skip_titles"),
        (lambda d: d.update(filters={"skip_tag": ["x"]}), "Unknown"),
        (lambda d: d.update(style={"colour": "red"}), "Unknown"),
        (lambda d: d.update(style={"channel_signature": 5}), "channel_signature"),
        (lambda d: d.update(digest={"local_hour": 25}), "local_hour"),
        (lambda d: d.update(digest={"enabled": "yes"}), "enabled"),
        (lambda d: d.update(digest={"title": ""}), "title"),
        (lambda d: d["topics"][0].update(big_names="OpenAI"), "big_names"),
        (lambda d: d["topics"][0].update(min_score=-1), "min_score"),
        (lambda d: d["topics"][0].update(emoji=123), "emoji"),
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


def test_repo_config_has_the_new_sections():
    config = load_config(ROOT / "config.yaml")
    assert config.style.banner and config.style.brand_name == "SAV NEWS"
    assert config.style.read_button and config.style.share_button and config.style.name_hashtags == 2
    assert config.arabic.enabled and config.arabic.top_story_label and config.arabic.read_button
    assert config.arabic.model == "gemini-3.5-flash-lite"
    assert config.weekly.enabled and config.weekly.weekday == 4 and config.weekly.size == 10
    assert config.weekly.poll and 2 <= config.weekly.poll_options <= 10
    assert {t.suit for t in config.topics} == {"spade", "heart", "diamond", "club"}
    assert all(t.arabic_name and t.arabic_hashtag for t in config.topics)
    assert "Nintendo" in config.topic("Games").names
    # AI and Tech sites rarely report the same news at once, so one big name or strong word is enough.
    assert [config.topic(t).min_score for t in ("AI", "Tech", "Anime")] == [1, 1, 1]
    assert config.topic("Games").min_score is None and config.settings.min_score == 2
    assert config.settings.topic_gap_minutes == 60  # a topic posts at most once an hour (except big news)
    blocked = config.filters.blocked
    assert any(p.search("New hentai game on Steam") for p in blocked)
    assert any(p.search("Online casino ads banned") for p in blocked)
    assert any(p.search("Beer brand sponsors esports team") for p in blocked)
    assert not any(p.search("Apple's big bet on AI glasses") for p in blocked)
    assert not any(p.search("AI model speeds up drug discovery") for p in blocked)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(weekly={"weekday": "funday"}), "weekday"),
        (lambda d: d.update(weekly={"poll_options": 20}), "poll_options"),
        (lambda d: d.update(arabic={"enabled": "yes"}), "enabled"),
        (lambda d: d.update(arabic={"colour": "red"}), "Unknown"),
        (lambda d: d.update(arabic={"model": "../evil"}), "model"),
        (lambda d: d.update(arabic={"model": ""}), "model"),
        (lambda d: d.update(style={"banner": "yes"}), "banner"),
        (lambda d: d.update(style={"name_hashtags": 9}), "name_hashtags"),
        (lambda d: d["topics"][0].update(suit="joker"), "suit"),
        (lambda d: d["topics"][0].update(arabic_hashtag="two words"), "arabic_hashtag"),
        (lambda d: d.update(filters={"blocked": ["(bad"]}), "blocked"),
    ],
)
def test_invalid_new_sections_are_rejected(mutate, message):
    data = valid_data()
    mutate(data)
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_weekday_names_are_understood():
    data = valid_data()
    data["weekly"] = {"weekday": "Monday"}
    assert parse_config(data).weekly.weekday == 0


@pytest.mark.parametrize(
    "title",
    [
        "Wine 10 brings big Proton gains for Linux gaming",
        "SpongeBob: The Cosmic Shake heads back to Bikini Bottom",
        "Metal Gear Solid Delta: Naked Snake returns in new trailer",
        "Escort missions are finally fun in this new RPG",
    ],
)
def test_blocked_words_leave_harmless_news_alone(title):
    blocked = load_config(ROOT / "config.yaml").filters.blocked
    assert not any(p.search(title) for p in blocked)


@pytest.mark.parametrize(
    "title",
    [
        "Topless scene removed from game",
        "Rule 34 artists target new anime",
        "BDSM visual novel banned",
        "Escort service app",
    ],
)
def test_blocked_words_catch_more_adult_terms(title):
    blocked = load_config(ROOT / "config.yaml").filters.blocked
    assert any(p.search(title) for p in blocked)
