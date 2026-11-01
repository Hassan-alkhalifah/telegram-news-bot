import json

import pytest
import requests
import responses

from newsbot.translate import GeminiTranslator, TranslationError
from newsbot.web import make_session
from tests.helpers import GEMINI_KEY, GEMINI_URL, gemini_answer, gemini_error

RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
QUOTA_FAILURE = "type.googleapis.com/google.rpc.QuotaFailure"
ERROR_INFO = "type.googleapis.com/google.rpc.ErrorInfo"


def translator(sleeps=None, model="gemini-3.5-flash-lite"):
    sleeps = [] if sleeps is None else sleeps
    return GeminiTranslator(GEMINI_KEY, make_session(), model=model, timeout=5, sleep=sleeps.append)


LONG_TEXTS = [
    "Nintendo reveals a surprise Direct for Pikmin fans",
    "The show starts at 9am Pacific with news about four new games",
]


def sent(call_index=0) -> dict:
    return json.loads(responses.calls[call_index].request.body)


def sent_texts(call_index=0) -> dict:
    return json.loads(sent(call_index)["contents"][0]["parts"][0]["text"])


@responses.activate
def test_translates_texts_in_one_request():
    responses.post(GEMINI_URL, json=gemini_answer("عنوان", "ملخص"))
    result = translator().to_arabic(["Title", "Summary"], context="Video game news")
    assert result == ["عنوان", "ملخص"]
    request = responses.calls[0].request
    assert request.headers["x-goog-api-key"] == GEMINI_KEY
    assert GEMINI_KEY not in request.url
    assert sent_texts() == {"topic": "Video game news", "texts": ["Title", "Summary"]}
    body = sent()
    assert "Arabic" in body["systemInstruction"]["parts"][0]["text"]
    assert body["generationConfig"]["response_mime_type"] == "application/json"
    schema = body["generationConfig"]["response_schema"]
    assert schema["properties"]["translations"]["type"] == "ARRAY"


@responses.activate
def test_the_model_comes_from_the_settings():
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    responses.post(url, json=gemini_answer("نص"))
    assert translator(model="gemini-3.8-flash").to_arabic(["a"]) == ["نص"]


@responses.activate
def test_empty_texts_are_not_sent():
    responses.post(GEMINI_URL, json=gemini_answer("عنوان"))
    assert translator().to_arabic(["Title", "", "  "]) == ["عنوان", "", ""]
    assert sent_texts()["texts"] == ["Title"]


def test_nothing_to_translate_makes_no_request():
    assert translator().to_arabic(["", " "]) == ["", ""]


@responses.activate
def test_thinking_parts_are_skipped():
    answer = gemini_answer("عنوان")
    answer["candidates"][0]["content"]["parts"].insert(0, {"text": "Let me think...", "thought": True})
    responses.post(GEMINI_URL, json=answer)
    assert translator().to_arabic(["Title"]) == ["عنوان"]


@responses.activate
def test_translations_are_trimmed():
    responses.post(GEMINI_URL, json=gemini_answer("  عنوان \n"))
    assert translator().to_arabic(["Title"]) == ["عنوان"]


@pytest.mark.parametrize(
    "answer",
    [
        gemini_answer("عنوان"),  # one translation for two texts
        gemini_answer("عنوان", ""),  # a text left untranslated
        gemini_answer(*LONG_TEXTS),  # not Arabic at all
        gemini_answer("عنوان", "ملخص " * 60),  # far longer than the text: not a translation
        gemini_answer("عنوان", "اقرأ المزيد على https://evil.example"),  # a link the text did not have
        gemini_answer("عنوان", "تابعونا على t.me/evil"),
        gemini_answer("انضموا إلى @evil_channel", "ملخص"),  # a handle the text did not have
        gemini_answer("عنوان", "٣٣٣ ، ؟"),  # Arabic digits and punctuation are not words
        {
            "candidates": [
                {"content": {"parts": [{"text": '{"translations": ["عنو'}]}, "finishReason": "MAX_TOKENS"}
            ]
        },
        {"candidates": [{"content": {"parts": [{"text": '{"translations": "عنوان"}'}]}}]},
        {"candidates": [{"finishReason": "MAX_TOKENS"}]},
        {"candidates": [{"content": {"parts": [{"text": "not json"}]}, "finishReason": "STOP"}]},
        {"candidates": [{"content": {"parts": [{"text": '{"other": []}'}]}, "finishReason": "STOP"}]},
        {"candidates": [{"content": {"parts": [{"text": '{"translations": ["عنوان", 5]}'}]}}]},
        {"candidates": []},
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]},
        [],
    ],
)
@responses.activate
def test_odd_answers_are_errors(answer):
    responses.post(GEMINI_URL, json=answer)
    with pytest.raises(TranslationError) as caught:
        translator().to_arabic(LONG_TEXTS)
    assert not caught.value.quota_reached and not caught.value.setup_problem


