import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.common import build_warehouse_staff_mentions, mention_warehouse_staff


class WarehouseStaffMentionFormattingTests(unittest.TestCase):
    def test_mentions_only_active_warehouse_employees(self):
        employees = [
            {
                "employee_id": "1",
                "full_name": "Анна",
                "telegram_username": "@anna",
                "roles": ["warehouse_employee"],
                "is_active": True,
            },
            {
                "employee_id": "2",
                "full_name": "Борис",
                "telegram_user_id": "42",
                "roles": ["warehouse_employee", "admin"],
                "is_active": True,
            },
            {
                "employee_id": "3",
                "full_name": "Вера",
                "telegram_username": "vera",
                "roles": ["warehouse_employee"],
                "is_active": False,
            },
            {
                "employee_id": "4",
                "full_name": "Глеб",
                "telegram_username": "gleb",
                "roles": ["warehouse_manager"],
                "is_active": True,
            },
        ]

        text = build_warehouse_staff_mentions(employees)

        self.assertIn("@anna", text)
        self.assertIn('tg://user?id=42', text)
        self.assertNotIn("@vera", text)
        self.assertNotIn("@gleb", text)

    def test_employee_without_telegram_contact_is_reported(self):
        text = build_warehouse_staff_mentions([
            {
                "employee_id": "1",
                "full_name": "Иван & Пётр",
                "roles": ["warehouse_employee"],
                "is_active": True,
            }
        ])

        self.assertIn("не заполнены Telegram username и user ID", text)
        self.assertIn("Иван &amp; Пётр", text)


class WarehouseStaffCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_can_mention_warehouse_staff_in_group(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(type="supergroup"),
            effective_message=message,
            effective_user=SimpleNamespace(id=7, username="manager"),
        )
        context = SimpleNamespace()
        employees = [{
            "employee_id": "1",
            "full_name": "Анна",
            "telegram_username": "anna",
            "roles": ["warehouse_employee"],
            "is_active": True,
        }]

        with (
            patch(
                "handlers.common.find_employee_for_telegram_user",
                return_value={"roles": ["warehouse_manager"], "is_active": True},
            ),
            patch("handlers.common.get_employees", return_value=employees),
        ):
            await mention_warehouse_staff(update, context)

        self.assertIn("@anna", message.reply_text.await_args.args[0])

    async def test_regular_employee_cannot_mass_mention(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(type="group"),
            effective_message=message,
            effective_user=SimpleNamespace(id=8, username="employee"),
        )

        with patch(
            "handlers.common.find_employee_for_telegram_user",
            return_value={"roles": ["warehouse_employee"], "is_active": True},
        ):
            await mention_warehouse_staff(update, SimpleNamespace())

        self.assertIn("только руководители", message.reply_text.await_args.args[0])

    def test_bot_registers_warehouse_staff_command_and_alias(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text())
        command_handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CommandHandler"
        ]

        self.assertTrue(any(
            isinstance(handler.args[0], ast.List)
            and [item.value for item in handler.args[0].elts] == ["warehouse_staff", "sklad"]
            for handler in command_handlers
        ))


if __name__ == "__main__":
    unittest.main()
