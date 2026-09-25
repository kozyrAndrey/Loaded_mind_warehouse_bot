import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.returns.admin import synchronize_topic
from modules.returns.loaded_mind import send_return_to_topic, split_caption


class ReturnTopicAlbumTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_photo_has_report_as_caption(self):
        bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=11)),
            send_media_group=AsyncMock(), send_message=AsyncMock(),
        )
        ids = []
        result = await send_return_to_topic(bot, "Отчёт о возврате", ["photo-1"],
                                            {"chat_id": -100, "message_thread_id": 27}, ids)
        self.assertEqual(result, "media")
        self.assertEqual(ids, [11])
        self.assertEqual(bot.send_photo.await_args.kwargs["caption"], "Отчёт о возврате")
        bot.send_message.assert_not_awaited()

    async def test_multiple_photos_are_one_album_with_caption(self):
        bot = SimpleNamespace(
            send_photo=AsyncMock(), send_message=AsyncMock(),
            send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=21),
                                                     SimpleNamespace(message_id=22)]),
        )
        ids = []
        await send_return_to_topic(bot, "Отчёт", ["a", "b"],
                                   {"chat_id": -100, "message_thread_id": 27}, ids)
        self.assertEqual(ids, [21, 22])
        media = bot.send_media_group.await_args.kwargs["media"]
        self.assertEqual([item.media for item in media], ["a", "b"])
        self.assertEqual([item.caption for item in media], ["Отчёт", None])
        bot.send_message.assert_not_awaited()

    async def test_more_than_ten_photos_make_two_albums(self):
        bot = SimpleNamespace(
            send_photo=AsyncMock(), send_message=AsyncMock(),
            send_media_group=AsyncMock(side_effect=[
                [SimpleNamespace(message_id=i) for i in range(1, 10)],
                [SimpleNamespace(message_id=10), SimpleNamespace(message_id=11)],
            ]),
        )
        ids = []
        await send_return_to_topic(bot, "Отчёт", [str(i) for i in range(11)],
                                   {"chat_id": -100, "message_thread_id": 27}, ids)
        self.assertEqual(ids, list(range(1, 12)))
        self.assertEqual(bot.send_media_group.await_count, 2)
        self.assertEqual(len(bot.send_media_group.await_args.kwargs["media"]), 2)
        self.assertIsNone(bot.send_media_group.await_args.kwargs["media"][0].caption)

    async def test_long_report_is_not_truncated(self):
        report = "Шапка\n" + "Т" * 1500
        caption, remainder = split_caption(report)
        self.assertEqual(caption + remainder, report)
        self.assertLessEqual(len(caption), 1024)
        bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=31)),
            send_media_group=AsyncMock(),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=32)),
        )
        ids = []
        await send_return_to_topic(bot, report, ["a"], {"chat_id": -100}, ids)
        self.assertEqual(ids, [31, 32])
        self.assertEqual(bot.send_photo.await_args.kwargs["caption"] +
                         bot.send_message.await_args.kwargs["text"], report)

    @patch("modules.returns.admin.summary", return_value="Обновлённый отчёт")
    @patch("modules.returns.admin.update_return_record")
    @patch("modules.returns.admin.get_return_record")
    async def test_admin_edits_album_caption(self, get_record, update_record, _summary):
        get_record.return_value = {
            "chat_id": "-100", "thread_id": "27", "message_ids": [41, 42],
            "photo_ids": ["a", "b"], "delivery_format": "media", "employee_name": "Иван",
        }
        bot = SimpleNamespace(edit_message_caption=AsyncMock(), edit_message_text=AsyncMock(),
                              send_message=AsyncMock(), delete_message=AsyncMock())
        await synchronize_topic(SimpleNamespace(bot=bot), 7)
        self.assertEqual(bot.edit_message_caption.await_args.kwargs["message_id"], 41)
        self.assertIn("Обновлённый отчёт", bot.edit_message_caption.await_args.kwargs["caption"])
        bot.edit_message_text.assert_not_awaited()
        update_record.assert_called_once_with(7, message_ids=[41, 42])

    @patch("modules.returns.admin.summary", return_value="Короткий отчёт")
    @patch("modules.returns.admin.update_return_record")
    @patch("modules.returns.admin.get_return_record")
    async def test_admin_removes_obsolete_long_report_continuation(self, get_record, update_record, _summary):
        get_record.return_value = {
            "chat_id": "-100", "thread_id": "27", "message_ids": [51, 52, 53],
            "photo_ids": ["a", "b"], "delivery_format": "media", "employee_name": "Иван",
        }
        bot = SimpleNamespace(edit_message_caption=AsyncMock(), edit_message_text=AsyncMock(),
                              send_message=AsyncMock(), delete_message=AsyncMock())
        await synchronize_topic(SimpleNamespace(bot=bot), 8)
        bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=53)
        update_record.assert_called_once_with(8, message_ids=[51, 52])
