"""Локальное OCR возвратов: подсказки всегда подтверждаются сотрудником."""

import io
import re
import subprocess

from PIL import Image, ImageOps


TRACK_LABEL = re.compile(r"(?:номер\s+накладной|накладная|накладной|трек(?:[-\s]*номер)?|номер\s+отправления|отправление\s*№)", re.I)
TRACK_NUMBER = re.compile(r"(?<!\d)(?:\d[\s-]?){10,16}(?!\d)")
SENDER_LABEL = re.compile(r"\b(?:отправитель|отправителя|от\s+кого)\b\s*[:.\-–—]*", re.I)
RECIPIENT_LABEL = re.compile(r"\b(?:получатель|получателя)\b", re.I)
NAME_WORD = re.compile(r"[А-ЯЁа-яё]{2,}(?:-[А-ЯЁа-яё]{2,})?")
ORDER_NUMBER = re.compile(r"\b(?:hmmls[-\s]*\d{8,12}|\d{10})\b", re.I)
CITY_WORDS = {"москва", "нижний", "новгород", "россия", "калининград", "санкт", "петербург"}
NAME_STOP = {"отправитель", "получатель", "контрагент", "клиент", "сдэк", "телефон", "адрес",
             "город", "компания", "возврат", "заказ", "отмена", "самовывоз", "размер",
             "состав", "страна", "натуральная", "кожа", "миллионная", "ул", "корп",
             "накладная", "заказу", "обмен", "информация", "отправлении"}


def _name(line):
    line = re.split(r"\b(?:телефон|тел\.?|адрес|e-mail|почта|сайт|продавец|получатель)\b|\d{5,}",
                    line, maxsplit=1, flags=re.I)[0]
    line = re.sub(r"^(?:компания|фио|ф\.?\s*и\.?\s*о\.?)\s*[:.\-–—]*\s*", "", line, flags=re.I)
    words = NAME_WORD.findall(line)
    if not 2 <= len(words) <= 3 or any(word.casefold() in NAME_STOP for word in words):
        return ""
    if all(word.casefold() in CITY_WORDS for word in words):
        return ""
    return " ".join(words)


def parse_cdek_text(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    result = {"counterparty": "", "track_number": ""}
    for line in lines[:max(5, len(lines) // 3)]:
        if SENDER_LABEL.search(line) or RECIPIENT_LABEL.search(line):
            break
        if re.search(r"тел|телефон|обмен|им\s+обмен|мест|заказ", line, re.I):
            continue
        match = TRACK_NUMBER.search(line)
        if match:
            number = re.sub(r"\D", "", match.group())
            if len(number) == 11:
                result["track_number"] = number
                break
    sender_candidates = []
    for index, line in enumerate(lines):
        sender = SENDER_LABEL.search(line)
        if sender:
            section = [line[sender.end():]]
            for following in lines[index + 1:index + 9]:
                if RECIPIENT_LABEL.search(following) or re.search(r"(?:№\s*места|всего\s+мест|габариты|тариф)", following, re.I):
                    break
                section.append(following)
            for candidate in section:
                name = _name(candidate)
                if name:
                    sender_candidates.append(("компания" in candidate.casefold(), name))
        if not result["track_number"] and TRACK_LABEL.search(line) and not re.search(r"обмен|заказ", line, re.I):
            for candidate in (line, *lines[index + 1:index + 4]):
                match = TRACK_NUMBER.search(candidate)
                if match:
                    result["track_number"] = re.sub(r"\D", "", match.group())
                    break
    if sender_candidates:
        result["counterparty"] = next((name for labelled, name in sender_candidates if labelled), sender_candidates[-1][1])
    return result


def parse_showroom_text(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    result = {"counterparty": "", "order_number": ""}
    for line in lines:
        match = ORDER_NUMBER.search(line)
        if match and not result["order_number"]:
            result["order_number"] = re.sub(r"\s+", "", match.group()).lower()
    candidates = []
    for index, line in enumerate(lines):
        if re.search(r"компания|получатель|отправитель|заказ\s+отмена", line, re.I):
            continue
        name = _name(line)
        if name:
            candidates.append((index, name))
        if index + 1 < len(lines):
            first = NAME_WORD.findall(line)
            second = NAME_WORD.findall(lines[index + 1])
            if len(first) in (1, 2) and len(second) in (1, 2) and len(first) + len(second) in (2, 3):
                joined = _name(line + " " + lines[index + 1])
                if joined:
                    candidates.append((index, joined))
        if name and len(NAME_WORD.findall(name)) == 2:
            for following in lines[index + 1:index + 4]:
                patronymic = NAME_WORD.findall(following)
                if len(patronymic) == 1 and re.search(r"(?:вич|вна|ична)$", patronymic[0], re.I):
                    candidates.append((index, name + " " + patronymic[0]))
                    break
    if candidates:
        result["counterparty"] = candidates[-1][1]
    return result


def _recognize_photo(image_bytes, parser, keys, crop_box=None, grayscale=False):
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source)
        if image.width * image.height > 24_000_000:
            raise ValueError("Слишком большое фото накладной")
        if crop_box:
            left, top, right, bottom = crop_box
            image = image.crop((int(image.width * left), int(image.height * top),
                                int(image.width * right), int(image.height * bottom)))
        if grayscale:
            image = ImageOps.grayscale(image)
        if max(image.size) > 2600:
            scale = 2600 / max(image.size)
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        output = io.BytesIO()
        image.save(output, format="PNG")
    result = dict.fromkeys(keys, "")
    for page_mode in (11, 6):
        try:
            completed = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "rus+eng", "--psm", str(page_mode)],
                input=output.getvalue(), capture_output=True, timeout=25, check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            continue
        parsed = parser(completed.stdout.decode("utf-8", errors="replace"))
        for key in keys:
            result[key] = result[key] or parsed[key]
        if all(result.values()):
            break
    return result


def recognize_cdek_photo(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as source:
        preview = ImageOps.exif_transpose(source).convert("RGB").resize((64, 64))
        green_fraction = sum(g > r * 1.2 and g > b * 1.2 and g > 80
                             for r, g, b in preview.getdata()) / 4096
    # Green CDEK bags carry a tall sticker. Paper waybills have the sender
    # and barcode in a narrow horizontal strip above the item table.
    crop_box = None if green_fraction > 0.15 else (0, 0.36, 0.75, 0.62)
    return _recognize_photo(image_bytes, parse_cdek_text, ("counterparty", "track_number"),
                            crop_box, grayscale=green_fraction <= 0.15)


def recognize_showroom_photo(image_bytes):
    return _recognize_photo(image_bytes, parse_showroom_text, ("counterparty", "order_number"))
