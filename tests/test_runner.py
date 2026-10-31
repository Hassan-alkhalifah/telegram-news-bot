import json

import requests
import responses

from newsbot.runner import run_once
from newsbot.state import BotState, PostedRecord, load_state, save_state
from newsbot.telegram import TelegramClient
from newsbot.web import make_session
from tests.helpers import API, NOW, TOKEN, make_config, rss, rss_item, unique_title

THUMB = '<media:thumbnail url="https://cdn.example.com/{n}.jpg"/>'


def no_sleep(_seconds):
    return None


def run(config, state_path, dry_run=False, out=None):
    session = make_session()
    client = None if dry_run else TelegramClient(TOKEN, session, timeout=5, sleep=no_sleep)
    return run_once(
        config=config,
        session=session,
        client=client,
        chat_id="@chan",
        state_path=state_path,
        now=NOW,
        dry_run=dry_run,
        sleep=no_sleep,
        out=(out.append if out is not None else print),
    )


def telegram_calls():
    return [c for c in responses.calls if c.request.url.startswith(API)]


def register_feeds(config):
    ai_url, games_url = (t.feeds[0].url for t in config.topics)
    responses.get(
        ai_url,
        body=rss(
            rss_item(unique_title("aione"), "https://ai.example.com/1", extra=THUMB.format(n=1)),
            rss_item(unique_title("aitwo"), "https://ai.example.com/2", extra=THUMB.format(n=2)),
        ),
    )
    responses.get(games_url, body=rss(rss_item(unique_title("game"), "https://games.example.com/1")))
    responses.get(
        "https://games.example.com/1",
        body='<head><meta property="og:image" content="https://cdn.example.com/og.jpg"></head>',
        content_type="text/html",
    )


@responses.activate
def test_posts_new_stories_and_remembers_them(tmp_path):
    config = make_config(topics=("AI", "Games"), max_posts_per_run=4)
    register_feeds(config)
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 1}})
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert (result.posted, result.failed_posts, result.fatal_error) == (3, 0, None)
    photos = [json.loads(c.request.body)["photo"] for c in telegram_calls()]
    assert photos == [
        "https://cdn.example.com/1.jpg",
        "https://cdn.example.com/og.jpg",
        "https://cdn.example.com/2.jpg",
    ]
    state = load_state(state_path)
    assert all(state.has(k) for k in ("ai.example.com/1", "ai.example.com/2", "games.example.com/1"))

    sent_before = len(telegram_calls())
    second = run(config, state_path)
    assert second.posted == 0
    assert len(telegram_calls()) == sent_before


@responses.activate
def test_dry_run_sends_and_saves_nothing(tmp_path):
    config = make_config(topics=("AI", "Games"))
    register_feeds(config)
    state_path = tmp_path / "posted.json"
    out = []

    result = run(config, state_path, dry_run=True, out=out)

    assert result.posted == 0
    assert result.would_post == 3
    assert telegram_calls() == []
    assert not state_path.exists()
    assert unique_title("aione") in "\n".join(out)


@responses.activate
def test_all_feeds_failing_is_reported(tmp_path):
    config = make_config(topics=("AI", "Games"))
    for topic in config.topics:
        responses.get(topic.feeds[0].url, status=503)
    result = run(config, tmp_path / "posted.json")
    assert result.fatal_error is not None
    assert "feeds" in result.fatal_error
    assert result.feeds_failed == 2


@responses.activate
def test_setup_error_stops_the_run_but_keeps_progress(tmp_path):
    config = make_config(topics=("AI", "Games"))
    register_feeds(config)
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {}})
    responses.post(
        f"{API}/sendPhoto",
        json={
            "ok": False,
            "error_code": 403,
            "description": "Forbidden: bot was kicked from the channel chat",
        },
        status=403,
    )
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert result.posted == 1
    assert "kicked" in result.fatal_error
    state = load_state(state_path)
    assert state.has("ai.example.com/1")
    assert not state.has("games.example.com/1")


def register_one_story(config, n=9):
    responses.get(
        config.topics[0].feeds[0].url,
        body=rss(rss_item(unique_title("x"), f"https://ai.example.com/{n}", extra=THUMB.format(n=n))),
    )


