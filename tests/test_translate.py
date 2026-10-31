import json

import pytest
import requests
import responses

from newsbot.translate import DeepLTranslator, TranslationError
from newsbot.web import make_session

FREE_KEY = "279a2e9d-83b3-c416-7e2d-f721593e42a0:fx"
PRO_KEY = "279a2e9d-83b3-c416-7e2d-f721593e42a0"
FREE_URL = "https://api-free.deepl.com/v2/translate"


def translator(key=FREE_KEY, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return DeepLTranslator(key, make_session(), timeout=5, sleep=sleeps.append)


@responses.activate
def test_translates_texts_in_one_request():
    responses.post(FREE_URL, json={"translations": [{"text": "عنوان"}, {"text": "ملخص"}]})
    result = translator().to_arabic(["Title", "Summary"], context="Video game news")
    assert result == ["عنوان", "ملخص"]
    request = responses.calls[0].request
    assert request.headers["Authorization"] == f"DeepL-Auth-Key {FREE_KEY}"
    body = json.loads(request.body)
    assert body == {
        "text": ["Title", "Summary"],
        "target_lang": "AR",
        "source_lang": "EN",
        "context": "Video game news",
    }


@responses.activate
def test_pro_keys_use_the_pro_address():
    responses.post("https://api.deepl.com/v2/translate", json={"translations": [{"text": "x"}]})
    assert translator(PRO_KEY).to_arabic(["a"]) == ["x"]


@responses.activate
def test_empty_texts_are_not_sent():
    responses.post(FREE_URL, json={"translations": [{"text": "x"}]})
    assert translator().to_arabic(["Title", "", "  "]) == ["x", "", ""]
    assert json.loads(responses.calls[0].request.body)["text"] == ["Title"]


def test_nothing_to_translate_makes_no_request():
    assert translator().to_arabic(["", " "]) == ["", ""]


@responses.activate
def test_quota_reached_is_not_retried():
    responses.post(FREE_URL, json={"message": "Quota exceeded"}, status=456)
    sleeps = []
    with pytest.raises(TranslationError) as caught:
        translator(sleeps=sleeps).to_arabic(["Title"])
    assert caught.value.quota_reached and "monthly" in str(caught.value)
    assert sleeps == [] and len(responses.calls) == 1


@responses.activate
def test_busy_server_is_retried():
    responses.post(FREE_URL, json={"message": "Too many requests"}, status=429)
    responses.post(FREE_URL, json={"translations": [{"text": "x"}]})
    sleeps = []
    assert translator(sleeps=sleeps).to_arabic(["a"]) == ["x"]
    assert sleeps == [2]


@responses.activate
def test_wrong_key_is_reported_without_the_key():
    responses.post(FREE_URL, json={"message": "Authorization failed"}, status=403)
    with pytest.raises(TranslationError) as caught:
        translator().to_arabic(["a"])
    assert "DEEPL_API_KEY" in str(caught.value) and FREE_KEY not in str(caught.value)
    assert caught.value.bad_key and not caught.value.quota_reached


@responses.activate
def test_network_errors_are_retried_then_reported():
    for _ in range(3):
        responses.post(FREE_URL, body=requests.ConnectionError("down"))
    sleeps = []
    with pytest.raises(TranslationError):
        translator(sleeps=sleeps).to_arabic(["a"])
    assert sleeps == [2, 4]


@responses.activate
def test_odd_answer_is_an_error():
    responses.post(FREE_URL, json={"translations": []})
    with pytest.raises(TranslationError):
        translator().to_arabic(["a"])


@responses.activate
def test_usage():
    responses.get(
        "https://api-free.deepl.com/v2/usage", json={"character_count": 1200, "character_limit": 500000}
    )
    assert translator().usage() == (1200, 500000)
