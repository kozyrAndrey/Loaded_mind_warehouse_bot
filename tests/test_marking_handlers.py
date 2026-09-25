import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from core.keyboards import build_marking_menu_keyboard
from modules.marking.handlers import (
    MARKING_DISCOUNTS,
    MARKING_DOCUMENT_NAME,
    MARKING_DUPLICATE_CHZ_CODE,
    MARKING_STOCK_CODES_DOCUMENT_NAME,
    MARKING_UNMARKED_CONFIRM,
    MARKING_UNMARKED_PRODUCT,
    MARKING_UNMARKED_QUANTITY,
    TREND_PRICE_TYPE_WITH_DISCOUNTS,
    TREND_PRICE_TYPE_WITHOUT_DISCOUNTS,
    trend_export_back,
    trend_export_discounts_received,
    trend_export_document_received,
    trend_export_start,
    duplicate_chz_start,
    stock_codes_export_start,
    trend_unmarked_quantity_received,
    catalog_start,
)


class MarkingHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_employee_menu_contains_all_actions_except_catalog(self):
        callbacks = [
            button.callback_data
            for row in build_marking_menu_keyboard(manager=False).inline_keyboard
            for button in row
        ]

        self.assertIn("marking:search", callbacks)
        self.assertIn("marking:duplicate_chz", callbacks)
        self.assertIn("marking:duplicate_chz_75x120", callbacks)
        self.assertNotIn("marking:catalog", callbacks)
        self.assertNotIn("marking:trend_export", callbacks)

    async def test_large_duplicate_stores_75x120_format(self):
        query = SimpleNamespace(
            data="marking:duplicate_chz_75x120",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})

        state = await duplicate_chz_start(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, MARKING_DUPLICATE_CHZ_CODE)
        self.assertEqual(context.user_data["marking_duplicate_chz_size"], "75x120")
        self.assertIn("75×120", query.edit_message_text.await_args.args[0])

    async def test_catalog_remains_manager_only(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)

        with patch("modules.marking.handlers.ensure_manager", return_value=False):
            state = await catalog_start(update, SimpleNamespace(user_data={}))

        self.assertEqual(state, ConversationHandler.END)
        self.assertIn("только руководителям", query.edit_message_text.await_args.args[0])

    async def test_stock_codes_export_is_available_without_manager_role(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)

        state = await stock_codes_export_start(update, SimpleNamespace(user_data={}))

        self.assertEqual(state, MARKING_STOCK_CODES_DOCUMENT_NAME)
        self.assertIn("Вывод из оборота", query.edit_message_text.await_args.args[0])

    async def test_export_starts_by_asking_about_discounts(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={"marking_trend_price_type": "Устаревшее значение"})

        with patch("modules.marking.handlers.ensure_manager", return_value=True):
            state = await trend_export_start(update, context)

        self.assertEqual(state, MARKING_DISCOUNTS)
        self.assertNotIn("marking_trend_price_type", context.user_data)
        self.assertEqual(query.edit_message_text.await_args.args[0], "Есть ли сейчас скидки?")
        callbacks = [
            button.callback_data
            for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("marking:trend_export:discounts:yes", callbacks)
        self.assertIn("marking:trend_export:discounts:no", callbacks)

    async def test_yes_selects_old_price(self):
        await self.assert_price_type_selected(
            callback_data="marking:trend_export:discounts:yes",
            expected=TREND_PRICE_TYPE_WITH_DISCOUNTS,
        )

    async def test_no_selects_sale_price(self):
        await self.assert_price_type_selected(
            callback_data="marking:trend_export:discounts:no",
            expected=TREND_PRICE_TYPE_WITHOUT_DISCOUNTS,
        )

    async def assert_price_type_selected(self, callback_data, expected):
        query = SimpleNamespace(
            data=callback_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        with patch("modules.marking.handlers.ensure_manager", return_value=True):
            state = await trend_export_discounts_received(update, context)

        self.assertEqual(state, MARKING_DOCUMENT_NAME)
        self.assertEqual(context.user_data["marking_trend_price_type"], expected)
        self.assertIn(f"цена «{expected}»", query.edit_message_text.await_args.args[0])

    async def test_document_name_is_saved_before_unmarked_question(self):
        message = SimpleNamespace(
            text="Вывод из оборота № 15",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={"marking_trend_price_type": "Цена продажи"}
        )

        with patch("modules.marking.handlers.ensure_manager", return_value=True):
            state = await trend_export_document_received(update, context)

        self.assertEqual(state, MARKING_UNMARKED_CONFIRM)
        self.assertEqual(
            context.user_data["marking_trend_document_name"],
            "Вывод из оборота № 15",
        )
        self.assertEqual(context.user_data["marking_trend_unmarked_quantities"], {})
        self.assertIn(
            "Есть ли немаркируемая продукция",
            message.reply_text.await_args.args[0],
        )

    async def test_quantity_is_saved_and_returns_to_product_choice(self):
        product = {
            "id": 7,
            "gtin": None,
            "honest_sign_name": 'СУМКА "MODEL" ЧЕРНАЯ',
            "size": "250x190x100",
        }
        message = SimpleNamespace(text="4", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={"marking_trend_pending_unmarked_id": "7"}
        )

        with (
            patch(
                "modules.marking.handlers.get_unmarked_product",
                return_value=product,
            ),
            patch(
                "modules.marking.handlers.list_unmarked_products",
                return_value=[product],
            ),
        ):
            state = await trend_unmarked_quantity_received(update, context)

        self.assertEqual(state, MARKING_UNMARKED_PRODUCT)
        self.assertEqual(
            context.user_data["marking_trend_unmarked_quantities"],
            {"7": 4},
        )
        callbacks = [
            button.callback_data
            for row in message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("marking:trend:unmarked:done", callbacks)
        self.assertIn("marking:trend:back:unmarked", callbacks)

    async def test_back_navigation_returns_to_previous_export_steps(self):
        cases = {
            "marking:trend:back:discounts": MARKING_DISCOUNTS,
            "marking:trend:back:document": MARKING_DOCUMENT_NAME,
            "marking:trend:back:unmarked": MARKING_UNMARKED_CONFIRM,
            "marking:trend:back:products": MARKING_UNMARKED_PRODUCT,
        }
        for callback_data, expected_state in cases.items():
            with self.subTest(callback_data=callback_data):
                query = SimpleNamespace(
                    data=callback_data,
                    answer=AsyncMock(),
                    edit_message_text=AsyncMock(),
                )
                update = SimpleNamespace(callback_query=query)
                context = SimpleNamespace(
                    user_data={
                        "marking_trend_price_type": "Цена продажи",
                        "marking_trend_pending_unmarked_id": "7",
                    }
                )
                with patch(
                    "modules.marking.handlers.list_unmarked_products",
                    return_value=[],
                ):
                    state = await trend_export_back(update, context)

                self.assertEqual(state, expected_state)
                if callback_data.endswith(":products"):
                    self.assertNotIn(
                        "marking_trend_pending_unmarked_id",
                        context.user_data,
                    )

    async def test_invalid_quantity_stays_on_quantity_step(self):
        message = SimpleNamespace(text="0", reply_text=AsyncMock())
        state = await trend_unmarked_quantity_received(
            SimpleNamespace(message=message),
            SimpleNamespace(user_data={}),
        )

        self.assertEqual(state, MARKING_UNMARKED_QUANTITY)
        self.assertIn("положительным целым", message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