@responses.activate
def test_short_names_may_stay_in_latin_letters():
    responses.post(GEMINI_URL, json=gemini_answer("GTA 6", "روكستار تنشر عرضاً جديداً للعبة اليوم"))
    result = translator().to_arabic(["GTA 6", "Rockstar shares a new trailer for the game today"])
    assert result == ["GTA 6", "روكستار تنشر عرضاً جديداً للعبة اليوم"]


@responses.activate
def test_links_and_handles_from_the_text_may_stay():
    answer = gemini_answer("شاهد العرض على https://nintendo.com/direct مع @NintendoAmerica")
    responses.post(GEMINI_URL, json=answer)
    text = "Watch the show at https://nintendo.com/direct with @NintendoAmerica"
    assert translator().to_arabic([text]) == [
        "شاهد العرض على https://nintendo.com/direct مع @NintendoAmerica"
    ]


@responses.activate
def test_answer_that_is_not_json_is_an_error():
    responses.post(GEMINI_URL, body="<html>oops</html>")
    with pytest.raises(TranslationError):
        translator().to_arabic(["Title"])


@responses.activate
def test_busy_minute_waits_for_the_given_delay_then_retries():
    slow_down = gemini_error(
        429, "RESOURCE_EXHAUSTED", "slow down", {"@type": RETRY_INFO, "retryDelay": "7s"}
    )
    responses.post(GEMINI_URL, json=slow_down, status=429)
    responses.post(GEMINI_URL, json=gemini_answer("نص"))
    sleeps = []
    assert translator(sleeps=sleeps).to_arabic(["a"]) == ["نص"]
    assert sleeps == [7.0]


@responses.activate
def test_busy_without_a_delay_backs_off_then_counts_as_the_limit():
    for _ in range(3):
        responses.post(GEMINI_URL, json=gemini_error(429, "RESOURCE_EXHAUSTED"), status=429)
    sleeps = []
    with pytest.raises(TranslationError) as caught:
        translator(sleeps=sleeps).to_arabic(["a"])
    assert caught.value.quota_reached and caught.value.pauses_run and sleeps == [10, 20]


@pytest.mark.parametrize(
    "details",
    [
        (
            {
                "@type": QUOTA_FAILURE,
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
            },
        ),
        ({"@type": RETRY_INFO, "retryDelay": "3600s"},),  # too long to wait inside a run
    ],
)
@responses.activate
def test_daily_limit_pauses_at_once(details):
    responses.post(GEMINI_URL, json=gemini_error(429, "RESOURCE_EXHAUSTED", "quota", *details), status=429)
    sleeps = []
    with pytest.raises(TranslationError) as caught:
        translator(sleeps=sleeps).to_arabic(["a"])
    assert caught.value.quota_reached and "limit" in str(caught.value)
    assert sleeps == [] and len(responses.calls) == 1


@pytest.mark.parametrize(
    "status, body",
    [
        (
            400,
            gemini_error(
                400,
                "INVALID_ARGUMENT",
                "API key not valid. Please pass a valid API key.",
                {"@type": ERROR_INFO, "reason": "API_KEY_INVALID"},
            ),
        ),
        (401, gemini_error(401, "UNAUTHENTICATED")),
        (403, gemini_error(403, "PERMISSION_DENIED", "Your API key was reported as leaked.")),
        (400, gemini_error(400, "FAILED_PRECONDITION", "User location is not supported for the API use.")),
        (404, gemini_error(404, "NOT_FOUND", "models/gemini-old is not found")),
    ],
)
@responses.activate
def test_setup_problems_are_reported_without_the_key(status, body):
    responses.post(GEMINI_URL, json=body, status=status)
    with pytest.raises(TranslationError) as caught:
        translator().to_arabic(["a"])
    message = str(caught.value)
    assert caught.value.setup_problem and not caught.value.quota_reached
    assert GEMINI_KEY not in message and ("GEMINI_API_KEY" in message or "model" in message)
    assert len(responses.calls) == 1


