import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from modules.returns.cdek_ocr import parse_cdek_text, recognize_cdek_photo
from modules.returns.loaded_mind import REVIEW, COUNT, photo, review_confirm, summary


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
    @patch("modules.returns.loaded_mind.asyncio.to_thread", new_callable=AsyncMock)
    async def test_photo_offers_review_and_manual_corrections(self, to_thread):
        to_thread.return_value = {"counterparty": "Иванов Иван", "track_number": "1234567890"}
        status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id="photo-id")],
            reply_text=AsyncMock(return_value=status),
        )
        telegram_file = SimpleNamespace(download_to_memory=AsyncMock(side_effect=lambda out: out.write(b"image")))
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file)),
            user_data={"lm_return": {"return_type": "cdek", "photo_ids": [], "items": [], "current": {}}},
        )

        state = await photo(SimpleNamespace(message=message), context)

        self.assertEqual(state, REVIEW)
        self.assertEqual(context.user_data["lm_return"]["track_number"], "1234567890")
        self.assertIn("Иванов Иван", status.edit_text.await_args.args[0])
        buttons = status.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertTrue(any(button.callback_data == "lmret:edit_counterparty" for row in buttons for button in row))
        self.assertTrue(any(button.callback_data == "lmret:edit_track" for row in buttons for button in row))

        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        next_state = await review_confirm(SimpleNamespace(callback_query=query), context)
        self.assertEqual(next_state, COUNT)

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


if __name__ == "__main__":
    unittest.main()
