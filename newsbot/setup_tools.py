"""Setup helpers: check the bot and channels, check the Gemini key, find a channel id, pin the language picker."""

import html
from collections.abc import Callable

from newsbot.banner import render_welcome
from newsbot.models import Config
from newsbot.telegram import TelegramClient, TelegramError
from newsbot.translate import GeminiTranslator, TranslationError

OK, FAILED = 0, 1
Out = Callable[[str], None]


def run_check(client: TelegramClient, chat_id: str, out: Out = print, label: str = "Channel") -> int:
    """Walk through the setup of one channel and say exactly which step is wrong."""
    try:
        me = client.get_me()
        out(f"Bot token OK: @{me.get('username')}")
        chat = client.get_chat(chat_id)
        out(f"{label} found: {chat.get('title')} ({chat_id}, type: {chat.get('type')})")
        member = client.get_chat_member(chat_id, me["id"])
    except TelegramError as exc:
        out(f"Problem with {chat_id}: {exc}")
        return FAILED

    status = member.get("status")
    if status not in ("administrator", "creator"):
        out(f"Problem: in {chat_id} the bot is '{status}', not an admin. Add it as an admin.")
        return FAILED
    if status == "administrator" and member.get("can_post_messages") is False:
        out(
            f"Problem: in {chat_id} the bot is an admin but cannot post. Turn on its 'Post Messages' permission."
        )
        return FAILED
    if status == "administrator" and member.get("can_edit_messages") is False:
        out(
            f"Note: in {chat_id} the bot cannot pin the language picker. Turn on 'Edit Messages' to allow it."
        )
    out(f"All good: the bot is an admin and can post in {chat_id}.")
    return OK


def check_translator(translator: GeminiTranslator, out: Out = print) -> int:
    """Translate one short headline: proves the key, the model and the free tier all work."""
    sample = "Breaking news"
    try:
        (arabic,) = translator.to_arabic([sample], context="Tech news")
    except TranslationError as exc:
        out(f"Problem with Gemini: {exc}")
        return FAILED
    out(f"Gemini key OK ({translator.model}): '{sample}' -> '{arabic}'")
    return OK


def run_find_chat_id(client: TelegramClient, out: Out = print) -> int:
    """List chats from the bot's recent updates, to find a private channel's numeric id."""
    try:
        updates = client.get_updates()
    except TelegramError as exc:
        out(f"Problem: {exc}")
        return FAILED

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
        return FAILED
    for chat_id, chat in chats.items():
        out(f"{chat_id}  {chat.get('title') or chat.get('username') or ''}  (type: {chat.get('type')})")
    out("Use the number for your channel as TELEGRAM_CHANNEL_ID.")
    return OK


def setup_language_picker(
    client: TelegramClient, english_chat: str, arabic_chat: str, config: Config, out: Out = print
) -> int:
    """Post the welcome banner with 'English' and 'Arabic' buttons in both channels, and pin it.

    Run again, it updates the pinned picker in place (new banner, text and buttons) instead of posting
    a second one.
    """
    if not (english_chat.startswith("@") and arabic_chat.startswith("@")):
        out("Problem: the language picker links to both channels, so both need a public @username.")
        return FAILED
    picker = config.language_picker
    banner = render_welcome(config.style.brand_name, picker.banner_subtitle, picker.banner_subtitle_ar)
    links = (f"https://t.me/{english_chat[1:]}", f"https://t.me/{arabic_chat[1:]}")
    buttons = {
        "inline_keyboard": [
            [
                {"text": picker.english_button, "url": links[0]},
                {"text": picker.arabic_button, "url": links[1]},
            ]
        ]
    }
    caption = html.escape(picker.text, quote=False)
    result = OK
    for chat in (english_chat, arabic_chat):
        pinned_id = _pinned_picker(client, chat, links, out)
        if pinned_id is not None:
            result = max(result, _update(client, chat, pinned_id, banner, caption, buttons, out))
            continue
        try:
            message = client.send_photo(chat, banner, caption, buttons=buttons)
        except TelegramError as exc:
            out(f"Problem: could not post the language picker in {chat}: {exc}")
            result = FAILED
            continue
        result = max(result, _pin(client, chat, message["message_id"], out))
    return result


def _update(
    client: TelegramClient, chat: str, message_id: int, banner: bytes, caption: str, buttons: dict, out: Out
) -> int:
    try:
        client.edit_message_media(chat, message_id, banner, caption, buttons=buttons)
    except TelegramError as exc:
        if "not modified" in str(exc):  # the same banner and text as before (or a retried edit that worked)
            out(f"The pinned language picker in {chat} is already up to date.")
            return OK
        out(f"Problem: could not update the pinned language picker in {chat}: {exc}")
        return FAILED
    out(f"Updated the pinned language picker in {chat}.")
    return OK


def _pinned_picker(client: TelegramClient, chat: str, links: tuple[str, str], out: Out) -> int | None:
    """The id of the pinned post if it is an earlier language picker (a photo with both channel links)."""
    try:
        pinned = client.get_chat(chat).get("pinned_message")
    except (TelegramError, AttributeError) as exc:
        out(f"Note: could not check the pinned post in {chat} ({exc}), so a new language picker is posted.")
        return None
    if not isinstance(pinned, dict) or not pinned.get("photo"):
        return None
    rows = (pinned.get("reply_markup") or {}).get("inline_keyboard") or []
    urls = {button.get("url") for row in rows for button in row if isinstance(button, dict)}
    message_id = pinned.get("message_id")
    return message_id if set(links) <= urls and isinstance(message_id, int) else None


def _pin(client: TelegramClient, chat: str, message_id: int, out: Out) -> int:
    try:
        client.pin_chat_message(chat, message_id)
    except TelegramError as exc:
        out(
            f"Posted the language picker in {chat} but could not pin it ({exc}). "
            "Give the bot the 'Edit Messages' right and run this again, or pin it by hand."
        )
        return FAILED
    out(f"Pinned the language picker in {chat}.")
    return OK
