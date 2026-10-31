"""Draw the branded 1280x720 banner for each post: news photo, headline, logo, topic badge, brand bar.

The look comes from the channel logo: a red and black split ring, card suits in hexagons, and a dark
slate hexagon pattern. Arabic banners are mirrored (right to left) and shaped with arabic-reshaper
and python-bidi, because Pillow's own Arabic support needs system libraries that are often missing.
"""

import io
import logging
import math
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import arabic_reshaper
import requests
from bidi import get_display
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1280, 720
ASSETS = Path(__file__).resolve().parent.parent / "assets"

RED = (0xDA, 0x00, 0x37)
RING_BLACK = (0x17, 0x17, 0x17)
SLATE = (0x35, 0x39, 0x41)
HEX_LINE = (0x26, 0x28, 0x2B)
SHADE = (0x12, 0x14, 0x18)
WHITE = (0xF7, 0xF7, 0xF5)
MUTED = (0xB9, 0xBE, 0xC8)

MARGIN = 56
_MAX_PHOTO_BYTES = 10_000_000
_MAX_PHOTO_PIXELS = 40_000_000  # larger images would take hundreds of MB to decode
# Feed photos are untrusted: only the common web formats are decoded, never TIFF, BMP, EPS and the like.
_PHOTO_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")
_MAX_PHOTO_SECONDS = 30.0  # a slow server can keep each chunk under the timeout forever
_clock = time.monotonic
_HEADLINE_SIZES = (66, 60, 54, 48)
_HEADLINE_MAX_LINES = 3
_HEADLINE_BOTTOM = HEIGHT - 96  # the headline's last line ends here, above the source line
_TOP_STORY_GAP = 58
_SUITS = ("heart", "spade", "diamond", "club")


@dataclass(frozen=True)
class BannerText:
    headline: str
    topic: str  # shown in the badge, e.g. "GAMES" or "ألعاب"
    suit: str  # one of _SUITS
    source: str
    handle: str  # e.g. "@sav_news_channel"
    brand: str = "SAV NEWS"
    top_story: str = ""  # label text, empty for normal stories
    rtl: bool = False  # Arabic


def download_photo(session: requests.Session, url: str | None, timeout: float) -> bytes | None:
    """The news photo, or None if it cannot be downloaded. Never raises."""
    if not url:
        return None
    try:
        started = _clock()
        with session.get(url, timeout=timeout, stream=True) as response:
            if response.status_code != 200:
                log.info("No banner photo: %s answered HTTP %s", url, response.status_code)
                return None
            data = bytearray()
            for chunk in response.iter_content(65_536):
                data.extend(chunk)
                if len(data) > _MAX_PHOTO_BYTES:
                    log.info("No banner photo: %s is larger than %d bytes", url, _MAX_PHOTO_BYTES)
                    return None
                if _clock() - started > _MAX_PHOTO_SECONDS:
                    log.info("No banner photo: %s took longer than %ds", url, _MAX_PHOTO_SECONDS)
                    return None
            return bytes(data)
    except requests.RequestException as exc:
        log.info("No banner photo: could not download %s (%s)", url, exc)
        return None


def render_banner(photo: bytes | None, text: BannerText) -> bytes:
    """A JPEG banner. Without a usable photo, the brand's hexagon background is used instead."""
    canvas = _background(photo)
    headline = _layout_headline(canvas, text)
    label_room = _TOP_STORY_GAP if text.top_story else 0
    _shade(canvas, fade_from=headline.top - label_room - 170)
    _draw_header(canvas, text)
    _draw_headline_block(canvas, text, headline)
    _draw_footer(canvas, text)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=88, optimize=True, progressive=True)
    return out.getvalue()


