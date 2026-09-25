import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.tasks.handlers import (
    export_general_tasks_for_date,
    personal_general_tasks,
    task_is_assigned_to_employee,
)


class TaskPersonalDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.employee = {
            "employee_id": "emp004",
            "telegram_username": "fadexdf",
            "telegram_user_id": "444",
        }

    def test_assignment_matches_employee_id_or_exact_username(self):
        self.assertTrue(task_is_assigned_to_employee({"Исполнители ID": "emp001,emp004"}, self.employee))
        self.assertTrue(task_is_assigned_to_employee({"Исполнители": "@other, @fadexdf"}, self.employee))
        self.assertFalse(task_is_assigned_to_employee({"Исполнители": "@fadexdf_extra"}, self.employee))

    def test_personal_list_contains_only_assigned_general_tasks(self):
        tasks = [
            {"task_id": "mine", "Тип задачи": "general", "Исполнители ID": "emp004"},
            {"task_id": "other", "Тип задачи": "general", "Исполнители ID": "emp999"},
            {"task_id": "warehouse", "Тип задачи": "warehouse", "Исполнители ID": "emp004"},
        ]

        self.assertEqual(
            [task["task_id"] for task in personal_general_tasks(tasks, self.employee)],
            ["mine"],
        )

    async def test_export_sends_employee_only_their_general_tasks(self):
        tasks = [
            {
                "task_id": "mine",
                "Тип задачи": "general",
                "Описание": "Моя задача",
                "Исполнители ID": "emp004",
                "Статус": "active",
                "Дедлайн": "18:00",
            },
            {
                "task_id": "other",
                "Тип задачи": "general",
                "Описание": "Чужая задача",
                "Исполнители ID": "emp999",
                "Статус": "active",
                "Дедлайн": "",
            },
        ]
        manager = {"telegram_user_id": "777"}
        sender = AsyncMock(return_value="отправлено")

        with (
            patch("modules.tasks.handlers.materialize_templates_for_date"),
            patch("modules.tasks.handlers.get_tasks_by_date", return_value=tasks),
            patch("modules.tasks.handlers.get_warehouse_managers", return_value=[manager]),
            patch("modules.tasks.handlers.personal_general_task_recipients", return_value=[self.employee]),
            patch("modules.tasks.handlers.send_or_edit_task_message", sender),
        ):
            status = await export_general_tasks_for_date(SimpleNamespace(), date(2026, 9, 12))

        self.assertEqual(sender.await_count, 2)
        personal_call = sender.await_args_list[1].args
        self.assertEqual(personal_call[2], "general_assignee:emp004")
        self.assertEqual(personal_call[3], "444")
        self.assertIn("Моя задача", personal_call[5])
        self.assertNotIn("Чужая задача", personal_call[5])
        self.assertIn("@fadexdf: отправлено", status)

    async def test_export_skips_personal_message_when_no_tasks_are_assigned(self):
        tasks = [{
            "task_id": "other",
            "Тип задачи": "general",
            "Описание": "Чужая задача",
            "Исполнители ID": "emp999",
            "Статус": "active",
            "Дедлайн": "",
        }]
        sender = AsyncMock(return_value="отправлено")

        with (
            patch("modules.tasks.handlers.materialize_templates_for_date"),
            patch("modules.tasks.handlers.get_tasks_by_date", return_value=tasks),
            patch("modules.tasks.handlers.get_warehouse_managers", return_value=[]),
            patch("modules.tasks.handlers.personal_general_task_recipients", return_value=[self.employee]),
            patch("modules.tasks.handlers.send_or_edit_task_message", sender),
        ):
            await export_general_tasks_for_date(SimpleNamespace(), date(2026, 9, 12))

        sender.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
