"""Command line entry point: python -m newsbot [--dry-run | --check | --find-chat-id]."""

import argparse
import logging
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone

from dotenv import load_dotenv

from newsbot.config import ConfigError, load_config
from newsbot.runner import RunResult, run_once
from newsbot.telegram import TelegramClient, TelegramError
from newsbot.web import make_session

log = logging.getLogger("newsbot")

EXIT_OK, EXIT_FAILED, EXIT_SETUP = 0, 1, 2
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
_CHAT_ID_RE = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-?\d+)$")

Out = Callable[[str], None]


def main(argv: Sequence[str] | None = None, env_file: str | None = ".env") -> int:
    args = _parse_args(argv)
    use_utf8_output((sys.stdout, sys.stderr))
    _setup_logging(args.verbose)
    if env_file:
        load_dotenv(env_file)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("Config problem: %s", exc)
        return EXIT_SETUP

    session = make_session()
    if args.dry_run:
        result = run_once(
            config=config,
            session=session,
            client=None,
            chat_id="",
            state_path=args.state,
            now=_now(),
            dry_run=True,
        )
        return _report(result)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    setup_error = _setup_error(token, chat_id, need_chat=not args.find_chat_id)
    if setup_error:
        log.error(setup_error)
        return EXIT_SETUP

    client = TelegramClient(token, session, timeout=config.settings.request_timeout_seconds)
    if args.check:
        return run_check(client, chat_id)
    if args.find_chat_id:
        return run_find_chat_id(client)
    result = run_once(
        config=config, session=session, client=client, chat_id=chat_id, state_path=args.state, now=_now()
    )
    return _report(result)


def use_utf8_output(streams: Iterable[object]) -> None:
    """Windows writes piped output as cp1252, which crashes on titles like Japanese anime names."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m newsbot", description="Post the latest news to a Telegram channel."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="show what would be posted; send nothing, save nothing"
    )
    mode.add_argument(
        "--check", action="store_true", help="check the bot token, the channel and the bot's admin rights"
    )
    mode.add_argument(
        "--find-chat-id", action="store_true", help="show the numeric id of channels the bot has seen"
    )
    parser.add_argument("--config", default="config.yaml", help="config file (default: config.yaml)")
    parser.add_argument(
        "--state", default="data/posted.json", help="posted-stories file (default: data/posted.json)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    return parser.parse_args(argv)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    logging.getLogger().setLevel(level)
    # urllib3 logs full request URLs at debug level, and Telegram URLs contain the bot token.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _setup_error(token: str, chat_id: str, need_chat: bool) -> str | None:
    if not token:
        return (
            "TELEGRAM_BOT_TOKEN is not set. Put it in .env (on your PC) or in the GitHub repository secrets."
        )
    if not _TOKEN_RE.match(token):
        return "TELEGRAM_BOT_TOKEN does not look like a bot token (expected something like 123456789:AAH...)."
    if need_chat and not _CHAT_ID_RE.match(chat_id):
        return (
            "TELEGRAM_CHANNEL_ID must be the channel's @username (like @mynewschannel) "
            "or its numeric id (like -1001234567890)."
        )
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _report(result: RunResult) -> int:
    log.info(
        "Done. posted=%d unconfirmed=%d would_post=%d repeats_skipped=%d failed=%d feeds_failed=%d/%d",
        result.posted,
        result.unconfirmed,
        result.would_post,
        result.duplicates,
        result.failed_posts,
        result.feeds_failed,
        result.feeds_total,
    )
    if result.fatal_error:
        log.error(result.fatal_error)
        return EXIT_FAILED
    if result.failed_posts and not (result.posted or result.unconfirmed):
        # Exit 1 turns the GitHub run red, so a bot that silently posts nothing gets noticed.
        log.error("Nothing could be posted this run; see the warnings above.")
        return EXIT_FAILED
    return EXIT_OK


def run_check(client: TelegramClient, chat_id: str, out: Out = print) -> int:
    """Walk through the setup and say exactly which step is wrong."""
    try:
        me = client.get_me()
        out(f"Bot token OK: @{me.get('username')}")
        chat = client.get_chat(chat_id)
        out(f"Channel found: {chat.get('title')} (type: {chat.get('type')})")
        member = client.get_chat_member(chat_id, me["id"])
    except TelegramError as exc:
        out(f"Problem: {exc}")
        return EXIT_FAILED

    status = member.get("status")
    if status not in ("administrator", "creator"):
        out(f"Problem: the bot is in the channel as '{status}', not as an admin. Add it as an admin.")
        return EXIT_FAILED
    if status == "administrator" and member.get("can_post_messages") is False:
        out("Problem: the bot is an admin but cannot post. Turn on its 'Post Messages' permission.")
        return EXIT_FAILED
    out("All good: the bot is an admin and can post in the channel.")
    return EXIT_OK


def run_find_chat_id(client: TelegramClient, out: Out = print) -> int:
    """List chats from the bot's recent updates, to find a private channel's numeric id."""
    try:
        updates = client.get_updates()
    except TelegramError as exc:
        out(f"Problem: {exc}")
        return EXIT_FAILED

    chats = {}
    for update in updates or []:
        for kind in ("channel_post", "edited_channel_post", "message", "my_chat_member"):
            chat = (update.get(kind) or {}).get("chat")
            if chat and "id" in chat:
                chats[chat["id"]] = chat
    if not chats:
        out(
            "No channels found yet. Add the bot as an admin of your channel, post a message in your channel, "
            "then run this again within 24 hours."
        )
        return EXIT_FAILED
    for chat_id, chat in chats.items():
        out(f"{chat_id}  {chat.get('title') or chat.get('username') or ''}  (type: {chat.get('type')})")
    out("Use the number for your channel as TELEGRAM_CHANNEL_ID.")
    return EXIT_OK
