from datetime import timedelta

from newsbot.selector import select_articles
from newsbot.state import BotState, PostedRecord
from tests.helpers import NOW, make_article, make_config, unique_title


def posted(state, key, title, topic="AI", at=NOW):
    return state.with_record(key, PostedRecord(at=at, title=title, topic=topic, status="posted"))


def test_old_and_already_posted_articles_are_skipped():
    config = make_config(max_age_hours=12)
    old = make_article(
        url="https://e.com/old", title=unique_title("old"), published=NOW - timedelta(hours=13)
    )
    seen = make_article(url="https://e.com/seen", title=unique_title("seen"))
    new = make_article(url="https://e.com/new", title=unique_title("new"))
    state = posted(BotState.empty(), seen.key, unique_title("other"))
    selection = select_articles([old, seen, new], state, config, NOW)
    assert selection.to_post == (new,)
    assert selection.duplicates == ()


def test_same_link_from_two_feeds_is_posted_once():
    first = make_article(url="https://e.com/x", source="Verge")
    second = make_article(url="https://e.com/x?utm_source=feed", source="Ars")
    selection = select_articles([first, second], BotState.empty(), make_config(), NOW)
    assert selection.to_post == (first,)


def test_story_matching_a_posted_title_is_a_duplicate():
    state = posted(BotState.empty(), "other.com/1", "Nintendo announces new Zelda game for Switch 2")
    article = make_article(url="https://e.com/z", title="New Zelda game announced by Nintendo for Switch 2")
    selection = select_articles([article], state, make_config(), NOW)
    assert selection.to_post == ()
    assert selection.duplicates == (article,)


def test_same_story_twice_in_one_run_keeps_the_newest():
    newer = make_article(url="https://a.com/z", title="Nintendo announces new Zelda game for Switch 2")
    older = make_article(
        url="https://b.com/z",
        title="New Zelda game announced by Nintendo for Switch 2",
        published=NOW - timedelta(hours=2),
    )
    selection = select_articles([older, newer], BotState.empty(), make_config(), NOW)
    assert selection.to_post == (newer,)
    assert selection.duplicates == ()


def test_topics_take_turns_up_to_the_cap():
    config = make_config(topics=("AI", "Games", "Anime"), max_posts_per_run=4)

    def batch(topic, count):
        return [
            make_article(
                url=f"https://{topic.lower()}.com/{n}",
                title=unique_title(f"{topic}{n}x"),
                topic=topic,
                hashtag=topic,
                published=NOW - timedelta(minutes=n + 1),
            )
            for n in range(count)
        ]

    ai, games, anime = batch("AI", 5), batch("Games", 2), batch("Anime", 1)
    selection = select_articles(ai + games + anime, BotState.empty(), config, NOW)
    assert selection.to_post == (ai[0], games[0], anime[0], ai[1])


def test_topic_posted_longest_ago_goes_first():
    config = make_config(topics=("AI", "Games"), max_posts_per_run=1)
    state = posted(
        BotState.empty(), "x.com/1", unique_title("p1"), topic="AI", at=NOW - timedelta(minutes=10)
    )
    state = posted(state, "x.com/2", unique_title("p2"), topic="Games", at=NOW - timedelta(minutes=60))
    ai = make_article(url="https://ai.com/1", title=unique_title("ai"), topic="AI")
    games = make_article(
        url="https://games.com/1", title=unique_title("games"), topic="Games", hashtag="Games"
    )
    selection = select_articles([ai, games], state, config, NOW)
    assert selection.to_post == (games,)


def test_newest_first_within_a_topic():
    config = make_config(topics=("AI",), max_posts_per_run=2)
    older = make_article(
        url="https://e.com/1", title=unique_title("older"), published=NOW - timedelta(hours=3)
    )
    newer = make_article(
        url="https://e.com/2", title=unique_title("newer"), published=NOW - timedelta(minutes=5)
    )
    selection = select_articles([older, newer], BotState.empty(), config, NOW)
    assert selection.to_post == (newer, older)


def test_stories_beyond_the_cap_are_counted_as_waiting():
    config = make_config(topics=("AI",), max_posts_per_run=1)
    newer = make_article(url="https://e.com/1", title=unique_title("w1"))
    older = make_article(url="https://e.com/2", title=unique_title("w2"), published=NOW - timedelta(hours=2))
    selection = select_articles([older, newer], BotState.empty(), config, NOW)
    assert selection.to_post == (newer,)
    assert selection.waiting == 1
