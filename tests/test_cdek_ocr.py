import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from modules.returns.cdek_ocr import parse_cdek_text, parse_showroom_text, recognize_cdek_photo
from modules.returns.loaded_mind import COUNTERPARTY, TRACK, counterparty, photo, summary


class CdekOcrTests(unittest.TestCase):
    def test_labelled_sender_and_waybill_are_extracted(self):
        text = (
            "СДЭК\nНомер накладной: 1234 5678 9012\n"
            "Отправитель: Иванов Иван Иванович\nТелефон: +7 999 111-22-33\n"
            "Получатель: ООО Лоадед Майнд"
        )
        self.assertEqual(parse_cdek_text(text), {
            "counterparty": "Иванов Иван Иванович", "track_number": "123456789012",
        })

    def test_multiline_sender_and_missing_track(self):
        text = "Отправитель\nПетрова Анна Сергеевна\nТелефон: +7 999 111-22-33"
        self.assertEqual(parse_cdek_text(text), {
            "counterparty": "Петрова Анна Сергеевна", "track_number": "",
        })

    def test_unlabelled_phone_is_not_treated_as_track(self):
        self.assertEqual(
            parse_cdek_text("Телефон: +7 999 111 22 33\nПолучатель: Тест Тест"),
            {"counterparty": "", "track_number": ""},
        )

    def test_cdek_sticker_prefers_sender_name_over_city_and_recipient(self):
        text = ("10320234492\nПолучатель\nАхмед Дамир Султанович\n"
                "Отправитель\nНижний Новгород\nИнна Кам\n"
                "№ места обмен - 1449757464 - 1")
        self.assertEqual(parse_cdek_text(text), {
            "counterparty": "Инна Кам", "track_number": "10320234492",
        })

    def test_cdek_paper_waybill_uses_sender_company(self):
        text = ("Накладная\n10270820676\nОТПРАВИТЕЛЬ\nИНФОРМАЦИЯ ОБ ОТПРАВЛЕНИИ\n"
                "Компания: мельник максим александрович;\nг. Калининград\n"
                "ПОЛУЧАТЕЛЬ\nКомпания: Ахмед Дамир Султанович")
        self.assertEqual(parse_cdek_text(text), {
            "counterparty": "мельник максим александрович", "track_number": "10270820676",
        })

    def test_showroom_labels(self):
        examples = [
            ("DIAMOND JEANS SKY BLUE L\nhmmls-1229868408\nВОЗВРАТ\nСоколов Егор Дмитриевич",
             "Соколов Егор Дмитриевич", "hmmls-1229868408"),
            ("DIAMOND OG BELT\nРазмер 105\nЗаказ отмена\nДягилев Денис\nВладимирович",
             "Дягилев Денис Владимирович", ""),
            ("Заказ отмена\nDIAMOND HOODIE BLACK\nБойченков Сергей\nВитальевич",
             "Бойченков Сергей Витальевич", ""),
            ("DIAMOND HOODIE BLACK\nБойченков Сергей\nЗаказ отмена\nВитальевич",
             "Бойченков Сергей Витальевич", ""),
            ("Андреев Кирилл\n1077644501\nLeather jacket black m\n2000000026183",
             "Андреев Кирилл", "1077644501"),
        ]
        for sample, name, number in examples:
            with self.subTest(name=name):
                self.assertEqual(parse_showroom_text(sample), {
                    "counterparty": name, "order_number": number,
                })

    @patch("modules.returns.cdek_ocr.subprocess.run")
    def test_image_is_processed_locally_without_external_service(self, run):
        run.return_value = SimpleNamespace(
            stdout="Отправитель: Иванов Иван Иванович\nНакладная № 1234567890".encode()
        )
        image = Image.new("RGB", (300, 200), "white")
        source = io.BytesIO()
        image.save(source, format="PNG")

        fields = recognize_cdek_photo(source.getvalue())

        self.assertEqual(fields["counterparty"], "Иванов Иван Иванович")
        self.assertEqual(fields["track_number"], "1234567890")
        self.assertEqual(run.call_args.args[0][0], "tesseract")


class CdekReturnFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_cdek_photo_requests_manual_counterparty_and_track(self):
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo-id")],
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"lm_return": {"return_type": "cdek", "photo_ids": [], "items": [], "current": {}}},
        )

        state = await photo(SimpleNamespace(message=message), context)

        self.assertEqual(state, COUNTERPARTY)
        self.assertEqual(context.user_data["lm_return"]["photo_ids"], ["photo-id"])
        self.assertIn("Введите ФИО", message.reply_text.await_args.args[0])

        name_message = SimpleNamespace(text="Иванов Иван", reply_text=AsyncMock())
        next_state = await counterparty(SimpleNamespace(message=name_message), context)
        self.assertEqual(next_state, TRACK)
        self.assertIn("номер накладной", name_message.reply_text.await_args.args[0])

    async def test_showroom_photo_requests_manual_counterparty(self):
        message = SimpleNamespace(photo=[SimpleNamespace(file_id="showroom-photo")],
                                  reply_text=AsyncMock())
        context = SimpleNamespace(
            user_data={"lm_return": {"return_type": "showroom", "photo_ids": [], "items": [], "current": {}}},
        )
        state = await photo(SimpleNamespace(message=message), context)
        self.assertEqual(state, COUNTERPARTY)
        self.assertIn("Введите ФИО", message.reply_text.await_args.args[0])

    @patch("modules.returns.loaded_mind.get_employees", return_value=[])
    def test_return_summary_uses_model_and_size(self, _employees):
        value = {
            "return_type": "cdek", "counterparty": "Иванов Иван", "track_number": "1234567890",
            "items": [{"product_name": "LEO PUFFER BLACK (L, черный, полиэстр 100%)",
                       "size": "L", "condition_key": "normal", "condition_label": "норм"}],
        }
        text = summary(value)
        self.assertIn("LEO PUFFER BLACK L · норм", text)
        self.assertNotIn("полиэстр", text)

    @patch("modules.returns.loaded_mind.get_employees", return_value=[])
    def test_showroom_summary_includes_order_number(self, _employees):
        value = {"return_type": "showroom", "counterparty": "Андреев Кирилл",
                 "order_number": "1077644501", "items": []}
        self.assertIn("Номер заказа: 1077644501", summary(value))


if __name__ == "__main__":
    unittest.main()
