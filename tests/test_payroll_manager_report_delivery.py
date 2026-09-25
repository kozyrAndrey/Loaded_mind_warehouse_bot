import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.payroll.handlers import (
    finish_manager_only_report,
    finish_create_report,
    finish_edit_report,
    payroll_main_keyboard,
    remember_manager_wizard_state,
    restore_manager_wizard_state,
    send_daily_report_to_private_target,
    validate_manager_wizard_value,
)


def warehouse_manager():
    return {
        "employee_id": "emp_manager",
        "full_name": "Руководитель склада",
        "telegram_user_id": "42",
        "role": "warehouse_manager",
        "hourly_rate": 500,
    }


def report_model():
    return {
        "date": "16.07.2026",
        "employee": warehouse_manager(),
        "interval": "10:00-19:00",
        "hours": 8,
        "tasks": "Управление складом",
        "kpi_items": [],
        "manager_report": None,
    }


class PayrollManagerReportDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        summary_patch = patch("modules.payroll.handlers.refresh_daily_summary", new_callable=AsyncMock)
        self.refresh_summary = summary_patch.start()
        self.addCleanup(summary_patch.stop)
        period_patch = patch("modules.payroll.google_sheets.get_period_for_date", return_value={"payment_mode": "hourly"})
        period_patch.start()
        self.addCleanup(period_patch.stop)

    def test_manager_menu_has_separate_management_report_button(self):
        keyboard = payroll_main_keyboard(manager=True, warehouse_manager=True)
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("pay:manager_report", callbacks)

    def test_manager_report_validates_numbers_and_day_score(self):
        self.assertEqual(validate_manager_wizard_value("sent_orders", "12"), ("12", None))
        self.assertIsNotNone(validate_manager_wizard_value("sent_orders", "много")[1])
        self.assertEqual(validate_manager_wizard_value("day_score", "10"), ("10", None))
        self.assertIsNotNone(validate_manager_wizard_value("day_score", "11")[1])

    def test_manager_wizard_can_restore_previous_step(self):
        context = SimpleNamespace(
            user_data={
                "manager_wizard_step": "sent_orders",
                "manager_wizard_values": {},
                "manager_wizard_history": [],
            }
        )
        remember_manager_wizard_state(context)
        context.user_data["manager_wizard_step"] = "accepted_goods"
        context.user_data["manager_wizard_values"]["sent_orders"] = "5"
        self.assertTrue(restore_manager_wizard_state(context))
        self.assertEqual(context.user_data["manager_wizard_step"], "sent_orders")
        self.assertEqual(context.user_data["manager_wizard_values"], {})

    async def test_private_callback_message_is_reused_for_report(self):
        edited_message = SimpleNamespace(chat_id=42, message_id=101)
        query = SimpleNamespace(
            message=SimpleNamespace(chat_id=42, message_id=101),
            edit_message_text=AsyncMock(return_value=edited_message),
        )

        data = await send_daily_report_to_private_target(query, report_model(), "menu")

        self.assertEqual(data, {"chat_id": 42, "thread_id": "", "message_id": 101})
        query.edit_message_text.assert_awaited_once()
        self.assertEqual(query.edit_message_text.await_args.kwargs["reply_markup"], "menu")

    async def test_own_manager_report_produces_only_one_bot_message(self):
        sent_message = SimpleNamespace(chat_id=42, message_id=202)
        target = SimpleNamespace(reply_text=AsyncMock(return_value=sent_message))
        context = SimpleNamespace(
            user_data={
                "employee_id": "emp_manager",
                "report_date": "16.07.2026",
                "interval": "10:00-19:00",
                "hours": 8,
                "report_period": {"payment_mode": "hourly"},
                "tasks": "Управление складом",
                "kpi_items": [],
            },
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        telegram_user = SimpleNamespace(id=42)

        with (
            patch("modules.payroll.handlers.get_employee_by_id", return_value=warehouse_manager()),
            patch(
                "modules.payroll.google_sheets.get_employee_by_id",
                return_value=warehouse_manager(),
            ),
            patch(
                "modules.payroll.handlers.find_employee_for_telegram_user",
                return_value=warehouse_manager(),
            ),
            patch("modules.payroll.handlers.append_daily_report") as append_report,
        ):
            state = await finish_create_report(target, context, telegram_user)

        self.assertEqual(state, ConversationHandler.END)
        target.reply_text.assert_awaited_once()
        context.bot.send_message.assert_not_awaited()
        self.assertEqual(context.user_data, {})
        self.refresh_summary.assert_awaited_once_with(context, "16.07.2026")
        telegram_data = append_report.call_args.args[-1]
        self.assertEqual(
            telegram_data,
            {"chat_id": 42, "thread_id": "", "message_id": 202},
        )

    async def test_manager_only_report_refreshes_brand_daily_summary(self):
        target = SimpleNamespace(reply_text=AsyncMock())
        context = SimpleNamespace(
            user_data={
                "employee_id": "emp_manager",
                "report_date": "16.07.2026",
                "manager_report": {"volumes": "готово"},
            }
        )
        with (
            patch("modules.payroll.handlers.get_employee_by_id", return_value=warehouse_manager()),
            patch("modules.payroll.handlers.send_manager_report_to_recipients", new=AsyncMock(return_value=[])),
            patch("modules.payroll.handlers.append_manager_report"),
        ):
            state = await finish_manager_only_report(target, context, SimpleNamespace(id=42))

        self.assertEqual(state, ConversationHandler.END)
        self.refresh_summary.assert_awaited_once_with(context, "16.07.2026")

    async def test_working_manager_gets_two_separate_reports_and_brand_manager_copy(self):
        sent_message = SimpleNamespace(chat_id=42, message_id=202)
        target = SimpleNamespace(reply_text=AsyncMock(return_value=sent_message))
        manager_data = {
            "volumes": "объемы",
            "speed": "скорость",
            "errors": "ошибки",
            "problems": "проблемы",
            "personal_plan": "личный план",
            "warehouse_plan": "складской план",
            "completion": "оценка",
        }
        context = SimpleNamespace(
            user_data={
                "employee_id": "emp_manager",
                "report_date": "16.07.2026",
                "interval": "10:00-19:00",
                "hours": 8,
                "report_period": {"payment_mode": "hourly"},
                "tasks": "Складские задачи",
                "kpi_items": [],
                "manager_report": manager_data,
            },
            bot=SimpleNamespace(
                send_message=AsyncMock(
                    side_effect=[
                        SimpleNamespace(message_id=301),
                        SimpleNamespace(message_id=302),
                    ]
                )
            ),
        )
        telegram_user = SimpleNamespace(id=42)
        brand_manager = {
            "employee_id": "brand",
            "full_name": "Руководитель бренда",
            "telegram_user_id": "77",
            "role": "brand_manager",
        }

        with (
            patch("modules.payroll.handlers.get_employee_by_id", return_value=warehouse_manager()),
            patch("modules.payroll.handlers.find_employee_for_telegram_user", return_value=warehouse_manager()),
            patch("modules.payroll.handlers.get_brand_managers", return_value=[brand_manager]),
            patch("modules.payroll.handlers.append_daily_report"),
            patch("modules.payroll.handlers.append_manager_report") as append_manager,
        ):
            state = await finish_create_report(target, context, telegram_user)

        self.assertEqual(state, ConversationHandler.END)
        warehouse_text = target.reply_text.await_args.args[0]
        self.assertIn("Складские задачи", warehouse_text)
        self.assertNotIn("Руководительский отчет", warehouse_text)
        self.assertEqual(context.bot.send_message.await_count, 2)
        manager_call, brand_call = context.bot.send_message.await_args_list
        self.assertEqual(manager_call.kwargs["chat_id"], 42)
        self.assertEqual(brand_call.kwargs["chat_id"], 77)
        self.assertIn("Руководительский отчет", manager_call.kwargs["text"])
        append_manager.assert_called_once()
        self.refresh_summary.assert_awaited_once_with(context, "16.07.2026")

    async def test_own_edited_report_reuses_current_bot_message(self):
        edited_message = SimpleNamespace(chat_id=42, message_id=303)
        query = SimpleNamespace(
            message=SimpleNamespace(chat_id=42, message_id=303),
            edit_message_text=AsyncMock(return_value=edited_message),
        )
        report_data = {
            "report_id": "report_1",
            "Дата": "16.07.2026",
            "employee_id": "emp_manager",
            "ФИО": "Руководитель склада",
            "telegram_user_id": "42",
            "Рабочий промежуток": "10:00-19:00",
            "Отработано часов": 8,
            "Задачи": "Управление складом",
            "KPI данные": "[]",
            "KPI сумма": 0,
            "telegram_chat_id": "42",
            "telegram_message_id": "100",
        }
        context = SimpleNamespace(
            user_data={"edit_row_index": 2, "edit_report_data": report_data},
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        telegram_user = SimpleNamespace(id=42)

        with (
            patch("modules.payroll.handlers.get_employee_by_id", return_value=warehouse_manager()),
            patch(
                "modules.payroll.google_sheets.get_employee_by_id",
                return_value=warehouse_manager(),
            ),
            patch(
                "modules.payroll.handlers.find_employee_for_telegram_user",
                return_value=warehouse_manager(),
            ),
            patch(
                "modules.payroll.handlers.delete_old_report_message",
                new=AsyncMock(return_value=True),
            ),
            patch("modules.payroll.handlers.update_daily_report") as update_report,
        ):
            state = await finish_edit_report(query, context, telegram_user)

        self.assertEqual(state, ConversationHandler.END)
        query.edit_message_text.assert_awaited_once()
        context.bot.send_message.assert_not_awaited()
        self.assertEqual(context.user_data, {})
        saved_report = update_report.call_args.args[1]
        self.assertEqual(saved_report["telegram_chat_id"], 42)
        self.assertEqual(saved_report["telegram_message_id"], 303)
        self.refresh_summary.assert_awaited_once_with(context, "16.07.2026")


if __name__ == "__main__":
    unittest.main()