def render_welcome(brand: str, subtitle: str, subtitle_ar: str) -> bytes:
    """The banner for the pinned 'choose your language' post: big logo, brand name, both languages."""
    canvas = _brand_background()
    shade = Image.new("RGBA", canvas.size, SHADE + (110,))
    canvas.alpha_composite(shade)
    logo_size = 380
    canvas.alpha_composite(
        _logo().resize((logo_size, logo_size), Image.Resampling.LANCZOS),
        (110, (HEIGHT - logo_size) // 2 - 10),
    )
    x = 110 + logo_size + 70
    _text_with_shadow(canvas, (x, 300), brand, _latin_font(124, 700), WHITE, anchor="ls")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([x, 330, x + 120, 338], fill=RED)
    room = WIDTH - x - MARGIN
    english_font = fitting_font(draw, subtitle, room, sizes=(40, 36, 32, 28), rtl=False)
    arabic_font = fitting_font(draw, subtitle_ar, room, sizes=(32, 29, 26, 23, 20), rtl=True)
    draw.text((x, 372), subtitle, font=english_font, fill=WHITE, anchor="lm")
    draw.text((x, 436), _visual(subtitle_ar, True), font=arabic_font, fill=MUTED, anchor="lm")
    _brand_bar(canvas, rtl=False)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out.getvalue()


def fitting_font(
    draw: ImageDraw.ImageDraw, text: str, max_width: float, sizes: tuple[int, ...], rtl: bool
) -> ImageFont.FreeTypeFont:
    """The largest of `sizes` at which the text fits in max_width (the smallest if none fits)."""
    font = _arabic_font(sizes[-1], 600) if rtl else _latin_font(sizes[-1], 400)
    for size in sizes:
        candidate = _arabic_font(size, 600) if rtl else _latin_font(size, 400)
        if draw.textlength(_visual(text, rtl), font=candidate) <= max_width:
            return candidate
    return font


# ---------- background ----------


def _background(photo: bytes | None) -> Image.Image:
    image = _open_photo(photo)
    if image is None:
        return _brand_background()
    return _cover(image, WIDTH, HEIGHT).convert("RGBA")


def _open_photo(photo: bytes | None) -> Image.Image | None:
    if not photo:
        return None
    try:
        with Image.open(io.BytesIO(photo), formats=_PHOTO_FORMATS) as image:
            if image.width * image.height > _MAX_PHOTO_PIXELS:
                log.info("Banner photo is too large (%sx%s); using the brand background", *image.size)
                return None
            image.load()
            if min(image.size) < 200:
                return None  # icons and tracking pixels make ugly banners
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        log.info("Banner photo could not be read (%s); using the brand background", exc)
        return None


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.Resampling.LANCZOS
    )
    left = (resized.width - width) // 2
    top = max(0, (resized.height - height) // 3)  # keep faces near the top in view
    return resized.crop((left, top, left + width, top + height))


def _brand_background() -> Image.Image:
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), SLATE + (255,))
    _hex_pattern(canvas, HEX_LINE + (255,), size=46, width=7)
    badge = _logo().resize((560, 560), Image.Resampling.LANCZOS)
    faded = badge.copy()
    faded.putalpha(badge.getchannel("A").point(lambda a: a * 0.16))
    canvas.alpha_composite(faded, (WIDTH - 470, -60))
    return canvas


