import json
import unittest
from unittest.mock import MagicMock, patch

from modules.moysklad.client import MoySkladClient


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


if __name__ == "__main__":
    unittest.main()
