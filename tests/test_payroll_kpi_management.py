import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.payroll.google_sheets import (
    KPI_HEADERS,
    append_kpi,
    get_kpi_by_id,
    get_kpi_items,
    set_kpi_active,
    sync_kpi_sheet,
    update_kpi_fields,
)
from modules.payroll.kpi_handlers import (
    KPI_ADD_RATE,
    KPI_MANAGE_MENU,
    kpi_add_rate_received,
    kpi_management_start,
)


class FakeKpiWorksheet:
    def __init__(self, rows=None):
        self.rows = [list(row) for row in (rows or [])]
        self.appended_rows = []
        self.updated_ranges = []

    def get_all_values(self):
        return [list(KPI_HEADERS)] + [list(row) for row in self.rows]

    def get_all_records(self, numericise_ignore=None):
        return [dict(zip(KPI_HEADERS, row)) for row in self.rows]

    def append_row(self, row):
        self.rows.append(list(row))

    def append_rows(self, rows):
        rows = [list(row) for row in rows]
        self.rows.extend(rows)
        self.appended_rows.extend(rows)

    def update(self, a1_range, values):
        match = re.match(r"^[A-Z]+(\d+):[A-Z]+\d+$", a1_range)
        if not match:
            raise AssertionError(f"Unexpected range: {a1_range}")
        row_index = int(match.group(1)) - 2
        self.rows[row_index] = list(values[0])
        self.updated_ranges.append(a1_range)


class PayrollKpiStorageTests(unittest.TestCase):
    def test_seed_sync_preserves_manager_changes_and_adds_only_missing_items(self):
        worksheet = FakeKpiWorksheet(
            [["kpi001", "Изменённое название", 99, "FALSE"]]
        )
        seed = [
            {"kpi_id": "kpi001", "name": "Название из кода", "rate": 15, "is_active": True},
            {"kpi_id": "kpi002", "name": "Новая стартовая позиция", "rate": 20, "is_active": True},
        ]

        with patch("modules.payroll.google_sheets.PAYROLL_KPI", seed):
            sync_kpi_sheet(worksheet)

        self.assertEqual(
            worksheet.rows[0],
            ["kpi001", "Изменённое название", 99, "FALSE"],
        )
        self.assertEqual(
            worksheet.appended_rows,
            [["kpi002", "Новая стартовая позиция", 20, "TRUE"]],
        )
        self.assertEqual(worksheet.updated_ranges, [])

    def test_add_edit_and_soft_delete_kpi(self):
        worksheet = FakeKpiWorksheet()
        with (
            patch("modules.payroll.google_sheets.get_worksheet", return_value=worksheet),
            patch("modules.payroll.google_sheets.generate_id", return_value="kpi_custom"),
        ):
            created = append_kpi("Сборка заказов", "12,5")
            updated = update_kpi_fields(
                created["kpi_id"],
                name="Сборка и проверка заказов",
                rate=18,
            )
            deleted = set_kpi_active(created["kpi_id"], False)

            self.assertEqual(created["rate"], 12.5)
            self.assertEqual(updated["name"], "Сборка и проверка заказов")
            self.assertEqual(updated["rate"], 18)
            self.assertFalse(deleted["is_active"])
            self.assertEqual(get_kpi_items(active_only=True), [])
            self.assertEqual(get_kpi_by_id("kpi_custom"), deleted)

    def test_duplicate_name_is_rejected_case_insensitively(self):
        worksheet = FakeKpiWorksheet(
            [["kpi001", "Упаковка", 15, "TRUE"]]
        )
        with patch("modules.payroll.google_sheets.get_worksheet", return_value=worksheet):
            with self.assertRaisesRegex(ValueError, "уже существует"):
                append_kpi("  упаковка  ", 20)


class PayrollKpiHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_manager_can_open_management(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1))
        context = SimpleNamespace(user_data={})

        with patch("modules.payroll.kpi_handlers.ensure_manager", return_value=False):
            state = await kpi_management_start(update, context)

        self.assertEqual(state, ConversationHandler.END)
        query.edit_message_text.assert_awaited_once_with(
            "⛔️ Управление KPI доступно только руководителям."
        )

    async def test_added_item_returns_to_working_management_menu(self):
        message = SimpleNamespace(text="25,5", reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
        context = SimpleNamespace(user_data={"kpi_add": {"name": "Комплектация"}})
        item = {
            "kpi_id": "kpi_custom",
            "name": "Комплектация",
            "rate": 25.5,
            "is_active": True,
        }

        with (
            patch("modules.payroll.kpi_handlers.ensure_manager", return_value=True),
            patch("modules.payroll.kpi_handlers.append_kpi", return_value=item) as append,
        ):
            state = await kpi_add_rate_received(update, context)

        self.assertEqual(state, KPI_MANAGE_MENU)
        self.assertNotEqual(state, KPI_ADD_RATE)
        self.assertNotIn("kpi_add", context.user_data)
        append.assert_called_once_with("Комплектация", 25.5)
        reply_markup = message.reply_text.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in reply_markup.inline_keyboard
            for button in row
        ]
        self.assertIn("kpimgr:add", callbacks)


if __name__ == "__main__":
    unittest.main()
