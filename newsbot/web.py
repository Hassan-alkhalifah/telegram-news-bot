"""The one HTTP session the bot uses for feeds, article pages and Telegram."""

import requests

USER_AGENT = "Mozilla/5.0 (compatible; TelegramNewsBot/1.0)"


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session