@responses.activate
def test_rejected_post_is_remembered_and_never_retried(tmp_path):
    config = make_config(topics=("AI",))
    register_one_story(config)
    bad = {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
    responses.post(f"{API}/sendPhoto", json=bad, status=400)
    responses.post(f"{API}/sendMessage", json=bad, status=400)
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert (result.posted, result.failed_posts, result.fatal_error) == (0, 1, None)
    assert load_state(state_path).records["ai.example.com/9"].status == "failed"
    sent_before = len(telegram_calls())
    run(config, state_path)
    assert len(telegram_calls()) == sent_before


@responses.activate
def test_temporary_failure_is_retried_next_run(tmp_path):
    config = make_config(topics=("AI",))
    register_one_story(config)
    for method in ("sendPhoto", "sendMessage"):
        for _ in range(3):
            responses.post(f"{API}/{method}", body=requests.ConnectionError("network is down"))
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert (result.posted, result.failed_posts) == (0, 1)
    assert not load_state(state_path).has("ai.example.com/9")


@responses.activate
def test_unclear_delivery_is_remembered_so_it_is_never_sent_twice(tmp_path):
    config = make_config(topics=("AI",))
    register_one_story(config)
    responses.post(f"{API}/sendPhoto", body="Bad Gateway", status=502)
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert (result.posted, result.unconfirmed, result.failed_posts) == (0, 1, 0)
    assert [c.request.url for c in telegram_calls()] == [f"{API}/sendPhoto"]
    assert load_state(state_path).records["ai.example.com/9"].status == "posted"


@responses.activate
def test_broken_state_file_relearns_stories_without_posting(tmp_path):
    config = make_config(topics=("AI", "Games"))
    register_feeds(config)
    state_path = tmp_path / "posted.json"
    state_path.write_text("{broken", encoding="utf-8")

    result = run(config, state_path)

    assert result.posted == 0
    assert "could not be read" in result.fatal_error
    assert telegram_calls() == []
    assert (tmp_path / "posted.json.corrupt").read_text(encoding="utf-8") == "{broken"
    records = load_state(state_path).records
    assert {k: r.status for k, r in records.items()} == {
        "ai.example.com/1": "seeded",
        "ai.example.com/2": "seeded",
        "games.example.com/1": "seeded",
    }
    second = run(config, state_path)
    assert (second.posted, second.fatal_error) == (0, None)
    assert telegram_calls() == []


@responses.activate
def test_dry_run_with_broken_state_file_changes_nothing(tmp_path):
    config = make_config(topics=("AI", "Games"))
    register_feeds(config)
    state_path = tmp_path / "posted.json"
    state_path.write_text("{broken", encoding="utf-8")

    result = run(config, state_path, dry_run=True, out=[])

    assert result.would_post == 3
    assert state_path.read_text(encoding="utf-8") == "{broken"
    assert not (tmp_path / "posted.json.corrupt").exists()


@responses.activate
def test_duplicate_story_is_recorded_not_posted(tmp_path):
    config = make_config(topics=("AI",))
    state_path = tmp_path / "posted.json"
    earlier = PostedRecord(
        at=NOW, title="Nintendo announces new Zelda game for Switch 2", topic="AI", status="posted"
    )
    save_state(state_path, BotState.empty().with_record("other.com/z", earlier))
    responses.get(
        config.topics[0].feeds[0].url,
        body=rss(rss_item("New Zelda game announced by Nintendo for Switch 2", "https://ai.example.com/z")),
    )

    result = run(config, state_path)

    assert (result.posted, result.duplicates) == (0, 1)
    assert telegram_calls() == []
    record = load_state(state_path).records["ai.example.com/z"]
    assert record.status == "duplicate"


@responses.activate
def test_broken_state_file_is_kept_when_feeds_are_down(tmp_path):
    config = make_config(topics=("AI", "Games"))
    for topic in config.topics:
        responses.get(topic.feeds[0].url, status=503)
    state_path = tmp_path / "posted.json"
    state_path.write_text("{broken", encoding="utf-8")

    result = run(config, state_path)

    assert "feeds" in result.fatal_error
    assert state_path.read_text(encoding="utf-8") == "{broken"
    assert not (tmp_path / "posted.json.corrupt").exists()
