import json

import pytest
import requests
import responses

from newsbot.telegram import FatalTelegramError, TelegramClient, TelegramError, UncertainDeliveryError
from newsbot.web import make_session
from tests.helpers import API, TOKEN, refused, reset


def make_client(sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return TelegramClient(TOKEN, make_session(), timeout=5, sleep=sleeps.append)


def sent_body(index=0):
    return json.loads(responses.calls[index].request.body)


@responses.activate
def test_send_photo():
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 1}})
    assert make_client().send_photo("@chan", "https://cdn/p.jpg", "<b>x</b>") == {"message_id": 1}
    assert sent_body() == {
        "chat_id": "@chan",
        "photo": "https://cdn/p.jpg",
        "caption": "<b>x</b>",
        "parse_mode": "HTML",
    }


@responses.activate
def test_send_message_with_link_preview():
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {"message_id": 2}})
    make_client().send_message("@chan", "hello", preview_url="https://a.com/1")
    assert sent_body()["link_preview_options"] == {"url": "https://a.com/1", "prefer_large_media": True}
    assert sent_body()["parse_mode"] == "HTML"


@responses.activate
def test_send_message_without_preview():
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {"message_id": 3}})
    make_client().send_message("@chan", "hello")
    assert sent_body()["link_preview_options"] == {"is_disabled": True}


@responses.activate
def test_flood_control_waits_then_retries():
    flood = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests",
        "parameters": {"retry_after": 7},
    }
    responses.post(f"{API}/sendMessage", json=flood, status=429)
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {"message_id": 4}})
    sleeps = []
    assert make_client(sleeps).send_message("@chan", "x") == {"message_id": 4}
    assert sleeps == [7]


@responses.activate
def test_flood_wait_is_capped():
    flood = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests",
        "parameters": {"retry_after": 900},
    }
    responses.post(f"{API}/sendMessage", json=flood, status=429)
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {}})
    sleeps = []
    make_client(sleeps).send_message("@chan", "x")
    assert sleeps == [60]


@pytest.mark.parametrize(
    "status, description",
    [
        (401, "Unauthorized"),
        (403, "Forbidden: bot is not a member of the channel chat"),
        (400, "Bad Request: chat not found"),
        (400, "Bad Request: need administrator rights in the channel chat"),
        (400, "Bad Request: have no rights to send a message"),
        (400, "Bad Request: not enough rights to send photos to the chat"),
    ],
)
@responses.activate
def test_setup_problems_are_fatal(status, description):
    responses.post(
        f"{API}/sendPhoto",
        json={"ok": False, "error_code": status, "description": description},
        status=status,
    )
    with pytest.raises(FatalTelegramError):
        make_client().send_photo("@chan", "https://cdn/p.jpg", "x")


@responses.activate
def test_other_bad_request_is_not_fatal():
    body = {"ok": False, "error_code": 400, "description": "Bad Request: wrong type of the web page content"}
    responses.post(f"{API}/sendPhoto", json=body, status=400)
    with pytest.raises(TelegramError) as caught:
        make_client().send_photo("@chan", "https://cdn/p.jpg", "x")
    assert not isinstance(caught.value, FatalTelegramError)
    assert "wrong type" in str(caught.value)


@responses.activate
def test_server_error_is_retried_for_safe_requests():
    responses.post(f"{API}/getMe", body="Bad Gateway", status=502)
    responses.post(f"{API}/getMe", json={"ok": True, "result": {"id": 1}})
    sleeps = []
    assert make_client(sleeps).get_me() == {"id": 1}
    assert sleeps == [2]


@responses.activate
def test_server_error_on_a_post_is_not_repeated():
    responses.post(f"{API}/sendMessage", body="Bad Gateway", status=502)
    sleeps = []
    with pytest.raises(UncertainDeliveryError):
        make_client(sleeps).send_message("@chan", "x")
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_read_timeout_on_a_post_is_not_repeated():
    responses.post(f"{API}/sendPhoto", body=requests.ReadTimeout("read timed out"))
    with pytest.raises(UncertainDeliveryError):
        make_client().send_photo("@chan", "https://cdn/p.jpg", "x")
    assert len(responses.calls) == 1


@responses.activate
def test_connect_timeout_on_a_post_is_retried():
    responses.post(f"{API}/sendMessage", body=requests.ConnectTimeout("connect timed out"))
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {"message_id": 6}})
    sleeps = []
    assert make_client(sleeps).send_message("@chan", "x") == {"message_id": 6}
    assert sleeps == [2]


