import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.shipping.handlers import (
    SHIPPING_REVIEW,
    SHIPPING_SCAN,
    marking_code_received,
    no_code_selected,
    shipping_finish,
)
from tests.test_shipping_service import FULL_CODE, VALID_GTIN


def shipping_draft(codes=None):
    return {
        "order": {"id": "o1", "name": "mind-5098"},
        "units": [{
            "position_id": "p1",
            "assortment_id": "a1",
            "product_name": "Куртка",
            "gtin": VALID_GTIN,
            "price": "1000.00",
            "unit_number": 1,
            "position_quantity": 1,
        }],
        "unit_index": 0,
        "codes": list(codes or []),
        "unmarked_count": 0,
        "no_code_count": 0,
    }


class ShippingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_code_in_current_shipping_is_rejected(self):
        duplicate = "010460123456789321ABCDEFGHIJKLM"
        message = SimpleNamespace(text=FULL_CODE, reply_text=AsyncMock())
        context = SimpleNamespace(user_data={"shipping_draft": shipping_draft([
            {"short_code": duplicate},
        ])})

        state = await marking_code_received(SimpleNamespace(message=message), context)

        self.assertEqual(state, SHIPPING_SCAN)
        self.assertIn("уже использован", message.reply_text.await_args.args[0])
        self.assertEqual(context.user_data["shipping_draft"]["unit_index"], 0)

    async def test_no_code_skips_only_current_unit_and_opens_review(self):
        prompt = SimpleNamespace(edit_text=AsyncMock())
        query = SimpleNamespace(answer=AsyncMock(), message=prompt)
        context = SimpleNamespace(user_data={"shipping_draft": shipping_draft()})

        state = await no_code_selected(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, SHIPPING_REVIEW)
        self.assertEqual(context.user_data["shipping_draft"]["no_code_count"], 1)
        self.assertEqual(context.user_data["shipping_draft"]["unit_index"], 1)
        self.assertIn("Нажатий «Кода нет»: 1", prompt.edit_text.await_args.args[0])

    async def test_finish_saves_shipping_then_updates_moysklad_order(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        user = SimpleNamespace(id=7, full_name="Сотрудник", username="worker")
        draft = shipping_draft()
        draft["codes"] = [{"short_code": "010460123456789321ABCDEFGHIJKLM"}]
        context = SimpleNamespace(user_data={"shipping_draft": draft})
        client = object()

        with (
            patch("modules.shipping.handlers.save_shipping", return_value=1) as save,
            patch("modules.shipping.handlers.set_order_shipping_flag") as set_flag,
            patch("modules.shipping.handlers.build_moysklad_client", return_value=client),
            patch("modules.shipping.handlers._employee_name", return_value="Сотрудник"),
        ):
            state = await shipping_finish(
                SimpleNamespace(callback_query=query, effective_user=user),
                context,
            )

        self.assertEqual(state, ConversationHandler.END)
        save.assert_called_once()
        set_flag.assert_called_once_with(client, "o1")
        self.assertNotIn("shipping_draft", context.user_data)
        self.assertIn("Поле заказа обновлено: уедет", query.edit_message_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
