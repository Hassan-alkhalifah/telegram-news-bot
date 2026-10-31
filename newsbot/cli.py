"""Command line entry point: python -m newsbot [--dry-run | --check | --find-chat-id | --setup-language-picker]."""

import argparse
import logging
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from newsbot.config import ConfigError, load_config
from newsbot.models import Config
from newsbot.runner import RunResult, run_once
from newsbot.setup_tools import check_deepl, run_check, run_find_chat_id, setup_language_picker
from newsbot.telegram import TelegramClient
from newsbot.translate import DeepLTranslator
from newsbot.web import make_session

log = logging.getLogger("newsbot")

EXIT_OK, EXIT_FAILED, EXIT_SETUP = 0, 1, 2
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
_CHAT_ID_RE = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-?\d+)$")


@dataclass(frozen=True)
class _Secrets:
    token: str
    chat_id: str
    arabic_chat_id: str
    deepl_key: str


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

    secrets = _read_secrets()
    problem = _secrets_problem(secrets, args)
    if problem:
        log.error(problem)
        return EXIT_SETUP
    session = make_session()
    timeout = config.settings.request_timeout_seconds
    translator = DeepLTranslator(secrets.deepl_key, session, timeout=timeout) if secrets.deepl_key else None
    arabic_chat = _arabic_chat(config, secrets)
    run_args = {
        "config": config,
        "session": session,
        "chat_id": secrets.chat_id,
        "state_path": args.state,
        "force_digest": args.digest_now,
        "force_weekly": args.weekly_now,
        "arabic_chat_id": arabic_chat,
        "translator": translator if arabic_chat else None,
    }
    if args.dry_run:
        banners = Path(args.banners_dir) if args.banners_dir else None
        return _report(run_once(**run_args, client=None, now=_now(), dry_run=True, banners_dir=banners))

    client = TelegramClient(secrets.token, session, timeout=timeout)
    if args.check:
        return _check_everything(client, secrets, translator)
    if args.find_chat_id:
        return run_find_chat_id(client)
    if args.setup_language_picker:
        if not secrets.arabic_chat_id:
            log.error("Set TELEGRAM_ARABIC_CHANNEL_ID first: the language picker links to both channels.")
            return EXIT_SETUP
        return setup_language_picker(client, secrets.chat_id, secrets.arabic_chat_id, config)
    return _report(run_once(**run_args, client=client, now=_now()))


def use_utf8_output(streams: Iterable[object]) -> None:
    """Windows writes piped output as cp1252, which crashes on titles like Japanese anime names."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m newsbot", description="Post the latest news to Telegram."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="show what would be posted; send nothing, save nothing"
    )
    mode.add_argument("--check", action="store_true", help="check the token, both channels and the DeepL key")
    mode.add_argument(
        "--find-chat-id", action="store_true", help="show the numeric id of channels the bot has seen"
    )
    mode.add_argument(
        "--setup-language-picker",
        action="store_true",
        help="post and pin the 'choose your language' message in both channels",
    )
    parser.add_argument("--digest-now", action="store_true", help="send the evening top stories post now")
    parser.add_argument("--weekly-now", action="store_true", help="send the weekly top 10 and its poll now")
    parser.add_argument(
        "--banners-dir", help="with --dry-run: save the banners as image files in this folder"
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


def _read_secrets() -> _Secrets:
    def value(name: str) -> str:
        return os.environ.get(name, "").strip()

    return _Secrets(
        token=value("TELEGRAM_BOT_TOKEN"),
        chat_id=value("TELEGRAM_CHANNEL_ID"),
        arabic_chat_id=value("TELEGRAM_ARABIC_CHANNEL_ID"),
        deepl_key=value("DEEPL_API_KEY"),
    )


def _secrets_problem(secrets: _Secrets, args: argparse.Namespace) -> str | None:
    if secrets.arabic_chat_id and not _CHAT_ID_RE.match(secrets.arabic_chat_id):
        return "TELEGRAM_ARABIC_CHANNEL_ID must be the Arabic channel's @username or its numeric id."
    if secrets.arabic_chat_id and secrets.arabic_chat_id.casefold() == secrets.chat_id.casefold():
        return "TELEGRAM_ARABIC_CHANNEL_ID must be a different channel from TELEGRAM_CHANNEL_ID."
    if args.dry_run:
        return None
    if not secrets.token:
        return (
            "TELEGRAM_BOT_TOKEN is not set. Put it in .env (on your PC) or in the GitHub repository secrets."
        )
    if not _TOKEN_RE.match(secrets.token):
        return "TELEGRAM_BOT_TOKEN does not look like a bot token (expected something like 123456789:AAH...)."
    if not args.find_chat_id and not _CHAT_ID_RE.match(secrets.chat_id):
        return (
            "TELEGRAM_CHANNEL_ID must be the channel's @username (like @mynewschannel) "
            "or its numeric id (like -1001234567890)."
        )
    return None


def _arabic_chat(config: Config, secrets: _Secrets) -> str | None:
    """The Arabic channel, when it is switched on and both of its secrets are set."""
    if not config.arabic.enabled:
        return None
    if secrets.arabic_chat_id and secrets.deepl_key:
        return secrets.arabic_chat_id
    if secrets.arabic_chat_id or secrets.deepl_key:
        log.warning("Arabic channel is off: set both TELEGRAM_ARABIC_CHANNEL_ID and DEEPL_API_KEY.")
    return None


def _check_everything(client: TelegramClient, secrets: _Secrets, translator: DeepLTranslator | None) -> int:
    results = [run_check(client, secrets.chat_id)]
    if secrets.arabic_chat_id:
        results.append(run_check(client, secrets.arabic_chat_id, label="Arabic channel"))
    if translator is not None:
        results.append(check_deepl(translator))
    return EXIT_FAILED if any(results) else EXIT_OK


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _report(result: RunResult) -> int:
    log.info(
        "Done. posted=%d arabic=%d unconfirmed=%d would_post=%d repeats_skipped=%d failed=%d "
        "feeds_failed=%d/%d digest=%s weekly=%s",
        result.posted,
        result.arabic_posted,
        result.unconfirmed,
        result.would_post,
        result.duplicates,
        result.failed_posts,
        result.feeds_failed,
        result.feeds_total,
        "sent" if result.digest_sent else "no",
        "sent" if result.weekly_sent else "no",
    )
    if result.fatal_error:
        log.error(result.fatal_error)
        return EXIT_FAILED
    if result.failed_posts and not (result.posted or result.unconfirmed):
        # Exit 1 turns the GitHub run red, so a bot that silently posts nothing gets noticed.
        log.error("Nothing could be posted this run; see the warnings above.")
        return EXIT_FAILED
    return EXIT_OK
