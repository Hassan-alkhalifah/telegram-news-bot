"""Filler filter, same-story matching and scoring, checked against 211 real stories."""

import re

import pytest

from newsbot.config import load_config
from newsbot.models import Filters
from newsbot.quality import StoryMatcher, filler_reason, group_stories, story_score, story_words
from tests.helpers import ROOT, live_article, live_articles, make_article, make_config, make_story

REPO = load_config(ROOT / "config.yaml")
TOPIC_ORDER = [t.name for t in REPO.topics]


@pytest.fixture(scope="module")
def real_news():
    return [a for a in live_articles() if filler_reason(a, REPO.filters) is None]


@pytest.fixture(scope="module")
def matcher(real_news):
    return StoryMatcher(real_news, threshold=REPO.settings.same_story_similarity)


class TestFiller:
    @pytest.mark.parametrize(
        "title_start",
        [
            "10 Best Anime Characters to Take Trick-Or-Treating",
            "15 Excellent Horror Games",
            "4 drawbacks you should know",
            "How to fix your phone screen",
            "What is a PDF reader",
            "Is IMAX 3D a better experience",
            "GNSS Vs GPS",
            "Oppo Find X10 review",
            "Castlevania: Belmont",
            "Deals: iPhone 17 series",
            "Best Buy is bundling",
            "Talking Point: What Are You Playing This Weekend? (31st",
            "Poll: Are You Happy",
            "I built an AI Tamagotchi",
            "9to5Mac Daily:",
            "Grab a new 3D printer",
            "How to Build Mitya",
        ],
    )
    def test_real_filler_is_skipped(self, title_start):
        assert filler_reason(live_article(title_start), REPO.filters)

    @pytest.mark.parametrize(
        "title_start",
        [
            "Final Fantasy XIV Evercold Delayed",
            "Nintendo Reveals Surprise Pikmin Day Direct",
            "Activision to remove Itaewon",
            "Anthropic VS code extension",  # "VS Code" is a product, not "versus"
            "Salesforce’s M&A spree",  # a company deal, not a shopping deal
            "Sam Altman says it’s better for RAM",
            "Romelia War Chronicle Anime's 2nd Part",
            "Apple Intelligence is finally coming to China",
            "Hiragi-san's House of Vampires Gets Anime",
        ],
    )
    def test_real_news_is_kept(self, title_start):
        assert filler_reason(live_article(title_start), REPO.filters) is None

    def test_reason_says_which_rule(self):
        filters = Filters(skip_tags=frozenset({"reviews"}), skip_url_parts=("/guides/",))
        assert "Reviews" in filler_reason(make_article(tags=("Reviews",)), filters)
        assert "/guides/" in filler_reason(make_article(url="https://site.com/guides/x"), filters)
        titled = Filters(skip_titles=(re.compile(r"^poll:", re.I),))
        assert "poll" in filler_reason(make_article(title="Poll: which game?"), titled)

    def test_most_real_stories_survive(self, real_news):
        assert 120 <= len(real_news) <= 180  # 211 stories, about a quarter is filler


class TestStoryWords:
    def test_roman_numerals_match_numbers(self):
        assert story_words("Final Fantasy XIV") == story_words("Final Fantasy 14")

    def test_apostrophes_plurals_and_tenses_are_ignored(self):
        assert story_words("Nintendo’s games delayed") == story_words("Nintendos game delays")

    def test_small_words_are_dropped(self):
        assert story_words("The game is out now for you") == {"game"}


class TestMatcher:
    @pytest.mark.parametrize(
        "first, second",
        [
            ("Final Fantasy XIV Evercold Delayed", "Final Fantasy 14: Evercold Delayed to February 2027"),
            ("Nintendo Reveals Surprise Pikmin Day Direct", "Nintendo Drops Surprise 'Pikmin Day' Direct"),
            ("Apple Intelligence is finally coming to China", "Apple confirms iOS 27.3 beta"),
            ("Romelia War Chronicle Anime's 2nd Part", "Romelia War Chronicle Cour 2"),
            ("Comisma, Sumitomo Mitsui", "Sumitomo Mitsui Trust Bank, Comisma"),
        ],
    )
    def test_same_story_on_two_sites(self, matcher, first, second):
        assert matcher.same_story(live_article(first), live_article(second))

    @pytest.mark.parametrize(
        "first, second",
        [
            ("Activision to remove Itaewon", "Digital Foundry Delivers Its Tech Verdict"),
            ("Hideo Kojima Makes Surprise Cameo", "Nintendo Reveals Surprise Pikmin Day Direct"),
            ("New games in 2027", "Final Fantasy 14: Evercold Delayed to February 2027"),
            ("Supersonic airliner in doubt", "Intel shifts production capacity"),
            ("Meta’s Muse Could Upend", "Microsoft offers a quarter of its workforce"),
        ],
    )
    def test_different_stories_are_not_merged(self, matcher, first, second):
        assert not matcher.same_story(live_article(first), live_article(second))

    @pytest.mark.parametrize(
        "first, second",
        [
            ("Sam Altman says it’s better for RAM", "Sam Altman is upset at you for hoarding RAM"),
            ("Silver Palace Arrives on PS5", "PS5 Adds Another Free Open World Game"),
            ("Anthropic updates Claude to 'steer it away'", "Anthropic Nerfs Claude Again"),
        ],
    )
    def test_reworded_story_counts_as_related(self, matcher, first, second):
        assert matcher.related(live_article(first).title, live_article(second).title)

    @pytest.mark.parametrize(
        "first, second",
        [
            # Titles share only "OpenAI" and "target"; the first summary sentences match.
            ("OpenAI reportedly lowers funding target", "OpenAI raises just $11 billion"),
            # The same anime under its English and its Japanese title.
            ("Inherit the Winds Anime Reveals Four More Cast", "Supporting Cast for 'Kaze wo Tsugumono'"),
        ],
    )
    def test_summaries_catch_stories_with_different_titles(self, matcher, first, second):
        a, b = live_article(first), live_article(second)
        assert not matcher.related(a.title, b.title)
        assert matcher.related(a.title, b.title, a.summary, b.summary)

    def test_unrelated_titles(self, matcher):
        a, b = live_article("Activision to remove Itaewon"), live_article("Bolt Graphics powers on")
        assert not matcher.related(a.title, b.title, a.summary, b.summary)


