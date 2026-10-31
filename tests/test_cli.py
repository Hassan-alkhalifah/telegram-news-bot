import io
from datetime import datetime, timedelta, timezone

import pytest
import responses

from newsbot.cli import main, use_utf8_output
from tests.helpers import API, TOKEN, rfc822, rss, rss_item, unique_title


def test_output_is_switched_to_utf8():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    use_utf8_output([stream, object()])  # objects without reconfigure are left alone
    stream.write("鬼滅の刃")
    stream.flush()
    assert stream.buffer.getvalue().decode("utf-8") == "鬼滅の刃"


FEED_URL = "https://feed.example.com/rss"
CONFIG_YAML = f"""
settings:
  max_posts_per_run: 2
  post_delay_seconds: 0
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
    return {"config": str(config), "state": str(tmp_path / "posted.json")}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@my_news")
    return monkeypatch


def cli(files, *flags):
    return main([*flags, "--config", files["config"], "--state", files["state"]], env_file=None)


def register_feed():
    recent = rfc822(datetime.now(timezone.utc) - timedelta(minutes=30))
    responses.get(
        FEED_URL,
        body=rss(
            rss_item(
                unique_title("cli"),
                "https://feed.example.com/1",
                pub=recent,
                extra='<media:thumbnail url="https://cdn.example.com/1.jpg"/>',
            )
        ),
    )


def test_missing_token_is_a_setup_error(files, monkeypatch, caplog):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@my_news")
    assert cli(files) == 2
    assert "TELEGRAM_BOT_TOKEN" in caplog.text


def test_malformed_token_is_rejected(files, env, caplog):
    env.setenv("TELEGRAM_BOT_TOKEN", "not-a-token")
    assert cli(files) == 2
    assert "does not look like a bot token" in caplog.text


@pytest.mark.parametrize("bad_id", ["", "my_news", "@ab", "https://t.me/my_news"])
def test_bad_channel_id_is_rejected(files, env, caplog, bad_id):
    env.setenv("TELEGRAM_CHANNEL_ID", bad_id)
    assert cli(files) == 2
    assert "TELEGRAM_CHANNEL_ID" in caplog.text


def test_broken_config_is_a_setup_error(files, env, caplog, tmp_path):
    (tmp_path / "config.yaml").write_text("topics: []", encoding="utf-8")
    assert cli(files) == 2
    assert "topics" in caplog.text


@responses.activate
def test_dry_run_needs_no_token(files, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)
    register_feed()
    assert cli(files, "--dry-run") == 0
    assert unique_title("cli") in capsys.readouterr().out


@responses.activate
def test_normal_run_posts(files, env):
    register_feed()
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 1}})
    assert cli(files) == 0
    assert any(c.request.url == f"{API}/sendPhoto" for c in responses.calls)


@responses.activate
def test_run_fails_when_bot_cannot_post(files, env, caplog):
    register_feed()
    responses.post(
        f"{API}/sendPhoto", json={"ok": False, "error_code": 401, "description": "Unauthorized"}, status=401
    )
    assert cli(files) == 1
    assert "token" in caplog.text.lower()


def register_check(member):
    responses.post(f"{API}/getMe", json={"ok": True, "result": {"id": 42, "username": "my_news_bot"}})
    responses.post(f"{API}/getChat", json={"ok": True, "result": {"title": "My News", "type": "channel"}})
    responses.post(f"{API}/getChatMember", json={"ok": True, "result": member})


@responses.activate
def test_check_passes_for_admin_bot(files, env, capsys):
    register_check({"status": "administrator", "can_post_messages": True})
    assert cli(files, "--check") == 0
    out = capsys.readouterr().out
    assert "@my_news_bot" in out and "My News" in out and "can post" in out


@responses.activate
def test_check_fails_when_bot_is_not_admin(files, env, capsys):
    register_check({"status": "member"})
    assert cli(files, "--check") == 1
    assert "admin" in capsys.readouterr().out


@responses.activate
def test_check_fails_without_post_permission(files, env, capsys):
    register_check({"status": "administrator", "can_post_messages": False})
    assert cli(files, "--check") == 1
    assert "Post Messages" in capsys.readouterr().out


@responses.activate
def test_check_reports_wrong_token(files, env, capsys):
    responses.post(
        f"{API}/getMe", json={"ok": False, "error_code": 401, "description": "Unauthorized"}, status=401
    )
    assert cli(files, "--check") == 1
    assert "token" in capsys.readouterr().out.lower()


@responses.activate
def test_find_chat_id_lists_channels(files, env, capsys):
    env.delenv("TELEGRAM_CHANNEL_ID")
    responses.post(
        f"{API}/getUpdates",
        json={
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "channel_post": {"chat": {"id": -1001234567890, "title": "My News", "type": "channel"}},
                }
            ],
        },
    )
    assert cli(files, "--find-chat-id") == 0
    assert "-1001234567890" in capsys.readouterr().out


@responses.activate
def test_find_chat_id_explains_when_nothing_found(files, env, capsys):
    responses.post(f"{API}/getUpdates", json={"ok": True, "result": []})
    assert cli(files, "--find-chat-id") == 1
    assert "post a message in your channel" in capsys.readouterr().out


@responses.activate
def test_run_fails_when_nothing_could_be_posted(files, env, caplog):
    register_feed()
    rejected = {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
    responses.post(f"{API}/sendPhoto", json=rejected, status=400)
    responses.post(f"{API}/sendMessage", json=rejected, status=400)
    assert cli(files) == 1
    assert "Nothing could be posted" in caplog.text
