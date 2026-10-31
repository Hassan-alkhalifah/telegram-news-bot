import re
from datetime import timedelta

import pytest

from newsbot.models import Filters
from newsbot.selector import select_stories
from newsbot.state import BotState, PostedRecord
from tests.helpers import NOW, make_article, make_config, unique_title

SAME_WORDS = "Nintendo announces Switch 3 with launch date and price"


def posted(state, key, title, topic="AI", at=NOW):
    return state.with_record(key, PostedRecord(at=at, title=title, topic=topic, status="posted"))


def leads(selection):
    return tuple(s.lead for s in selection.to_post)


def test_old_and_already_posted_articles_are_skipped():
    old = make_article(
        url="https://e.com/old", title=unique_title("old"), published=NOW - timedelta(hours=13)
    )
    seen = make_article(url="https://e.com/seen", title=unique_title("seen"))
    new = make_article(url="https://e.com/new", title=unique_title("new"))
    state = posted(BotState.empty(), seen.key, unique_title("other"))
    selection = select_stories([old, seen, new], state, make_config(max_age_hours=12), NOW)
    assert leads(selection) == (new,)
    assert selection.duplicates == ()


def test_same_link_from_two_feeds_is_one_story():
    first = make_article(url="https://e.com/x", source="Verge")
    second = make_article(url="https://e.com/x?utm_source=feed", source="Ars")
    selection = select_stories([first, second], BotState.empty(), make_config(), NOW)
    assert leads(selection) == (first,)
    assert selection.to_post[0].others == ()


def test_same_story_on_two_sites_is_posted_once_with_both_counted():
    a = make_article(url="https://a.com/1", title=SAME_WORDS, source="IGN")
    b = make_article(url="https://b.com/1", title=SAME_WORDS + " leaked", source="Kotaku")
    selection = select_stories([a, b], BotState.empty(), make_config(), NOW)
    assert len(selection.to_post) == 1
    story = selection.to_post[0]
    assert story.sources == 2
    assert {story.lead.key, *(o.key for o in story.others)} == {a.key, b.key}


def test_story_already_posted_is_a_duplicate():
    state = posted(BotState.empty(), "other.com/1", "Nintendo announces new Zelda game for Switch 2")
    article = make_article(url="https://e.com/z", title="New Zelda game announced by Nintendo for Switch 2")
    selection = select_stories([article], state, make_config(), NOW)
    assert selection.to_post == ()
    assert selection.duplicates == (article,)


def test_filler_is_never_posted():
    filters = Filters(skip_titles=(re.compile(r"^\d+ best\b", re.I),))
    filler = make_article(url="https://e.com/list", title="10 best anime characters for Halloween")
    news = make_article(url="https://e.com/news", title=unique_title("news"))
    selection = select_stories([filler, news], BotState.empty(), make_config(filters=filters), NOW)
    assert leads(selection) == (news,)
    assert selection.filler == 1


def test_weak_stories_wait_for_more_sites():
    lonely = make_article(url="https://e.com/1", title=unique_title("lonely"))
    selection = select_stories([lonely], BotState.empty(), make_config(min_score=2), NOW)
    assert selection.to_post == ()
    assert selection.weak == 1


def test_strong_single_site_story_is_posted():
    config = make_config(
        min_score=2,
        filters=Filters(strong_words=(re.compile(r"\bannounc\w*", re.I),)),
        topic_extra={"AI": {"big_names": re.compile(r"\bOpenAI\b", re.I)}},
    )
    article = make_article(url="https://e.com/1", title="OpenAI announces its first phone")
    selection = select_stories([article], BotState.empty(), config, NOW)
    assert leads(selection) == (article,)
    assert selection.to_post[0].score == 2


def test_topic_can_lower_its_own_bar():
    config = make_config(topics=("AI", "Anime"), min_score=2, topic_extra={"Anime": {"min_score": 0}})
    ai = make_article(url="https://ai.com/1", title=unique_title("ai"))
    anime = make_article(
        url="https://anime.com/1", title=unique_title("anime"), topic="Anime", hashtag="Anime"
    )
    selection = select_stories([ai, anime], BotState.empty(), config, NOW)
    assert leads(selection) == (anime,)


def test_strongest_topic_goes_first_then_topics_take_turns():
    config = make_config(topics=("AI", "Games"), max_posts_per_run=3)
    ai = [
        make_article(
            url=f"https://ai.com/{n}", title=unique_title(f"ai{n}x"), published=NOW - timedelta(minutes=n)
        )
        for n in range(3)
    ]
    big = [
        make_article(
            url=f"https://site{n}.com/g", title=SAME_WORDS, topic="Games", hashtag="Games", source=f"S{n}"
        )
        for n in range(3)
    ]
    selection = select_stories(ai + big, BotState.empty(), config, NOW)
    assert [s.lead.topic for s in selection.to_post] == ["Games", "AI", "AI"]
    assert selection.to_post[0].sources == 3
    assert selection.waiting == 1


def test_topic_posted_longest_ago_breaks_ties():
    config = make_config(topics=("AI", "Games"), max_posts_per_run=1)
    state = posted(
        BotState.empty(), "x.com/1", unique_title("p1"), topic="AI", at=NOW - timedelta(minutes=10)
    )
    state = posted(state, "x.com/2", unique_title("p2"), topic="Games", at=NOW - timedelta(minutes=60))
    ai = make_article(url="https://ai.com/1", title=unique_title("ai"))
    games = make_article(
        url="https://games.com/1", title=unique_title("games"), topic="Games", hashtag="Games"
    )
    selection = select_stories([ai, games], state, config, NOW)
    assert leads(selection) == (games,)


