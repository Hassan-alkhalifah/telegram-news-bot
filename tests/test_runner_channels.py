"""Runs with banners, the Arabic channel, and the weekly top 10."""

import io
import json
from datetime import datetime, timedelta, timezone

import responses
from PIL import Image

from newsbot.models import ArabicSettings, PostStyle, WeeklySettings
from newsbot.runner import run_once
from newsbot.state import BotState, PostedRecord, load_state, save_state
from newsbot.telegram import TelegramClient
from newsbot.translate import GeminiTranslator
from newsbot.web import make_session
from tests.helpers import (
    API,
    GEMINI_KEY,
    GEMINI_URL,
    NOW,
    TOKEN,
    gemini_answer,
    gemini_error,
    make_config,
    rss,
    rss_item,
    unique_title,
)

ARABIC_TITLE = "عنوان الخبر"
THUMB = '<media:thumbnail url="https://cdn.example.com/{n}.jpg"/>'


def no_sleep(_seconds):
    return None


def run(config, state_path, now=NOW, arabic=True, **extra):
    session = make_session()
    return run_once(
        config=config,
        session=session,
        client=TelegramClient(TOKEN, session, timeout=5, sleep=no_sleep),
        chat_id="@chan",
        arabic_chat_id="@chan_ar" if arabic else None,
        translator=GeminiTranslator(GEMINI_KEY, session, timeout=5, sleep=no_sleep) if arabic else None,
        state_path=state_path,
        now=now,
        sleep=no_sleep,
        out=lambda _text: None,
        **extra,
    )


def telegram_calls(method=None):
    calls = [c for c in responses.calls if c.request.url.startswith(API)]
    return [c for c in calls if method is None or c.request.url.endswith("/" + method)]


def body(call):
    return json.loads(call.request.body)


def register_stories(config, count=1):
    items = [
        rss_item(unique_title(f"story{n}x"), f"https://ai.example.com/{n}", extra=THUMB.format(n=n))
        for n in range(count)
    ]
    responses.get(config.topics[0].feeds[0].url, body=rss(*items))


def arabic_config(**settings):
    return make_config(topics=("AI",), arabic=ArabicSettings(enabled=True), **settings)


@responses.activate
def test_banner_is_uploaded_with_buttons(tmp_path):
    config = make_config(topics=("AI",), style=PostStyle(banner=True, channel_signature="@chan"))
    register_stories(config)
    picture = io.BytesIO()
    Image.new("RGB", (800, 450), (10, 120, 200)).save(picture, "PNG")
    responses.get("https://cdn.example.com/0.jpg", body=picture.getvalue(), content_type="image/png")
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 1}})

    result = run(config, tmp_path / "posted.json", arabic=False)

    assert result.posted == 1
    [call] = telegram_calls("sendPhoto")
    assert b'filename="banner.jpg"' in call.request.body
    assert b"inline_keyboard" in call.request.body and b"https://ai.example.com/0" in call.request.body


@responses.activate
def test_story_is_posted_in_english_then_in_arabic(tmp_path):
    config = arabic_config()
    register_stories(config)
    responses.post(GEMINI_URL, json=gemini_answer(ARABIC_TITLE))
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 1}})
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert (result.posted, result.arabic_posted, result.fatal_error) == (1, 1, None)
    english, arabic = telegram_calls("sendPhoto")
    assert body(english)["chat_id"] == "@chan" and unique_title("story0x") in body(english)["caption"]
    assert body(arabic)["chat_id"] == "@chan_ar" and ARABIC_TITLE in body(arabic)["caption"]
    assert body(arabic)["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://ai.example.com/0"
    assert load_state(state_path).records["ai.example.com/0"].title_ar == ARABIC_TITLE


@responses.activate
def test_used_up_translations_pause_arabic_but_english_goes_on(tmp_path):
    config = arabic_config()
    register_stories(config, count=2)
    responses.post(
        GEMINI_URL,
        json=gemini_error(
            429,
            "RESOURCE_EXHAUSTED",
            "quota",
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
            },
        ),
        status=429,
    )
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {}})

    result = run(config, tmp_path / "posted.json")

    assert (result.posted, result.arabic_posted, result.fatal_error) == (2, 0, None)
    assert len([c for c in responses.calls if c.request.url == GEMINI_URL]) == 1  # not tried again this run
    assert {body(c)["chat_id"] for c in telegram_calls("sendPhoto")} == {"@chan"}


@responses.activate
def test_gemini_outage_pauses_arabic_for_the_run(tmp_path):
    config = arabic_config()
    register_stories(config, count=2)
    responses.post(GEMINI_URL, json=gemini_error(503, "UNAVAILABLE"), status=503)
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {}})

    result = run(config, tmp_path / "posted.json")

    assert (result.posted, result.arabic_posted, result.fatal_error) == (2, 0, None)
    assert len([c for c in responses.calls if c.request.url == GEMINI_URL]) == 3  # one story's tries


@responses.activate
def test_arabic_setup_problem_turns_the_run_red_but_english_goes_on(tmp_path):
    config = arabic_config()
    register_stories(config, count=2)
    responses.post(GEMINI_URL, json=gemini_answer(ARABIC_TITLE))

    def answer(request):
        if json.loads(request.body)["chat_id"] == "@chan_ar":
            return (
                403,
                {},
                json.dumps({"ok": False, "error_code": 403, "description": "Forbidden: bot is not a member"}),
            )
        return 200, {}, json.dumps({"ok": True, "result": {}})

    responses.add_callback(responses.POST, f"{API}/sendPhoto", callback=answer)

    result = run(config, tmp_path / "posted.json")

    assert result.posted == 2 and result.arabic_posted == 0
    assert result.fatal_error.startswith("Arabic channel")
    assert len([c for c in telegram_calls("sendPhoto") if body(c)["chat_id"] == "@chan_ar"]) == 1


