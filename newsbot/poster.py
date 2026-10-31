"""Send one article: as a photo post, or as a text post with a link preview if the photo fails."""

import logging

from newsbot.models import Article
from newsbot.telegram import FatalTelegramError, TelegramClient, TelegramError, UncertainDeliveryError

log = logging.getLogger(__name__)


def post_article(client: TelegramClient, chat_id: str, article: Article, caption: str) -> str:
    """Returns "photo" or "text". Raises TelegramError if both ways fail."""
    if article.image_url:
        try:
            client.send_photo(chat_id, article.image_url, caption)
            return "photo"
        except (FatalTelegramError, UncertainDeliveryError):
            # Setup problems must surface; an unclear result may already be posted, so no second copy.
            raise
        except TelegramError as exc:
            log.info("Photo was not accepted for %s (%s); sending as text instead", article.url, exc)
    client.send_message(chat_id, caption, preview_url=article.url)
    return "text"
