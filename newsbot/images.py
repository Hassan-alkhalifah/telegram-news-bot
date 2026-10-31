"""Find a picture for an article: first in the feed entry, then on the article page."""

import html
import logging
import re
from collections.abc import Iterator, Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

log = logging.getLogger(__name__)

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""\b(src|width|height)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_VIDEO_OR_AUDIO_RE = re.compile(r"\.(mp4|m3u8|webm|mov|mp3|m4a)(\?|$)", re.IGNORECASE)
_META_KEYS = ("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")
_MAX_PAGE_BYTES = 512_000


def image_from_entry(entry: Mapping[str, Any], base_url: str) -> str | None:
    """The best image link a feed entry offers, or None."""
    for candidate in _entry_candidates(entry):
        url = _absolute_http(candidate, base_url)
        if url:
            return url
    return None


def _entry_candidates(entry: Mapping[str, Any]) -> Iterator[str | None]:
    media = [m for m in entry.get("media_content", []) if _is_image_media(m)]
    for item in sorted(media, key=lambda m: _to_int(m.get("width")), reverse=True):
        yield item.get("url")
    for thumb in entry.get("media_thumbnail", []):
        yield thumb.get("url")
    for enclosure in entry.get("enclosures", []):
        if str(enclosure.get("type", "")).startswith("image/"):
            yield enclosure.get("href") or enclosure.get("url")
    for snippet in _entry_html(entry):
        yield from _img_sources(snippet)


def _is_image_media(media: Mapping[str, Any]) -> bool:
    medium = str(media.get("medium", "")).lower()
    kind = str(media.get("type", "")).lower()
    if medium == "image" or kind.startswith("image/"):
        return True
    if medium or kind:
        return False
    return not _VIDEO_OR_AUDIO_RE.search(str(media.get("url", "")))


def _entry_html(entry: Mapping[str, Any]) -> Iterator[str]:
    for content in entry.get("content", []):
        yield str(content.get("value", ""))
    yield str(entry.get("summary", ""))


def _img_sources(snippet: str) -> Iterator[str]:
    for tag in _IMG_TAG_RE.findall(snippet):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        if attrs.get("width") == "1" or attrs.get("height") == "1":
            continue  # tracking pixel
        if attrs.get("src"):
            yield html.unescape(attrs["src"])


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _absolute_http(url: str | None, base_url: str) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        joined = urljoin(base_url, url)
        scheme = urlsplit(joined).scheme
    except ValueError:  # e.g. "//[x" is read as a broken IPv6 address
        return None
    return joined if scheme in ("http", "https") else None


class _MetaImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "meta":
            return
        values = {k.lower(): (v or "").strip() for k, v in attrs}
        key = (values.get("property") or values.get("name") or "").lower()
        if key in _META_KEYS and values.get("content"):
            self.found.setdefault(key, values["content"])


def og_image_from_html(page: str, base_url: str) -> str | None:
    """The page's share image (og:image or twitter:image), or None."""
    parser = _MetaImageParser()
    try:
        parser.feed(page)
    except AssertionError:  # Python 3.10's HTMLParser on odd markup; keep what was found so far
        log.info("Could not read the whole page at %s; using what was read", base_url)
    for key in _META_KEYS:
        url = _absolute_http(parser.found.get(key), base_url)
        if url:
            return url
    return None


def fetch_og_image(session: requests.Session, url: str, timeout: float) -> str | None:
    """Open the article page and read its share image. Never raises; returns None on any problem."""
    try:
        with session.get(url, timeout=timeout, stream=True) as response:
            if response.status_code != 200:
                log.info("No image: %s answered HTTP %s", url, response.status_code)
                return None
            if "html" not in response.headers.get("Content-Type", "").lower():
                return None
            raw = _read_head(response)
            encoding = response.encoding or "utf-8"
    except requests.RequestException as exc:
        log.info("No image: could not open %s (%s)", url, exc)
        return None
    return og_image_from_html(raw.decode(encoding, errors="replace"), url)


def _read_head(response: requests.Response) -> bytes:
    chunks, size = [], 0
    for chunk in response.iter_content(16_384):
        chunks.append(chunk)
        size += len(chunk)
        if size >= _MAX_PAGE_BYTES or b"</head>" in chunk.lower():
            break
    return b"".join(chunks)
