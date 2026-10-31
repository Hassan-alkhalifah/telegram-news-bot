"""The evening top stories and the weekly top 10 with its poll, for the English and Arabic channels."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from newsbot.digest import (
    DAY,
    WEEK,
    DigestEntry,
    build_digest,
    date_label,
    digest_day_if_due,
    digest_entries,
    poll_options,
    weekly_day_if_due,
)
from newsbot.models import Config
from newsbot.publish import Target, send_list, send_week_poll
from newsbot.state import BotState
from newsbot.telegram import FatalTelegramError, TelegramClient, TelegramError, UncertainDeliveryError

log = logging.getLogger(__name__)

Out = Callable[[str], None]


@dataclass(frozen=True)
class ScheduledOutcome:
    state: BotState
    digest_sent: bool = False
    weekly_sent: bool = False
    fatal_error: str | None = None


@dataclass(frozen=True)
class _Plan:
    kind: str  # "digest" or "weekly"
    day: str
    title_en: str
    lookback: timedelta
    size: int
    min_stories: int
    poll_options: int  # 0: no poll
    forced: bool  # asked for by hand: never uses up the scheduled one when there is nothing to list


def send_scheduled(
    client: TelegramClient | None,
    english: Target,
    arabic: Target | None,
    state: BotState,
    config: Config,
    now: datetime,
    force_digest: bool = False,
    force_weekly: bool = False,
    out: Out = print,
) -> ScheduledOutcome:
    """Send what is due. With no client (dry run) the posts are only printed and nothing is marked."""
    digest_sent = weekly_sent = False
    fatal: str | None = None
    for plan in _due_plans(state, config, now, force_digest, force_weekly):
        state, sent, fatal = _send_plan(client, english, arabic, state, config, now, plan, out)
        digest_sent = digest_sent or (sent and plan.kind == "digest")
        weekly_sent = weekly_sent or (sent and plan.kind == "weekly")
        if fatal:
            break
    return ScheduledOutcome(state=state, digest_sent=digest_sent, weekly_sent=weekly_sent, fatal_error=fatal)


def _due_plans(
    state: BotState, config: Config, now: datetime, force_digest: bool, force_weekly: bool
) -> list[_Plan]:
    digest, weekly = config.digest, config.weekly
    plans = []
    day = digest_day_if_due(state, digest, now, force_digest)
    if day:
        needed = 1 if force_digest else digest.min_stories
        plans.append(_Plan("digest", day, digest.title, DAY, digest.size, needed, 0, force_digest))
    day = weekly_day_if_due(state, weekly, digest.utc_offset_hours, now, force_weekly)
    if day:
        needed = 1 if force_weekly else weekly.min_stories
        options = weekly.poll_options if weekly.poll else 0
        plans.append(_Plan("weekly", day, weekly.title, WEEK, weekly.size, needed, options, force_weekly))
    return plans


def _send_plan(
    client: TelegramClient | None,
    english: Target,
    arabic: Target | None,
    state: BotState,
    config: Config,
    now: datetime,
    plan: _Plan,
    out: Out,
) -> tuple[BotState, bool, str | None]:
    entries = digest_entries(state, config, now, plan.lookback, plan.size)
    if len(entries) < plan.min_stories:
        log.info(
            "%s skipped for %s: only %d story(ies) to list", plan.kind.capitalize(), plan.day, len(entries)
        )
        keep_open = client is None or plan.forced
        return (state if keep_open else _mark(state, plan)), False, None
    date = date_label(now, config.digest.utc_offset_hours)
    if client is None:
        out(
            f"----- {plan.kind.upper()} for {plan.day}\n{_preview(entries, plan, english.edition.signature, date)}\n"
        )
        return state, False, None
    try:
        send_list(client, english, entries, plan.title_en, date)
    except FatalTelegramError as exc:
        return state, False, str(exc)
    except UncertainDeliveryError as exc:
        log.warning("%s. The %s may already be in the channel, so it is not sent again.", exc, plan.kind)
        return _mark(state, plan), True, None
    except TelegramError as exc:
        if exc.retryable:
            log.warning("Could not send the %s: %s (will try again next run)", plan.kind, exc)
            return state, False, None
        log.warning("Telegram rejected the %s: %s (skipped this time)", plan.kind, exc)
        return _mark(state, plan), False, None
    log.info("%s sent for %s with %d stories", plan.kind.capitalize(), plan.day, len(entries))
    if plan.poll_options:
        _send_poll(client, english, entries, plan)
    if arabic is not None:
        _send_arabic(client, arabic, state, config, now, plan, date)
    return _mark(state, plan), True, None


def _send_poll(client: TelegramClient, target: Target, entries: list[DigestEntry], plan: _Plan) -> None:
    """Best effort: the list is already out and marked, so a failed poll never re-sends the list."""
    try:
        send_week_poll(client, target, entries, plan.poll_options)
    except TelegramError as exc:
        log.warning("Could not send the weekly poll to %s: %s", target.chat_id, exc)


def _send_arabic(
    client: TelegramClient,
    arabic: Target,
    state: BotState,
    config: Config,
    now: datetime,
    plan: _Plan,
    date: str,
) -> None:
    """Best effort: the Arabic list only shows stories that were posted in Arabic."""
    entries = digest_entries(state, config, now, plan.lookback, plan.size, arabic=True)
    if len(entries) < plan.min_stories:
        log.info("Arabic %s skipped: only %d Arabic story(ies)", plan.kind, len(entries))
        return
    title = arabic.edition.digest_title if plan.kind == "digest" else arabic.edition.weekly_title
    try:
        send_list(client, arabic, entries, title, date)
    except TelegramError as exc:
        log.warning("Could not send the Arabic %s: %s", plan.kind, exc)
        return
    if plan.poll_options:
        _send_poll(client, arabic, entries, plan)


def _mark(state: BotState, plan: _Plan) -> BotState:
    return state.with_digest_day(plan.day) if plan.kind == "digest" else state.with_weekly_day(plan.day)


def _preview(entries: list[DigestEntry], plan: _Plan, signature: str, date: str) -> str:
    text = build_digest(entries, plan.title_en, date, signature)
    if plan.poll_options:
        text += "\n\nPoll: " + " / ".join(poll_options(entries, plan.poll_options))
    return text
