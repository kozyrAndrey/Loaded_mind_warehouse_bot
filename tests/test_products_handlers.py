import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.products.handlers import (
    PRODUCT_ADD_CONFIRM,
    PRODUCT_ADD_CHZ_NAME,
    PRODUCT_ADD_COLOR,
    PRODUCT_ADD_CUSTOM_SIZE,
    PRODUCT_ADD_GTIN,
    PRODUCT_ADD_MODEL_SELECT,
    PRODUCT_ADD_RULE_SELECT,
    PRODUCT_ADD_SIZES,
    PRODUCT_EDIT_FIELD,
    PRODUCT_EDIT_VALUE,
    product_add_confirmed,
    product_add_category_selected,
    product_add_custom_size_received,
    product_add_gtin_received,
    product_add_marking_selected,
    product_add_model_selected,
    product_add_size_selected,
    product_add_variant_from_edit,
    product_edit_field_selected,
    product_edit_product_selected,
    product_edit_save,
    product_edit_value_received,
)


class ProductWizardTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_category_is_selected_from_keyboard(self):
        query = SimpleNamespace(data="prodcat:hoodies", answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={"product_add": {}})

        with patch(
            "modules.products.handlers.CATEGORIES",
            {"hoodies": {"name": "Худи", "models": {}}},
        ):
            state = await product_add_category_selected(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, PRODUCT_ADD_MODEL_SELECT)
        self.assertEqual(context.user_data["product_add"]["category_name"], "Худи")
        self.assertIn("модель", query.edit_message_text.await_args.args[0])

    async def test_existing_model_can_be_selected_to_add_color(self):
        query = SimpleNamespace(
            data="prodaddmodel:culture_hoodie",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"product_add": {"category_id": "hoodies", "category_name": "Худи"}}
        )
        catalog = {
            "hoodies": {
                "name": "Худи",
                "models": {"culture_hoodie": {"name": "CULTURE HOODIE"}},
            }
        }

        with patch("modules.products.handlers.CATEGORIES", catalog):
            state = await product_add_model_selected(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, PRODUCT_ADD_COLOR)
        self.assertEqual(context.user_data["product_add"]["model_name"], "CULTURE HOODIE")
        self.assertIn("цвет", query.edit_message_text.await_args.args[0])

    async def test_custom_size_is_added_to_product(self):
        query = SimpleNamespace(
            data="prodsize:custom",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"product_add": {"sizes": ["S"]}})

        state = await product_add_size_selected(SimpleNamespace(callback_query=query), context)
        self.assertEqual(state, PRODUCT_ADD_CUSTOM_SIZE)

        message = SimpleNamespace(text="4XL", reply_text=AsyncMock())
        state = await product_add_custom_size_received(SimpleNamespace(message=message), context)

        self.assertEqual(state, PRODUCT_ADD_SIZES)
        self.assertEqual(context.user_data["product_add"]["sizes"], ["S", "4XL"])
    async def test_marked_product_requests_base_honest_sign_name(self):
        query = SimpleNamespace(data="prodmark:yes", answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={"product_add": {"sizes": ["S"]}})

        state = await product_add_marking_selected(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, PRODUCT_ADD_CHZ_NAME)
        self.assertTrue(context.user_data["product_add"]["is_marked"])
        self.assertIn("базовое название", query.edit_message_text.await_args.args[0])

    async def test_gtin_uses_base_name_with_size(self):
        message = SimpleNamespace(text="04670332744239", reply_text=AsyncMock())
        context = SimpleNamespace(
            user_data={
                "product_add": {
                    "sizes": ["S"],
                    "marking_index": 0,
                    "marking": [],
                    "chz_base_name": 'ХУДИ "TEST"',
                }
            }
        )

        with (
            patch("modules.products.handlers.get_honest_sign_product", return_value=None),
            patch("modules.products.handlers.prompt_product_rules", new=AsyncMock(return_value=PRODUCT_ADD_RULE_SELECT)),
        ):
            state = await product_add_gtin_received(SimpleNamespace(message=message), context)

        self.assertEqual(state, PRODUCT_ADD_RULE_SELECT)
        marking = context.user_data["product_add"]["marking"]
        self.assertEqual(marking[0]["size"], "S")
        self.assertEqual(marking[0]["honest_sign_name"], 'ХУДИ "TEST" S')

    async def test_confirmation_saves_catalog_marking_and_consumable_rule(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(
            user_data={
                "product_add": {
                    "category_name": "Худи",
                    "model_name": "TEST HOODIE",
                    "color": "BLACK",
                    "is_marked": True,
                    "marking": [
                        {
                            "gtin": "04670332744239",
                            "size": "S",
                            "honest_sign_name": "TEST S",
                        }
                    ],
                    "consumable_rules": [
                        {"item_id": 7, "item_name": "Пакет", "unit": "шт", "quantity": 1},
                    ],
                }
            }
        )
        product = {
            "product_id": "custom_test",
            "product_name": "TEST HOODIE BLACK",
            "category_name": "Худи",
            "model_name": "TEST HOODIE",
            "color": "BLACK",
        }

        with (
            patch("modules.products.handlers.add_custom_product", return_value=product),
            patch("modules.products.handlers.upsert_honest_sign_products") as save_marking,
            patch("modules.products.handlers.set_product_consumable_rule") as save_rule,
        ):
            state = await product_add_confirmed(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, ConversationHandler.END)
        save_marking.assert_called_once_with([
            {"gtin": "04670332744239", "size": "S", "honest_sign_name": "TEST S"}
        ])
        save_rule.assert_called_once_with("custom_test", "TEST HOODIE BLACK", 7, 1)
        self.assertNotIn("product_add", context.user_data)

    async def test_unmarked_product_is_saved_for_each_selected_size(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(
            user_data={
                "product_add": {
                    "category_name": "Худи",
                    "model_name": "TEST HOODIE",
                    "color": "BLACK",
                    "sizes": ["S", "M"],
                    "is_marked": False,
                    "consumable_rules": [],
                }
            }
        )
        product = {
            "product_id": "custom_test",
            "product_name": "TEST HOODIE BLACK",
            "category_name": "Худи",
            "model_name": "TEST HOODIE",
            "color": "BLACK",
        }

        with (
            patch("modules.products.handlers.add_custom_product", return_value=product),
            patch("modules.products.handlers.upsert_honest_sign_products") as save_marking,
        ):
            state = await product_add_confirmed(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, ConversationHandler.END)
        save_marking.assert_called_once_with(
            [
                {"gtin": None, "honest_sign_name": "TEST HOODIE BLACK", "size": "S"},
                {"gtin": None, "honest_sign_name": "TEST HOODIE BLACK", "size": "M"},
            ]
        )

    async def test_existing_product_can_be_selected_for_editing(self):
        query = SimpleNamespace(
            data="prodeditproduct:h001",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"product_edit": {}})
        product = {
            "product_id": "h001",
            "category_name": "Худи",
            "model_name": "TEST HOODIE",
            "color": "BLACK",
        }

        with patch("modules.products.handlers.get_catalog_product", return_value=product):
            state = await product_edit_product_selected(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, PRODUCT_EDIT_FIELD)
        self.assertEqual(context.user_data["product_edit"], product)
        self.assertIn("TEST HOODIE", query.edit_message_text.await_args.args[0])

    async def test_edit_field_is_changed_in_draft(self):
        query = SimpleNamespace(
            data="prodeditfield:color",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "product_edit": {
                    "product_id": "h001",
                    "category_name": "Худи",
                    "model_name": "TEST HOODIE",
                    "color": "BLACK",
                }
            }
        )

        state = await product_edit_field_selected(SimpleNamespace(callback_query=query), context)
        self.assertEqual(state, PRODUCT_EDIT_VALUE)

        message = SimpleNamespace(text="grey", reply_text=AsyncMock())
        state = await product_edit_value_received(SimpleNamespace(message=message), context)

        self.assertEqual(state, PRODUCT_EDIT_FIELD)
        self.assertEqual(context.user_data["product_edit"]["color"], "GREY")

    async def test_add_variant_from_edit_prefills_current_model(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={"product_edit": {"product_id": "h001"}})
        product = {
            "product_id": "h001",
            "category_id": "hoodies",
            "category_name": "Худи",
            "model_id": "culture_hoodie",
            "model_name": "CULTURE HOODIE",
            "color": "BLACK",
        }

        with patch("modules.products.handlers.get_catalog_product", return_value=product):
            state = await product_add_variant_from_edit(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, PRODUCT_ADD_COLOR)
        self.assertNotIn("product_edit", context.user_data)
        self.assertEqual(context.user_data["product_add"]["model_id"], "culture_hoodie")

    async def test_edit_confirmation_keeps_product_id_and_updates_rule_names(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(
            user_data={
                "product_edit": {
                    "product_id": "h001",
                    "category_name": "Худи",
                    "model_name": "NEW HOODIE",
                    "color": "BLACK",
                }
            }
        )
        updated = {
            "product_id": "h001",
            "product_name": "NEW HOODIE BLACK",
            "category_name": "Худи",
            "model_name": "NEW HOODIE",
            "color": "BLACK",
            "updated_products": [
                {"product_id": "h001", "product_name": "NEW HOODIE BLACK"},
                {"product_id": "h002", "product_name": "NEW HOODIE BLUE"},
            ],
        }

        with (
            patch("modules.products.handlers.update_catalog_product", return_value=updated) as save_product,
            patch("modules.products.handlers.update_product_consumable_rules_name") as update_rules,
        ):
            state = await product_edit_save(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(save_product.call_args.kwargs["product_id"], "h001")
        self.assertEqual(
            update_rules.call_args_list,
            [
                unittest.mock.call("h001", "NEW HOODIE BLACK"),
                unittest.mock.call("h002", "NEW HOODIE BLUE"),
            ],
        )
        self.assertNotIn("product_edit", context.user_data)


if __name__ == "__main__":
    unittest.main()
