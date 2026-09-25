import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx


logger = logging.getLogger(__name__)
LEGACY_B2B_API_BASE_URL = "https://api-b2b.lamoda.ru/api/v1"


class LamodaAPIError(RuntimeError):
    """Base Seller API error safe to show to an operator."""

    def __init__(self, message, *, status_code=None, details=None, uncertain=False):
        super().__init__(message)
        self.status_code = status_code
        self.details = details
        self.uncertain = uncertain


class LamodaConfigurationError(LamodaAPIError):
    pass


class LamodaAuthenticationError(LamodaAPIError):
    pass


class LamodaValidationError(LamodaAPIError):
    pass


class LamodaTemporaryError(LamodaAPIError):
    pass


class LamodaDocumentError(LamodaAPIError):
    pass


class LamodaClient:
    def __init__(self, client_id, client_secret, seller_id, base_url, *, transport=None, timeout=30):
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.seller_id = str(seller_id or "").strip()
        self.base_url = str(base_url or "").rstrip("/")
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=True,
        )

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret and self.seller_id and self.base_url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        await self._http.aclose()

    async def _access_token(self, force=False):
        if not self.configured:
            raise LamodaConfigurationError(
                "Интеграция Lamoda не настроена: проверьте Client ID, Client Secret и Seller ID."
            )
        if not force and self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        async with self._token_lock:
            if not force and self._token and time.monotonic() < self._token_expires_at - 60:
                return self._token
            try:
                response = await self._http.post("/v2/auth-token", json={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                })
            except httpx.HTTPError as error:
                raise LamodaTemporaryError("Не удалось подключиться к авторизации Lamoda.") from error
            if response.status_code >= 400:
                logger.error("Lamoda authentication failed: status=%s", response.status_code)
                raise LamodaAuthenticationError(
                    "Lamoda отклонила данные авторизации.", status_code=response.status_code
                )
            data = response.json()
            token = str(data.get("access_token") or data.get("accessToken") or "")
            if not token:
                raise LamodaAuthenticationError("Lamoda не вернула токен доступа.")
            self._token = token
            self._token_expires_at = time.monotonic() + max(int(data.get("expires_in") or 3600), 1)
            return token

    async def request(self, method, path, *, params=None, json=None, safe=False, retry_401=True):
        method = method.upper()
        token = await self._access_token()
        retries = 3 if safe or method == "GET" else 1
        for attempt in range(retries):
            try:
                response = await self._http.request(
                    method, path, params=params, json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt + 1 < retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise LamodaTemporaryError(
                    "Lamoda временно недоступна. Повторите позже.",
                    uncertain=method != "GET",
                ) from error

            if response.status_code == 401 and retry_401 and (safe or method == "GET"):
                token = await self._access_token(force=True)
                return await self.request(
                    method, path, params=params, json=json,
                    safe=safe, retry_401=False,
                )
            if response.status_code in {429, 500, 503} and attempt + 1 < retries:
                retry_after = response.headers.get("Retry-After")
                await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt)
                continue
            if response.status_code >= 400:
                self._raise_response_error(response, method)
            if response.status_code == 204 or not response.content:
                return {}
            try:
                return response.json()
            except ValueError as error:
                raise LamodaAPIError("Lamoda вернула ответ в неизвестном формате.") from error
        raise LamodaTemporaryError("Lamoda временно недоступна.")

    @staticmethod
    def _raise_response_error(response, method):
        try:
            details = response.json()
        except ValueError:
            details = response.text[:500]
        messages = {
            400: "Lamoda отклонила параметры запроса.",
            401: "Сессия Lamoda истекла.",
            403: "Для операции недостаточно прав Lamoda.",
            404: "Объект не найден в Lamoda.",
            429: "Lamoda ограничила частоту запросов. Повторите позже.",
            500: "Внутренняя ошибка Lamoda.",
            503: "Lamoda временно недоступна.",
        }
        error_type = LamodaValidationError if response.status_code == 400 else LamodaAPIError
        if response.status_code in {429, 500, 503}:
            error_type = LamodaTemporaryError
        message = messages.get(response.status_code, f"Ошибка Lamoda HTTP {response.status_code}.")
        detail_message = LamodaClient._error_detail_message(details)
        if detail_message:
            message = f"{message} {detail_message}"
        logger.error(
            "Lamoda API request failed: method=%s status=%s details=%r",
            method,
            response.status_code,
            details,
        )
        raise error_type(
            message,
            status_code=response.status_code,
            details=details,
            uncertain=method != "GET" and response.status_code >= 500,
        )

    @staticmethod
    def _error_detail_message(details):
        """Return a concise ApiError explanation suitable for an operator."""
        if isinstance(details, str):
            return details.strip()[:500]
        if not isinstance(details, dict):
            return ""
        error = details.get("error", details)
        if isinstance(error, str):
            return error.strip()[:500]
        if not isinstance(error, dict):
            return ""
        parts = []
        message = error.get("message")
        if message:
            parts.append(str(message).strip())
        rows = error.get("details")
        if isinstance(rows, list):
            for row in rows[:5]:
                if isinstance(row, dict):
                    field = str(row.get("field") or "").strip()
                    issue = str(row.get("issue") or row.get("message") or "").strip()
                    value = ": ".join(part for part in (field, issue) if part)
                else:
                    value = str(row).strip()
                if value:
                    parts.append(value)
        return "; ".join(dict.fromkeys(parts))[:500]

    @staticmethod
    def data(payload):
        if isinstance(payload, dict):
            return payload.get("data", payload)
        return payload

    async def paginate(self, path, *, params=None, page_size=100) -> AsyncIterator[dict]:
        page = 1
        while True:
            query = dict(params or {})
            query.setdefault("page", page)
            query.setdefault("limit", page_size)
            payload = await self.request("GET", path, params=query, safe=True)
            data = self.data(payload)
            if isinstance(data, dict):
                rows = data.get("items") or data.get("orders") or data.get("returnItems") or []
                pagination = (
                    data.get("pagination")
                    or data.get("meta")
                    or payload.get("pagination")
                    or payload.get("meta")
                    or {}
                )
            else:
                rows = data or []
                pagination = (
                    payload.get("pagination") or payload.get("meta") or {}
                    if isinstance(payload, dict)
                    else {}
                )
            for row in rows:
                yield row
            total_pages = int(
                pagination.get("totalPages")
                or pagination.get("total_pages")
                or pagination.get("pages")
                or 0
            )
            if not rows or (total_pages and page >= total_pages) or (not total_pages and len(rows) < page_size):
                break
            page += 1

    async def list_orders(self, **params):
        return [row async for row in self.paginate("/v2/orders", params=params)]

    async def get_order(self, order_id):
        return self.data(await self.request(
            "GET", f"/v2/orders/{order_id}", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def get_order_item_statuses(self, order_id):
        return self.data(await self.request(
            "GET", f"/v2/orders/{order_id}/item-statuses", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def get_order_status_history(self, order_id):
        return self.data(await self.request(
            "GET", f"/v2/orders/{order_id}/status-history", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def create_assembly(self, order_id, packs):
        return self.data(await self.request("POST", f"/v2/orders/{order_id}/assembly", json={
            "sellerId": self.seller_id, "packs": packs,
        }))

    async def create_packs(self, order_id, count):
        return self.data(await self.request(
            "POST",
            f"/v2/orders/{order_id}/packs",
            json={"sellerId": self.seller_id, "count": int(count)},
        ))

    async def existing_order_pack_numbers(self, order_id):
        """Read pack numbers already assigned to an order by Lamoda's v1 API."""
        payload = await self.request(
            "GET",
            f"{LEGACY_B2B_API_BASE_URL}/orders/{order_id}",
            safe=True,
        )
        if not isinstance(payload, dict):
            return []
        embedded = payload.get("_embedded") or {}
        values = embedded.get("packNumbers") or payload.get("packNumbers") or []
        return [str(value) for value in values if str(value or "").strip()]

    async def order_item_labels(self, item_ids, label_format="S"):
        return self.data(await self.request("POST", "/v2/labels/order-items", json={
            "sellerId": self.seller_id, "items": list(item_ids), "labelFormat": label_format,
        }, safe=True))

    async def order_pack_labels(self, pack_numbers, label_format="M"):
        return self.data(await self.request("POST", "/v2/labels/order-packs", json={
            "sellerId": self.seller_id, "packs": list(pack_numbers), "labelFormat": label_format,
        }, safe=True))

    async def pallet_labels(self, pallet_barcodes, label_format="M"):
        return self.data(await self.request("POST", "/v2/labels/pallets", json={
            "sellerId": self.seller_id, "palletBarcodes": list(pallet_barcodes), "labelFormat": label_format,
        }, safe=True))

    async def create_shipment(self, ship_at, pallets):
        return self.data(await self.request("POST", "/v2/fbs/shipments", json={
            "sellerId": self.seller_id, "shipAt": ship_at, "pallets": pallets,
        }))

    async def list_shipments(self, **params):
        return [row async for row in self.paginate("/v2/fbs/shipments", params=params)]

    async def get_shipment(self, shipment_id):
        return self.data(await self.request(
            "GET", f"/v2/fbs/shipments/{shipment_id}", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def get_shipment_items(self, shipment_id):
        return self.data(await self.request(
            "GET", f"/v2/fbs/shipments/{shipment_id}/items", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def get_order_container(self, barcode):
        return self.data(await self.request(
            "GET", f"/v2/fbs/order-containers/{barcode}", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def list_return_items(self, **params):
        return [row async for row in self.paginate("/v2/fbs/return-items", params=params)]

    async def get_return_item_history(self, item_id):
        return self.data(await self.request(
            "GET", f"/v2/fbs/return-items/{item_id}/status-history", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def list_return_boxes(self, **params):
        return [row async for row in self.paginate("/v2/fbs/return-boxes", params=params)]

    async def get_return_box(self, box_id):
        return self.data(await self.request(
            "GET", f"/v2/fbs/return-boxes/{box_id}", params={"sellerId": self.seller_id}, safe=True,
        ))

    async def download_pdf(self, url):
        if not str(url or "").strip():
            raise LamodaDocumentError("Lamoda не вернула ссылку на PDF.")
        for attempt in range(3):
            try:
                response = await self._http.get(str(url))
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"):
                    raise LamodaDocumentError("По ссылке Lamoda получен не PDF-файл.")
                return response.content
            except LamodaDocumentError:
                raise
            except httpx.HTTPError as error:
                if attempt == 2:
                    raise LamodaDocumentError("Не удалось скачать PDF Lamoda.") from error
                await asyncio.sleep(2 ** attempt)
