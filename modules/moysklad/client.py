import gzip
import json
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MoySkladError(RuntimeError):
    pass


class MoySkladClient:
    def __init__(
        self,
        token,
        base_url="https://api.moysklad.ru/api/remap/1.2",
        ssl_verify=True,
        ca_file=None,
    ):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.ssl_context = build_ssl_context(ssl_verify=ssl_verify, ca_file=ca_file)

        if not self.token:
            raise MoySkladError("Не указан MOYSKLAD_TOKEN.")

    def get(self, path, params=None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json;charset=utf-8",
                "Content-Type": "application/json;charset=utf-8",
                "Accept-Encoding": "gzip",
            },
            method="GET",
        )

        try:
            with urlopen(request, timeout=30, context=self.ssl_context) as response:
                raw_body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw_body = gzip.decompress(raw_body)
                raw = raw_body.decode("utf-8")
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise MoySkladError(
                f"МойСклад вернул HTTP {error.code}: {details}"
            ) from error
        except URLError as error:
            raise MoySkladError(f"Не удалось подключиться к МоемуСкладу: {error}") from error

        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise MoySkladError("МойСклад вернул невалидный JSON.") from error

    def get_href(self, href, params=None):
        normalized_href = str(href or "").strip()
        if not normalized_href:
            raise MoySkladError("Пустая ссылка МойСклад.")

        prefix = f"{self.base_url}/"
        if normalized_href.startswith(prefix):
            return self.get(normalized_href[len(prefix):], params=params)
        if normalized_href.startswith(self.base_url):
            return self.get(normalized_href[len(self.base_url):].lstrip("/"), params=params)
        return self.get(normalized_href, params=params)

    def list_entities(self, entity_type, params=None):
        return self.get(f"entity/{entity_type}", params=params or {})

    def get_entity(self, entity_type, entity_id, params=None):
        return self.get(f"entity/{entity_type}/{entity_id}", params=params or {})

    def get_positions(self, entity_type, entity_id, params=None):
        return self.get(f"entity/{entity_type}/{entity_id}/positions", params=params or {})

    def get_position_tracking_codes(self, entity_type, entity_id, position_id, params=None):
        return self.get(
            f"entity/{entity_type}/{entity_id}/positions/{position_id}/trackingCodes",
            params=params or {},
        )


def extract_tracking_codes(value):
    codes = []

    def walk(node):
        if isinstance(node, dict):
            cis = node.get("cis") or node.get("cis_1162")
            if cis:
                codes.append(str(cis))
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return codes


def build_ssl_context(ssl_verify=True, ca_file=None):
    if not ssl_verify:
        return ssl._create_unverified_context()

    if ca_file:
        return ssl.create_default_context(cafile=ca_file)

    try:
        import certifi
    except ModuleNotFoundError:
        return ssl.create_default_context()

    return ssl.create_default_context(cafile=certifi.where())


def write_json_debug(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
