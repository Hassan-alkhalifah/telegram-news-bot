"""The English and Arabic "editions": each channel's language, labels and signature."""

from newsbot.models import Config, Edition, Topic


def english_edition(config: Config) -> Edition:
    style, digest, weekly = config.style, config.digest, config.weekly
    return Edition(
        language="en",
        signature=style.channel_signature,
        top_story_label=style.top_story_label,
        read_button=style.read_button,
        share_button=style.share_button,
        source_label=style.source_label,
        digest_title=digest.title,
        weekly_title=weekly.title,
        poll_question=weekly.poll_question,
    )


def arabic_edition(config: Config, chat_id: str) -> Edition:
    """Without a signature in config.yaml, a public channel's @username is used."""
    arabic = config.arabic
    signature = arabic.channel_signature or (chat_id if chat_id.startswith("@") else "")
    return Edition(
        language="ar",
        signature=signature,
        top_story_label=arabic.top_story_label,
        read_button=arabic.read_button,
        share_button=arabic.share_button,
        source_label=arabic.source_label,
        digest_title=arabic.digest_title,
        weekly_title=arabic.weekly_title,
        poll_question=arabic.poll_question,
    )


def topic_label(topic: Topic | None, fallback: str, edition: Edition) -> str:
    """'GAMES' in English; the Arabic name (falling back to the English one) in Arabic."""
    name = topic.name if topic else fallback
    if edition.rtl:
        return topic.arabic_name if topic and topic.arabic_name else name
    return name.upper()


def topic_hashtag(topic: Topic | None, fallback: str, edition: Edition) -> str:
    if topic is None:
        return fallback
    if edition.rtl and topic.arabic_hashtag:
        return topic.arabic_hashtag
    return topic.hashtag
