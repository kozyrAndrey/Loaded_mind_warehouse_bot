from datetime import date
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from modules.tasks.handlers import (
    daily_staff_job,
    export_warehouse_tasks_for_date,
    send_or_edit_task_message,
)


class MorningWarehouseRoutingTests(IsolatedAsyncioTestCase):
    async def test_existing_export_in_old_topic_is_recreated_in_warehouse_topic(self):
        sent_message = SimpleNamespace(message_id=99)
        context = SimpleNamespace(
            bot=SimpleNamespace(
                edit_message_text=AsyncMock(),
                send_message=AsyncMock(return_value=sent_message),
            )
        )
        day = date(2026, 10, 16)

        with (
            patch(
                "modules.tasks.handlers.get_task_export",
                return_value={
                    "chat_id": "-100123",
                    "thread_id": "111",
                    "message_id": "55",
                },
            ),
            patch("modules.tasks.handlers.upsert_task_export") as save_export,
        ):
            status = await send_or_edit_task_message(
                context,
                day,
                "warehouse",
                "-100123",
                "456",
                "Задачи",
            )

        self.assertEqual(status, "отправлено")
        context.bot.edit_message_text.assert_not_awaited()
        context.bot.send_message.assert_awaited_once_with(
            chat_id=-100123,
            message_thread_id=456,
            text="Задачи",
        )
        save_export.assert_called_once_with(day, "warehouse", "-100123", "456", 99)

    async def test_staff_message_is_sent_to_warehouse_topic(self):
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with (
            patch("modules.tasks.handlers.GROUP_CHAT_ID", "-100123"),
            patch("modules.tasks.handlers.WAREHOUSE_MAIN_TOPIC", "456"),
            patch("modules.tasks.handlers.is_module_enabled", return_value=True),
            patch("modules.tasks.handlers.today_msk", return_value=date(2026, 10, 16)),
            patch(
                "modules.tasks.handlers.get_schedule_matrix",
                return_value=([], [], {}, {}),
            ),
            patch(
                "modules.tasks.handlers.format_daily_staff_message",
                return_value="Сотрудники на смене",
            ),
        ):
            await daily_staff_job(context)

        context.bot.send_message.assert_awaited_once_with(
            chat_id=-100123,
            message_thread_id=456,
            text="Сотрудники на смене",
        )

    async def test_warehouse_tasks_are_sent_to_warehouse_topic(self):
        context = SimpleNamespace(bot=SimpleNamespace())
        day = date(2026, 10, 16)

        with (
            patch("modules.tasks.handlers.GROUP_CHAT_ID", "-100123"),
            patch("modules.tasks.handlers.WAREHOUSE_MAIN_TOPIC", "456"),
            patch("modules.tasks.handlers.materialize_templates_for_date"),
            patch("modules.tasks.handlers.assign_working_employees_to_unassigned_template_tasks"),
            patch("modules.tasks.handlers.get_tasks_by_date", return_value=[]),
            patch("modules.tasks.handlers.format_warehouse_tasks_message", return_value="Задачи"),
            patch("modules.tasks.handlers.warehouse_tasks_inline_keyboard", return_value="keyboard"),
            patch(
                "modules.tasks.handlers.send_or_edit_task_message",
                new=AsyncMock(return_value="отправлено"),
            ) as send_message,
        ):
            status = await export_warehouse_tasks_for_date(context, day)

        self.assertEqual(status, "отправлено")
        send_message.assert_awaited_once_with(
            context,
            day,
            "warehouse",
            "-100123",
            "456",
            "Задачи",
            "keyboard",
        )
