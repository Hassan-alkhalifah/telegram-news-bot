import html
import re
from urllib.parse import parse_qs, urlsplit

from newsbot.formatter import CAPTION_LIMIT, build_buttons, build_caption, name_hashtags
from newsbot.models import Edition, Topic
from newsbot.text import utf16_len
from tests.helpers import make_article, make_story

ENGLISH = Edition(
    language="en",
    signature="@sav_news_channel",
    top_story_label="TOP STORY",
    read_button="Read full story",
    share_button="Share",
    source_label="Source",
    digest_title="Top",
    weekly_title="Week",
    poll_question="Which?",
)
ARABIC = Edition(
    language="ar",
    signature="@sav_ar",
    top_story_label="خبر بارز",
    read_button="اقرأ",
    share_button="مشاركة",
    source_label="المصدر",
    digest_title="Top",
    weekly_title="Week",
    poll_question="Which?",
)
GAMES = Topic(
    name="Games",
    hashtag="Games",
    feeds=(),
    emoji="[G]",
    names=("Nintendo", "Switch 2", "Final Fantasy", "Re:Zero"),
    arabic_name="ألعاب",
    arabic_hashtag="ألعاب",
)


def visible(caption: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", caption))


def games_article(**overrides):
    values = {
        "title": "Big & bold <news>",
        "summary": "Short summary.",
        "topic": "Games",
        "hashtag": "Games",
        "emoji": "[G]",
        "source": "IGN",
        "url": "https://ign.com/a?x=1&y=2",
    }
    return make_article(**{**values, **overrides})


def three_sites(lead):
    return make_story(lead, *(make_article(url=f"https://s{n}.com/x", source=f"S{n}") for n in range(2)))


def test_english_caption_layout():
    caption = build_caption(three_sites(games_article()), ENGLISH, GAMES, 3, name_tags=["Nintendo"])
    assert caption == (
        "[G] GAMES  |  TOP STORY\n"
        "<b>Big &amp; bold &lt;news&gt;</b>\n\n"
        "Short summary.\n\n"
        "Source: IGN\n"
        "#Games #Nintendo  |  @sav_news_channel"
    )


def test_arabic_caption_uses_arabic_labels_and_hashtag():
    caption = build_caption(make_story(games_article(summary="")), ARABIC, GAMES, 3, name_tags=["Nintendo"])
    assert caption == (
        "[G] ألعاب\n" "<b>Big &amp; bold &lt;news&gt;</b>\n\n" "المصدر: IGN\n" "#ألعاب #Nintendo  |  @sav_ar"
    )


def test_caption_without_signature_or_topic_settings():
    edition = Edition(**{**ENGLISH.__dict__, "signature": ""})
    caption = build_caption(make_story(games_article(emoji="")), edition, None, 3)
    assert caption.startswith("GAMES\n<b>")
    assert caption.endswith("Source: IGN\n#Games")


def test_buttons_open_the_story_and_share_it():
    article = games_article(title="Zelda & friends", url="https://ign.com/a?x=1")
    [[read, share]] = build_buttons(article, ENGLISH)["inline_keyboard"]
    assert read == {"text": "Read full story", "url": "https://ign.com/a?x=1"}
    assert share["text"] == "Share"
    shared = parse_qs(urlsplit(share["url"]).query)
    assert share["url"].startswith("https://t.me/share/url?")
    assert shared == {"url": ["https://ign.com/a?x=1"], "text": ["Zelda & friends"]}


class TestNameHashtags:
    def test_names_in_the_story_become_hashtags(self):
        story = make_story(games_article(title="Nintendo shows Switch 2 sales"))
        assert name_hashtags(story, GAMES, 2) == ["Nintendo", "Switch2"]

    def test_limit_and_punctuation(self):
        story = make_story(games_article(title="Re:Zero and Final Fantasy collab on Switch 2"))
        assert name_hashtags(story, GAMES, 1) == ["ReZero"]
        assert name_hashtags(story, GAMES, 5) == ["ReZero", "FinalFantasy", "Switch2"]

    def test_other_sites_titles_count_too(self):
        other = make_article(url="https://x.com/1", title="Nintendo confirms it", source="X")
        assert name_hashtags(make_story(games_article(title="Big news today"), other), GAMES, 2) == [
            "Nintendo"
        ]

    def test_no_names_or_turned_off(self):
        assert name_hashtags(make_story(games_article(title="Big news today")), GAMES, 2) == []
        assert name_hashtags(make_story(games_article(title="Nintendo news")), GAMES, 0) == []
        assert name_hashtags(make_story(games_article(title="Nintendo news")), None, 2) == []


def test_long_text_fits_the_caption_limit():
    caption = build_caption(
        make_story(games_article(title="T" * 300, summary="word " * 400)), ENGLISH, GAMES, 3
    )
    assert utf16_len(visible(caption)) <= CAPTION_LIMIT
    assert "…" in visible(caption)
    assert caption.endswith("@sav_news_channel")


def test_huge_title_alone_still_fits():
    caption = build_caption(make_story(games_article(title="x" * 2000, summary="")), ENGLISH, GAMES, 3)
    assert utf16_len(visible(caption)) <= CAPTION_LIMIT


def test_wide_characters_are_counted_correctly():
    caption = build_caption(make_story(games_article(summary="\U0001d538" * 600)), ENGLISH, GAMES, 3)
    assert utf16_len(visible(caption)) <= CAPTION_LIMIT
