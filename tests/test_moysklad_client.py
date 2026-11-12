import gzip
import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from modules.moysklad.client import MoySkladClient, MoySkladError


class MoySkladClientWriteTests(unittest.TestCase):
    def test_update_entity_sends_utf8_json_with_put(self):
        response = MagicMock()
        response.headers = {}
        response.read.return_value = b'{"id":"order-1"}'
        response.__enter__.return_value = response
        client = MoySkladClient("token", base_url="https://api.example.test")

        with patch("modules.moysklad.client.urlopen", return_value=response) as urlopen:
            result = client.update_entity(
                "customerorder",
                "order-1",
                {"attributes": [{"value": "уедет"}]},
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url, "https://api.example.test/entity/customerorder/order-1")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {
            "attributes": [{"value": "уедет"}],
        })
        self.assertEqual(result, {"id": "order-1"})

    def test_create_entity_sends_utf8_json_with_post(self):
        response = MagicMock()
        response.headers = {}
        response.read.return_value = b'{"id":"demand-1","applicable":true}'
        response.__enter__.return_value = response
        client = MoySkladClient("token", base_url="https://api.example.test")

        with patch("modules.moysklad.client.urlopen", return_value=response) as urlopen:
            result = client.create_entity(
                "demand",
                {"applicable": True, "description": "Отгрузка"},
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.example.test/entity/demand")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {
            "applicable": True,
            "description": "Отгрузка",
        })
        self.assertEqual(result, {"id": "demand-1", "applicable": True})

    def test_move_entity_to_trash_accepts_empty_response(self):
        response = MagicMock()
        response.headers = {}
        response.read.return_value = b""
        response.__enter__.return_value = response
        client = MoySkladClient("token", base_url="https://api.example.test")

        with patch("modules.moysklad.client.urlopen", return_value=response) as urlopen:
            result = client.move_entity_to_trash("demand", "demand-1")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            "https://api.example.test/entity/demand/demand-1/trash",
        )
        self.assertEqual(result, {})

    def test_gzip_http_error_is_decompressed_before_reporting(self):
        response_body = gzip.compress(
            '{"errors":[{"error":"Недостаточно прав"}]}'.encode("utf-8")
        )
        error = HTTPError(
            "https://api.example.test/entity/customerorder",
            403,
            "Forbidden",
            {"Content-Encoding": "gzip"},
            io.BytesIO(response_body),
        )
        client = MoySkladClient("token", base_url="https://api.example.test")

        with patch("modules.moysklad.client.urlopen", side_effect=error):
            with self.assertRaises(MoySkladError) as raised:
                client.list_entities("customerorder")

        message = str(raised.exception)
        self.assertIn("HTTP 403", message)
        self.assertIn("Недостаточно прав", message)

    def test_invalid_gzip_http_error_does_not_expose_binary_data(self):
        error = HTTPError(
            "https://api.example.test/entity/customerorder",
            403,
            "Forbidden",
            {"Content-Encoding": "gzip"},
            io.BytesIO(b"not actually gzip\x00\xff"),
        )
        client = MoySkladClient("token", base_url="https://api.example.test")

        with patch("modules.moysklad.client.urlopen", side_effect=error):
            with self.assertRaisesRegex(
                MoySkladError,
                "HTTP 403: сервер не прислал описание ошибки",
            ):
                client.list_entities("customerorder")


if __name__ == "__main__":
    unittest.main()
