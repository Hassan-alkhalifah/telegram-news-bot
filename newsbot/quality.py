"""Judge news: skip filler, spot the same story on several sites, and score how big a story is."""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import replace
from functools import lru_cache
from itertools import combinations
from urllib.parse import urlsplit

from newsbot.models import Article, Config, Filters, Story
from newsbot.text import split_sentences

POINTS_PER_EXTRA_SITE = 2
# With 3+ shared words, slightly less overlap still means the same story.
_MANY_WORDS_ALLOWANCE = 0.08
# "Related" is looser than "same story". It is used only so the channel never posts the same news twice.
_RELATED_MIN_OVERLAP = 0.35

_STOPWORDS = frozenset(
    "a an the and or but of for to in on at by with from as is are was were be been it its this that these "
    "those you your we our they their he she his her will can now new how why what who after before into over "
    "about more than just not no yes has have had get gets got out up down off all any some one two first last "
    "next says said year years day days week here there".split()
)
_ROMAN = {
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16",
}  # fmt: skip
_WORD_RE = re.compile(r"[a-z0-9]+")
_APOSTROPHES = str.maketrans("", "", "'’")
_SUFFIXES = ("ing", "ed", "es", "s")


def filler_reason(article: Article, filters: Filters) -> str | None:
    """Why this article is filler (a list, guide, deal, review, column), or None for real news."""
    for tag in article.tags:
        if tag.casefold() in filters.skip_tags:
            return f"tag '{tag}'"
    path = urlsplit(article.url).path.casefold()
    for part in filters.skip_url_parts:
        if part in path:
            return f"link contains '{part}'"
    for pattern in filters.skip_titles:
        if pattern.search(article.title):
            return f"title matches '{pattern.pattern}'"
    return None


@lru_cache(maxsize=32768)
def story_words(text: str) -> frozenset[str]:
    """The meaningful words of a title: no small words, no plurals or tenses, XIV read as 14."""
    words = set()
    for raw in _WORD_RE.findall(text.casefold().translate(_APOSTROPHES)):
        word = _ROMAN.get(raw, raw)
        if word in _STOPWORDS or (len(word) < 2 and not word.isdigit()):
            continue
        words.add(_stem(word))
    return frozenset(words)


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def first_sentence(text: str) -> str:
    sentences = split_sentences(text)
    return sentences[0] if sentences else ""


def _with_first_sentence(title: str, summary: str) -> str:
    return f"{title} {first_sentence(summary)}"


def _full_text(article: Article) -> str:
    return _with_first_sentence(article.title, article.summary)


class StoryMatcher:
    """Decides whether two articles report the same news.

    Shared words are weighted by how rare they are among today's stories, so a shared game or
    company name counts far more than a shared "game" or "launch". That lets differently worded
    titles from two sites match, while two stories that only share a common word do not.
    """

    def __init__(self, articles: Iterable[Article], threshold: float, extra_titles: Iterable[str] = ()):
        texts = [_full_text(a) for a in articles] + list(extra_titles)
        counts = Counter(word for text in texts for word in story_words(text))
        docs = len(texts)
        self._idf = {word: math.log((docs + 1) / n) for word, n in counts.items()}
        self._unseen = math.log(docs + 2)  # a word no story used is as rare as it gets
        self._threshold = threshold

    def same_story(self, a: Article, b: Article) -> bool:
        """Strict: safe to count as one more site reporting the story."""
        shared, ratio = self._overlap(story_words(a.title), story_words(b.title))
        if (shared >= 2 and ratio >= self._threshold) or (
            shared >= 3 and ratio >= self._threshold - _MANY_WORDS_ALLOWANCE
        ):
            return True
        shared, ratio = self._overlap(story_words(_full_text(a)), story_words(_full_text(b)))
        return shared >= 3 and ratio >= self._threshold

    def related(self, title_a: str, title_b: str, summary_a: str = "", summary_b: str = "") -> bool:
        """Loose: probably the same news, so never post both.

        When both summaries are known, their first sentences are compared too, which catches
        titles that share few words ("OpenAI lowers funding target" / "OpenAI raises just $11 billion").
        """
        shared, ratio = self._overlap(story_words(title_a), story_words(title_b))
        if (shared >= 2 and ratio >= self._threshold) or (shared >= 3 and ratio >= _RELATED_MIN_OVERLAP):
            return True
        if not (summary_a and summary_b):
            return False
        shared, ratio = self._overlap(
            story_words(_with_first_sentence(title_a, summary_a)),
            story_words(_with_first_sentence(title_b, summary_b)),
        )
        return shared >= 3 and ratio >= _RELATED_MIN_OVERLAP

    def closeness(self, a: Article, b: Article) -> float:
        return self._overlap(story_words(a.title), story_words(b.title))[1]

    def _weight(self, word: str) -> float:
        return self._idf.get(word, self._unseen)

    def _overlap(self, a: frozenset[str], b: frozenset[str]) -> tuple[int, float]:
        shared = a & b
        if not shared:
            return 0, 0.0
        smaller = min(sum(map(self._weight, a)), sum(map(self._weight, b)))
        return len(shared), (sum(map(self._weight, shared)) / smaller if smaller else 0.0)


def group_stories(
    articles: Sequence[Article], matcher: StoryMatcher, topic_order: Sequence[str]
) -> list[Story]:
    """Put articles about the same news (from different sites) together into one Story each."""
    parent = list(range(len(articles)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in combinations(range(len(articles)), 2):
        if articles[i].source != articles[j].source and matcher.same_story(articles[i], articles[j]):
            parent[root(i)] = root(j)
    groups: dict[int, list[Article]] = {}
    for i, article in enumerate(articles):
        groups.setdefault(root(i), []).append(article)
    stories: list[Story] = []
    for members in groups.values():
        stories.extend(_without_strays(_as_story(members, matcher, topic_order), matcher, topic_order))
    return stories


def _without_strays(story: Story, matcher: StoryMatcher, topic_order: Sequence[str]) -> list[Story]:
    """Grouping is chained (A~B and B~C puts A and C together), so a member that does not match the
    lead is split off and grouped again. Otherwise it would be remembered as a repeat and never posted.
    """
    lead = story.lead
    strays = [m for m in story.others if not matcher.related(lead.title, m.title, lead.summary, m.summary)]
    if not strays:
        return [story]
    kept = tuple(m for m in story.others if m not in strays)
    return [replace(story, others=kept), *group_stories(strays, matcher, topic_order)]


def _as_story(members: list[Article], matcher: StoryMatcher, topic_order: Sequence[str]) -> Story:
    """The story's topic is where most sites put it; its lead is the title most like all the others."""
    counts = Counter(a.topic for a in members)
    rank = {name: i for i, name in enumerate(topic_order)}
    topic = min(counts, key=lambda t: (-counts[t], rank.get(t, len(rank))))
    candidates = [a for a in members if a.topic == topic]
    lead = max(candidates, key=lambda a: sum(matcher.closeness(a, b) for b in members if b is not a))
    return Story(lead=lead, others=tuple(a for a in members if a is not lead))


def story_score(story: Story, config: Config) -> int:
    """Points: 2 per extra site reporting it, 1 for a strong word, 1 for a big name of its topic."""
    titles = [a.title for a in (story.lead, *story.others)]
    strong = any(p.search(t) for p in config.filters.strong_words for t in titles)
    topic = config.topic(story.lead.topic)
    names = topic.big_names if topic else None
    big = names is not None and any(names.search(t) for t in titles)
    return POINTS_PER_EXTRA_SITE * (story.sources - 1) + int(strong) + int(big)