@responses.activate
def test_rejection_is_final_but_flood_control_is_temporary():
    rejected = {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
    responses.post(f"{API}/sendPhoto", json=rejected, status=400)
    with pytest.raises(TelegramError) as caught:
        make_client().send_photo("@chan", "https://cdn/p.jpg", "x")
    assert caught.value.retryable is False

    flood = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests",
        "parameters": {"retry_after": 1},
    }
    for _ in range(3):
        responses.post(f"{API}/sendMessage", json=flood, status=429)
    with pytest.raises(TelegramError) as caught:
        make_client().send_message("@chan", "x")
    assert caught.value.retryable is True
    assert not isinstance(caught.value, UncertainDeliveryError)


@pytest.mark.parametrize("parameters", ["not a mapping", {"retry_after": "soon"}, None])
@responses.activate
def test_odd_flood_reply_uses_the_default_wait(parameters):
    flood = {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": parameters}
    responses.post(f"{API}/sendMessage", json=flood, status=429)
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {}})
    sleeps = []
    make_client(sleeps).send_message("@chan", "x")
    assert sleeps == [5]


@responses.activate
def test_network_error_never_shows_the_token():
    for _ in range(3):
        responses.post(
            f"{API}/sendMessage",
            body=refused(f"{API}/sendMessage"),
        )
    sleeps = []
    with pytest.raises(TelegramError) as caught:
        make_client(sleeps).send_message("@chan", "x")
    assert TOKEN not in str(caught.value)
    assert caught.value.__suppress_context__
    assert caught.value.retryable is True
    assert sleeps == [2, 4]


@responses.activate
def test_setup_helper_methods():
    responses.post(f"{API}/getMe", json={"ok": True, "result": {"id": 9, "username": "my_bot"}})
    responses.post(f"{API}/getChat", json={"ok": True, "result": {"title": "News"}})
    responses.post(f"{API}/getChatMember", json={"ok": True, "result": {"status": "administrator"}})
    responses.post(f"{API}/getUpdates", json={"ok": True, "result": []})
    client = make_client()
    assert client.get_me()["username"] == "my_bot"
    assert client.get_chat("@chan")["title"] == "News"
    assert client.get_chat_member("@chan", 9)["status"] == "administrator"
    assert client.get_updates() == []
    assert sent_body(1) == {"chat_id": "@chan"}
    assert sent_body(2) == {"chat_id": "@chan", "user_id": 9}


BUTTONS = {"inline_keyboard": [[{"text": "Read", "url": "https://e.com/1"}]]}


@responses.activate
def test_send_photo_uploads_a_banner_with_buttons():
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 7}})
    result = make_client().send_photo("@chan", b"\xff\xd8jpegbytes", "<b>x</b>", buttons=BUTTONS)
    assert result == {"message_id": 7}
    body = responses.calls[0].request.body
    assert b'filename="banner.jpg"' in body and b"jpegbytes" in body
    assert b'name="reply_markup"' in body and json.dumps(BUTTONS).encode() in body
    assert b'name="parse_mode"' in body and b"HTML" in body


@responses.activate
def test_send_photo_by_link_and_message_with_buttons():
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {}})
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {}})
    client = make_client()
    client.send_photo("@chan", "https://cdn/p.jpg", "x", buttons=BUTTONS)
    client.send_message("@chan", "x", preview_url="https://e.com/1", buttons=BUTTONS)
    assert sent_body(0)["reply_markup"] == BUTTONS and sent_body(0)["photo"] == "https://cdn/p.jpg"
    assert sent_body(1)["reply_markup"] == BUTTONS


@responses.activate
def test_send_poll_and_pin():
    responses.post(f"{API}/sendPoll", json={"ok": True, "result": {"message_id": 3}})
    responses.post(f"{API}/pinChatMessage", json={"ok": True, "result": True})
    client = make_client()
    client.send_poll("@chan", "Which?", ["A", "B"])
    client.pin_chat_message("@chan", 3)
    assert sent_body(0) == {
        "chat_id": "@chan",
        "question": "Which?",
        "options": [{"text": "A"}, {"text": "B"}],
    }
    assert sent_body(1) == {"chat_id": "@chan", "message_id": 3, "disable_notification": True}


@responses.activate
def test_a_poll_is_never_sent_twice():
    responses.post(f"{API}/sendPoll", body="Bad Gateway", status=502)
    with pytest.raises(UncertainDeliveryError):
        make_client().send_poll("@chan", "Which?", ["A", "B"])
    assert len(responses.calls) == 1


@responses.activate
def test_connection_drop_during_an_upload_is_not_repeated():
    responses.post(f"{API}/sendPhoto", body=reset())
    with pytest.raises(UncertainDeliveryError):
        make_client().send_photo("@chan", b"jpeg", "x")
    assert len(responses.calls) == 1


@responses.activate
def test_connection_drop_on_a_safe_request_is_retried():
    responses.post(f"{API}/getMe", body=reset())
    responses.post(f"{API}/getMe", json={"ok": True, "result": {"id": 1}})
    sleeps = []
    assert make_client(sleeps).get_me() == {"id": 1}
