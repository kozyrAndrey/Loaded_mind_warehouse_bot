import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.tasks.handlers import (
    TASK_EDIT_FIELD,
    return_to_task_edit,
    task_assignee_selected,
    task_deadline_selected,
    task_edit_description_received,
    task_select_keyboard,
    task_status_selected,
)


TASK = {
    "task_id": "task-1",
    "Дата": "24.07.2026",
    "Тип задачи": "warehouse",
    "Описание": "Проверить поставку",
}


class TaskEditHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_edit_task_list_is_grouped_by_type_and_sorted_by_deadline(self):
        tasks = [
            {"task_id": "g-none", "Тип задачи": "general", "Описание": "Без срока", "Дедлайн": ""},
            {"task_id": "w-late", "Тип задачи": "warehouse", "Описание": "Поздняя", "Дедлайн": "18:00"},
            {"task_id": "g-early", "Тип задачи": "general", "Описание": "Нескладская", "Дедлайн": "12:00"},
            {"task_id": "w-none", "Тип задачи": "warehouse", "Описание": "Складская без срока", "Дедлайн": ""},
            {"task_id": "w-early", "Тип задачи": "warehouse", "Описание": "Ранняя", "Дедлайн": "11:00"},
        ]

        keyboard = task_select_keyboard(tasks, "taskedit", edit_mode=True)
        task_buttons = [row[0] for row in keyboard.inline_keyboard[:-1]]

        self.assertEqual(
            [button.callback_data for button in task_buttons],
            [
                "taskedit:w-early",
                "taskedit:w-late",
                "taskedit:w-none",
                "taskedit:g-early",
                "taskedit:g-none",
            ],
        )
        self.assertTrue(task_buttons[0].text.endswith(" · 11:00"))
        self.assertTrue(task_buttons[2].text.endswith(" · без дедлайна"))

    def test_edit_task_button_preserves_deadline_after_truncation(self):
        keyboard = task_select_keyboard(
            [{
                "task_id": "task-1",
                "Тип задачи": "warehouse",
                "Описание": "Очень длинное описание задачи, которое не помещается в кнопку целиком",
                "Дедлайн": "18:00",
            }],
            "taskedit",
            edit_mode=True,
        )

        label = keyboard.inline_keyboard[0][0].text
        self.assertIn("...", label)
        self.assertTrue(label.endswith(" · 18:00"))
        self.assertLessEqual(len(label), 45)

    async def test_returns_to_same_task_after_description_update(self):
        message = SimpleNamespace(
            text="Новое описание",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(
            user_data={
                "edit_task_id": "task-1",
                "edit_task_date": "24.07.2026",
            }
        )

        with (
            patch("modules.tasks.handlers.update_task_fields") as update_fields,
            patch("modules.tasks.handlers.get_task_by_id", return_value=(0, TASK)),
            patch(
                "modules.tasks.handlers.refresh_existing_exports_for_date",
                new=AsyncMock(),
            ),
        ):
            state = await task_edit_description_received(update, context)

        self.assertEqual(state, TASK_EDIT_FIELD)
        self.assertEqual(context.user_data["edit_task_id"], "task-1")
        self.assertEqual(context.user_data["edit_task_date"], "24.07.2026")
        update_fields.assert_called_once_with("task-1", **{"Описание": "Новое описание"})
        self.assertIn("Что изменить дальше?", message.reply_text.await_args.args[0])
        callbacks = [
            button.callback_data
            for row in message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("taskeditfield:description", callbacks)
        self.assertIn("taskeditfield:assignees", callbacks)
        self.assertIn("taskeditfield:deadline", callbacks)
        self.assertIn("taskeditfield:status", callbacks)

    async def test_clears_only_temporary_selection_when_returning_to_edit(self):
        send_message = AsyncMock()
        context = SimpleNamespace(
            user_data={
                "edit_task_id": "task-1",
                "edit_task_date": "24.07.2026",
                "selected_employee_ids": ["employee-1"],
            }
        )

        with patch("modules.tasks.handlers.get_task_by_id", return_value=(0, TASK)):
            state = await return_to_task_edit(send_message, context, "Сохранено ✅")

        self.assertEqual(state, TASK_EDIT_FIELD)
        self.assertNotIn("selected_employee_ids", context.user_data)
        self.assertEqual(context.user_data["edit_task_id"], "task-1")
        self.assertEqual(context.user_data["edit_task_date"], "24.07.2026")

    async def test_returns_to_same_task_after_deadline_update(self):
        query = SimpleNamespace(
            data="taskdeadline:18:00",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "edit_task_id": "task-1",
                "edit_task_date": "24.07.2026",
            }
        )

        with (
            patch("modules.tasks.handlers.update_task_fields") as update_fields,
            patch("modules.tasks.handlers.get_task_by_id", return_value=(0, TASK)),
            patch(
                "modules.tasks.handlers.refresh_existing_exports_for_date",
                new=AsyncMock(),
            ),
        ):
            state = await task_deadline_selected(
                SimpleNamespace(callback_query=query),
                context,
            )

        self.assertEqual(state, TASK_EDIT_FIELD)
        self.assertEqual(context.user_data["edit_task_id"], "task-1")
        update_fields.assert_called_once_with("task-1", **{"Дедлайн": "18:00"})

    async def test_returns_to_same_task_after_status_update(self):
        query = SimpleNamespace(
            data="taskstatus:done",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "edit_task_id": "task-1",
                "edit_task_date": "24.07.2026",
            }
        )

        with (
            patch("modules.tasks.handlers.update_task_fields"),
            patch("modules.tasks.handlers.get_task_by_id", return_value=(0, TASK)),
            patch(
                "modules.tasks.handlers.refresh_existing_exports_for_date",
                new=AsyncMock(),
            ),
        ):
            state = await task_status_selected(
                SimpleNamespace(callback_query=query),
                context,
            )

        self.assertEqual(state, TASK_EDIT_FIELD)
        self.assertEqual(context.user_data["edit_task_id"], "task-1")

    async def test_returns_to_same_task_after_assignee_update(self):
        employee = {"employee_id": "employee-1", "full_name": "Иван"}
        query = SimpleNamespace(
            data="taskassignee:done",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "edit_task_id": "task-1",
                "edit_task_date": "24.07.2026",
                "selected_employee_ids": ["employee-1"],
            }
        )

        with (
            patch(
                "modules.tasks.handlers.get_working_employees_for_date",
                return_value=[employee],
            ),
            patch("modules.tasks.handlers.set_task_assignees") as set_assignees,
            patch("modules.tasks.handlers.get_task_by_id", return_value=(0, TASK)),
            patch(
                "modules.tasks.handlers.refresh_existing_exports_for_date",
                new=AsyncMock(),
            ),
        ):
            state = await task_assignee_selected(
                SimpleNamespace(callback_query=query),
                context,
            )

        self.assertEqual(state, TASK_EDIT_FIELD)
        self.assertEqual(context.user_data["edit_task_id"], "task-1")
        set_assignees.assert_called_once_with("task-1", [employee])


if __name__ == "__main__":
    unittest.main()
