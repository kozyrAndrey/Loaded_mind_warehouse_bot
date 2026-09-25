import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.payroll.handlers import (
    EDIT_FIELD,
    edit_field_keyboard,
    edit_report_date_selected,
    edit_report_delete_confirmed,
)


class DailyReportManagementTests(unittest.IsolatedAsyncioTestCase):
    def test_edit_menu_contains_date_and_delete(self):
        callbacks = {
            button.callback_data
            for row in edit_field_keyboard().inline_keyboard
            for button in row
        }
        self.assertIn("editfield:date", callbacks)
        self.assertIn("editfield:delete", callbacks)

    async def test_manager_can_change_report_date_when_target_has_no_duplicate(self):
        query = SimpleNamespace(
            data="editnewdate:08.09.2026",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        report = {"employee_id": "employee", "Дата": "07.09.2026"}
        context = SimpleNamespace(user_data={"edit_report_data": report})
        with (
            patch("modules.payroll.handlers.find_report_row", return_value=(None, None)),
            patch("modules.payroll.handlers.get_period_for_date", return_value={"payment_mode": "hourly"}),
        ):
            state = await edit_report_date_selected(update, context)

        self.assertEqual(state, EDIT_FIELD)
        self.assertEqual(report["Дата"], "08.09.2026")

    async def test_delete_removes_saved_report_message_and_refreshes_summary(self):
        query = SimpleNamespace(
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            user_data={
                "edit_row_index": 2,
                "edit_report_data": {"Дата": "07.09.2026", "employee_id": "employee"},
            },
            bot=SimpleNamespace(),
        )
        deleted = {"date": "07.09.2026"}
        with (
            patch("modules.payroll.handlers.report_data_to_model", return_value=deleted),
            patch("modules.payroll.handlers.delete_old_report_message", new=AsyncMock(return_value=True)) as delete_message,
            patch("modules.payroll.handlers.delete_daily_report", return_value=deleted) as delete_report,
            patch("modules.payroll.handlers.refresh_daily_summary", new=AsyncMock()) as refresh,
            patch("modules.payroll.handlers.current_employee_or_none", return_value={"role": "warehouse_manager"}),
        ):
            state = await edit_report_delete_confirmed(update, context)

        self.assertEqual(state, ConversationHandler.END)
        delete_message.assert_awaited_once()
        delete_report.assert_called_once_with(2)
        refresh.assert_awaited_once_with(context, "07.09.2026")
