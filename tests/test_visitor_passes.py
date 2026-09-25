import unittest
from contextlib import contextmanager
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram.ext import ConversationHandler

from modules.passes.handlers import (
    PASS_DATA_KEY,
    PASS_HAS_VEHICLE,
    format_pass_summary,
    get_pass_handlers,
    no_patronymic_selected,
    normalize_license_plate,
    parse_pass_date,
    template_to_pass_data,
    valid_name_part,
)
from modules.passes.pdf import CAPACITY_LABELS, create_visitor_pass_pdf, format_pass_date
from modules.passes.storage import (
    PassTemplate,
    count_pass_templates,
    create_pass_template,
    delete_pass_template,
    get_pass_template,
    list_pass_templates,
    update_pass_template,
)
from modules.storage.postgres import Base


class VisitorPassTests(unittest.IsolatedAsyncioTestCase):
    def sample_data(self, **overrides):
        data = {
            "date": date(2026, 9, 12),
            "surname": "Челянов",
            "first_name": "Егор",
            "patronymic": "Сергеевич",
            "has_vehicle": True,
            "vehicle_make": "Mercedes-Benz Actros",
            "vehicle_type": "cargo",
            "capacity_code": "from_10_to_20",
            "license_plate": "А123ВС777",
        }
        data.update(overrides)
        return data

    def test_pdf_is_single_page_and_contains_filled_values(self):
        content = create_visitor_pass_pdf(self.sample_data())
        reader = PdfReader(BytesIO(content))

        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text()
        self.assertIn("Челянов Егор Сергеевич", text)
        self.assertIn("Mercedes-Benz Actros", text)
        self.assertIn(CAPACITY_LABELS["from_10_to_20"], text)
        self.assertIn("А123ВС777", text)

    def test_pdf_without_vehicle_leaves_vehicle_values_empty(self):
        content = create_visitor_pass_pdf(
            self.sample_data(
                has_vehicle=False,
                vehicle_make="Не должна попасть",
                vehicle_type="cargo",
                capacity_code="over_20",
                license_plate="А000АА00",
            )
        )
        text = PdfReader(BytesIO(content)).pages[0].extract_text()

        self.assertNotIn("Не должна попасть", text)
        self.assertNotIn(CAPACITY_LABELS["over_20"], text)
        self.assertNotIn("А000АА00", text)

    def test_date_uses_russian_genitive_month(self):
        self.assertEqual(format_pass_date(date(2026, 9, 12)), "«12» сентября 2026г.")
        self.assertEqual(parse_pass_date("31.12.2026"), date(2026, 12, 31))
        self.assertIsNone(parse_pass_date("31.02.2026"))

    def test_summary_only_shows_capacity_for_cargo_vehicle(self):
        cargo_summary = format_pass_summary(self.sample_data())
        passenger_summary = format_pass_summary(
            self.sample_data(vehicle_type="passenger", capacity_code="from_10_to_20")
        )
        pedestrian_summary = format_pass_summary(self.sample_data(has_vehicle=False))

        self.assertIn("Грузоподъёмность", cargo_summary)
        self.assertNotIn("Грузоподъёмность", passenger_summary)
        self.assertEqual(pedestrian_summary.splitlines()[-1], "Автомобиль: нет")

    def test_template_data_does_not_carry_a_date(self):
        template = self.sample_data(id=17, name="Челянов — А123ВС777")
        data = template_to_pass_data(template)

        self.assertNotIn("date", data)
        self.assertEqual(data["source_template_id"], 17)

    def test_license_plate_is_uppercased_and_compacted(self):
        self.assertEqual(normalize_license_plate("а 123 вс 777"), "А123ВС777")

    def test_name_parts_may_contain_multiple_words(self):
        self.assertTrue(valid_name_part("Абдул Рахим"))
        self.assertTrue(valid_name_part("Де Ла Круз"))
        self.assertTrue(valid_name_part("Анна-Мария"))
        self.assertFalse(valid_name_part("Абдул 123"))

    def test_pdf_keeps_both_vehicle_type_labels(self):
        content = create_visitor_pass_pdf(self.sample_data(vehicle_type="cargo"))
        text = PdfReader(BytesIO(content)).pages[0].extract_text()

        self.assertIn("Легковой / грузовой", text)
        self.assertIn("(легковая/грузовая)", text)

    async def test_no_patronymic_button_stores_dash(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={PASS_DATA_KEY: {}})

        state = await no_patronymic_selected(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, PASS_HAS_VEHICLE)
        self.assertEqual(context.user_data[PASS_DATA_KEY]["patronymic"], "-")

    def test_module_registers_a_conversation_handler(self):
        handlers = get_pass_handlers()
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], ConversationHandler)


class VisitorPassStorageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine, tables=[PassTemplate.__table__])
        self.factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        self.storage_patch = patch("modules.passes.storage.session_scope", new=self.session_scope)
        self.storage_patch.start()

    def tearDown(self):
        self.storage_patch.stop()
        self.engine.dispose()

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def test_shared_template_can_be_created_edited_listed_and_deleted(self):
        user = SimpleNamespace(id=42, full_name="Сотрудник Склада")
        data = {
            "name": "Постоянная машина",
            "surname": "Иванов",
            "first_name": "Иван",
            "patronymic": "-",
            "has_vehicle": True,
            "vehicle_make": "ГАЗель",
            "vehicle_type": "cargo",
            "capacity_code": "up_to_3_5",
            "license_plate": "А123ВС777",
        }

        created = create_pass_template(data, user)
        self.assertEqual(count_pass_templates(), 1)
        self.assertEqual(list_pass_templates()[0]["id"], created["id"])
        self.assertEqual(get_pass_template(created["id"])["patronymic"], "-")

        updated = update_pass_template(created["id"], name="Новая подпись")
        self.assertEqual(updated["name"], "Новая подпись")

        self.assertTrue(delete_pass_template(created["id"]))
        self.assertEqual(count_pass_templates(), 0)


if __name__ == "__main__":
    unittest.main()
