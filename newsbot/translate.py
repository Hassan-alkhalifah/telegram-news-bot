"""Translate stories into Arabic with the DeepL API (the Free plan allows 500,000 characters a month)."""

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

log = logging.getLogger(__name__)

_FREE_API = "https://api-free.deepl.com"
_PRO_API = "https://api.deepl.com"
_RETRY_STATUSES = {429, 500, 502, 503, 504, 529}


class TranslationError(Exception):
    """A translation failed.

    quota_reached: the monthly character limit is used up (expected now and then; Arabic just pauses).
    bad_key: DeepL refused the key (a setup problem the owner must fix).
    """

    def __init__(self, message: str, quota_reached: bool = False, bad_key: bool = False) -> None:
        super().__init__(message)
        self.quota_reached = quota_reached
        self.bad_key = bad_key


class DeepLTranslator:
    def __init__(
        self,
        api_key: str,
        session: requests.Session,
        timeout: float = 15,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        self._key = api_key
        self._base = _FREE_API if api_key.endswith(":fx") else _PRO_API  # Free keys end in ":fx"
        self._session = session
        self._timeout = timeout
        self._sleep = sleep
        self._max_attempts = max_attempts

    def to_arabic(self, texts: list[str], context: str = "") -> list[str]:
        """Each text translated to Modern Standard Arabic, in the same order. Empty texts stay empty.

        `context` (for example "Video game news") guides the wording and is not billed.
        """
        wanted = [(i, t) for i, t in enumerate(texts) if t.strip()]
        if not wanted:
            return ["" for _ in texts]
        payload: dict[str, Any] = {"text": [t for _, t in wanted], "target_lang": "AR", "source_lang": "EN"}
        if context:
            payload["context"] = context
        data = self._request("POST", "/v2/translate", payload)
        translations = data.get("translations") if isinstance(data, dict) else None
        if not isinstance(translations, list) or len(translations) != len(wanted):
            raise TranslationError("DeepL answered without the expected translations")
        result = ["" for _ in texts]
        for (index, _), item in zip(wanted, translations, strict=True):
            result[index] = str(item.get("text", "")) if isinstance(item, dict) else ""
        return result

    def usage(self) -> tuple[int, int]:
        """(characters used this month, monthly limit)."""
        data = self._request("GET", "/v2/usage", None)
        return int(data.get("character_count", 0)), int(data.get("character_limit", 0))

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        headers = {"Authorization": f"DeepL-Auth-Key {self._key}"}
        for attempt in range(1, self._max_attempts + 1):
            last_try = attempt == self._max_attempts
            try:
                response = self._session.request(
                    method, self._base + path, json=payload, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                if last_try:
                    raise TranslationError(f"DeepL could not be reached: {type(exc).__name__}") from None
                log.warning("DeepL could not be reached, retrying (%s)", type(exc).__name__)
                self._sleep(2**attempt)
                continue
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    raise TranslationError("DeepL answered with something that is not JSON") from None
            if response.status_code in _RETRY_STATUSES and not last_try:
                log.warning("DeepL is busy (HTTP %s), retrying", response.status_code)
                self._sleep(2**attempt)
                continue
            raise _error_for(response.status_code)
        raise TranslationError("DeepL: gave up")  # pragma: no cover


def _error_for(status: int) -> TranslationError:
    if status == 456:
        return TranslationError(
            "DeepL's monthly character limit is used up; Arabic posts pause until next month",
            quota_reached=True,
        )
    if status == 403:
        return TranslationError("DeepL refused the key (HTTP 403). Check DEEPL_API_KEY.", bad_key=True)
    return TranslationError(f"DeepL translation failed (HTTP {status})")
