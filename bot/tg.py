"""Telegram notification layer.

Provides two send functions:
- notify_group_chat(): sends HTML text messages
- notify_group_chat_photo(): sends a photo with HTML caption, falls back to text

The Bot instance is created once at module level and reused across
all sends, avoiding the overhead of instantiation per message.
"""

import logging
import os

from telegram import Bot

logger = logging.getLogger(__name__)

BOT_ACCESS_TOKEN = os.getenv("BOT_ACCESS_TOKEN", "")
if BOT_ACCESS_TOKEN == "":
    raise RuntimeError("!BOT_ACCESS_TOKEN")

GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
if GROUP_CHAT_ID == 0:
    raise RuntimeError("!GROUP_CHAT_ID")

ERROR_GROUP_CHAT_ID = int(os.getenv("ERROR_GROUP_CHAT_ID", "0"))
if ERROR_GROUP_CHAT_ID == 0:
    raise RuntimeError("!ERROR_GROUP_CHAT_ID")

# Singleton Bot instance — created once, reused for all sends
_bot = Bot(token=BOT_ACCESS_TOKEN)


async def notify_group_chat(
    text: str,
    parse_mode: str = "HTML",
    chat_id: int = GROUP_CHAT_ID,
    disable_web_page_preview: bool = True,
) -> None:
    """Send an HTML text message to a Telegram group chat.

    Args:
        text: HTML-formatted message body.
        parse_mode: Telegram parse mode (default HTML).
        chat_id: Target chat ID (defaults to GROUP_CHAT_ID).
        disable_web_page_preview: Suppress link previews (default True).
    """
    try:
        await _bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as e:
        logger.error("Failed to send message to chat %s: %s", chat_id, e)


async def notify_group_chat_photo(
    photo_url: str,
    caption: str,
    parse_mode: str = "HTML",
    chat_id: int = GROUP_CHAT_ID,
) -> None:
    """Send a photo with caption to a Telegram group chat.

    Attempts to send the image from photo_url with an HTML caption.
    If the photo send fails (bad URL, Telegram rejects it, caption
    too long for photo mode's 1024 char limit), falls back to a
    plain text message via notify_group_chat().

    Args:
        photo_url: URL of the image to send.
        caption: HTML-formatted caption text.
        chat_id: Target chat ID (defaults to GROUP_CHAT_ID).
    """
    try:
        await _bot.send_photo(
            chat_id=chat_id,
            photo=photo_url,
            caption=caption,
            parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning("Photo send failed (falling back to text): %s", e)
        await notify_group_chat(text=caption, parse_mode=parse_mode, chat_id=chat_id)
