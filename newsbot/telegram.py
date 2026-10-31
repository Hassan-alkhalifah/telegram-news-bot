"""Small Telegram Bot API client with retries, flood-control waits and clear setup errors."""

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_FLOOD_WAIT = 60  # seconds; Telegram's retry_after can be longer than a run should block
DEFAULT_FLOOD_WAIT = 5
# Methods that publish something. Repeating one after Telegram may have done it could post twice.
_POSTING_METHODS = frozenset({"sendPhoto", "sendMessage"})


class TelegramError(Exception):
    """A request failed. The bot can skip this post and continue.

    retryable: True when the problem is temporary (network, flood control) and the same post can be
    tried in a later run; False when Telegram rejected the post itself.
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class FatalTelegramError(TelegramError):
    """A setup problem (bad token, wrong channel, bot not admin). Every later post would fail too."""


class UncertainDeliveryError(TelegramError):
    """The post reached Telegram but no clear answer came back. It may already be in the channel."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        session: requests.Session,
        timeout: float = 15,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        self._token = token
        self._session = session
        self._timeout = timeout
        self._sleep = sleep
        self._max_attempts = max_attempts

    def send_photo(self, chat_id: str, photo_url: str, caption: str) -> Any:
        payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "HTML"}
        return self._call("sendPhoto", payload)

    def send_message(self, chat_id: str, text: str, preview_url: str | None = None) -> Any:
        preview = {"url": preview_url, "prefer_large_media": True} if preview_url else {"is_disabled": True}
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": preview}
        return self._call("sendMessage", payload)

    def get_me(self) -> Any:
        return self._call("getMe")

    def get_chat(self, chat_id: str) -> Any:
        return self._call("getChat", {"chat_id": chat_id})

    def get_chat_member(self, chat_id: str, user_id: int) -> Any:
        return self._call("getChatMember", {"chat_id": chat_id, "user_id": user_id})

    def get_updates(self) -> Any:
        return self._call("getUpdates")

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{API_BASE}/bot{self._token}/{method}"
        posting = method in _POSTING_METHODS
        for attempt in range(1, self._max_attempts + 1):
            last_try = attempt == self._max_attempts
            try:
                response = self._session.post(url, json=payload or {}, timeout=self._timeout)
            except requests.RequestException as exc:
                # "from None" below: the original exception text contains the token.
                reason = self._redact(str(exc))
                if posting and _may_have_arrived(exc):
                    raise UncertainDeliveryError(f"{method}: no answer from Telegram ({reason})") from None
                if last_try:
                    raise TelegramError(f"{method}: network error: {reason}", retryable=True) from None
                log.warning("%s: network error, retrying (%s)", method, reason)
                self._sleep(2**attempt)
                continue

            data = _json_or_empty(response)
            if data.get("ok"):
                return data.get("result")
            code = _int_or(data.get("error_code"), response.status_code)
            description = str(data.get("description") or f"HTTP {response.status_code}")

            if code == 429:
                if last_try:
                    raise TelegramError(f"{method}: Telegram flood control: {description}", retryable=True)
                wait = _retry_after(data)
                log.warning("%s: Telegram asked to slow down, waiting %ss", method, wait)
                self._sleep(wait)
                continue
            if code >= 500:
                if posting:
                    raise UncertainDeliveryError(f"{method}: Telegram server error {code}: {description}")
                if last_try:
                    raise TelegramError(f"{method}: Telegram server error {code}", retryable=True)
                log.warning("%s: Telegram server error %s, retrying", method, code)
                self._sleep(2**attempt)
                continue
            raise _classify(method, code, description)
        raise TelegramError(f"{method}: gave up after {self._max_attempts} attempts")  # pragma: no cover

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "<token>")


def _may_have_arrived(exc: requests.RequestException) -> bool:
    """Whether a failed post might still have been published.

    Connection errors count as "never arrived": nearly always the connection could not be made, and
    treating them as published would drop a story whenever the network blips. Read timeouts and broken
    answers count as "may have arrived", because Telegram may have posted and only the reply was lost.
    """
    if isinstance(exc, requests.ConnectTimeout):
        return False
    if isinstance(exc, requests.Timeout):
        return True
    return not isinstance(exc, requests.ConnectionError)


def _json_or_empty(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _retry_after(data: dict[str, Any]) -> int:
    parameters = data.get("parameters")
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    return min(max(_int_or(value, DEFAULT_FLOOD_WAIT), 1), MAX_FLOOD_WAIT)


def _classify(method: str, code: int, description: str) -> TelegramError:
    text = description.lower()
    if code == 401:
        return FatalTelegramError("The bot token was rejected (401 Unauthorized). Check TELEGRAM_BOT_TOKEN.")
    if code == 403 or "chat not found" in text or "rights" in text or "forbidden" in text:
        return FatalTelegramError(
            f"Telegram refused to post: {description}. Check TELEGRAM_CHANNEL_ID and that the bot is "
            "an admin of the channel with the 'Post Messages' permission."
        )
    return TelegramError(f"{method} failed ({code}): {description}", retryable=False)
