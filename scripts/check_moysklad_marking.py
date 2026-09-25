#!/usr/bin/env python3
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from config import (
    MOYSKLAD_API_BASE_URL,
    MOYSKLAD_CA_FILE,
    MOYSKLAD_SSL_VERIFY,
    MOYSKLAD_TOKEN,
)
from modules.moysklad.client import (
    MoySkladClient,
    MoySkladError,
    extract_tracking_codes,
    write_json_debug,
)


ENTITY_TYPES = [
    ("enrollorder", "Ввод кодов маркировки в оборот"),
    ("enrollreturn", "Возврат кодов маркировки в оборот"),
    ("emissionorder", "Заказ кодов маркировки"),
    ("retireorder", "Вывод кодов маркировки из оборота"),
    ("supply", "Приемка"),
    ("demand", "Отгрузка"),
    ("retaildemand", "Розничная продажа"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Проверить, отдает ли API МоегоСклада коды маркировки по документу."
    )
    parser.add_argument(
        "document_name",
        nargs="?",
        default="ЕВ",
        help="Название/номер документа в МоемСкладе. По умолчанию: ЕВ.",
    )
    parser.add_argument(
        "--debug-dir",
        default="tmp/moysklad_debug",
        help="Папка для JSON-ответов API. По умолчанию: tmp/moysklad_debug.",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Отключить проверку SSL только для диагностики.",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=0,
        help="Показать последние N документов каждого типа вместо поиска.",
    )
    return parser.parse_args()


def load_env_file(path):
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def rows(payload):
    return payload.get("rows", []) if isinstance(payload, dict) else []


def unique(values):
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def get_entity_id(entity):
    return str(entity.get("id") or "").strip()


def parse_bool(value, default=True):
    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "y", "on", "да"):
        return True
    if normalized in ("0", "false", "no", "n", "off", "нет"):
        return False
    return default


def find_documents(client, entity_type, document_name):
    attempts = [
        {"filter": f"name={document_name}", "limit": 10},
        {"search": document_name, "limit": 10},
    ]

    found = []
    for params in attempts:
        try:
            found.extend(rows(client.list_entities(entity_type, params=params)))
        except MoySkladError as error:
            print(f"  {entity_type}: запрос {params} не прошел: {error}")

    by_id = {}
    for entity in found:
        entity_id = get_entity_id(entity)
        if entity_id:
            by_id[entity_id] = entity

    return list(by_id.values())


def show_recent_documents(client, limit):
    for entity_type, label in ENTITY_TYPES:
        print(f"{label} ({entity_type})")
        try:
            payload = client.list_entities(
                entity_type,
                params={"limit": limit, "order": "updated,desc"},
            )
        except MoySkladError as error:
            print(f"  Не удалось получить список: {error}")
            print()
            continue

        documents = rows(payload)
        if not documents:
            print("  Документы не найдены.")
            print()
            continue

        for document in documents:
            positions = document.get("positions") or {}
            positions_size = positions.get("meta", {}).get("size", "")
            parts = [
                f"name={document.get('name', '')}",
                f"id={document.get('id', '')}",
                f"updated={document.get('updated', '')}",
                f"moment={document.get('moment', '')}",
                f"documentState={document.get('documentState', '')}",
                f"emissionType={document.get('emissionType', '')}",
                f"trackingType={document.get('trackingType', '')}",
                f"positions={positions_size}",
            ]
            print("  " + " | ".join(parts))
        print()


def inspect_document(client, entity_type, entity, debug_dir):
    entity_id = get_entity_id(entity)
    if not entity_id:
        return [], {"error": "В документе нет id."}

    payload = {"summary": entity}

    try:
        detailed = client.get_entity(entity_type, entity_id, params={"expand": "positions"})
        payload["detailed_expand_positions"] = detailed
    except MoySkladError as error:
        detailed = {}
        payload["detailed_expand_positions_error"] = str(error)

    try:
        positions = client.get_positions(entity_type, entity_id)
        payload["positions"] = positions
    except MoySkladError as error:
        positions = {}
        payload["positions_error"] = str(error)

    tracking_codes_by_position = {}
    for position in rows(positions):
        position_id = get_entity_id(position)
        if not position_id:
            continue

        try:
            position_codes = client.get_position_tracking_codes(
                entity_type,
                entity_id,
                position_id,
                params={"codetype": "all", "limit": 100},
            )
            tracking_codes_by_position[position_id] = position_codes
        except MoySkladError as error:
            tracking_codes_by_position[position_id] = {"error": str(error)}

    if tracking_codes_by_position:
        payload["tracking_codes_by_position"] = tracking_codes_by_position

    codes = []
    codes.extend(extract_tracking_codes(detailed))
    codes.extend(extract_tracking_codes(positions))
    codes.extend(extract_tracking_codes(tracking_codes_by_position))
    codes = unique(codes)

    safe_name = entity.get("name") or entity_id
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in safe_name)
    debug_path = Path(debug_dir) / f"{entity_type}_{safe_name}_{entity_id}.json"
    write_json_debug(debug_path, payload)

    return codes, {"debug_path": str(debug_path)}


def main():
    load_env_file(ROOT_DIR / ".env")
    args = parse_args()

    token = os.getenv("MOYSKLAD_TOKEN") or MOYSKLAD_TOKEN
    base_url = os.getenv("MOYSKLAD_API_BASE_URL") or MOYSKLAD_API_BASE_URL
    ca_file = os.getenv("MOYSKLAD_CA_FILE") or MOYSKLAD_CA_FILE or None
    ssl_verify = parse_bool(os.getenv("MOYSKLAD_SSL_VERIFY") or MOYSKLAD_SSL_VERIFY)
    if args.no_verify_ssl:
        ssl_verify = False

    try:
        client = MoySkladClient(
            token=token,
            base_url=base_url,
            ssl_verify=ssl_verify,
            ca_file=ca_file,
        )
    except MoySkladError as error:
        print(f"Ошибка настройки: {error}")
        print("Добавьте MOYSKLAD_TOKEN в .env и повторите запуск.")
        return 2

    print(f"Ищу документ: {args.document_name}")
    print(f"Время проверки: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if not ssl_verify:
        print("SSL-проверка отключена. Используйте это только для диагностики.")
    print()

    if args.recent:
        show_recent_documents(client, args.recent)
        return 0

    total_codes = []
    any_documents = False

    for entity_type, label in ENTITY_TYPES:
        print(f"{label} ({entity_type})")
        documents = find_documents(client, entity_type, args.document_name)

        if not documents:
            print("  Документы не найдены.")
            print()
            continue

        any_documents = True
        for document in documents:
            name = document.get("name", "")
            moment = document.get("moment", "")
            document_state = document.get("documentState", "")
            print(f"  Найден: {name} {moment} {document_state}".strip())

            codes, info = inspect_document(client, entity_type, document, args.debug_dir)
            total_codes.extend(codes)
            print(f"  Кодов найдено: {len(codes)}")
            print(f"  JSON: {info.get('debug_path')}")

            if codes[:5]:
                print("  Первые коды:")
                for index, code in enumerate(codes[:5], start=1):
                    print(f"    {index}. {code}")
        print()

    total_codes = unique(total_codes)
    print(f"Итого уникальных кодов: {len(total_codes)}")

    if not any_documents:
        print("Документ с таким названием не найден в проверенных сущностях.")
    elif not total_codes:
        print(
            "Документ найден, но коды маркировки в проверенных ответах API не обнаружены. "
            "Посмотрите сохраненные JSON-файлы: возможно, нужный документ находится в другой сущности."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
