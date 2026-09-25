import json
import unittest
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram.error import TimedOut

from modules.payroll import daily_summary as delivery
from modules.payroll import report_automation as automation
from modules.payroll.handlers import (
    ask_next_manager_step, manager_report_suggestion, manager_report_wizard_callback,
    manager_report_wizard_text_received, validate_manager_wizard_value,
)
from modules.storage.postgres import Base


REPORT_DATE = "05.09.2026"
DAY = date(2026, 9, 5)


def employee(employee_id, role="warehouse_employee", telegram_id=""):
    return {"employee_id": employee_id, "full_name": employee_id,
            "role": role, "telegram_user_id": telegram_id}


def row(employee_id, *kpis, report_date=REPORT_DATE):
    return {"employee_id": employee_id, "ФИО": employee_id, "Дата": report_date,
            "Рабочий промежуток": "10:00–19:00", "Отработано часов": 8,
            "Задачи": "Работа на складе", "KPI данные": json.dumps(list(kpis))}


def kpi(kpi_id, qty, name="Отправка"):
    return {"kpi_id": kpi_id, "name": name, "qty": qty, "rate": 999, "sum": 999999}


class ReportAggregationTests(unittest.TestCase):
    def test_quantities_packaging_and_custom_kpis_do_not_sum_salary(self):
        rows = [row("a", kpi("kpi007", 10), kpi("kpi001", 15, "Упаковка 1 слой")),
                row("b", kpi("kpi007", 7), kpi("kpi006", 3, "Упаковка Ремни"),
                    kpi("custom", "2.5", "Упаковка новая"), kpi("kpi008", 30, "Сток"),
                    kpi("kpi011", 4, "Возврат"), kpi("other", 6, "Новое KPI"))]
        self.assertEqual(automation.volume_values(rows), {
            "sent_orders": "17", "posted_goods": "20.5", "stock_shipments": "30", "posted_returns": "4",
        })
        self.assertEqual(automation.total_kpis(rows)["other"]["qty"], 6)

    def test_date_filter_and_latest_employee_report_prevent_double_count(self):
        reports = automation.latest_reports([
            row("a", kpi("kpi007", 3)), row("a", kpi("kpi007", 9)),
            row("b", kpi("kpi007", 100), report_date="04.09.2026"),
        ], REPORT_DATE)
        self.assertEqual(automation.volume_values(reports.values())["sent_orders"], "9")

    def test_draft_replaces_saved_report_and_missing_roster_is_explicit(self):
        with (
            patch.object(automation, "reports_for_date", return_value={"manager": row("manager", kpi("kpi007", 1))}),
            patch.object(automation, "get_working_employees_for_date", return_value=[employee("manager"), employee("a")]),
        ):
            day = automation.load_day_reports(REPORT_DATE, draft=row("manager", kpi("kpi007", 8)))
        self.assertEqual(automation.volume_values(day["reports"].values())["sent_orders"], "8")
        self.assertEqual([item["employee_id"] for item in day["missing"]], ["a"])
        self.assertIn("черновик", automation.report_coverage_text(day))
        self.assertIn("Нет отчетов: a", automation.report_coverage_text(day))

    def test_standalone_manager_report_completes_coverage_without_entering_totals(self):
        with (
            patch.object(automation, "reports_for_date", return_value={"a": row("a", kpi("kpi007", 10))}),
            patch.object(
                automation,
                "get_working_employees_for_date",
                return_value=[employee("a"), employee("manager", "warehouse_manager")],
            ),
            patch.object(automation, "manager_report_ids_for_date", return_value={"manager"}),
        ):
            day = automation.load_day_reports(REPORT_DATE, include_manager_reports=True)

        self.assertEqual(day["missing"], [])
        self.assertEqual(set(day["reports"]), {"a"})
        self.assertEqual(automation.volume_values(day["reports"].values())["sent_orders"], "10")

    def test_only_exported_warehouse_tasks_are_used(self):
        tasks = [{"Описание": "Готово", "Тип задачи": "warehouse", "Статус": "done"},
                 {"Описание": "В работе", "Тип задачи": "warehouse", "Статус": "active"},
                 {"Описание": "Личное", "Тип задачи": "general", "Статус": "done"}]
        with (
            patch.object(automation, "get_task_export", return_value={"message_id": 1}),
            patch.object(automation, "get_tasks_by_date", return_value=tasks),
        ):
            actual = automation.warehouse_tasks_for_report(REPORT_DATE)
        self.assertEqual(automation.completed_tasks_text(actual), "• Готово")
        with patch.object(automation, "get_task_export", return_value=None):
            self.assertEqual(automation.warehouse_tasks_for_report(REPORT_DATE), [])

    def test_corrupt_kpis_fail_instead_of_becoming_zero(self):
        for raw in ('{broken', '{}', '[null]', '[{"qty": "NaN"}]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                automation.total_kpis([{"KPI данные": raw}])

    def test_long_lines_and_emoji_are_preserved_within_message_size(self):
        text = "Начало\n" + "📦" * 6000 + "\nИТОГО KPI: 100"
        chunks = automation.summary_chunks(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 3800 for chunk in chunks))


class ManagerSuggestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = SimpleNamespace(user_data={
            "report_date": REPORT_DATE, "employee_id": "manager", "manager_report_only": True,
            "manager_wizard_step": "sent_orders", "manager_wizard_values": {},
        })
        self.query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock(), data="")
        self.update = SimpleNamespace(callback_query=self.query, effective_user=SimpleNamespace(id=42))

    async def test_accept_manual_input_and_back_do_not_reuse_stale_button(self):
        with patch("modules.payroll.handlers.manager_report_suggestion", return_value=("17", "Подсказка")):
            await ask_next_manager_step(self.query, self.context)
        token = self.context.user_data["manager_auto_suggestion"]["token"]
        self.query.data = f"mgrwiz:auto:accept:{token}"
        await manager_report_wizard_callback(self.update, self.context)
        self.assertEqual(self.context.user_data["manager_wizard_values"], {"sent_orders": "17"})
        self.assertEqual(self.context.user_data["manager_wizard_step"], "accepted_goods")
        await manager_report_wizard_callback(self.update, self.context)
        self.assertEqual(self.context.user_data["manager_wizard_step"], "accepted_goods")
        self.query.data = "mgrwiz:back"
        with patch("modules.payroll.handlers.manager_report_suggestion", return_value=("20", "Обновлено")):
            await manager_report_wizard_callback(self.update, self.context)
        self.assertEqual(self.context.user_data["manager_wizard_values"], {})
        token = self.context.user_data["manager_auto_suggestion"]["token"]
        self.query.data = f"mgrwiz:auto:manual:{token}"
        await manager_report_wizard_callback(self.update, self.context)
        self.update.message = SimpleNamespace(text="19", reply_text=AsyncMock())
        await manager_report_wizard_text_received(self.update, self.context)
        self.assertEqual(self.context.user_data["manager_wizard_values"], {"sent_orders": "19"})

    async def test_refresh_replaces_value_and_invalidates_old_button(self):
        with patch("modules.payroll.handlers.manager_report_suggestion", return_value=("17", "Подсказка")):
            await ask_next_manager_step(self.query, self.context)
        old_token = self.context.user_data["manager_auto_suggestion"]["token"]
        self.query.data = f"mgrwiz:auto:refresh:{old_token}"
        with patch("modules.payroll.handlers.manager_report_suggestion", return_value=("25", "Подсказка")):
            await manager_report_wizard_callback(self.update, self.context)
        self.query.data = f"mgrwiz:auto:accept:{old_token}"
        await manager_report_wizard_callback(self.update, self.context)
        self.assertEqual(self.context.user_data["manager_wizard_values"], {})
        self.assertEqual(self.context.user_data["manager_auto_suggestion"]["value"], "25")

    def test_receiving_is_manual_and_no_shipments_work_uses_confirmed_count(self):
        self.assertEqual(manager_report_suggestion(self.context, "accepted_goods"), (None, ""))
        self.context.user_data["manager_wizard_values"]["sent_orders"] = "2"
        self.assertEqual(manager_report_suggestion(self.context, "no_shipments_work")[0], "Не актуально")
        self.context.user_data["manager_wizard_values"]["sent_orders"] = "0"
        with patch("modules.payroll.handlers.warehouse_tasks_for_report", return_value=[
            {"Описание": "Разбор возвратов", "Статус": "done"},
            {"Описание": "Уборка", "Статус": "active"},
        ]):
            self.assertEqual(manager_report_suggestion(self.context, "no_shipments_work")[0], "• Разбор возвратов")

    async def test_source_error_offers_manual_answer_without_zero(self):
        with patch("modules.payroll.handlers.manager_report_suggestion", side_effect=RuntimeError("Unavailable")):
            with self.assertLogs(level="ERROR"):
                await ask_next_manager_step(self.query, self.context)
        self.assertIsNone(self.context.user_data["manager_auto_suggestion"]["value"])
        self.assertIn("Не удалось загрузить", self.query.edit_message_text.await_args.args[0])

    async def test_long_task_suggestion_fits_prompt_and_accepts_full_text(self):
        self.context.user_data["manager_wizard_step"] = "no_shipments_work"
        tasks = "• " + "📦" * 5000
        with patch("modules.payroll.handlers.manager_report_suggestion", return_value=(tasks, "Выполнено")):
            await ask_next_manager_step(self.query, self.context)
        text = self.query.edit_message_text.await_args.args[0]
        self.assertLess(len(text.encode("utf-16-le")) // 2, 4096)
        token = self.context.user_data["manager_auto_suggestion"]["token"]
        self.query.data = f"mgrwiz:auto:accept:{token}"
        await manager_report_wizard_callback(self.update, self.context)
        self.assertEqual(self.context.user_data["manager_wizard_values"]["no_shipments_work"], tasks)

    def test_fractional_kpi_is_not_rounded_and_invalid_values_are_rejected(self):
        self.assertEqual(validate_manager_wizard_value("posted_goods", "2,5"), ("2.5", None))
        for value in ("-1", "NaN", "1e9", "много"):
            self.assertIsNotNone(validate_manager_wizard_value("posted_goods", value)[1])


class DailySummaryDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[delivery.DailySummary.__table__])
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.employees = [employee("a"), employee("manager", "warehouse_manager")]
        self.reports = {"a": row("a", kpi("kpi007", 10))}
        self.manager_report_ids = set()
        self.recipients = [employee("brand", "brand_manager", "77")]
        patches = [
            patch("config.AUTO_DAILY_SUMMARY_ENABLED", True),
            patch.object(delivery, "is_module_enabled", return_value=True),
            patch.object(delivery, "session_scope", self.session_scope),
            patch.object(automation, "reports_for_date", side_effect=lambda _: dict(self.reports)),
            patch.object(
                automation,
                "manager_report_ids_for_date",
                side_effect=lambda _: set(self.manager_report_ids),
            ),
            patch.object(automation, "get_working_employees_for_date", side_effect=lambda _: self.employees),
            patch.object(delivery, "get_employees", side_effect=lambda **_: self.recipients),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)
        self.context = SimpleNamespace(bot=SimpleNamespace(
            send_message=AsyncMock(side_effect=lambda **kwargs: SimpleNamespace(message_id=self.context.bot.send_message.await_count)),
            edit_message_text=AsyncMock(), delete_message=AsyncMock(),
        ))

    @contextmanager
    def session_scope(self):
        with self.factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    async def test_waits_for_working_manager_then_sends_totals_and_updates_in_place(self):
        await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.context.bot.send_message.assert_not_awaited()
        self.reports["manager"] = row("manager", kpi("kpi007", 5))
        await delivery.retry_daily_summaries(self.context)
        self.context.bot.send_message.assert_awaited_once()
        sent = self.context.bot.send_message.await_args.kwargs
        self.assertEqual(sent["chat_id"], 77)
        self.assertIn("Отправка: 15", sent["text"])
        await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.context.bot.send_message.assert_awaited_once()
        self.context.bot.edit_message_text.assert_not_awaited()
        self.reports["a"] = row("a", kpi("kpi007", 12))
        await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.assertIn("Отправка: 17", self.context.bot.edit_message_text.await_args.kwargs["text"])
        self.context.bot.send_message.assert_awaited_once()

    async def test_manager_only_report_sends_employee_summary_without_manager_daily_report(self):
        self.manager_report_ids.add("manager")

        await delivery.refresh_daily_summary(self.context, REPORT_DATE)

        self.context.bot.send_message.assert_awaited_once()
        text = self.context.bot.send_message.await_args.kwargs["text"]
        self.assertIn("Отправка: 10", text)
        self.assertNotIn("\nmanager\n", text)

    async def test_off_schedule_manager_report_can_close_day_with_daily_reports(self):
        self.employees = []
        self.manager_report_ids.add("manager")

        await delivery.refresh_daily_summary(self.context, REPORT_DATE)

        self.context.bot.send_message.assert_awaited_once()
        self.assertIn("Отправка: 10", self.context.bot.send_message.await_args.kwargs["text"])

    async def test_no_schedule_never_means_everyone_reported(self):
        self.employees = []
        await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.context.bot.send_message.assert_not_awaited()

    async def test_failed_edit_is_retried_without_sending_new_summary(self):
        self.reports["manager"] = row("manager")
        await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.reports["a"] = row("a", kpi("kpi007", 20))
        self.context.bot.edit_message_text.side_effect = TimedOut()
        with self.assertLogs(level="ERROR"):
            await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.context.bot.edit_message_text.side_effect = None
        await delivery.retry_daily_summaries(self.context)
        self.context.bot.send_message.assert_awaited_once()
        self.assertIn("Отправка: 20", delivery.summary_state(DAY)["77"][0]["text"])

    async def test_retries_failed_recipient_without_resending_successful_copy(self):
        self.reports["manager"] = row("manager")
        self.recipients.append(employee("brand2", "brand_manager", "88"))
        self.context.bot.send_message.side_effect = [SimpleNamespace(message_id=1), TimedOut()]
        with self.assertLogs(level="ERROR"):
            await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.assertTrue(delivery.summary_state(DAY)["77"])
        self.context.bot.send_message.reset_mock(side_effect=True)
        self.context.bot.send_message.return_value = SimpleNamespace(message_id=2)
        await delivery.retry_daily_summaries(self.context)
        self.context.bot.send_message.assert_awaited_once()
        self.assertEqual(self.context.bot.send_message.await_args.kwargs["chat_id"], 88)

    async def test_long_report_retry_and_shortened_report_remove_obsolete_parts(self):
        self.reports["manager"] = row("manager")
        self.reports["a"]["Задачи"] = "x" * 9000
        self.context.bot.send_message.side_effect = [SimpleNamespace(message_id=1), TimedOut()]
        with self.assertLogs(level="ERROR"):
            await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.assertEqual(len(delivery.summary_state(DAY)["77"]), 1)
        self.context.bot.send_message.side_effect = [SimpleNamespace(message_id=n) for n in range(2, 10)]
        await delivery.retry_daily_summaries(self.context)
        self.assertGreater(len(delivery.summary_state(DAY)["77"]), 1)
        self.reports["a"]["Задачи"] = "Кратко"
        await delivery.refresh_daily_summary(self.context, REPORT_DATE)
        self.assertEqual(len(delivery.summary_state(DAY)["77"]), 1)
        self.context.bot.delete_message.assert_awaited()

    async def test_off_schedule_report_included_and_parallel_checks_do_not_duplicate(self):
        import asyncio
        self.reports["manager"] = row("manager")
        self.reports["extra"] = row("extra", kpi("kpi007", 4))
        await asyncio.gather(*(delivery.refresh_daily_summary(self.context, REPORT_DATE) for _ in range(3)))
        self.context.bot.send_message.assert_awaited_once()
        text = self.context.bot.send_message.await_args.kwargs["text"]
        self.assertIn("extra (вне расписания)", text)
        self.assertIn("Отправка: 14", text)


if __name__ == "__main__":
    unittest.main()
