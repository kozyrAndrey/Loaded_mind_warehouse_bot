import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.consumables.handlers import (
    INVENTORY_EXIT_CONFIRM,
    RECEIPT_LAYOUT_PHOTOS,
    delete_item_confirmed,
    inventory_exit_requested,
    inventory_item_selected,
    inventory_record_category,
    inventory_review_apply,
    inventory_review_keyboard,
    inventory_review_text,
    inventory_session_keyboard,
    receipt_layout_photos_finished,
    send_completed_inventory_pdf_to_topic,
)
from modules.consumables.pdf_reports import create_consumables_stock_pdf
from modules.consumables.storage import DEFAULT_CONSUMABLE_ITEMS


class ConsumablesInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_can_confirm_consumable_item_deletion(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=42),
        )
        context = SimpleNamespace(user_data={"delete_consumable_item_id": 17})
        deleted_item = {
            "item_id": 17,
            "name": "Старые пакеты",
            "is_active": False,
        }

        with (
            patch(
                "modules.consumables.handlers.current_employee_or_none",
                return_value={"role": "warehouse_manager"},
            ),
            patch(
                "modules.consumables.handlers.deactivate_consumable_item",
                return_value=deleted_item,
            ) as deactivate_item,
            patch(
                "modules.consumables.handlers.consumables_main_keyboard",
                return_value="menu",
            ),
        ):
            state = await delete_item_confirmed(update, context)

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(context.user_data, {})
        deactivate_item.assert_called_once_with(17)
        self.assertIn("удален из активного учета", query.edit_message_text.await_args.args[0])
        self.assertEqual(query.edit_message_text.await_args.kwargs["reply_markup"], "menu")

    def test_inventory_review_is_paginated_below_telegram_text_limit(self):
        records = [
            {
                "item_id": item_id,
                "item_name": f"Очень длинное название расходника номер {item_id}",
                "unit": "Очень длинное название единицы измерения",
                "system_quantity": 123,
                "counted_quantity": 125,
                "counted": True,
                "counted_by_name": "Очень Длинное Имя Сотрудника Склада" * 8,
            }
            for item_id in range(1, 55)
        ]
        inventory_session = {"completed_by_name": "Очень Длинное Имя Сотрудника Склада" * 8}

        first_page_text = inventory_review_text(inventory_session, records)
        first_page_keyboard = inventory_review_keyboard(records)
        second_page_keyboard = inventory_review_keyboard(records, page=1)

        self.assertLessEqual(len(first_page_text), 4096)
        self.assertIn("страница 1/4", first_page_text)
        self.assertEqual(
            [row[0].callback_data for row in first_page_keyboard.inline_keyboard[:15]],
            [f"consreview:item:{item_id}" for item_id in range(1, 16)],
        )
        self.assertEqual(
            [row[0].callback_data for row in second_page_keyboard.inline_keyboard[:15]],
            [f"consreview:item:{item_id}" for item_id in range(16, 31)],
        )
        self.assertTrue(
            any(
                button.callback_data == "consreview:page:1"
                for row in first_page_keyboard.inline_keyboard
                for button in row
            )
        )

    async def test_employee_cannot_apply_inventory_review(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
        )
        context = SimpleNamespace(
            user_data={"inventory_review_session_id": "inventory-1"}
        )

        with (
            patch(
                "modules.consumables.handlers.current_employee_or_none",
                return_value={"role": "warehouse_employee"},
            ),
            patch("modules.consumables.handlers.apply_inventory_session") as apply_inventory,
        ):
            state = await inventory_review_apply(update, context)

        self.assertEqual(state, ConversationHandler.END)
        apply_inventory.assert_not_called()
        self.assertIn("Недостаточно прав", query.edit_message_text.await_args.args[0])

    async def test_receipt_requires_at_least_one_layout_photo(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={"receipt_layout_photo_file_ids": []})

        state = await receipt_layout_photos_finished(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, RECEIPT_LAYOUT_PHOTOS)
        self.assertIn("хотя бы одно фото", query.edit_message_text.await_args.args[0])

    async def test_cancel_inventory_keeps_saved_draft(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={"inventory_session_id": "inventory-1"})

        state = await inventory_exit_requested(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, INVENTORY_EXIT_CONFIRM)
        self.assertEqual(context.user_data["inventory_session_id"], "inventory-1")
        self.assertIn("сохранен как черновик", query.edit_message_text.await_args.args[0])

    async def test_counted_item_shows_current_value_before_editing(self):
        query = SimpleNamespace(
            data="consinventoryitem:7",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"inventory_session_id": "inventory-1"})

        with (
            patch("modules.consumables.handlers.get_consumable_item", return_value={"name": "Коробки"}),
            patch(
                "modules.consumables.handlers.get_inventory_session_items",
                return_value=[
                    {
                        "item_id": 7,
                        "counted": True,
                        "system_quantity": 10,
                        "counted_quantity": 12,
                        "unit": "шт",
                    }
                ],
            ),
        ):
            await inventory_item_selected(SimpleNamespace(callback_query=query), context)

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Расходник: Коробки", text)
        self.assertIn("Значение в системе: 10 шт", text)
        self.assertIn("Фактическое значение: 12 шт", text)

    async def test_uncounted_item_shows_zero_as_actual_value(self):
        query = SimpleNamespace(
            data="consinventoryitem:7",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"inventory_session_id": "inventory-1"})

        with (
            patch(
                "modules.consumables.handlers.get_consumable_item",
                return_value={"name": "Коробки", "unit": "шт"},
            ),
            patch(
                "modules.consumables.handlers.get_inventory_session_items",
                return_value=[
                    {
                        "item_id": 7,
                        "counted": False,
                        "system_quantity": 10,
                        "counted_quantity": None,
                        "unit": "шт",
                    }
                ],
            ),
        ):
            await inventory_item_selected(
                SimpleNamespace(callback_query=query),
                context,
            )

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Значение в системе: 10 шт", text)
        self.assertIn("Фактическое значение: 0 шт", text)

    def test_stock_pdf_is_created(self):
        report_path = create_consumables_stock_pdf(
            [{"name": "Коробки", "current_quantity": 12, "unit": "шт"}],
            filename="consumables_stock_test.pdf",
        )
        try:
            self.assertTrue(report_path.exists())
            self.assertTrue(report_path.read_bytes().startswith(b"%PDF"))
        finally:
            report_path.unlink(missing_ok=True)

    def test_inventory_keyboard_keeps_checkmark_before_long_name(self):
        keyboard = inventory_session_keyboard(
            [
                {
                    "item_id": 1,
                    "item_name": "Очень длинное название расходника, которое не помещается в кнопку Telegram",
                    "unit": "шт",
                    "counted": True,
                    "counted_quantity": 12,
                }
            ],
            category_key="other",
        )

        self.assertTrue(keyboard.inline_keyboard[0][0].text.startswith("✅ "))

    def test_inventory_groups_items_into_semantic_folders(self):
        records = [
            {"item_id": 1, "item_name": "Коробки для футболок", "counted": False},
            {"item_id": 2, "item_name": "Открытка для заказа", "counted": True},
            {"item_id": 3, "item_name": "Нестандартный расходник", "counted": False},
        ]

        folders_keyboard = inventory_session_keyboard(records)
        folder_callbacks = [
            button.callback_data
            for row in folders_keyboard.inline_keyboard
            for button in row
        ]
        inserts_keyboard = inventory_session_keyboard(records, category_key="inserts")
        inserts_callbacks = [
            button.callback_data
            for row in inserts_keyboard.inline_keyboard
            for button in row
        ]

        self.assertIn("consinventory:category:boxes", folder_callbacks)
        self.assertIn("consinventory:category:inserts", folder_callbacks)
        self.assertIn("consinventory:category:other", folder_callbacks)
        self.assertNotIn("consinventoryitem:1", folder_callbacks)
        self.assertIn("consinventoryitem:2", inserts_callbacks)
        self.assertNotIn("consinventoryitem:1", inserts_callbacks)
        self.assertIn("consinventory:categories", inserts_callbacks)

    def test_default_inventory_folders_contain_at_most_fifteen_items(self):
        folder_counts = {}
        for item_name, _ in DEFAULT_CONSUMABLE_ITEMS:
            category_key = inventory_record_category({"item_name": item_name})
            folder_counts[category_key] = folder_counts.get(category_key, 0) + 1

        self.assertNotIn("other", folder_counts)
        self.assertLessEqual(max(folder_counts.values()), 15)

    async def test_completed_inventory_pdf_is_sent_to_consumables_topic(self):
        bot = SimpleNamespace(send_document=AsyncMock())
        context = SimpleNamespace(bot=bot)
        result = {
            "session": {"session_id": "inventory-test", "completed_by_name": "Сотрудник"},
            "records": [
                {
                    "item_name": "Коробки",
                    "system_quantity": 10,
                    "counted_quantity": 12,
                    "difference": 2,
                    "unit": "шт",
                    "counted_by_name": "Сотрудник",
                }
            ],
        }

        with (
            patch("modules.consumables.handlers.GROUP_CHAT_ID", "-100321"),
            patch("modules.consumables.handlers.CONSUMABLES_TOPIC_ID", "103"),
        ):
            status = await send_completed_inventory_pdf_to_topic(context, result)

        self.assertIn("отправлен", status)
        kwargs = bot.send_document.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100321)
        self.assertEqual(kwargs["message_thread_id"], 103)
        self.assertEqual(kwargs["filename"], "consumables_inventory_inventory-test.pdf")


if __name__ == "__main__":
    unittest.main()
