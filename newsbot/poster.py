"""Send one article: as a banner, as the news photo, or as a text post with a link preview."""

import logging

from newsbot.models import Article
from newsbot.telegram import FatalTelegramError, TelegramClient, TelegramError, UncertainDeliveryError

log = logging.getLogger(__name__)


def post_article(
    client: TelegramClient,
    chat_id: str,
    article: Article,
    caption: str,
    banner: bytes | None = None,
    buttons: dict | None = None,
) -> str:
    """Returns "banner", "photo" or "text". Raises TelegramError if every way fails.

    Each way is tried in turn when Telegram refuses the one before (a picture it cannot use).
    Setup problems and unclear answers are raised at once: the post may already be in the channel.
    """
    attempts: list[tuple[str, str | bytes]] = []
    if banner:
        attempts.append(("banner", banner))
    if article.image_url:
        attempts.append(("photo", article.image_url))
    for kind, photo in attempts:
        try:
            client.send_photo(chat_id, photo, caption, buttons=buttons)
            return kind
        except (FatalTelegramError, UncertainDeliveryError):
            raise
        except TelegramError as exc:
            log.info("The %s was not accepted for %s (%s); trying the next way", kind, article.url, exc)
    client.send_message(chat_id, caption, preview_url=article.url, buttons=buttons)
    return "text"
