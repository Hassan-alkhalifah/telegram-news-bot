"""Decide which stories to post this run: fresh, not posted before, not a repeat, topics taking turns."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from newsbot.models import Article, Config
from newsbot.state import BotState
from newsbot.text import title_similarity

_NEVER = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Selection:
    to_post: tuple[Article, ...]
    duplicates: tuple[Article, ...]  # same story as something already posted; remember, never post
    waiting: int = 0  # new stories left for a later run because of max_posts_per_run


def select_articles(articles: Iterable[Article], state: BotState, config: Config, now: datetime) -> Selection:
    settings = config.settings
    cutoff = now - timedelta(hours=settings.max_age_hours)
    fresh = _unique_by_key(a for a in articles if a.published >= cutoff and not state.has(a.key))
    newest_first = sorted(fresh, key=lambda a: a.published, reverse=True)

    posted_titles = state.recent_titles()
    threshold = settings.duplicate_title_similarity
    accepted: list[Article] = []
    duplicates: list[Article] = []
    for article in newest_first:
        if _matches_any(article.title, posted_titles, threshold):
            duplicates.append(article)
        elif not _matches_any(article.title, [a.title for a in accepted], threshold):
            accepted.append(article)

    order = _topic_order(config, state)
    picked = _take_turns(accepted, order, settings.max_posts_per_run)
    return Selection(to_post=tuple(picked), duplicates=tuple(duplicates), waiting=len(accepted) - len(picked))


def _unique_by_key(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    unique = []
    for article in articles:
        if article.key not in seen:
            seen.add(article.key)
            unique.append(article)
    return unique


def _matches_any(title: str, others: Sequence[str], threshold: float) -> bool:
    return any(title_similarity(title, other) >= threshold for other in others)


def _topic_order(config: Config, state: BotState) -> list[str]:
    """Topics that waited longest since their last post go first; ties keep config order."""
    names = [t.name for t in config.topics]
    return sorted(names, key=lambda n: (state.topic_last_posted.get(n, _NEVER), names.index(n)))


def _take_turns(articles: Sequence[Article], order: Sequence[str], limit: int) -> list[Article]:
    queues = {name: [a for a in articles if a.topic == name] for name in order}
    picked: list[Article] = []
    round_index = 0
    while len(picked) < limit:
        this_round = [q[round_index] for q in queues.values() if round_index < len(q)]
        if not this_round:
            break
        picked.extend(this_round[: limit - len(picked)])
        round_index += 1
    return picked
