from datetime import datetime, timezone

import pytest
import requests
import responses

from newsbot.feeds import FeedError, collect_articles, parse_feed
from newsbot.models import FeedSource, Topic
from newsbot.web import make_session
from tests.helpers import NOW, make_config, rss, rss_item

SOURCE = FeedSource(name="ANN", url="https://ann.example.com/rss")
TOPIC = Topic(name="Anime", hashtag="Anime", feeds=(SOURCE,))


def parse(content, summary_max_chars=280):
    return parse_feed(content, TOPIC, SOURCE, summary_max_chars, NOW)


def test_builds_articles_from_entries():
    content = rss(
        rss_item(
            "Show &amp; Tell gets season 2",
            "https://ann.example.com/news/1?utm_source=rss",
            pub="Sat, 31 Oct 2026 07:13:57 -0400",
            description="<p>The anime <b>returns</b> in 2027.</p>",
            extra='<media:thumbnail url="https://cdn.example.com/1.jpg"/>',
        )
    )
    [article] = parse(content)
    assert article.title == "Show & Tell gets season 2"
    assert article.summary == "The anime returns in 2027."
    assert article.url == "https://ann.example.com/news/1?utm_source=rss"
    assert article.key == "ann.example.com/news/1"
    assert article.published == datetime(2026, 10, 31, 11, 13, 57, tzinfo=timezone.utc)
    assert article.image_url == "https://cdn.example.com/1.jpg"
    assert (article.topic, article.hashtag, article.source) == ("Anime", "Anime", "ANN")


def test_entries_missing_link_title_or_date_are_skipped():
    content = rss(
        rss_item("No link here", None),
        rss_item(None, "https://ann.example.com/2"),
        rss_item("No date here", "https://ann.example.com/3", pub=None),
        rss_item("Not a web link", "mailto:someone@example.com"),
        rss_item("A valid one", "https://ann.example.com/4"),
    )
    assert [a.title for a in parse(content)] == ["A valid one"]


def test_future_date_is_treated_as_now():
    [article] = parse(
        rss(rss_item("From the future", "https://ann.example.com/f", pub="Sun, 01 Nov 2026 12:00:00 +0000"))
    )
    assert article.published == NOW


def test_summary_that_repeats_the_title_is_dropped():
    [article] = parse(rss(rss_item("Same words", "https://ann.example.com/s", description="Same words")))
    assert article.summary == ""


def test_long_summary_is_truncated():
    [article] = parse(rss(rss_item("Long one", "https://ann.example.com/l", description="word " * 200)), 50)
    assert len(article.summary) <= 50
    assert article.summary.endswith("…")


def test_content_encoded_used_when_no_description():
    extra = "<content:encoded><![CDATA[<p>From content</p>]]></content:encoded>"
    [article] = parse(rss(rss_item("Content only", "https://ann.example.com/c", extra=extra)))
    assert article.summary == "From content"


def test_empty_feed_returns_nothing():
    assert parse(rss()) == []


@pytest.mark.parametrize(
    "content",
    [
        b"<html><head><title>Just a moment...</title></head><body>Access denied</body></html>",
        b"this is not a feed at all",
        b"",
    ],
)
def test_page_that_is_not_a_feed_raises(content):
    with pytest.raises(FeedError, match="ANN"):
        parse(content)


def test_cut_off_feed_keeps_the_readable_stories():
    complete = rss_item("Complete story", "https://ann.example.com/ok")
    cut_off = rss_item("Unfinished story", "https://ann.example.com/cut", description="x" * 200)
    content = rss(complete, cut_off)[:-150]  # the download stopped inside the second story's text
    assert [a.title for a in parse(content)] == ["Complete story", "Unfinished story"]


@responses.activate
def test_collect_continues_when_a_feed_fails():
    config = make_config(topics=("AI", "Games"))
    responses.get(
        config.topics[0].feeds[0].url, body=rss(rss_item("AI news story", "https://ai.example.com/1"))
    )
    responses.get(config.topics[1].feeds[0].url, status=500)
    result = collect_articles(make_session(), config, NOW)
    assert [a.title for a in result.articles] == ["AI news story"]
    assert result.failed == ("Games/Games Feed",)
    assert result.total == 2


@responses.activate
def test_collect_handles_network_errors():
    config = make_config(topics=("AI",))
    responses.get(config.topics[0].feeds[0].url, body=requests.ConnectionError("down"))
    result = collect_articles(make_session(), config, NOW)
    assert result.articles == ()
    assert result.failed == ("AI/AI Feed",)


def test_story_with_a_broken_link_is_skipped_not_fatal():
    content = rss(
        rss_item("Broken link story", "http://[abc/1"),
        rss_item("Good story", "https://ann.example.com/good"),
    )
    assert [a.title for a in parse(content)] == ["Good story"]


def test_story_with_odd_markup_in_the_title_is_kept():
    [article] = parse(rss(rss_item("&amp;lt;![ b Odd title", "https://ann.example.com/odd")))
    assert "Odd title" in article.title


@responses.activate
def test_unexpected_error_in_one_feed_does_not_stop_the_others(monkeypatch):
    import newsbot.feeds as feeds_module

    config = make_config(topics=("AI", "Games"))
    for topic in config.topics:
        responses.get(
            topic.feeds[0].url, body=rss(rss_item(f"{topic.name} story here", f"https://{topic.name}.com/1"))
        )
    real_parse = feeds_module.parse_feed

    def flaky_parse(content, topic, source, summary_max_chars, now):
        if topic.name == "AI":
            raise RuntimeError("something nobody expected")
        return real_parse(content, topic, source, summary_max_chars, now)

    monkeypatch.setattr(feeds_module, "parse_feed", flaky_parse)
    result = collect_articles(make_session(), config, NOW)
    assert [a.title for a in result.articles] == ["Games story here"]
    assert result.failed == ("AI/AI Feed",)
