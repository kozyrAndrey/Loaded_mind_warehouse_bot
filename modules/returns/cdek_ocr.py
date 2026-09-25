"""Локальное OCR накладной СДЭК: только подсказки, подтверждаемые сотрудником."""

import io
import re
import subprocess

from PIL import Image, ImageEnhance, ImageOps


TRACK_LABEL = re.compile(
    r"(?:номер\s+накладной|накладная|накладной|трек(?:[-\s]*номер)?|номер\s+отправления|отправление\s*№)",
    re.IGNORECASE,
)
TRACK_NUMBER = re.compile(r"(?<!\d)(?:\d[\s-]?){8,16}(?!\d)")
SENDER_LABEL = re.compile(
    r"(?:ф\.?\s*и\.?\s*о\.?\s*(?:отправителя|клиента)?|отправитель|контрагент|клиент|от\s+кого)\s*[:.\-–—]*",
    re.IGNORECASE,
)
NAME_WORD = re.compile(r"[А-ЯЁа-яё]{2,}(?:-[А-ЯЁа-яё]{2,})?")
NAME_STOP = {"отправитель", "получатель", "контрагент", "клиент", "сдэк", "телефон", "адрес"}


def parse_cdek_text(text):
    """Из распознанного текста берёт только явно подписанные поля."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    result = {"counterparty": "", "track_number": ""}
    for index, line in enumerate(lines):
        if not result["track_number"] and TRACK_LABEL.search(line):
            for candidate_line in (line, *lines[index + 1:index + 3]):
                match = TRACK_NUMBER.search(candidate_line)
                if match:
                    result["track_number"] = re.sub(r"\D", "", match.group())
                    break
        if not result["counterparty"]:
            label = SENDER_LABEL.search(line)
            if not label:
                continue
            for candidate_line in (line[label.end():], *lines[index + 1:index + 3]):
                candidate_line = re.split(
                    r"\b(?:телефон|тел\.?|адрес|e-mail|почта|получатель)\b|\d{5,}",
                    candidate_line, maxsplit=1, flags=re.IGNORECASE,
                )[0]
                words = NAME_WORD.findall(candidate_line)
                if 2 <= len(words) <= 4 and not any(word.casefold() in NAME_STOP for word in words):
                    result["counterparty"] = " ".join(words)
                    break
    return result


def recognize_cdek_photo(image_bytes):
    """Распознаёт фото в памяти, не отправляя его во внешние OCR-сервисы."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source)
        if image.width * image.height > 24_000_000:
            raise ValueError("Слишком большое фото накладной")
        image = ImageOps.grayscale(image)
        if max(image.size) < 1800:
            scale = 1800 / max(image.size)
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        image = ImageEnhance.Contrast(image).enhance(1.5)
        output = io.BytesIO()
        image.save(output, format="PNG")
    prepared = output.getvalue()
    result = {"counterparty": "", "track_number": ""}
    for page_mode in (6, 11):
        completed = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "rus+eng", "--psm", str(page_mode)],
            input=prepared, capture_output=True, timeout=30, check=True,
        )
        parsed = parse_cdek_text(completed.stdout.decode("utf-8", errors="replace"))
        for key in result:
            result[key] = result[key] or parsed[key]
        if all(result.values()):
            break
    return result
