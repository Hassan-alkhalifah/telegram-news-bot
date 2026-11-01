"""Decide what to post this run: real news, not posted before, strong enough, strongest first."""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from newsbot.models import Article, Config, Story
from newsbot.quality import StoryMatcher, blocked_reason, filler_reason, group_stories, story_score
from newsbot.state import BotState

log = logging.getLogger(__name__)

_NEVER = datetime.min.replace(tzinfo=timezone.utc)
# Posts older than max_age_hours plus this are not compared for repeats, so a follow-up a day or two
# later (a trailer after its announcement) is not mistaken for the same news.
_REPEAT_EXTRA_HOURS = 24


@dataclass(frozen=True)
class Selection:
    to_post: tuple[Story, ...]
    duplicates: tuple[Article, ...]  # same news as something already posted; remember, never post
    waiting: int = 0  # strong stories left for a later run (max_posts_per_run or the topic wait)
    weak: int = 0  # below min_score for now; they can grow stronger as more sites report them
    filler: int = 0  # lists, guides, deals, reviews and columns that were skipped
    blocked: int = 0  # 18+ and other unwanted subjects that were skipped


def select_stories(articles: Iterable[Article], state: BotState, config: Config, now: datetime) -> Selection:
    settings = config.settings
    articles = list(articles)
    cutoff = now - timedelta(hours=settings.max_age_hours)
    fresh = _unique_by_key(a for a in articles if a.published >= cutoff and not state.has(a.key))
    allowed = [a for a in fresh if not _is_blocked(a, config)]
    news = [a for a in allowed if not _is_filler(a, config)]

    known = _known_versions(
        state, articles, since=now - timedelta(hours=settings.max_age_hours + _REPEAT_EXTRA_HOURS)
    )
    matcher = StoryMatcher(news, settings.same_story_similarity, extra_titles=[f"{t} {s}" for t, s in known])
    duplicates = [a for a in news if any(matcher.related(a.title, t, a.summary, s) for t, s in known)]
    duplicate_keys = {a.key for a in duplicates}
    remaining = [a for a in news if a.key not in duplicate_keys]

    stories = group_stories(remaining, matcher, [t.name for t in config.topics])
    scored = [replace(s, score=story_score(s, config)) for s in stories]
    strong = [s for s in scored if s.score >= _min_score(s, config)]
    distinct = _drop_related(sorted(strong, key=_strength, reverse=True), matcher)
    picked = _take_turns(distinct, config, state, settings.max_posts_per_run, now)
    return Selection(
        to_post=tuple(picked),
        duplicates=tuple(duplicates),
        waiting=len(distinct) - len(picked),
        weak=len(scored) - len(strong),
        filler=len(allowed) - len(news),
        blocked=len(fresh) - len(allowed),
    )


def _one_per_rested_topic(
    queues: dict[str, list[Story]],
    order: list[str],
    config: Config,
    state: BotState,
    limit: int,
    now: datetime,
) -> list[Story]:
    """One story per topic, from topics that waited topic_gap_minutes since their last post.

    TOP STORY news (reported by top_story_min_sources sites) does not wait.
    """
    rested_since = now - timedelta(minutes=config.settings.topic_gap_minutes)
    big = config.settings.top_story_min_sources
    picked: list[Story] = []
    for name in order:
        if len(picked) == limit:
            break
        rested = state.topic_last_posted.get(name, _NEVER) <= rested_since
        story = next((s for s in queues[name] if rested or s.sources >= big), None)
        if story is not None:
            picked.append(story)
    return picked


def _known_versions(state: BotState, articles: list[Article], since: datetime) -> list[tuple[str, str]]:
    """(title, summary) of every posted version. A record saved without a summary borrows it from
    the same article while that article is still in its feed."""
    summaries = {a.key: a.summary for a in articles}
    return [
        (r.title, r.summary or summaries.get(key, "")) for key, r in state.known_stories() if r.at >= since
    ]


def _unique_by_key(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    unique = []
    for article in articles:
        if article.key not in seen:
            seen.add(article.key)
            unique.append(article)
    return unique


def _is_blocked(article: Article, config: Config) -> bool:
    reason = blocked_reason(article, config.filters)
    if reason:
        log.info("Blocked (%s): %s", reason, article.title)
    return reason is not None


def _is_filler(article: Article, config: Config) -> bool:
    reason = filler_reason(article, config.filters)
    if reason:
        log.debug("Filler skipped (%s): %s", reason, article.title)
    return reason is not None


def _min_score(story: Story, config: Config) -> int:
    topic = config.topic(story.lead.topic)
    if topic is not None and topic.min_score is not None:
        return topic.min_score
    return config.settings.min_score


def _strength(story: Story) -> tuple[int, datetime]:
    return story.score, story.lead.published


def _drop_related(stories: Sequence[Story], matcher: StoryMatcher) -> list[Story]:
    """Strongest first; a story that looks like one already kept is left out of this run.

    Every site's wording is compared, because two versions can match while their leads do not.
    """
    kept: list[Story] = []
    kept_versions: list[Article] = []
    for story in stories:
        versions = [story.lead, *story.others]
        if not any(
            matcher.related(v.title, k.title, v.summary, k.summary) for v in versions for k in kept_versions
        ):
            kept.append(story)
            kept_versions.extend(versions)
    return kept


def _take_turns(
    stories: Sequence[Story], config: Config, state: BotState, limit: int, now: datetime
) -> list[Story]:
    """The topic with the strongest story goes first, then topics take turns.

    Ties go to the topic that waited longest since its last post, then to config order.
    With topic_gap_minutes set, each topic posts at most once per run and only after its wait.
    """
    names = [t.name for t in config.topics]
    queues = {name: [s for s in stories if s.lead.topic == name] for name in names}
    order = sorted(
        (name for name in names if queues[name]),
        key=lambda n: (-queues[n][0].score, state.topic_last_posted.get(n, _NEVER), names.index(n)),
    )
    if config.settings.topic_gap_minutes:
        return _one_per_rested_topic(queues, order, config, state, limit, now)
    picked: list[Story] = []
    round_index = 0
    while len(picked) < limit:
        this_round = [queues[n][round_index] for n in order if round_index < len(queues[n])]
        if not this_round:
            break
        picked.extend(this_round[: limit - len(picked)])
        round_index += 1
    return picked
