import io

import pytest
import requests
import responses
from PIL import Image, ImageFont

from newsbot.banner import HEIGHT, WIDTH, BannerText, download_photo, render_banner, render_welcome
from newsbot.web import make_session

TEXT = BannerText(
    headline="Nintendo reveals a surprise Direct", topic="GAMES", suit="heart", source="IGN", handle="@chan"
)


def photo_bytes(size=(800, 450), colour=(200, 40, 40), fmt="PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, fmt)
    return out.getvalue()


def opened(jpeg: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(jpeg))
    image.load()
    return image


def test_banner_is_a_1280x720_jpeg():
    image = opened(render_banner(photo_bytes(), TEXT))
    assert image.format == "JPEG" and image.size == (WIDTH, HEIGHT)


def test_photo_fills_the_top_of_the_banner():
    image = opened(render_banner(photo_bytes(colour=(0, 160, 0)), TEXT)).convert("RGB")
    r, g, b = image.getpixel((WIDTH // 2, 260))
    assert g > r + 40 and g > b + 40


def test_missing_tiny_or_broken_photos_use_the_brand_background():
    for photo in (None, b"", photo_bytes(size=(60, 60)), b"not an image"):
        image = opened(render_banner(photo, TEXT)).convert("RGB")
        r, g, b = image.getpixel((WIDTH // 2, 260))
        assert abs(r - g) < 25 and b >= r - 5  # dark slate, not a photo colour


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF"])
def test_common_web_photo_formats_are_used(fmt):
    image = opened(render_banner(photo_bytes(colour=(0, 160, 0), fmt=fmt), TEXT)).convert("RGB")
    r, g, b = image.getpixel((WIDTH // 2, 260))
    assert g > r + 40 and g > b + 40


@pytest.mark.parametrize("fmt", ["BMP", "TIFF", "PPM"])
def test_other_photo_formats_are_never_decoded(fmt):
    # Pillow's less common decoders have had memory-safety bugs, and feed photos are untrusted.
    image = opened(render_banner(photo_bytes(colour=(0, 160, 0), fmt=fmt), TEXT)).convert("RGB")
    r, g, b = image.getpixel((WIDTH // 2, 260))
    assert abs(r - g) < 25 and b >= r - 5


def test_arabic_and_very_long_headlines_render():
    arabic = BannerText(
        headline="نينتندو تكشف عن Switch 2 " * 6,
        topic="ألعاب",
        suit="heart",
        source="IGN",
        handle="@chan_ar",
        top_story="خبر بارز",
        rtl=True,
    )
    assert opened(render_banner(photo_bytes(), arabic)).size == (WIDTH, HEIGHT)
    long = BannerText(
        headline="word " * 120, topic="AI", suit="unknown-suit", source="X", handle="@c", top_story="TOP"
    )
    assert opened(render_banner(None, long)).size == (WIDTH, HEIGHT)


def test_welcome_banner():
    assert opened(render_welcome("SAV NEWS", "AI, games, anime and tech news", "أخبار")).size == (
        WIDTH,
        HEIGHT,
    )


class TestDownloadPhoto:
    URL = "https://cdn.example.com/p.jpg"

    @responses.activate
    def test_ok(self):
        responses.get(self.URL, body=b"abc")
        assert download_photo(make_session(), self.URL, 5) == b"abc"

    @responses.activate
    def test_failures_return_none(self):
        responses.get(self.URL, status=404)
        assert download_photo(make_session(), self.URL, 5) is None
        responses.replace(responses.GET, self.URL, body=requests.ConnectionError("down"))
        assert download_photo(make_session(), self.URL, 5) is None
        assert download_photo(make_session(), None, 5) is None

    @responses.activate
    def test_too_big_is_refused(self, monkeypatch):
        import newsbot.banner as banner

        monkeypatch.setattr(banner, "_MAX_PHOTO_BYTES", 10)
        responses.get(self.URL, body=b"x" * 100)
        assert download_photo(make_session(), self.URL, 5) is None

    @responses.activate
    def test_too_slow_is_given_up(self, monkeypatch):
        import newsbot.banner as banner

        ticks = iter([0.0, banner._MAX_PHOTO_SECONDS + 1])
        monkeypatch.setattr(banner, "_clock", lambda: next(ticks))
        responses.get(self.URL, body=b"abc")
        assert download_photo(make_session(), self.URL, 5) is None


def test_fonts_always_ask_for_the_basic_text_engine(monkeypatch):
    # The bot shapes and orders Arabic itself. Pillow's Raqm engine (present on Linux, absent on
    # Windows) would do it a second time and print every Arabic word backwards.
    import newsbot.banner as banner

    engines = []
    real_truetype = ImageFont.truetype

    def spy(*args, **kwargs):
        engines.append(kwargs.get("layout_engine"))
        return real_truetype(*args, **kwargs)

    monkeypatch.setattr(banner.ImageFont, "truetype", spy)
    banner._latin_font.cache_clear()
    banner._arabic_font.cache_clear()
    fonts = (banner._latin_font(41, 700), banner._arabic_font(41, 700))
    assert engines == [ImageFont.Layout.BASIC, ImageFont.Layout.BASIC]
    assert all(font.layout_engine == ImageFont.Layout.BASIC for font in fonts)


def test_welcome_subtitles_shrink_to_fit():
    from PIL import ImageDraw

    from newsbot.banner import _visual, fitting_font

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    long_arabic = "أخبار الذكاء " * 4
    font = fitting_font(draw, long_arabic, 600, sizes=(32, 26, 20, 14), rtl=True)
    assert draw.textlength(_visual(long_arabic, True), font=font) <= 600
    assert fitting_font(draw, "Short", 600, sizes=(40, 30), rtl=False).size == 40
