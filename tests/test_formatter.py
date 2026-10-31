import html
import re

from newsbot.formatter import CAPTION_LIMIT, build_caption
from newsbot.text import utf16_len
from tests.helpers import make_article


def visible(caption: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", caption))


def test_caption_layout_and_escaping():
    article = make_article(
        title="Big & bold <news>",
        summary="Short summary.",
        hashtag="Games",
        source="IGN",
        url="https://ign.com/a?x=1&y=2",
    )
    assert build_caption(article) == (
        "<b>Big &amp; bold &lt;news&gt;</b>\n\n"
        "Short summary.\n\n"
        "#Games | Source: IGN\n"
        '<a href="https://ign.com/a?x=1&amp;y=2">Read more</a>'
    )


def test_caption_without_summary():
    article = make_article(title="Title", summary="", hashtag="AI", source="Verge", url="https://v.com/1")
    assert build_caption(article) == (
        "<b>Title</b>\n\n#AI | Source: Verge\n" '<a href="https://v.com/1">Read more</a>'
    )


def test_long_text_fits_the_caption_limit():
    caption = build_caption(make_article(title="T" * 300, summary="word " * 400))
    assert utf16_len(visible(caption)) <= CAPTION_LIMIT
    assert "…" in visible(caption)
    assert "Read more" in caption


def test_huge_title_alone_still_fits():
    caption = build_caption(make_article(title="x" * 2000, summary=""))
    assert utf16_len(visible(caption)) <= CAPTION_LIMIT
    assert "Read more" in caption


def test_wide_characters_are_counted_correctly():
    caption = build_caption(make_article(title="Title", summary="\U0001d538" * 600))
    assert utf16_len(visible(caption)) <= CAPTION_LIMIT
