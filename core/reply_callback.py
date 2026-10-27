"""Reply-keyboard compatibility for handlers built around callback queries.

Telegram reply keyboards send a text message instead of a callback query.  The
classes below keep the existing keyboard-building API while rendering buttons
under the input field.  A short invisible token links the visible button back
to its callback data; :func:`route_reply_callback` then replays the update as a
callback query so existing ConversationHandler states continue to work.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop


_TOKEN_PREFIX = "\u2063"
_TOKEN_SIZE = 4
_TOKENS: dict[bytes, str] = {}


def _token_for(callback_data: str) -> bytes:
    nonce = 0
    while True:
        source = callback_data if nonce == 0 else f"{callback_data}:{nonce}"
        token = hashlib.blake2s(source.encode("utf-8"), digest_size=_TOKEN_SIZE).digest()
        registered = _TOKENS.get(token)
        if registered in (None, callback_data):
            _TOKENS[token] = callback_data
            return token
        nonce += 1


def _variation_selector(byte: int) -> str:
    # Unicode variation selectors are not visibly rendered next to button text.
    return chr(0xE0100 + byte)


def _encoded_text(text: str, callback_data: str) -> str:
    token = _token_for(callback_data)
    suffix = _TOKEN_PREFIX + "".join(_variation_selector(byte) for byte in token)
    # Keep the UTF-16 representation below Telegram's 64-character limit too:
    # supplementary-plane variation selectors use two UTF-16 code units.
    return f"{text[:55]}{suffix}"


def callback_data_from_text(text: str | None) -> str | None:
    if not text:
        return None
    marker = text.rfind(_TOKEN_PREFIX)
    if marker < 0:
        return None
    encoded = text[marker + 1 :]
    if len(encoded) != _TOKEN_SIZE:
        return None
    try:
        token = bytes(ord(char) - 0xE0100 for char in encoded)
    except (ValueError, OverflowError):
        return None
    if any(byte < 0 or byte > 255 for byte in token):
        return None
    return _TOKENS.get(token)


@dataclass(frozen=True)
class InlineKeyboardButton:
    """Drop-in button definition used by existing keyboard builders."""

    text: str
    callback_data: str | None = None


class InlineKeyboardMarkup(ReplyKeyboardMarkup):
    """Render callback-style definitions as a persistent reply keyboard."""

    def __init__(self, inline_keyboard):
        callback_rows = tuple(tuple(row) for row in inline_keyboard)
        keyboard = []
        for row in callback_rows:
            keyboard.append(
                [
                    KeyboardButton(
                        _encoded_text(button.text, button.callback_data)
                        if button.callback_data
                        else button.text
                    )
                    for button in row
                ]
            )
        super().__init__(keyboard, resize_keyboard=True, is_persistent=True)

    @property
    def inline_keyboard(self):
        """Expose original-style rows for tests and keyboard introspection."""
        rows = []
        for row in self.keyboard:
            converted = []
            for button in row:
                text = button.text
                marker = text.rfind(_TOKEN_PREFIX)
                visible_text = text[:marker] if marker >= 0 else text
                converted.append(
                    InlineKeyboardButton(
                        visible_text,
                        callback_data=callback_data_from_text(text),
                    )
                )
            rows.append(tuple(converted))
        return tuple(rows)


class ReplyKeyboardCallbackQuery:
    """Minimal callback-query facade backed by the user's button message."""

    def __init__(self, message, callback_data: str):
        self.data = callback_data
        self.message = message
        self.from_user = message.from_user
        self.id = f"reply-keyboard:{message.message_id}"
        self.chat_instance = str(message.chat_id)
        self.game_short_name = None

    async def answer(self, *args, **kwargs):
        return True

    async def edit_message_text(self, text, *args, **kwargs):
        # Reply keyboards cannot be attached to an edited Telegram message.
        # Sending the next prompt also replaces the keyboard in the input panel.
        return await self.message.reply_text(text, *args, **kwargs)

    async def edit_message_reply_markup(self, reply_markup=None, *args, **kwargs):
        return await self.message.reply_text(
            "Выберите действие:",
            reply_markup=reply_markup,
        )


async def route_reply_callback(update, context):
    """Convert a reply-keyboard button message into a callback-query update."""
    message_text = update.effective_message.text
    callback_data = callback_data_from_text(message_text)
    if callback_data is None:
        if message_text and _TOKEN_PREFIX in message_text:
            await update.effective_message.reply_text(
                "Эта клавиатура устарела после перезапуска бота. Откройте её заново командой /menu."
            )
            raise ApplicationHandlerStop
        return

    callback_query = ReplyKeyboardCallbackQuery(update.effective_message, callback_data)
    callback_update = Update(update.update_id, callback_query=callback_query)
    await context.application.process_update(callback_update)
    raise ApplicationHandlerStop