def _hex_pattern(canvas: Image.Image, colour: tuple[int, ...], size: int, width: int, top: int = 0) -> None:
    """Flat-top hexagon outlines like the logo's background."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    w, h = size * 2, math.sqrt(3) * size
    col = 0
    x: float = -size
    while x < canvas.width + size:
        y = top - h + (h / 2 if col % 2 else 0)
        while y < canvas.height + h:
            points = [
                (x + size * math.cos(math.radians(a)), y + size * math.sin(math.radians(a)))
                for a in range(0, 360, 60)
            ]
            draw.polygon(points, outline=colour, width=width)
            y += h
        x += w * 0.75
        col += 1
    canvas.alpha_composite(layer)


def _shade(canvas: Image.Image, fade_from: int) -> None:
    """A soft dark top edge for the logo, and a deep fade that is already dark where the headline starts."""
    shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    for y in range(190):
        draw.line([(0, y), (WIDTH, y)], fill=SHADE + (int(150 * (1 - y / 190) ** 1.6),))
    start = max(int(HEIGHT * 0.22), fade_from)
    solid_at = start + 210  # fully dark a little above the first headline line
    for y in range(start, HEIGHT):
        t = min(1.0, (y - start) / (solid_at - start))
        draw.line([(0, y), (WIDTH, y)], fill=SHADE + (int(236 * t**1.2),))
    canvas.alpha_composite(shade)
    texture = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    texture_top = start + 120
    _hex_pattern(texture, (255, 255, 255, 13), size=34, width=2, top=texture_top)
    mask = Image.new("L", canvas.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    for y in range(texture_top, HEIGHT):
        mask_draw.line([(0, y), (WIDTH, y)], fill=int(255 * (y - texture_top) / max(1, HEIGHT - texture_top)))
    texture.putalpha(Image.composite(texture.getchannel("A"), Image.new("L", canvas.size, 0), mask))
    canvas.alpha_composite(texture)


# ---------- header: logo, brand name, topic badge ----------


def _draw_header(canvas: Image.Image, text: BannerText) -> None:
    draw = ImageDraw.Draw(canvas)
    logo_size = 92
    logo = _logo().resize((logo_size, logo_size), Image.Resampling.LANCZOS)
    brand_font = _latin_font(36, 600)
    brand_width = draw.textlength(text.brand, font=brand_font)
    top = 38
    if text.rtl:
        logo_x = WIDTH - MARGIN - logo_size
        brand_x = logo_x - 18 - brand_width
    else:
        logo_x = MARGIN
        brand_x = MARGIN + logo_size + 18
    canvas.alpha_composite(logo, (logo_x, top))
    _text_with_shadow(canvas, (brand_x, top + logo_size / 2), text.brand, brand_font, WHITE, anchor="lm")
    _draw_topic_badge(canvas, text, centre_y=top + logo_size / 2)


def _draw_topic_badge(canvas: Image.Image, text: BannerText, centre_y: float) -> None:
    font = _arabic_font(32, 700) if text.rtl else _latin_font(30, 500)
    label = _visual(text.topic, text.rtl)
    draw = ImageDraw.Draw(canvas)
    label_width = draw.textlength(label, font=font)
    icon_size, pad, gap = 54, 14, 12
    chip_w = int(pad + icon_size + gap + label_width + pad + 6)
    chip_h = 66
    chip_x = MARGIN if text.rtl else WIDTH - MARGIN - chip_w
    chip_y = int(centre_y - chip_h / 2)
    chip = _rounded_box(chip_w, chip_h, radius=chip_h // 2, fill=SHADE + (200,), outline=RED + (255,), line=3)
    canvas.alpha_composite(chip, (chip_x, chip_y))
    icon = _suit(text.suit).resize((icon_size, icon_size), Image.Resampling.LANCZOS)
    if text.rtl:
        icon_x = chip_x + chip_w - pad - icon_size
        label_x = icon_x - gap - label_width
    else:
        icon_x = chip_x + pad
        label_x = icon_x + icon_size + gap
    canvas.alpha_composite(icon, (icon_x, int(centre_y - icon_size / 2)))
    draw.text((label_x, centre_y), label, font=font, fill=WHITE, anchor="lm")


# ---------- headline ----------


@dataclass(frozen=True)
class _Headline:
    font: ImageFont.FreeTypeFont
    lines: tuple[str, ...]
    line_height: int
    top: int


def _layout_headline(canvas: Image.Image, text: BannerText) -> _Headline:
    draw = ImageDraw.Draw(canvas)
    font, lines = _fit_headline(draw, text.headline, WIDTH - 2 * MARGIN, text.rtl)
    line_height = int(font.size * (1.5 if text.rtl else 1.12))
    top = _HEADLINE_BOTTOM - line_height * len(lines)
    return _Headline(font=font, lines=tuple(lines), line_height=line_height, top=top)


def _draw_headline_block(canvas: Image.Image, text: BannerText, headline: _Headline) -> None:
    if text.top_story:
        _draw_top_story(canvas, text, headline.top - _TOP_STORY_GAP)
    x = WIDTH - MARGIN if text.rtl else MARGIN
    anchor = "ra" if text.rtl else "la"
    y = headline.top
    for line in headline.lines:
        _text_with_shadow(canvas, (x, y), _visual(line, text.rtl), headline.font, WHITE, anchor=anchor)
        y += headline.line_height


def _fit_headline(
    draw: ImageDraw.ImageDraw, headline: str, max_width: int, rtl: bool
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for size in _HEADLINE_SIZES:
        font = _arabic_font(size - 6, 800) if rtl else _latin_font(size, 700)
        lines = _wrap(draw, headline, font, max_width, rtl)
        if len(lines) <= _HEADLINE_MAX_LINES:
            return font, lines
    lines = _wrap(draw, headline, font, max_width, rtl)[:_HEADLINE_MAX_LINES]
    lines[-1] = _with_ellipsis(draw, lines[-1], font, max_width, rtl)
    return font, lines


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int, rtl: bool
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(_visual(candidate, rtl), font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _with_ellipsis(
    draw: ImageDraw.ImageDraw, line: str, font: ImageFont.FreeTypeFont, max_width: int, rtl: bool
) -> str:
    words = line.split()
    while words and draw.textlength(_visual(" ".join(words) + "…", rtl), font=font) > max_width:
        words.pop()
    return " ".join(words) + "…"


def _draw_top_story(canvas: Image.Image, text: BannerText, top: float) -> None:
    font = _arabic_font(26, 700) if text.rtl else _latin_font(24, 600)
    label = _visual(text.top_story, text.rtl)
    draw = ImageDraw.Draw(canvas)
    width = int(draw.textlength(label, font=font) + 32)
    height = 40
    x = WIDTH - MARGIN - width if text.rtl else MARGIN
    canvas.alpha_composite(_rounded_box(width, height, radius=4, fill=RED + (255,)), (x, int(top)))
    draw.text((x + width / 2, top + height / 2), label, font=font, fill=WHITE, anchor="mm")


# ---------- footer: source, channel name, brand bar ----------


def _draw_footer(canvas: Image.Image, text: BannerText) -> None:
    font_source = _arabic_font(24, 500) if text.rtl else _latin_font(24, 400)
    font_handle = _latin_font(24, 500)
    y = HEIGHT - 50
    source = _visual(text.source, text.rtl)
    draw = ImageDraw.Draw(canvas)
    if text.rtl:
        draw.text((WIDTH - MARGIN, y), source, font=font_source, fill=MUTED, anchor="rm")
        draw.text((MARGIN, y), text.handle, font=font_handle, fill=WHITE, anchor="lm")
    else:
        draw.text((MARGIN, y), source, font=font_source, fill=MUTED, anchor="lm")
        draw.text((WIDTH - MARGIN, y), text.handle, font=font_handle, fill=WHITE, anchor="rm")
    _brand_bar(canvas, text.rtl)


def _brand_bar(canvas: Image.Image, rtl: bool) -> None:
    """The logo ring's quarters: red, black, red, black (mirrored for Arabic)."""
    draw = ImageDraw.Draw(canvas)
    colours = [RED, RING_BLACK, RED, RING_BLACK]
    if rtl:
        colours.reverse()
    quarter = WIDTH / 4
    for i, colour in enumerate(colours):
        draw.rectangle([int(i * quarter), HEIGHT - 12, int((i + 1) * quarter), HEIGHT], fill=colour)