class TestGrouping:
    def test_real_top_stories_are_grouped(self, real_news, matcher):
        stories = group_stories(real_news, matcher, TOPIC_ORDER)
        by_lead = {s.lead.title: s for s in stories}
        ff = next(s for s in stories if any("Evercold" in a.title for a in (s.lead, *s.others)))
        assert ff.sources >= 6
        assert ff.lead.topic == "Games"
        pikmin = next(s for t, s in by_lead.items() if "Pikmin Day" in t)
        assert pikmin.sources >= 3
        assert len(stories) < len(real_news)

    def test_lead_is_the_most_typical_title(self, real_news, matcher):
        stories = group_stories(real_news, matcher, TOPIC_ORDER)
        isshiki = next(s for s in stories if any("Isshiki-san" in a.title for a in (s.lead, *s.others)))
        assert not isshiki.lead.title.startswith("Cosplayer")

    def test_topic_is_where_most_sites_put_it(self):
        config = make_config(topics=("AI", "Games"))
        words = "Nintendo Switch 3 revealed with launch date"
        stories = group_stories(
            [
                make_article(url="https://a.com/1", title=words, topic="AI", source="A"),
                make_article(url="https://b.com/1", title=words, topic="Games", hashtag="Games", source="B"),
                make_article(url="https://c.com/1", title=words, topic="Games", hashtag="Games", source="C"),
            ],
            StoryMatcher([], threshold=config.settings.same_story_similarity),
            ["AI", "Games"],
        )
        assert len(stories) == 1
        assert stories[0].lead.topic == "Games"
        assert stories[0].sources == 3

    def test_one_site_never_counts_twice(self):
        words = "Nintendo Switch 3 revealed with launch date"
        stories = group_stories(
            [
                make_article(url="https://a.com/1", title=words, source="Same"),
                make_article(url="https://a.com/2", title=words, source="Same"),
            ],
            StoryMatcher([], threshold=0.5),
            ["AI"],
        )
        assert sorted(s.sources for s in stories) == [1, 1]


class TestScore:
    def test_points(self):
        config = make_config(
            topic_extra={"AI": {"big_names": re.compile(r"\b(OpenAI)\b", re.I)}},
            filters=Filters(strong_words=(re.compile(r"\blaunch\w*", re.I),)),
        )
        plain = make_article(title="Something happened somewhere today")
        assert story_score(make_story(plain), config) == 0
        assert story_score(make_story(make_article(title="OpenAI launches a browser")), config) == 2
        second_site = make_article(url="https://b.com/1", source="Other")
        assert story_score(make_story(plain, second_site), config) == 2
        third = make_article(url="https://c.com/1", source="Third")
        assert story_score(make_story(plain, second_site, third), config) == 4

    def test_real_stories(self, real_news, matcher):
        stories = group_stories(real_news, matcher, TOPIC_ORDER)
        scores = {s.lead.title: story_score(s, REPO) for s in stories}
        ff = next(score for title, score in scores.items() if "Evercold" in title)
        assert ff >= 10
        assert scores[live_article("Bolt Graphics powers on").title] == 0


@pytest.mark.parametrize("copies", [3, 5])
def test_a_bridging_headline_does_not_hide_an_unrelated_story(copies):
    """A ~ B and B ~ C does not make A ~ C. Every grouped article must match its story's lead,
    so the Okami story is either named by the lead (the bridge) or stays a story of its own."""
    from tests.helpers import unique_title

    lead_words = "Capcom reveals Monster Hunter Wilds expansion release date"
    group = [make_article(url=f"https://a{n}.com/1", source=f"A{n}", title=lead_words) for n in range(copies)]
    bridge = make_article(
        url="https://b.com/1",
        source="B",
        title="Capcom reveals Monster Hunter Wilds expansion and Okami sequel at showcase",
    )
    okami = make_article(
        url="https://c.com/1", source="C", title="Okami sequel at Capcom showcase gets first trailer"
    )
    background = [
        make_article(url=f"https://z.com/{n}", source="Z", title=unique_title(f"bg{n}x")) for n in range(30)
    ]
    matcher = StoryMatcher([*group, bridge, okami, *background], threshold=0.5)

    stories = group_stories([*group, bridge, okami], matcher, ["AI"])

    for story in stories:
        for other in story.others:
            assert matcher.related(story.lead.title, other.title), (story.lead.title, other.title)
    if copies == 5:  # the plain Monster Hunter title leads, so Okami must be split off
        assert any(s.lead == okami for s in stories)
