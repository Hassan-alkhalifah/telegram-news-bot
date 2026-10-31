from datetime import datetime, timedelta, timezone

from newsbot.digest import DigestEntry, build_digest, digest_day_if_due, digest_entries
from newsbot.models import DigestSettings
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
    entries = digest_entries(state, config, EVENING, size=ON.size)
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
    text = build_digest(entries, "Top 5 & more", "31 Oct", "@chan")
    assert text == (
        "<b>Top 5 &amp; more</b>\n31 Oct\n\n"
        '1. [G] <a href="https://e.com/1?a=1&amp;b=2">Big &lt;one&gt;</a>\n'
        '2. <a href="https://e.com/2">Second</a>\n\n'
        "@chan"
    )


def test_digest_text_without_signature():
    entries = [DigestEntry(title="Only", url="https://e.com/1", emoji="", hashtag="AI", sources=1)]
    assert build_digest(entries, "Top", "31 Oct", "").endswith('<a href="https://e.com/1">Only</a>')


def test_stories_posted_in_this_run_are_not_repeated_right_away():
    config = make_config()
    state = (
        BotState.empty()
        .with_record("a", record("Earlier", url="https://e.com/a", score=1))
        .with_record("b", record("Just now", url="https://e.com/b", score=9, at=EVENING))
    )
    assert [e.title for e in digest_entries(state, config, EVENING, size=ON.size)] == ["Earlier"]


from newsbot.digest import poll_options, weekly_day_if_due  # noqa: E402
from newsbot.models import WeeklySettings  # noqa: E402

FRIDAY_EVENING = datetime(2026, 10, 30, 17, 30, tzinfo=timezone.utc)  # Friday 20:30 in UTC+3
WEEKLY = WeeklySettings(enabled=True, weekday=4, local_hour=20, size=3)


class TestWeekly:
    def test_due_on_the_day_after_the_hour(self):
        assert weekly_day_if_due(BotState.empty(), WEEKLY, 3, FRIDAY_EVENING) == "2026-10-30"

    def test_not_before_the_hour_or_on_other_days(self):
        assert weekly_day_if_due(BotState.empty(), WEEKLY, 3, FRIDAY_EVENING - timedelta(hours=1)) is None
        assert weekly_day_if_due(BotState.empty(), WEEKLY, 3, FRIDAY_EVENING + timedelta(days=1)) is None

    def test_once_per_week_and_switch(self):
        assert (
            weekly_day_if_due(BotState.empty().with_weekly_day("2026-10-30"), WEEKLY, 3, FRIDAY_EVENING)
            is None
        )
        off = WeeklySettings(enabled=False)
        assert weekly_day_if_due(BotState.empty(), off, 3, FRIDAY_EVENING) is None
        assert weekly_day_if_due(BotState.empty(), off, 3, FRIDAY_EVENING - timedelta(days=2), force=True)

    def test_week_entries_cover_seven_days(self):
        config = make_config()
        state = (
            BotState.empty()
            .with_record(
                "a",
                record("Monday story", url="https://e.com/a", score=5, at=FRIDAY_EVENING - timedelta(days=4)),
            )
            .with_record(
                "b", record("Too old", url="https://e.com/b", score=9, at=FRIDAY_EVENING - timedelta(days=8))
            )
            .with_record(
                "c",
                record(
                    "Thursday story", url="https://e.com/c", score=7, at=FRIDAY_EVENING - timedelta(days=1)
                ),
            )
        )
        entries = digest_entries(state, config, FRIDAY_EVENING, lookback=timedelta(days=7), size=10)
        assert [e.title for e in entries] == ["Thursday story", "Monday story"]


def test_arabic_entries_use_the_arabic_title_and_skip_untranslated():
    config = make_config()
    with_ar = PostedRecord(
        at=EVENING - timedelta(hours=1),
        title="T",
        topic="AI",
        status="posted",
        url="https://e.com/1",
        title_ar="ع",
    )
    state = (
        BotState.empty()
        .with_record("a", with_ar)
        .with_record("b", record("Only English", url="https://e.com/2"))
    )
    entries = digest_entries(state, config, EVENING, arabic=True)
    assert [e.title for e in entries] == ["ع"]


def test_poll_options_are_short_and_unique():
    entries = [
        DigestEntry(title="A" * 150, url="u1", emoji="", hashtag="AI", sources=1),
        DigestEntry(title="Same", url="u2", emoji="", hashtag="AI", sources=1),
        DigestEntry(title="Same", url="u3", emoji="", hashtag="AI", sources=1),
    ]
    options = poll_options(entries, 5)
    assert len(options[0]) <= 100 and options[0].endswith("…")
    assert options[1:] == ["Same"]
