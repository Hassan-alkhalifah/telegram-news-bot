import json
from datetime import datetime, timedelta, timezone

import responses

from newsbot.models import DigestSettings
from newsbot.runner import run_once
from newsbot.state import BotState, PostedRecord, load_state, save_state
from newsbot.telegram import TelegramClient
from newsbot.web import make_session
from tests.helpers import API, NOW, TOKEN, make_config, refused, rss, rss_item, unique_title

THUMB = '<media:thumbnail url="https://cdn.example.com/{n}.jpg"/>'


def no_sleep(_seconds):
    return None


def run(config, state_path, dry_run=False, out=None, now=NOW, force_digest=False):
    session = make_session()
    client = None if dry_run else TelegramClient(TOKEN, session, timeout=5, sleep=no_sleep)
    return run_once(
        config=config,
        session=session,
        client=client,
        chat_id="@chan",
        state_path=state_path,
        now=now,
        dry_run=dry_run,
        force_digest=force_digest,
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
            responses.post(f"{API}/{method}", body=refused())
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


@responses.activate
def test_record_keeps_link_score_and_site_count(tmp_path):
    config = make_config(topics=("AI", "Games"), max_posts_per_run=4)
    register_feeds(config)
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 1}})
    state_path = tmp_path / "posted.json"
    run(config, state_path)
    record = load_state(state_path).records["ai.example.com/1"]
    assert (record.url, record.sources, record.status) == ("https://ai.example.com/1", 1, "posted")


@responses.activate
def test_same_story_from_two_sites_is_posted_once_and_both_remembered(tmp_path):
    config = make_config(topics=("AI", "Games"))
    words = "Nintendo announces Switch 3 with launch date and price"
    responses.get(
        config.topics[0].feeds[0].url,
        body=rss(rss_item(words, "https://ai.example.com/s3", extra=THUMB.format(n=3))),
    )
    responses.get(
        config.topics[1].feeds[0].url, body=rss(rss_item(words + " leaked", "https://games.example.com/s3"))
    )
    responses.post(f"{API}/sendPhoto", json={"ok": True, "result": {"message_id": 1}})
    state_path = tmp_path / "posted.json"

    result = run(config, state_path)

    assert result.posted == 1
    statuses = {k: r.status for k, r in load_state(state_path).records.items()}
    assert sorted(statuses.values()) == ["duplicate", "posted"]
    assert set(statuses) == {"ai.example.com/s3", "games.example.com/s3"}


DIGEST = DigestSettings(
    enabled=True, local_hour=21, utc_offset_hours=3, size=5, min_stories=1, title="Top today"
)
EVENING = datetime(2026, 10, 31, 18, 30, tzinfo=timezone.utc)  # 21:30 in UTC+3


def save_posted(state_path, *titles):
    state = BotState.empty()
    for n, title in enumerate(titles):
        record = PostedRecord(
            at=EVENING - timedelta(hours=1),
            title=title,
            topic="AI",
            status="posted",
            url=f"https://e.com/{n}",
            score=n,
        )
        state = state.with_record(f"e.com/{n}", record)
    save_state(state_path, state)


def register_empty_feeds(config):
    for topic in config.topics:
        responses.get(topic.feeds[0].url, body=rss())


@responses.activate
def test_digest_is_sent_once_in_the_evening(tmp_path):
    config = make_config(digest=DIGEST)
    register_empty_feeds(config)
    responses.post(f"{API}/sendMessage", json={"ok": True, "result": {"message_id": 9}})
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "First story", "Second story")

    result = run(config, state_path, now=EVENING)

    assert result.digest_sent
    [call] = telegram_calls()
    body = json.loads(call.request.body)
    assert "Top today" in body["text"] and body["text"].index("Second story") < body["text"].index(
        "First story"
    )
    assert body["link_preview_options"]["url"] == "https://e.com/1"
    assert load_state(state_path).digest_day == "2026-10-31"

    again = run(config, state_path, now=EVENING + timedelta(minutes=30))
    assert not again.digest_sent
    assert len(telegram_calls()) == 1