@responses.activate
def test_other_bad_requests_only_skip_this_story():
    responses.post(
        GEMINI_URL, json=gemini_error(400, "INVALID_ARGUMENT", "Request contains bad text"), status=400
    )
    with pytest.raises(TranslationError) as caught:
        translator().to_arabic(["a"])
    assert not caught.value.setup_problem and not caught.value.quota_reached


@responses.activate
def test_server_errors_are_retried_then_reported():
    for _ in range(3):
        responses.post(GEMINI_URL, json=gemini_error(503, "UNAVAILABLE"), status=503)
    sleeps = []
    with pytest.raises(TranslationError) as caught:
        translator(sleeps=sleeps).to_arabic(["a"])
    assert sleeps == [2, 4] and not caught.value.quota_reached and not caught.value.setup_problem
    assert caught.value.unavailable and caught.value.pauses_run  # the next stories would fail too


@responses.activate
def test_network_errors_are_retried_then_reported():
    for _ in range(3):
        responses.post(GEMINI_URL, body=requests.ConnectionError("down"))
    sleeps = []
    with pytest.raises(TranslationError) as caught:
        translator(sleeps=sleeps).to_arabic(["a"])
    assert sleeps == [2, 4] and caught.value.unavailable and caught.value.pauses_run


@pytest.mark.parametrize("model", ["../../evil", "gemini-3.5-flash-lite\n", "Gemini", ""])
def test_a_strange_model_name_is_refused(model):
    with pytest.raises(ValueError):
        translator(model=model)


@responses.activate
def test_server_error_with_a_long_delay_is_not_called_a_quota():
    busy = gemini_error(503, "UNAVAILABLE", "overloaded", {"@type": RETRY_INFO, "retryDelay": "600s"})
    responses.post(GEMINI_URL, json=busy, status=503)
    with pytest.raises(TranslationError) as caught:
        translator().to_arabic(["a"])
    assert caught.value.unavailable and not caught.value.quota_reached and len(responses.calls) == 1


@responses.activate
def test_daily_limit_in_the_newer_error_style():
    body = {"error": {"code": "quota_exceeded", "message": "You have exceeded your daily quota."}}
    responses.post(GEMINI_URL, json=body, status=429)
    sleeps = []
    with pytest.raises(TranslationError) as caught:
        translator(sleeps=sleeps).to_arabic(["a"])
    assert caught.value.quota_reached and sleeps == []


@responses.activate
def test_per_minute_limit_is_not_mistaken_for_the_daily_one():
    minute = gemini_error(
        429,
        "RESOURCE_EXHAUSTED",
        "Quota exceeded (see GenerateRequestsPerDay docs)",
        {
            "@type": QUOTA_FAILURE,
            "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}],
        },
        {"@type": RETRY_INFO, "retryDelay": "5s"},
    )
    responses.post(GEMINI_URL, json=minute, status=429)
    responses.post(GEMINI_URL, json=gemini_answer("نص"))
    sleeps = []
    assert translator(sleeps=sleeps).to_arabic(["a"]) == ["نص"] and sleeps == [5.0]


@responses.activate
def test_a_key_echoed_by_google_is_hidden():
    body = gemini_error(
        400, "INVALID_ARGUMENT", f"Bad key {GEMINI_KEY} or AIzaSyD0123456789abcdefghijklmnopqrstu"
    )
    responses.post(GEMINI_URL, json=body, status=400)
    with pytest.raises(TranslationError) as caught:
        translator().to_arabic(["a"])
    assert GEMINI_KEY not in str(caught.value) and "AIzaSy" not in str(caught.value)
