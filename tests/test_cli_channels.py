"""Command line: the Arabic channel, Gemini, the language picker, the weekly post and banner previews."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import responses

from newsbot.cli import main
from tests.helpers import (
    API,
    GEMINI_KEY,
    GEMINI_URL,
    TOKEN,
    gemini_answer,
    gemini_error,
    rfc822,
    rss,
    rss_item,
    unique_title,
)

FEED_URL = "https://feed.example.com/rss"
CONFIG_YAML = f"""
settings:
  max_posts_per_run: 2
  post_delay_seconds: 0
  min_score: 0
arabic:
  enabled: true
weekly:
  title: Week top
topics:
  - name: AI
    hashtag: AI
    feeds:
      - name: Example
        url: {FEED_URL}
"""


@pytest.fixture
def files(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_YAML, encoding="utf-8")
    return {"config": str(config), "state": str(tmp_path / "posted.json"), "dir": tmp_path}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@my_news")
    monkeypatch.setenv("TELEGRAM_ARABIC_CHANNEL_ID", "@my_news_ar")
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    return monkeypatch


def cli(files, *flags):
    return main([*flags, "--config", files["config"], "--state", files["state"]], env_file=None)


def register_feed():
    recent = rfc822(datetime.now(timezone.utc) - timedelta(minutes=30))
    responses.get(FEED_URL, body=rss(rss_item(unique_title("cli"), "https://feed.example.com/1", pub=recent)))


def bodies(method):
    return [json.loads(c.request.body) for c in responses.calls if c.request.url == f"{API}/{method}"]


def test_bad_arabic_channel_id_is_a_setup_error(files, env, caplog):
    env.setenv("TELEGRAM_ARABIC_CHANNEL_ID", "not a channel")
    assert cli(files) == 2
    assert "TELEGRAM_ARABIC_CHANNEL_ID" in caplog.text


@pytest.mark.parametrize("key", ["two words", "line\nbreak", "short"])
def test_a_mistyped_gemini_key_is_a_setup_error(files, env, caplog, key):
    env.setenv("GEMINI_API_KEY", key)
    assert cli(files) == 2
    assert "GEMINI_API_KEY" in caplog.text and key not in caplog.text


@responses.activate
def test_arabic_needs_both_secrets(files, env, caplog, capsys):
    env.delenv("GEMINI_API_KEY")
    register_feed()
    assert cli(files, "--dry-run") == 0
    assert "Arabic channel is off" in caplog.text
    assert "translated into Arabic" not in capsys.readouterr().out


@responses.activate
def test_dry_run_mentions_the_arabic_version_and_saves_banners(files, env, capsys):
    register_feed()
    banners = files["dir"] / "banners"
    assert cli(files, "--dry-run", "--banners-dir", str(banners)) == 0
    assert "translated into Arabic and posted to @my_news_ar" in capsys.readouterr().out
    assert [p.name for p in banners.iterdir()] == ["banner_1.jpg"]
    assert not [
        c for c in responses.calls if "generativelanguage" in c.request.url
    ]  # previews never use Gemini


@responses.activate
def test_weekly_now_previews_the_weekly_post(files, env, capsys):
    from newsbot.state import BotState, PostedRecord, save_state

    record = PostedRecord(
        at=datetime.now(timezone.utc) - timedelta(days=2),
        title="Big week story",
        topic="AI",
        status="posted",
        url="https://e.com/1",
    )
    save_state(files["state"], BotState.empty().with_record("e.com/1", record))
    responses.get(FEED_URL, body=rss())
    assert cli(files, "--dry-run", "--weekly-now") == 0
    out = capsys.readouterr().out
    assert "WEEKLY" in out and "Week top" in out and "Big week story" in out


def register_member(status="administrator", **rights):
    responses.post(f"{API}/getMe", json={"ok": True, "result": {"id": 42, "username": "my_news_bot"}})
    responses.post(f"{API}/getChat", json={"ok": True, "result": {"title": "News", "type": "channel"}})
    responses.post(f"{API}/getChatMember", json={"ok": True, "result": {"status": status, **rights}})


@responses.activate
def test_check_covers_both_channels_and_gemini(files, env, capsys):
    register_member(can_post_messages=True, can_edit_messages=True)
    responses.post(GEMINI_URL, json=gemini_answer("أخبار عاجلة"))
    assert cli(files, "--check") == 0
    out = capsys.readouterr().out
    assert "@my_news_ar" in out and "Gemini key OK" in out and "أخبار عاجلة" in out


@responses.activate
def test_check_reports_a_wrong_gemini_key(files, env, capsys):
    register_member(can_post_messages=True)
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
    assert cli(files, "--check") == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out


def no_pinned_picker():
    responses.post(f"{API}/getChat", json={"ok": True, "result": {"id": 1, "type": "channel"}})


@responses.activate
def test_language_picker_is_posted_and_pinned_in_both_channels(files, env, capsys):
    no_pinned_picker()
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 77}})
    responses.post(f"{API}/pinChatMessage", json={"ok": True, "result": True})
    assert cli(files, "--setup-language-picker") == 0
    photos = [c.request.body for c in responses.calls if c.request.url == f"{API}/sendPhoto"]
    assert len(photos) == 2 and all(b'filename="banner.jpg"' in p for p in photos)
    assert b"https://t.me/my_news" in photos[0] and b"https://t.me/my_news_ar" in photos[0]
    assert [b["chat_id"] for b in bodies("pinChatMessage")] == ["@my_news", "@my_news_ar"]
    assert "Pinned" in capsys.readouterr().out


@responses.activate
def test_language_picker_explains_a_missing_pin_right(files, env, capsys):
    no_pinned_picker()
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 77}})
    rights = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: not enough rights to manage pinned messages in the chat",
    }
    responses.post(f"{API}/pinChatMessage", json=rights, status=400)
    assert cli(files, "--setup-language-picker") == 1
    assert "Edit Messages" in capsys.readouterr().out


def pinned(message_id, *urls):
    buttons = [[{"text": str(n), "url": url} for n, url in enumerate(urls)]]
    message = {
        "message_id": message_id,
        "photo": [{"file_id": "x"}],
        "reply_markup": {"inline_keyboard": buttons},
    }
    return {"ok": True, "result": {"id": 1, "type": "channel", "pinned_message": message}}


@responses.activate
def test_running_the_picker_again_updates_the_pinned_one(files, env, capsys):
    links = ("https://t.me/my_news", "https://t.me/my_news_ar")
    responses.post(f"{API}/getChat", json=pinned(25, *links))
    responses.post(f"{API}/getChat", json=pinned(3, *links))
    responses.post(f"{API}/editMessageMedia", json={"ok": True, "result": {"message_id": 1}})
    assert cli(files, "--setup-language-picker") == 0
    edits = [c.request.body for c in responses.calls if c.request.url == f"{API}/editMessageMedia"]
    assert len(edits) == 2 and b'name="message_id"\r\n\r\n25' in edits[0] and b"\r\n\r\n3\r\n" in edits[1]
    assert not bodies("pinChatMessage")
    assert not [c for c in responses.calls if c.request.url == f"{API}/sendPhoto"]
    assert "Updated the pinned language picker" in capsys.readouterr().out


@responses.activate
def test_some_other_pinned_post_is_left_alone(files, env, capsys):
    responses.post(f"{API}/getChat", json=pinned(9, "https://example.com/sale"))
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 77}})
    responses.post(f"{API}/pinChatMessage", json={"ok": True, "result": True})
    assert cli(files, "--setup-language-picker") == 0
    assert not [c for c in responses.calls if c.request.url == f"{API}/editMessageMedia"]
    assert len(bodies("pinChatMessage")) == 2


def test_language_picker_needs_both_public_channels(files, env, capsys):
    env.setenv("TELEGRAM_ARABIC_CHANNEL_ID", "-1001234567890")
    assert cli(files, "--setup-language-picker") == 1
    assert "@username" in capsys.readouterr().out


@pytest.mark.parametrize("same", ["@my_news", "@MY_NEWS"])
def test_arabic_channel_cannot_be_the_english_channel(files, env, caplog, same):
    env.setenv("TELEGRAM_ARABIC_CHANNEL_ID", same)
    assert cli(files) == 2
    assert "different" in caplog.text


@responses.activate
def test_picker_that_cannot_be_updated_is_reported(files, env, capsys):
    links = ("https://t.me/my_news", "https://t.me/my_news_ar")
    responses.post(f"{API}/getChat", json=pinned(25, *links))
    no_rights = {"ok": False, "error_code": 400, "description": "Bad Request: message can't be edited"}
    responses.post(f"{API}/editMessageMedia", json=no_rights, status=400)
    assert cli(files, "--setup-language-picker") == 1
    assert "could not update the pinned language picker" in capsys.readouterr().out
    edits = [c for c in responses.calls if c.request.url == f"{API}/editMessageMedia"]
    assert len(edits) == 2  # one channel's problem does not stop the other


@responses.activate
def test_picker_that_is_already_up_to_date_is_fine(files, env, capsys):
    links = ("https://t.me/my_news", "https://t.me/my_news_ar")
    responses.post(f"{API}/getChat", json=pinned(25, *links))
    same = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: message is not modified: specified new message content and reply "
        "markup are exactly the same as a current content and reply markup of the message",
    }
    responses.post(f"{API}/editMessageMedia", json=same, status=400)
    assert cli(files, "--setup-language-picker") == 0
    assert "already up to date" in capsys.readouterr().out


@responses.activate
def test_channel_that_cannot_be_read_gets_a_new_picker(files, env, capsys):
    unreadable = {"ok": False, "error_code": 400, "description": "Bad Request: something odd"}
    responses.post(f"{API}/getChat", json=unreadable, status=400)
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 77}})
    responses.post(f"{API}/pinChatMessage", json={"ok": True, "result": True})
    assert cli(files, "--setup-language-picker") == 0
    assert len(bodies("pinChatMessage")) == 2
    assert "could not check the pinned post" in capsys.readouterr().out


@responses.activate
def test_picker_that_cannot_be_posted_is_reported(files, env, capsys):
    no_pinned_picker()
    refused = {
        "ok": False,
        "error_code": 403,
        "description": "Forbidden: bot is not a member of the channel chat",
    }
    responses.post(f"{API}/sendPhoto", json=refused, status=403)
    assert cli(files, "--setup-language-picker") == 1
    assert "could not post the language picker" in capsys.readouterr().out
