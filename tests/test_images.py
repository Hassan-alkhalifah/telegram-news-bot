import feedparser
import requests
import responses

from newsbot.images import fetch_og_image, image_from_entry, og_image_from_html
from newsbot.web import make_session
from tests.helpers import rss, rss_item

LINK = "https://site.com/news/1"


def first_entry(extra="", description=""):
    return feedparser.parse(rss(rss_item("T", LINK, description=description, extra=extra))).entries[0]


class TestImageFromEntry:
    def test_largest_media_content_wins(self):
        entry = first_entry(
            extra='<media:content url="https://cdn.com/small.jpg" medium="image" width="300"/>'
            '<media:content url="https://cdn.com/big.jpg" medium="image" width="1200"/>'
        )
        assert image_from_entry(entry, LINK) == "https://cdn.com/big.jpg"

    def test_video_is_skipped_and_thumbnail_used(self):
        entry = first_entry(
            extra='<media:content url="https://cdn.com/clip.mp4" medium="video" type="video/mp4"/>'
            '<media:thumbnail url="https://cdn.com/thumb.jpg"/>'
        )
        assert image_from_entry(entry, LINK) == "https://cdn.com/thumb.jpg"

    def test_image_enclosure(self):
        entry = first_entry(extra='<enclosure url="https://cdn.com/e.jpg" type="image/jpeg" length="1000"/>')
        assert image_from_entry(entry, LINK) == "https://cdn.com/e.jpg"

    def test_audio_enclosure_ignored(self):
        entry = first_entry(extra='<enclosure url="https://cdn.com/e.mp3" type="audio/mpeg" length="1000"/>')
        assert image_from_entry(entry, LINK) is None

    def test_img_tag_in_description_is_unescaped(self):
        entry = first_entry(
            description='<p><img src="https://cdn.com/p.jpg?w=1200&amp;q=90" alt=""/>Text</p>'
        )
        assert image_from_entry(entry, LINK) == "https://cdn.com/p.jpg?w=1200&q=90"

    def test_tracking_pixel_skipped(self):
        entry = first_entry(
            description='<img src="https://pixel.com/t.gif" width="1" height="1"/><img src="https://cdn.com/real.jpg"/>'
        )
        assert image_from_entry(entry, LINK) == "https://cdn.com/real.jpg"

    def test_relative_src_resolved(self):
        entry = first_entry(description='<img src="/images/x.png">')
        assert image_from_entry(entry, LINK) == "https://site.com/images/x.png"

    def test_protocol_relative_src(self):
        entry = first_entry(description='<img src="//cdn.com/y.jpg">')
        assert image_from_entry(entry, LINK) == "https://cdn.com/y.jpg"

    def test_data_uri_ignored(self):
        entry = first_entry(description='<img src="data:image/png;base64,AAAA">')
        assert image_from_entry(entry, LINK) is None

    def test_no_image(self):
        assert image_from_entry(first_entry(description="Just text"), LINK) is None


class TestOgImageFromHtml:
    def test_og_image(self):
        html = '<html><head><meta property="og:image" content="https://cdn.com/og.jpg"></head></html>'
        assert og_image_from_html(html, LINK) == "https://cdn.com/og.jpg"

    def test_twitter_image_fallback_resolved(self):
        html = '<head><meta name="twitter:image" content="/tw.jpg"></head>'
        assert og_image_from_html(html, LINK) == "https://site.com/tw.jpg"

    def test_og_preferred_over_twitter(self):
        html = (
            '<meta name="twitter:image" content="https://cdn.com/tw.jpg">'
            '<meta property="og:image" content="https://cdn.com/og.jpg">'
        )
        assert og_image_from_html(html, LINK) == "https://cdn.com/og.jpg"

    def test_none(self):
        assert og_image_from_html("<html><head></head></html>", LINK) is None


class TestFetchOgImage:
    @responses.activate
    def test_found(self):
        responses.get(
            LINK,
            body='<head><meta property="og:image" content="https://cdn.com/og.jpg"></head>',
            content_type="text/html; charset=utf-8",
        )
        assert fetch_og_image(make_session(), LINK, 5) == "https://cdn.com/og.jpg"

    @responses.activate
    def test_http_error(self):
        responses.get(LINK, status=404)
        assert fetch_og_image(make_session(), LINK, 5) is None

    @responses.activate
    def test_not_html(self):
        responses.get(LINK, body=b"%PDF", content_type="application/pdf")
        assert fetch_og_image(make_session(), LINK, 5) is None

    @responses.activate
    def test_network_error(self):
        responses.get(LINK, body=requests.ConnectionError("boom"))
        assert fetch_og_image(make_session(), LINK, 5) is None


def test_broken_image_link_is_ignored():
    entry = first_entry(description="<img src='//[x'><img src=\"https://cdn.com/ok.jpg\">")
    assert image_from_entry(entry, LINK) == "https://cdn.com/ok.jpg"


def test_broken_share_image_link_is_ignored():
    assert og_image_from_html('<meta property="og:image" content="//[x">', LINK) is None


def test_odd_markup_on_the_page_never_crashes():
    page = '<head><![ b <meta property="og:image" content="https://cdn.com/og.jpg"></head>'
    assert og_image_from_html(page, LINK) in (None, "https://cdn.com/og.jpg")