@responses.activate
def test_no_digest_before_the_hour(tmp_path):
    config = make_config(digest=DIGEST)
    register_empty_feeds(config)
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "First story")
    result = run(config, state_path, now=EVENING - timedelta(hours=4))
    assert not result.digest_sent
    assert telegram_calls() == []


@responses.activate
def test_too_few_stories_skips_the_digest_for_the_day(tmp_path):
    config = make_config(
        digest=DigestSettings(enabled=True, local_hour=21, utc_offset_hours=3, min_stories=3)
    )
    register_empty_feeds(config)
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "Only one story")
    result = run(config, state_path, now=EVENING)
    assert not result.digest_sent
    assert telegram_calls() == []
    assert load_state(state_path).digest_day == "2026-10-31"


@responses.activate
def test_dry_run_previews_a_forced_digest_and_changes_nothing(tmp_path):
    config = make_config()
    register_empty_feeds(config)
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "First story")
    before = state_path.read_text(encoding="utf-8")
    out = []

    run(config, state_path, dry_run=True, out=out, now=EVENING, force_digest=True)

    assert "First story" in "\n".join(out)
    assert telegram_calls() == []
    assert state_path.read_text(encoding="utf-8") == before


@responses.activate
def test_digest_that_fails_to_send_is_tried_again_next_run(tmp_path):
    config = make_config(digest=DIGEST)
    register_empty_feeds(config)
    responses.post(f"{API}/sendMessage", body=refused())
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "First story")

    result = run(config, state_path, now=EVENING)

    assert not result.digest_sent and result.fatal_error is None
    assert load_state(state_path).digest_day == ""


@responses.activate
def test_digest_setup_error_is_reported(tmp_path):
    config = make_config(digest=DIGEST)
    register_empty_feeds(config)
    responses.post(
        f"{API}/sendMessage",
        json={
            "ok": False,
            "error_code": 403,
            "description": "Forbidden: bot was kicked from the channel chat",
        },
        status=403,
    )
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "First story")

    result = run(config, state_path, now=EVENING)

    assert "kicked" in result.fatal_error
    assert load_state(state_path).digest_day == ""


@responses.activate
def test_digest_with_an_unclear_answer_is_not_sent_twice(tmp_path):
    config = make_config(digest=DIGEST)
    register_empty_feeds(config)
    responses.post(f"{API}/sendMessage", body="Bad Gateway", status=502)
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "First story")

    first = run(config, state_path, now=EVENING)
    second = run(config, state_path, now=EVENING + timedelta(minutes=30))

    assert first.digest_sent and not second.digest_sent
    assert len(telegram_calls()) == 1
    assert load_state(state_path).digest_day == "2026-10-31"


@responses.activate
def test_digest_rejected_by_telegram_is_dropped_for_the_day(tmp_path):
    config = make_config(digest=DIGEST)
    register_empty_feeds(config)
    rejected = {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
    responses.post(f"{API}/sendMessage", json=rejected, status=400)
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "First story")

    result = run(config, state_path, now=EVENING)

    assert not result.digest_sent and result.fatal_error is None
    assert load_state(state_path).digest_day == "2026-10-31"


@responses.activate
def test_forced_digest_with_nothing_to_list_keeps_the_evening_digest(tmp_path):
    config = make_config(digest=DIGEST)
    register_empty_feeds(config)
    state_path = tmp_path / "posted.json"
    save_posted(state_path)  # nothing posted yet

    result = run(config, state_path, now=EVENING - timedelta(hours=6), force_digest=True)

    assert not result.digest_sent
    assert telegram_calls() == []
    assert load_state(state_path).digest_day == ""


@responses.activate
def test_dry_run_digest_matches_the_real_run(tmp_path):
    """The preview lists earlier posts only, exactly like a real run would."""
    config = make_config(topics=("AI", "Games"), max_posts_per_run=4)
    register_feeds(config)
    state_path = tmp_path / "posted.json"
    save_posted(state_path, "Posted earlier today")
    out = []

    run(config, state_path, dry_run=True, out=out, now=EVENING, force_digest=True)

    digest = next(text for text in out if text.startswith("----- DIGEST"))
    assert "Posted earlier today" in digest
    assert unique_title("aione") not in digest
