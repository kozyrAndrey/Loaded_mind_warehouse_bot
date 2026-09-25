import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from modules.lamoda_fbs.client import LamodaClient, LamodaTemporaryError, LamodaValidationError


class LamodaClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_v2_fbs_request_shapes_match_contract(self):
        requests = []

        async def handler(request):
            if request.url.path.endswith("/auth-token"):
                return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            requests.append(request)
            if request.url.path.endswith("/packs"):
                return httpx.Response(200, json={"data": [{"packNumber": "PACK-1"}]})
            return httpx.Response(200, json={"data": {"fileUrl": "https://files.test/labels.pdf"}})

        client = LamodaClient("id", "secret", "seller-1", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            await client.create_packs("order-1", 2)
            await client.order_item_labels(["item-1"], "S")
            await client.order_pack_labels(["pack-1"], "M")
        finally:
            await client.aclose()

        self.assertNotIn("sellerId", requests[0].url.params)
        self.assertEqual(json.loads(requests[0].content), {"sellerId": "seller-1", "count": 2})
        self.assertEqual(json.loads(requests[1].content), {
            "sellerId": "seller-1", "items": ["item-1"], "labelFormat": "S",
        })
        self.assertEqual(json.loads(requests[2].content), {
            "sellerId": "seller-1", "packs": ["pack-1"], "labelFormat": "M",
        })

    async def test_validation_error_includes_api_details(self):
        async def handler(request):
            if request.url.path.endswith("/auth-token"):
                return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            return httpx.Response(400, json={
                "error": {
                    "code": "VALIDATION_FAILED",
                    "message": "Проверьте переданные данные",
                    "details": [{"field": "count", "issue": "must be greater than 0"}],
                },
            })

        client = LamodaClient("id", "secret", "seller", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(LamodaValidationError) as caught:
                await client.create_packs("order-1", 0)
        finally:
            await client.aclose()
        self.assertIn("Проверьте переданные данные", str(caught.exception))
        self.assertIn("count: must be greater than 0", str(caught.exception))

    async def test_existing_pack_numbers_are_read_from_legacy_order_details(self):
        requests = []

        async def handler(request):
            if request.url.path.endswith("/auth-token"):
                return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            requests.append(request)
            return httpx.Response(200, json={
                "_embedded": {"packNumbers": ["FBS-2", "FBS-1"]},
            })

        client = LamodaClient(
            "id", "secret", "seller", "https://lamoda.test/api",
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await client.existing_order_pack_numbers("RU260807-188386")
        finally:
            await client.aclose()

        self.assertEqual(result, ["FBS-2", "FBS-1"])
        self.assertEqual(requests[0].url.host, "api-b2b.lamoda.ru")
        self.assertEqual(requests[0].url.path, "/api/v1/orders/RU260807-188386")

    async def test_list_payload_uses_v2_meta_pagination(self):
        pages = []

        async def handler(request):
            if request.url.path.endswith("/auth-token"):
                return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            page = int(request.url.params["page"])
            pages.append(page)
            return httpx.Response(200, json={
                "data": [{"id": f"order-{page}"}],
                "meta": {"totalPages": 2},
            })

        client = LamodaClient("id", "secret", "seller", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            rows = await client.list_orders()
        finally:
            await client.aclose()
        self.assertEqual([row["id"] for row in rows], ["order-1", "order-2"])
        self.assertEqual(pages, [1, 2])

    async def test_token_is_cached_and_orders_are_paginated(self):
        calls = {"token": 0, "orders": 0}

        async def handler(request):
            if request.url.path.endswith("/auth-token"):
                calls["token"] += 1
                return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
            calls["orders"] += 1
            self.assertEqual(request.headers["Authorization"], "Bearer token-1")
            page = int(request.url.params["page"])
            return httpx.Response(200, json={
                "data": {"items": [{"orderId": f"order-{page}"}], "pagination": {"totalPages": 2}}
            })

        client = LamodaClient("id", "secret", "seller", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            rows = await client.list_orders()
            self.assertEqual([row["orderId"] for row in rows], ["order-1", "order-2"])
            self.assertEqual(calls, {"token": 1, "orders": 2})
        finally:
            await client.aclose()

    async def test_401_refreshes_token_once(self):
        calls = {"token": 0, "orders": 0}

        async def handler(request):
            if request.url.path.endswith("/auth-token"):
                calls["token"] += 1
                return httpx.Response(200, json={"access_token": f"token-{calls['token']}", "expires_in": 3600})
            calls["orders"] += 1
            if calls["orders"] == 1:
                return httpx.Response(401, json={"error": "expired"})
            self.assertEqual(request.headers["Authorization"], "Bearer token-2")
            return httpx.Response(200, json={"data": {"items": []}})

        client = LamodaClient("id", "secret", "seller", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            await client.list_orders()
            self.assertEqual(calls, {"token": 2, "orders": 2})
        finally:
            await client.aclose()

    async def test_safe_get_retries_503(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            if request.url.path.endswith("/auth-token"):
                return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            calls += 1
            if calls < 3:
                return httpx.Response(503, json={"error": "temporary"})
            return httpx.Response(200, json={"data": {"orderId": "1"}})

        client = LamodaClient("id", "secret", "seller", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            with patch("modules.lamoda_fbs.client.asyncio.sleep", new=AsyncMock()):
                result = await client.get_order("1")
            self.assertEqual(result["orderId"], "1")
            self.assertEqual(calls, 3)
        finally:
            await client.aclose()

    async def test_non_idempotent_post_is_not_retried_after_timeout(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            if request.url.path.endswith("/auth-token"):
                return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            calls += 1
            raise httpx.ReadTimeout("timeout", request=request)

        client = LamodaClient("id", "secret", "seller", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(LamodaTemporaryError) as caught:
                await client.create_shipment("2026-01-01T00:00:00Z", [])
            self.assertTrue(caught.exception.uncertain)
            self.assertEqual(calls, 1)
        finally:
            await client.aclose()

    async def test_non_idempotent_post_is_not_replayed_after_401(self):
        calls = {"token": 0, "shipment": 0}

        async def handler(request):
            if request.url.path.endswith("/auth-token"):
                calls["token"] += 1
                return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
            calls["shipment"] += 1
            return httpx.Response(401, json={"error": "expired"})

        client = LamodaClient("id", "secret", "seller", "https://lamoda.test/api", transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(Exception):
                await client.create_shipment("2026-01-01T00:00:00Z", [])
            self.assertEqual(calls, {"token": 1, "shipment": 1})
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
