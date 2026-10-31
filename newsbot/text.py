"""Text helpers: clean feed HTML, shorten text, split sentences, normalise links."""

import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit

ELLIPSIS = "…"

_FOOTER_PATTERNS = (
    re.compile(r"\s*The post .{1,300}? appeared first on .{1,120}?\.?$", re.IGNORECASE),
    re.compile(r"\s*\[(?:…|\.\.\.)\]\s*$"),
    re.compile(
        r"\s*(?:Continue reading|Read more|Read the full story|Read full article)\b.{0,60}$", re.IGNORECASE
    ),
)
_SKIP_TAGS = {"script", "style", "noscript", "iframe"}
_TAG_RE = re.compile(r"<[^>]*>")
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "cmpid", "ito"}
# A sentence ends at . ! or ? followed by a capital or digit, so "U.S. government" stays whole.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'“‘(\[]?[A-Z0-9])")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def clean_html(value: str | None) -> str:
    """Turn a feed's HTML snippet into one line of plain text without 'read more' footers."""
    if not value:
        return ""
    try:
        parser = _TextExtractor()
        parser.feed(value)
        parser.close()
        raw = parser.text()
    except AssertionError:  # Python 3.10's HTMLParser fails on odd markup such as "<![ b"
        raw = _TAG_RE.sub(" ", value)
    text = re.sub(r"\s+", " ", html.unescape(raw)).strip()
    for pattern in _FOOTER_PATTERNS:
        text = pattern.sub("", text).strip()
    return text


def utf16_len(text: str) -> int:
    """Length the way Telegram counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def truncate(text: str, limit: int) -> str:
    """Shorten text to at most `limit` UTF-16 units, cutting at a word and adding an ellipsis."""
    if utf16_len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    budget = limit - 1  # room for the ellipsis
    cut, used = [], 0
    for char in text:
        size = utf16_len(char)
        if used + size > budget:
            break
        cut.append(char)
        used += size
    head = "".join(cut)
    space = head.rfind(" ")
    if space > budget // 2:
        head = head[:space]
    return head.rstrip(" ,;:-.") + ELLIPSIS


def url_key(url: str) -> str:
    """Normalise a link so the same article from two feeds gets the same key."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path if parts.path in ("", "/") else parts.path.rstrip("/")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        ]
    )
    return f"{host}{path or '/'}" + (f"?{query}" if query else "")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END_RE.split(text.strip()) if s]


def first_sentences(text: str, limit: int) -> str:
    """Whole sentences from the start that fit in `limit`; a too-long first sentence is cut at a word."""
    if utf16_len(text) <= limit:
        return text
    kept = ""
    for sentence in split_sentences(text):
        candidate = f"{kept} {sentence}".strip()
        if utf16_len(candidate) > limit:
            break
        kept = candidate
    return kept or truncate(text, limit)
