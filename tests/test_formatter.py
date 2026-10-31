import html
import re

from newsbot.formatter import CAPTION_LIMIT, build_caption
from newsbot.models import PostStyle
from newsbot.text import utf16_len
from tests.helpers import make_article, make_story

STYLE = PostStyle(channel_signature="@sav_news_channel", top_story_label="TOP STORY")


def visible(caption: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", caption))


def caption(story, style=STYLE, top_min=3):
    return build_caption(story, style, top_story_min_sources=top_min)


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


def test_headline_layout_with_top_story_label():
    lead = games_article()
    others = [make_article(url=f"https://s{n}.com/x", source=f"S{n}") for n in range(2)]
    assert caption(make_story(lead, *others)) == (
        "[G] GAMES  |  TOP STORY\n"
        "<b>Big &amp; bold &lt;news&gt;</b>\n\n"
        "Short summary.\n\n"
        'IGN  -  <a href="https://ign.com/a?x=1&amp;y=2">Read more</a>\n'
        "#Games  |  @sav_news_channel"
    )


def test_no_top_story_label_below_the_site_count():
    text = caption(make_story(games_article(), make_article(url="https://s.com/x", source="S")))
    assert text.startswith("[G] GAMES\n<b>")


def test_without_emoji_signature_or_summary():
    lead = games_article(emoji="", summary="", title="Title", url="https://v.com/1")
    assert caption(make_story(lead), PostStyle(channel_signature="")) == (
        "GAMES\n<b>Title</b>\n\n" 'IGN  -  <a href="https://v.com/1">Read more</a>\n' "#Games"
    )


def test_signature_and_label_are_escaped():
    style = PostStyle(channel_signature="<me>", top_story_label="A&B")
    others = [make_article(url=f"https://s{n}.com/x", source=f"S{n}") for n in range(2)]
    text = caption(make_story(games_article(), *others), style)
    assert "&lt;me&gt;" in text and "A&amp;B" in text


def test_long_text_fits_the_caption_limit():
    text = caption(make_story(games_article(title="T" * 300, summary="word " * 400)))
    assert utf16_len(visible(text)) <= CAPTION_LIMIT
    assert "…" in visible(text)
    assert "Read more" in text and "@sav_news_channel" in text


def test_huge_title_alone_still_fits():
    text = caption(make_story(games_article(title="x" * 2000, summary="")))
    assert utf16_len(visible(text)) <= CAPTION_LIMIT
    assert "Read more" in text


def test_wide_characters_are_counted_correctly():
    text = caption(make_story(games_article(title="Title", summary="\U0001d538" * 600)))
    assert utf16_len(visible(text)) <= CAPTION_LIMIT
