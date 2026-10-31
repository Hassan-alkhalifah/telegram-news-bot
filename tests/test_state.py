from datetime import timedelta

import pytest

from newsbot.state import BotState, PostedRecord, StateCorruptError, load_state, save_state, set_aside
from tests.helpers import NOW


def record(title="Title", topic="AI", status="posted", at=NOW):
    return PostedRecord(at=at, title=title, topic=topic, status=status)


def test_missing_file_gives_empty_state(tmp_path):
    state = load_state(tmp_path / "data" / "posted.json")
    assert not state.has("anything")
    assert state.recent_titles() == ()


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "data" / "posted.json"
    state = (
        BotState.empty()
        .with_record("k1", record("Title one"))
        .with_record("k2", record("Dup", status="duplicate"))
    )
    save_state(path, state)
    loaded = load_state(path)
    assert loaded.has("k1") and loaded.has("k2")
    assert loaded.recent_titles() == ("Title one",)
    assert loaded.topic_last_posted == {"AI": NOW}
    assert dict(loaded.records) == dict(state.records)


def test_with_record_leaves_original_unchanged():
    original = BotState.empty()
    updated = original.with_record("k1", record())
    assert not original.has("k1")
    assert updated.has("k1")


def test_duplicate_records_do_not_count_as_posts():
    state = BotState.empty().with_record("k", record("Dup", status="duplicate"))
    assert state.topic_last_posted == {}
    assert state.recent_titles() == ()


def test_pruned_drops_old_records():
    state = (
        BotState.empty()
        .with_record("old", record("Old", at=NOW - timedelta(days=8)))
        .with_record("new", record("New", at=NOW - timedelta(days=1)))
    )
    pruned = state.pruned(NOW, retention_days=7)
    assert not pruned.has("old")
    assert pruned.has("new")
    assert state.has("old")


@pytest.mark.parametrize("content", ["{not json", '{"posted": {"k": {"title": "missing fields"}}}', "[1, 2]"])
def test_unreadable_file_raises_and_stays_in_place(tmp_path, content):
    path = tmp_path / "posted.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(StateCorruptError):
        load_state(path)
    assert path.read_text(encoding="utf-8") == content


def test_set_aside_moves_the_file(tmp_path):
    path = tmp_path / "posted.json"
    path.write_text("{not json", encoding="utf-8")
    backup = set_aside(path)
    assert backup == tmp_path / "posted.json.corrupt"
    assert backup.read_text(encoding="utf-8") == "{not json"
    assert not path.exists()


@pytest.mark.parametrize("status", ["seeded", "failed"])
def test_other_statuses_do_not_count_as_posts(status):
    state = BotState.empty().with_record("k", record("Not posted", status=status))
    assert state.has("k")
    assert state.topic_last_posted == {}
    assert state.recent_titles() == ()


def test_file_is_readable_utf8(tmp_path):
    path = tmp_path / "posted.json"
    save_state(path, BotState.empty().with_record("k", record("Kimetsu no Yaiba 鬼滅")))
    assert "鬼滅" in path.read_text(encoding="utf-8")
