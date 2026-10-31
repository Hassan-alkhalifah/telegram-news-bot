from datetime import datetime, timedelta, timezone

from newsbot.digest import DigestEntry, build_digest, digest_day_if_due, digest_entries
from newsbot.models import DigestSettings, PostStyle
from newsbot.state import BotState, PostedRecord
from tests.helpers import make_config

ON = DigestSettings(enabled=True, local_hour=21, utc_offset_hours=3, size=2, min_stories=1, title="Top")
# 18:30 UTC is 21:30 in UTC+3
EVENING = datetime(2026, 10, 31, 18, 30, tzinfo=timezone.utc)
AFTERNOON = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)


def record(
    title,
    url="https://e.com/x",
    score=0,
    status="posted",
    topic="AI",
    at=EVENING - timedelta(hours=1),
    sources=1,
):
    return PostedRecord(at=at, title=title, topic=topic, status=status, url=url, score=score, sources=sources)


class TestWhenDue:
    def test_due_after_the_local_hour(self):
        assert digest_day_if_due(BotState.empty(), ON, EVENING) == "2026-10-31"

    def test_not_before_the_local_hour(self):
        assert digest_day_if_due(BotState.empty(), ON, AFTERNOON) is None

    def test_only_once_a_day(self):
        state = BotState.empty().with_digest_day("2026-10-31")
        assert digest_day_if_due(state, ON, EVENING) is None
        assert digest_day_if_due(state, ON, EVENING + timedelta(days=1)) == "2026-11-01"

    def test_local_day_can_differ_from_utc_day(self):
        late = datetime(2026, 10, 31, 22, 30, tzinfo=timezone.utc)  # 01:30 on Nov 1 in UTC+3
        assert digest_day_if_due(BotState.empty(), ON, late) is None

    def test_disabled(self):
        assert digest_day_if_due(BotState.empty(), DigestSettings(enabled=False), EVENING) is None

    def test_forced_ignores_hour_and_switch(self):
        off = DigestSettings(enabled=False)
        assert digest_day_if_due(BotState.empty(), off, AFTERNOON, force=True) == "2026-10-31"


def test_entries_are_the_strongest_posts_of_the_last_day():
    config = make_config(topic_extra={"AI": {"emoji": "[A]"}})
    state = (
        BotState.empty()
        .with_record("a", record("Weak", url="https://e.com/a", score=1))
        .with_record("b", record("Strong", url="https://e.com/b", score=9, sources=5))
        .with_record("c", record("Middle", url="https://e.com/c", score=4))
        .with_record("d", record("Old", url="https://e.com/d", score=20, at=EVENING - timedelta(hours=25)))
        .with_record("e", record("Repeat", url="https://e.com/e", score=30, status="duplicate"))
        .with_record("f", record("No link", url="", score=30))
    )
    entries = digest_entries(state, config, ON, EVENING)
    assert [e.title for e in entries] == ["Strong", "Middle"]
    assert entries[0] == DigestEntry(
        title="Strong", url="https://e.com/b", emoji="[A]", hashtag="AI", sources=5
    )


def test_digest_text():
    entries = [
        DigestEntry(
            title="Big <one>", url="https://e.com/1?a=1&b=2", emoji="[G]", hashtag="Games", sources=4
        ),
        DigestEntry(title="Second", url="https://e.com/2", emoji="", hashtag="AI", sources=1),
    ]
    text = build_digest(entries, "Top 5 & more", "31 Oct", PostStyle(channel_signature="@chan"))
    assert text == (
        "<b>Top 5 &amp; more</b>\n31 Oct\n\n"
        '1. [G] <a href="https://e.com/1?a=1&amp;b=2">Big &lt;one&gt;</a>\n'
        '2. <a href="https://e.com/2">Second</a>\n\n'
        "@chan"
    )


def test_digest_text_without_signature():
    entries = [DigestEntry(title="Only", url="https://e.com/1", emoji="", hashtag="AI", sources=1)]
    assert build_digest(entries, "Top", "31 Oct", PostStyle()).endswith('<a href="https://e.com/1">Only</a>')


def test_stories_posted_in_this_run_are_not_repeated_right_away():
    config = make_config()
    state = (
        BotState.empty()
        .with_record("a", record("Earlier", url="https://e.com/a", score=1))
        .with_record("b", record("Just now", url="https://e.com/b", score=9, at=EVENING))
    )
    assert [e.title for e in digest_entries(state, config, ON, EVENING)] == ["Earlier"]
