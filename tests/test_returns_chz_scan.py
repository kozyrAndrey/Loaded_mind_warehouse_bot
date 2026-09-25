import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.returns.handlers import (
    RET_ITEM_CHZ_PHOTO,
    RET_ITEM_CHZ_SCAN,
    item_chz_photo_received,
    item_chz_scan_received,
    send_item_chz_photo_to_topic,
)


class ReturnsChzScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_is_saved_before_separate_datamatrix_scan(self):
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo-chz")],
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})

        state = await item_chz_photo_received(
            SimpleNamespace(message=message), context,
        )

        self.assertEqual(state, RET_ITEM_CHZ_SCAN)
        self.assertEqual(
            context.user_data["return_current_item"]["chz_photo_file_id"],
            "photo-chz",
        )
        self.assertIn("отсканируйте DataMatrix", message.reply_text.await_args.args[0])

    async def test_scanned_code_is_converted_to_31_chars_and_sent_with_photo(self):
        raw_code = "010460123456789321ABCDEFGHIJKLM\x1d91ABCD\x1d92SIGNATURE"
        message = SimpleNamespace(text=raw_code, reply_text=AsyncMock())
        user = SimpleNamespace(id=7, full_name="Сотрудник", username="worker")
        context = SimpleNamespace(user_data={
            "return_current_item": {"chz_photo_file_id": "photo-chz"},
        })

        with (
            patch("modules.returns.handlers.send_item_chz_photo_to_topic", new=AsyncMock(return_value="Отправлено")) as send_photo,
            patch("modules.returns.handlers.append_current_item") as append_item,
            patch("modules.returns.handlers.ask_next_item_or_finish", new=AsyncMock(return_value=999)),
        ):
            state = await item_chz_scan_received(
                SimpleNamespace(message=message, effective_user=user), context,
            )

        self.assertEqual(state, 999)
        current_item = context.user_data["return_current_item"]
        self.assertEqual(current_item["chz_code_full"], raw_code)
        self.assertEqual(current_item["chz_code_short"], "010460123456789321ABCDEFGHIJKLM")
        self.assertEqual(len(current_item["chz_code_short"]), 31)
        send_photo.assert_awaited_once_with(
            context=context, user=user, photo_file_id="photo-chz",
        )
        append_item.assert_called_once_with(context)

    async def test_invalid_scan_keeps_employee_on_scan_step(self):
        message = SimpleNamespace(text="не код", reply_text=AsyncMock())
        context = SimpleNamespace(user_data={
            "return_current_item": {"chz_photo_file_id": "photo-chz"},
        })

        state = await item_chz_scan_received(
            SimpleNamespace(message=message), context,
        )

        self.assertEqual(state, RET_ITEM_CHZ_SCAN)
        self.assertIn("Повторно отсканируйте", message.reply_text.await_args.args[0])

    async def test_topic_caption_contains_short_code(self):
        bot = SimpleNamespace(send_photo=AsyncMock())
        context = SimpleNamespace(
            bot=bot,
            user_data={
                "return_current_item": {
                    "chz_code_short": "010460123456789321ABCDEFGHIJKLM",
                    "size": "M",
                },
                "return_counterparty": "Иванов Иван",
            },
        )
        user = SimpleNamespace(id=7, full_name="Сотрудник", username="worker")

        with (
            patch("modules.returns.handlers.RETURN_CHZ_CHAT_ID", "-100123"),
            patch("modules.returns.handlers.RETURN_CHZ_TOPIC_ID", "456"),
            patch("modules.returns.handlers.get_employee_full_name_for_user", return_value="Сотрудник"),
            patch("modules.returns.handlers.get_current_item_product_name", return_value="Футболка"),
        ):
            await send_item_chz_photo_to_topic(context, user, "photo-chz")

        caption = bot.send_photo.await_args.kwargs["caption"]
        self.assertIn("Короткий код ЧЗ: 010460123456789321ABCDEFGHIJKLM", caption)
        self.assertEqual(bot.send_photo.await_args.kwargs["message_thread_id"], 456)


if __name__ == "__main__":
    unittest.main()
