import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop

from core.keyboards import build_main_menu_keyboard
from core.module_control import MODULES, module_access_guard, module_for_callback
from modules.admin_panel.handlers import module_admin_keyboard


def callback_values(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


class ModuleCallbackTests(unittest.TestCase):
    def test_callback_routes_cover_main_and_nested_actions(self):
        cases = {
            "section:payroll": "payroll",
            "addpay:edit": "payroll",
            "schduty:day:1": "schedule",
            "consinventory:finish": "consumables",
            "marking:search": "marking",
            "empedit:42": "employees",
            "lmrecv:save": "receiving",
            "lmret:save": "returns",
        }
        for callback_data, expected in cases.items():
            with self.subTest(callback_data=callback_data):
                self.assertEqual(module_for_callback(callback_data), expected)

    def test_admin_and_main_menu_are_never_assigned_to_a_module(self):
        self.assertIsNone(module_for_callback("menu:start"))
        self.assertIsNone(module_for_callback("section:admin"))
        self.assertIsNone(module_for_callback("admin:module:returns"))


class ModuleKeyboardTests(unittest.TestCase):
    def test_disabled_modules_disappear_but_admin_panel_remains(self):
        enabled = set(MODULES) - {"employees"}
        callbacks = callback_values(
            build_main_menu_keyboard(
                manager=True,
                admin=True,
                enabled_modules=enabled,
            )
        )
        self.assertNotIn("section:employees", callbacks)
        self.assertEqual(callbacks, ["menu:start"])

    def test_admin_keyboard_exposes_every_registered_module(self):
        states = {module_key: module_key != "returns" for module_key in MODULES}
        markup = module_admin_keyboard(states)
        callbacks = callback_values(markup)
        for module_key in MODULES:
            self.assertIn(f"admin:module:{module_key}", callbacks)
        disabled_button = next(
            button
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data == "admin:module:returns"
        )
        self.assertTrue(disabled_button.text.startswith("⛔️"))


class ModuleGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_old_callback_is_blocked_and_dialog_is_cleared(self):
        query = SimpleNamespace(
            data="lmrecv:save",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_message=SimpleNamespace(text=None),
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=42, username="admin"),
        )
        context = SimpleNamespace(
            user_data={"draft": "value"},
            application=SimpleNamespace(handlers={}),
        )
        employee = {"roles": ["admin"], "role": "admin"}

        with (
            patch("core.module_control.is_module_enabled", return_value=False),
            patch("core.module_control.enabled_module_keys", return_value=set(MODULES) - {"receiving"}),
            patch("modules.payroll.google_sheets.find_employee_for_telegram_user", return_value=employee),
        ):
            with self.assertRaises(ApplicationHandlerStop):
                await module_access_guard(update, context)

        self.assertEqual(context.user_data, {})
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
