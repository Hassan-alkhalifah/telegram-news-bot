"""Turn a story into a post for one channel (English or Arabic) and send it, plus list posts and polls."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from newsbot.banner import BannerText, render_banner
from newsbot.digest import DigestEntry, build_digest, poll_options
from newsbot.editions import topic_label
from newsbot.formatter import build_buttons, build_caption, name_hashtags
from newsbot.models import Config, Edition, Story, Topic
from newsbot.poster import post_article
from newsbot.telegram import TelegramClient
from newsbot.translate import GeminiTranslator

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    """One channel and how posts look there."""

    chat_id: str
    edition: Edition


@dataclass(frozen=True)
class Post:
    caption: str
    buttons: dict
    banner: bytes | None


def render_post(
    story: Story, edition: Edition, config: Config, photo: bytes | None, tags: Sequence[str]
) -> Post:
    topic = config.topic(story.lead.topic)
    caption = build_caption(story, edition, topic, config.settings.top_story_min_sources, tags)
    banner = _banner(story, edition, topic, config, photo) if config.style.banner else None
    return Post(caption=caption, buttons=build_buttons(story.lead, edition), banner=banner)


def send_story(client: TelegramClient, target: Target, story: Story, post: Post) -> str:
    return post_article(
        client, target.chat_id, story.lead, post.caption, banner=post.banner, buttons=post.buttons
    )


def story_tags(story: Story, config: Config) -> list[str]:
    """Name hashtags, always taken from the English titles so names keep their usual spelling."""
    return name_hashtags(story, config.topic(story.lead.topic), config.style.name_hashtags)


def translate_story(translator: GeminiTranslator, story: Story, config: Config) -> Story:
    """The story with its lead's title and summary in Arabic (the other sites' versions are not needed)."""
    lead = story.lead
    topic = config.topic(lead.topic)
    title, summary = translator.to_arabic(
        [lead.title, lead.summary], context=f"{topic.name if topic else lead.topic} news"
    )
    return replace(story, lead=replace(lead, title=title or lead.title, summary=summary))


def send_list(
    client: TelegramClient, target: Target, entries: list[DigestEntry], title: str, date: str
) -> None:
    text = build_digest(entries, title, date, target.edition.signature)
    client.send_message(target.chat_id, text, preview_url=entries[0].url)


def send_week_poll(client: TelegramClient, target: Target, entries: list[DigestEntry], count: int) -> None:
    options = poll_options(entries, count)
    if len(options) >= 2:
        client.send_poll(target.chat_id, target.edition.poll_question, options)


def _banner(
    story: Story, edition: Edition, topic: Topic | None, config: Config, photo: bytes | None
) -> bytes | None:
    lead = story.lead
    text = BannerText(
        headline=lead.title,
        topic=topic_label(topic, lead.topic, edition),
        suit=topic.suit if topic else "spade",
        source=lead.source,
        handle=edition.signature,
        brand=config.style.brand_name,
        top_story=edition.top_story_label if story.sources >= config.settings.top_story_min_sources else "",
        rtl=edition.rtl,
    )
    try:
        return render_banner(photo, text)
    except Exception:  # a drawing problem must never stop the news; the plain photo is used instead
        log.exception("Could not draw the banner for %s", lead.url)
        return None