# ---------- helpers ----------


def _visual(text: str, rtl: bool) -> str:
    """Arabic letters joined and in display order; other text unchanged.

    The paragraph direction is forced to right-to-left: an Arabic line that starts with an English
    word ("Pikmin 4 ...") would otherwise be laid out left-to-right.
    """
    return get_display(arabic_reshaper.reshape(text), base_dir="R") if rtl else text


def _text_with_shadow(
    canvas: Image.Image, xy: tuple[float, float], text: str, font, fill, anchor: str
) -> None:
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((xy[0] + 2, xy[1] + 3), text, font=font, fill=(0, 0, 0, 150), anchor=anchor)
    canvas.alpha_composite(shadow)
    ImageDraw.Draw(canvas).text(xy, text, font=font, fill=fill, anchor=anchor)


def _rounded_box(box_w: int, box_h: int, radius: int, fill, outline=None, line: int = 0) -> Image.Image:
    """Anti-aliased rounded rectangle (drawn 4x larger, then scaled down)."""
    scale = 4
    big = Image.new("RGBA", (box_w * scale, box_h * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, box_w * scale - 1, box_h * scale - 1],
        radius=radius * scale,
        fill=fill,
        outline=outline,
        width=line * scale,
    )
    return big.resize((box_w, box_h), Image.Resampling.LANCZOS)


@cache
def _logo() -> Image.Image:
    return Image.open(ASSETS / "brand" / "logo_badge.png").convert("RGBA")


@cache
def _suit(name: str) -> Image.Image:
    if name not in _SUITS:
        name = "spade"
    return Image.open(ASSETS / "brand" / f"suit_{name}.png").convert("RGBA")


@cache
def _latin_font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(ASSETS / "fonts" / "Oswald-Variable.ttf"), size)
    font.set_variation_by_axes([weight])
    return font


@cache
def _arabic_font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    # Noto Kufi Arabic has every joined-letter form arabic-reshaper produces (Cairo and Tajawal do not).
    font = ImageFont.truetype(str(ASSETS / "fonts" / "NotoKufiArabic-Variable.ttf"), size)
    font.set_variation_by_axes([weight])
    return font
