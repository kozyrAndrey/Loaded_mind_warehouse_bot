import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.lamoda_fbs.handlers import (
    RETURN_CONDITION,
    RETURN_ITEM,
    RETURN_KIZ,
    RETURN_PACK,
    _pack_prompt,
    _send_return_report,
    marking_open,
    return_item_barcode,
    return_kiz_skip,
    return_pack_skip,
    return_photo,
    show_lamoda_menu,
)
from modules.lamoda_fbs.jobs import (
    lamoda_marking_reminder_job,
    setup_lamoda_jobs,
)


def callback_update(user_id=7):
    query = SimpleNamespace(
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
        data="",
    )
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id, full_name="Сотрудник", username="worker"),
    )


class LamodaHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_pack_prompt_uses_seller_article_and_full_name(self):
        text = _pack_prompt({
            "order_id": "RU-1",
            "product_name": "HOMME BIRKIN MESSENGER (19x25x10, черная, HBM-BAG)",
            "size": "19x25x10",
            "external_sku": "HBM-BAG",
            "sku": "XD001XU01OYXNS00",
            "item_id": "ITEM-1",
            "pack_number": "PACK-1",
        }, 1, 1)

        self.assertIn("Название: HOMME BIRKIN MESSENGER", text)
        self.assertIn("Артикул: HBM-BAG", text)
        self.assertIn("SKU Lamoda: XD001XU01OYXNS00", text)

    async def test_opening_section_clears_temporary_state(self):
        update = callback_update()
        context = SimpleNamespace(user_data={"old": "value"})
        await show_lamoda_menu(update, context)
        self.assertEqual(context.user_data, {})
        keyboard = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("lamoda:assembly:start", callbacks)
        self.assertIn("lamoda:returns", callbacks)

    async def test_employee_cannot_open_marking_operations(self):
        update = callback_update()
        context = SimpleNamespace(user_data={})
        with patch("modules.lamoda_fbs.handlers._is_lamoda_manager", return_value=False):
            await marking_open(update, context)
        update.callback_query.answer.assert_awaited_once()
        self.assertTrue(update.callback_query.answer.await_args.kwargs["show_alert"])
        update.callback_query.edit_message_text.assert_not_awaited()

    async def test_return_requires_item_label_then_allows_pack_and_kiz_skips(self):
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo")], text="ITEM-1", reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            message=message, effective_message=message,
            effective_user=SimpleNamespace(id=7, full_name="Сотрудник"),
        )
        context = SimpleNamespace(user_data={"lamoda_return": {}})

        self.assertEqual(await return_photo(update, context), RETURN_ITEM)
        with patch("modules.lamoda_fbs.handlers.resolve_return_item_barcode", new=AsyncMock(return_value={
            "order_id": "ORDER-1", "pack_number": "PACK-1", "item_id": "ITEM-1",
            "product_name": "Футболка", "size": "M", "sku": "SKU", "raw_code": "",
            "fingerprint": "", "requires_marking": False, "lamoda_status": "RETURN",
        })):
            self.assertEqual(await return_item_barcode(update, context), RETURN_PACK)

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(reply_text=AsyncMock()))
        callback_update = SimpleNamespace(callback_query=callback)
        self.assertEqual(await return_pack_skip(callback_update, context), RETURN_KIZ)
        self.assertEqual(await return_kiz_skip(callback_update, context), RETURN_CONDITION)
        self.assertFalse(context.user_data["lamoda_return"]["problematic"])
        self.assertTrue(context.user_data["lamoda_return"]["kiz_matches"])

    async def test_skipping_kiz_for_known_marked_product_requires_reconciliation(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(reply_text=AsyncMock()))
        update = SimpleNamespace(callback_query=callback)
        context = SimpleNamespace(user_data={"lamoda_return": {
            "pack": {"fingerprint": "known-code"}, "problematic": False, "problem_reason": "",
        }})

        self.assertEqual(await return_kiz_skip(update, context), RETURN_CONDITION)
        self.assertTrue(context.user_data["lamoda_return"]["problematic"])
        self.assertIn("не отсканирован", context.user_data["lamoda_return"]["problem_reason"])

    async def test_normal_return_uses_configured_topic_without_manager_mention(self):
        bot = SimpleNamespace(send_photo=AsyncMock(), send_media_group=AsyncMock())
        context = SimpleNamespace(bot=bot)
        data = self._return_data(condition="NORMAL", problematic=False)
        with (
            patch("modules.lamoda_fbs.handlers.GROUP_CHAT_ID", "-100123"),
            patch("modules.lamoda_fbs.handlers.LAMODA_RETURNS_TOPIC_ID", "77"),
            patch("modules.lamoda_fbs.handlers._mentions_for_roles", return_value="@boss"),
        ):
            await _send_return_report(context, data, 1)
        kwargs = bot.send_photo.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100123)
        self.assertEqual(kwargs["message_thread_id"], 77)
        self.assertNotIn("@boss", kwargs["caption"])

    async def test_defect_return_mentions_brand_warehouse_and_operations(self):
        bot = SimpleNamespace(send_photo=AsyncMock(), send_media_group=AsyncMock())
        context = SimpleNamespace(bot=bot)
        data = self._return_data(condition="DEFECT", problematic=False)
        data["defect_reason"] = "Пятно"
        with (
            patch("modules.lamoda_fbs.handlers.GROUP_CHAT_ID", "-100123"),
            patch("modules.lamoda_fbs.handlers.LAMODA_RETURNS_TOPIC_ID", "77"),
            patch(
                "modules.lamoda_fbs.handlers.get_employees",
                return_value=[
                    {"employee_id": "brand", "roles": ["brand_manager"], "telegram_username": "brand_boss"},
                    {"employee_id": "warehouse", "roles": ["warehouse_manager", "admin"], "telegram_username": "warehouse_boss"},
                    {"employee_id": "operator", "roles": ["warehouse_employee", "operations"], "telegram_username": "operator"},
                    {"employee_id": "worker", "roles": ["warehouse_employee"], "telegram_username": "worker"},
                ],
            ),
        ):
            await _send_return_report(context, data, 2)
        caption = bot.send_photo.await_args.kwargs["caption"]
        self.assertIn("@brand_boss", caption)
        self.assertIn("@warehouse_boss", caption)
        self.assertNotIn("@operator", caption)
        self.assertNotIn("@worker", caption)

    @staticmethod
    def _return_data(condition, problematic):
        return {
            "condition": condition,
            "problematic": problematic,
            "problem_reason": "",
            "employee_name": "Сотрудник",
            "label_photo": "label-file-id",
            "defect_photos": [],
            "kiz_matches": True,
            "pack": {
                "order_id": "ORDER-1", "pack_number": "PACK-1",
                "product_name": "Футболка", "size": "M",
            },
        }


class LamodaJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_reminder_is_sent_only_to_manager_rows_returned_by_storage(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        with (
            patch("modules.lamoda_fbs.jobs.is_module_enabled", return_value=True),
            patch("modules.lamoda_fbs.jobs.pending_counts", return_value={"WAITING_WITHDRAWAL": 2}),
            patch("modules.lamoda_fbs.jobs.get_warehouse_managers", return_value=[
                {"telegram_user_id": "42", "role": "warehouse_manager", "is_active": True},
            ]),
        ):
            await lamoda_marking_reminder_job(context)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs["chat_id"], 42)

    def test_jobs_have_stable_names_and_are_not_registered_twice(self):
        class FakeQueue:
            def __init__(self):
                self.jobs = {}

            def get_jobs_by_name(self, name):
                return tuple(self.jobs.get(name, []))

            def run_repeating(self, callback, **kwargs):
                self.jobs.setdefault(kwargs["name"], []).append((callback, kwargs))

            def run_daily(self, callback, **kwargs):
                self.jobs.setdefault(kwargs["name"], []).append((callback, kwargs))

        queue = FakeQueue()
        app = SimpleNamespace(job_queue=queue)
        setup_lamoda_jobs(app)
        setup_lamoda_jobs(app)
        self.assertEqual(len(queue.jobs["lamoda_status_sync"]), 1)
        self.assertEqual(len(queue.jobs["lamoda_marking_reminder"]), 1)
        self.assertNotIn("lamoda_cancellation_notice", queue.jobs)


if __name__ == "__main__":
    unittest.main()