@responses.activate
def test_wrong_gemini_key_turns_the_run_red(tmp_path):
    config = arabic_config()
    register_stories(config)
    responses.post(
        GEMINI_URL,
        json=gemini_error(
            400,
            "INVALID_ARGUMENT",
            "API key not valid.",
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"},
        ),
        status=400,
    )
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {}})

    result = run(config, tmp_path / "posted.json")

    assert result.posted == 1 and "GEMINI_API_KEY" in result.fatal_error


FRIDAY_EVENING = datetime(2026, 10, 30, 17, 30, tzinfo=timezone.utc)  # Friday 20:30 in UTC+3


def save_week(state_path):
    state = BotState.empty()
    for n in range(4):
        record = PostedRecord(
            at=FRIDAY_EVENING - timedelta(days=n + 1),
            title=f"Week story {n}",
            topic="AI",
            status="posted",
            url=f"https://e.com/{n}",
            score=10 - n,
            title_ar=f"خبر {n}",
        )
        state = state.with_record(f"e.com/{n}", record)
    save_state(state_path, state)


@responses.activate
def test_weekly_top_list_and_poll_in_both_channels(tmp_path):
    weekly = WeeklySettings(enabled=True, weekday=4, local_hour=20, size=10, poll_options=3, title="Week top")
    config = arabic_config(weekly=weekly)
    responses.get(config.topics[0].feeds[0].url, body=rss())
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {}})
    responses.post(f"{API}/sendPoll", json={"ok": True, "result": {}})
    state_path = tmp_path / "posted.json"
    save_week(state_path)

    result = run(config, state_path, now=FRIDAY_EVENING)

    assert result.weekly_sent
    lists = telegram_calls("sendMessage")
    polls = telegram_calls("sendPoll")
    assert [body(c)["chat_id"] for c in lists] == ["@chan", "@chan_ar"]
    assert "Week top" in body(lists[0])["text"] and "Week story 0" in body(lists[0])["text"]
    assert "خبر 0" in body(lists[1])["text"]
    assert [o["text"] for o in body(polls[0])["options"]] == ["Week story 0", "Week story 1", "Week story 2"]
    assert body(polls[1])["chat_id"] == "@chan_ar"
    assert load_state(state_path).weekly_day == "2026-10-30"

    again = run(config, state_path, now=FRIDAY_EVENING + timedelta(minutes=15))
    assert not again.weekly_sent and len(telegram_calls("sendPoll")) == 2


@responses.activate
def test_failed_poll_does_not_resend_the_weekly_list(tmp_path):
    weekly = WeeklySettings(enabled=True, weekday=4, local_hour=20, size=10, poll_options=3, title="Week top")
    config = make_config(topics=("AI",), weekly=weekly)
    responses.get(config.topics[0].feeds[0].url, body=rss())
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {}})
    no_rights = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: not enough rights to send polls",
    }
    responses.post(f"{API}/sendPoll", json=no_rights, status=400)
    state_path = tmp_path / "posted.json"
    save_week(state_path)

    first = run(config, state_path, now=FRIDAY_EVENING, arabic=False)
    second = run(config, state_path, now=FRIDAY_EVENING + timedelta(minutes=15), arabic=False)

    assert first.weekly_sent and first.fatal_error is None
    assert not second.weekly_sent
    assert len(telegram_calls("sendMessage")) == 1


@responses.activate
def test_arabic_problem_does_not_stop_the_english_digest(tmp_path):
    from newsbot.models import DigestSettings

    digest = DigestSettings(
        enabled=True, local_hour=21, utc_offset_hours=3, size=5, min_stories=1, title="Top today"
    )
    config = make_config(topics=("AI",), arabic=ArabicSettings(enabled=True), digest=digest)
    register_stories(config)
    responses.post(
        GEMINI_URL,
        json=gemini_error(
            400,
            "INVALID_ARGUMENT",
            "API key not valid.",
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"},
        ),
        status=400,
    )
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {}})
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {}})
    state_path = tmp_path / "posted.json"
    evening = datetime(2026, 10, 31, 18, 30, tzinfo=timezone.utc)
    earlier = PostedRecord(
        at=evening - timedelta(hours=3),
        title="Earlier story",
        topic="AI",
        status="posted",
        url="https://e.com/1",
    )
    save_state(state_path, BotState.empty().with_record("e.com/1", earlier))

    result = run(config, state_path, now=evening)

    assert result.posted == 1 and result.digest_sent
    assert "GEMINI_API_KEY" in result.fatal_error


@responses.activate
def test_unexpected_arabic_error_keeps_the_english_post_remembered(tmp_path, monkeypatch):
    import newsbot.runner as runner

    def broken(*_args, **_kwargs):
        raise RuntimeError("something nobody expected")

    monkeypatch.setattr(runner, "translate_story", broken)
    config = arabic_config()
    register_stories(config)
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {}})
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert result.posted == 1 and result.arabic_posted == 0
    assert load_state(state_path).has("ai.example.com/0")
