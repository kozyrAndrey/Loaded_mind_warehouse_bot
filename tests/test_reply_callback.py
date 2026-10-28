import unittest
from datetime import datetime
from unittest.mock import AsyncMock

from telegram import Chat, Message, ReplyKeyboardMarkup, Update, User
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters

from core.reply_callback import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    callback_data_from_text,
    route_reply_callback,
    send_or_edit_text,
)


class ReplyCallbackKeyboardTests(unittest.TestCase):
    def test_callback_keyboard_is_rendered_as_reply_keyboard(self):
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Открыть", callback_data="section:test")]]
        )

        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        self.assertTrue(markup.resize_keyboard)
        self.assertTrue(markup.is_persistent)

    def test_button_text_round_trips_callback_data(self):
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Открыть", callback_data="section:test")]]
        )
        sent_text = markup.keyboard[0][0].text

        self.assertTrue(sent_text.startswith("Открыть"))
        self.assertEqual(callback_data_from_text(sent_text), "section:test")
        self.assertEqual(markup.inline_keyboard[0][0].text, "Открыть")
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "section:test")

    def test_regular_user_text_is_not_a_callback(self):
        self.assertIsNone(callback_data_from_text("Обычный текст"))


class ReplyCallbackRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_button_reaches_existing_callback_handler(self):
        received = []

        async def callback_handler(update, context):
            received.append(update.callback_query.data)

        application = ApplicationBuilder().token("123:ABC").build()
        application._initialized = True
        application.add_handler(
            MessageHandler(filters.TEXT, route_reply_callback),
            group=-5,
        )
        application.add_handler(
            CallbackQueryHandler(callback_handler, pattern=r"^section:test$")
        )

        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Открыть", callback_data="section:test")]]
        )
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(99, "private"),
            from_user=User(7, "Test", False),
            text=markup.keyboard[0][0].text,
        )
        message.set_bot(application.bot)

        await application.process_update(Update(42, message=message))

        self.assertEqual(received, ["section:test"])

    async def test_reply_keyboard_is_sent_instead_of_attached_to_edit(self):
        message = type("Message", (), {})()
        message.edit_text = AsyncMock()
        message.reply_text = AsyncMock()
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Открыть", callback_data="section:test")]]
        )

        await send_or_edit_text(message, "Готово", reply_markup=markup)

        message.edit_text.assert_awaited_once_with("Готово")
        message.reply_text.assert_awaited_once_with("Выберите действие:", reply_markup=markup)