def test_newest_first_when_scores_tie():
    config = make_config(topics=("AI",), max_posts_per_run=2)
    older = make_article(
        url="https://e.com/1", title=unique_title("older"), published=NOW - timedelta(hours=3)
    )
    newer = make_article(
        url="https://e.com/2", title=unique_title("newer"), published=NOW - timedelta(minutes=5)
    )
    selection = select_stories([older, newer], BotState.empty(), config, NOW)
    assert leads(selection) == (newer, older)


def test_related_stories_are_not_both_posted_in_one_run():
    # Word rarity needs a realistic day of news, so other stories are mixed in.
    config = make_config(topics=("AI",), max_posts_per_run=20, same_story_similarity=0.95)
    first = make_article(
        url="https://a.com/1", title="Sam Altman says RAM belongs in a data center", source="A"
    )
    second = make_article(
        url="https://b.com/1", title="Sam Altman upset about people hoarding RAM", source="B"
    )
    others = [
        make_article(url=f"https://o.com/{n}", title=unique_title(f"other{n}x"), source="O")
        for n in range(10)
    ]
    selection = select_stories([first, second, *others], BotState.empty(), config, NOW)
    assert sum("Sam Altman" in s.lead.title for s in selection.to_post) == 1
    assert len(selection.to_post) == 11


def test_real_day_posts_each_story_once_and_skips_filler():
    """End to end on 211 real stories: big stories come first, none twice, no filler."""
    from newsbot.config import load_config
    from newsbot.quality import filler_reason
    from tests.helpers import ROOT, live_articles

    repo = load_config(ROOT / "config.yaml")
    config = repo.__class__(
        settings=repo.settings.__class__(**{**repo.settings.__dict__, "max_posts_per_run": 50}),
        topics=repo.topics,
        filters=repo.filters,
        style=repo.style,
        digest=repo.digest,
    )
    selection = select_stories(live_articles(), BotState.empty(), config, NOW)
    titles = [s.lead.title for s in selection.to_post]
    every_version = [a.title for s in selection.to_post for a in (s.lead, *s.others)]

    assert 15 <= len(titles) <= 40
    assert all(filler_reason(s.lead, repo.filters) is None for s in selection.to_post)
    for name in ("Evercold", "Pikmin Day", "Silver Palace", "Sam Altman", "Romelia War Chronicle"):
        assert sum(name.casefold() in t.casefold() for t in titles) <= 1, name
    assert "Evercold" in titles[0] or any("Evercold" in t for t in every_version[:9])
    assert not any(t.startswith("10 Best Anime") for t in titles)


@pytest.mark.parametrize("record_has_summary", [True, False])
def test_story_posted_earlier_in_other_words_is_a_repeat(record_has_summary):
    """TechCrunch's OpenAI funding story was posted; The Decoder's version uses different title words.

    Without a saved summary (records from the first version of the bot), the summary is taken
    from the posted article while it is still in its feed.
    """
    from newsbot.text import split_sentences
    from tests.helpers import live_article, live_articles

    posted_one = live_article("OpenAI reportedly lowers funding target")
    later = live_article("OpenAI raises just $11 billion")
    record = PostedRecord(
        at=NOW - timedelta(hours=6),
        title=posted_one.title,
        topic="AI",
        status="posted",
        summary=split_sentences(posted_one.summary)[0] if record_has_summary else "",
    )
    state = BotState.empty().with_record(posted_one.key, record)
    selection = select_stories(
        live_articles(), state, make_config(topics=("AI", "Games", "Anime", "Tech")), NOW
    )
    assert later.key in {a.key for a in selection.duplicates}


def test_only_recent_posts_count_as_repeats():
    """A story posted two days ago does not block today's follow-up with similar words."""
    title = "Nintendo announces new Zelda game for Switch 2"
    old = PostedRecord(at=NOW - timedelta(hours=40), title=title, topic="AI", status="posted")
    recent = PostedRecord(at=NOW - timedelta(hours=20), title=title, topic="AI", status="posted")
    article = make_article(url="https://e.com/z", title="New Zelda game announced by Nintendo for Switch 2")

    after_old = select_stories([article], BotState.empty().with_record("old.com/1", old), make_config(), NOW)
    after_recent = select_stories(
        [article], BotState.empty().with_record("new.com/1", recent), make_config(), NOW
    )

    assert after_old.duplicates == () and len(after_old.to_post) == 1
    assert after_recent.duplicates == (article,)


def test_blocked_stories_are_never_posted():
    filters = Filters(blocked=(re.compile(r"\bcasinos?\b", re.I),))
    bad = make_article(url="https://e.com/bad", title="New online casino launches in Europe")
    tagged = make_article(url="https://e.com/tag", title=unique_title("tagged"), tags=("Casino",))
    in_summary = make_article(
        url="https://e.com/sum", title=unique_title("sum"), summary="Visit the casino today."
    )
    good = make_article(url="https://e.com/good", title=unique_title("good"))
    selection = select_stories(
        [bad, tagged, in_summary, good], BotState.empty(), make_config(filters=filters), NOW
    )
    assert leads(selection) == (good,)
    assert selection.blocked == 3
