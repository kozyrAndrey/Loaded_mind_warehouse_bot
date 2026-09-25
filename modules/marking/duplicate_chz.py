import re
from pathlib import Path

from config import MARKING_LABEL_CUSTOMER, MARKING_LABEL_MANUFACTURER


GROUP_SEPARATOR = "\x1d"
MARKING_ASSET_DIR = Path(__file__).resolve().parents[2] / "resources" / "marking"
HONEST_SIGN_LOGO_PATH = MARKING_ASSET_DIR / "honest_sign.jpeg"
EAC_LOGO_PATH = MARKING_ASSET_DIR / "eac.png"
PDF_POINTS_PER_PIXEL = 72 / 96
LABEL_75X120_FONT_REDUCTION = 2 * PDF_POINTS_PER_PIXEL


class DuplicateChzError(RuntimeError):
    pass


def label_75x120_font_size(original_size):
    """Уменьшить шрифт этикетки на 2 экранных пикселя (1,5 PDF-пункта)."""
    return original_size - LABEL_75X120_FONT_REDUCTION


def normalize_chz_text(value):
    text = str(value or "").strip()
    replacements = {
        "\\x1d": GROUP_SEPARATOR,
        "\\u001d": GROUP_SEPARATOR,
        "<GS>": GROUP_SEPARATOR,
        "[GS]": GROUP_SEPARATOR,
        "{GS}": GROUP_SEPARATOR,
        "␝": GROUP_SEPARATOR,
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return text


def has_ai_parentheses(value):
    return bool(re.search(r"\(\d{2,4}\)", value or ""))


def to_bwipp_gs1_data(raw_code):
    code = normalize_chz_text(raw_code)
    if not code:
        raise DuplicateChzError("Код ЧЗ пустой.")

    if has_ai_parentheses(code):
        return code

    if not code.startswith("01") or len(code) < 18:
        raise DuplicateChzError(
            "Не удалось распознать GS1-структуру. Пришлите полный код в формате (01)...(21)...(91)...(92)..."
        )

    gtin = code[2:16]
    tail = code[16:]
    if not gtin.isdigit() or not tail.startswith("21"):
        raise DuplicateChzError(
            "Не удалось распознать GTIN и серийный номер. Пришлите код с AI в скобках: (01)...(21)..."
        )

    return "(01)" + gtin + _parse_variable_ai_tail(tail)


def extract_gs1_ai_values(raw_code):
    code = normalize_chz_text(raw_code)
    if not code:
        return {}

    if has_ai_parentheses(code):
        return dict(re.findall(r"\((\d{2,4})\)([^()]*)", code))

    if code.startswith("01") and len(code) >= 18:
        values = {"01": code[2:16]}
        tail = code[16:]
        for ai, value in re.findall(r"(21|91|92)(.*?)(?=91|92|$)", tail):
            values[ai] = value.strip(GROUP_SEPARATOR)
        return values

    return {}


def extract_gtin(raw_code):
    return extract_gs1_ai_values(raw_code).get("01", "")


def extract_short_marking_code(raw_code, serial_length=13):
    """Return the 31-character Russian marking UIT (AI 01 + AI 21)."""
    values = extract_gs1_ai_values(raw_code)
    gtin = str(values.get("01") or "")
    serial = str(values.get("21") or "").strip(GROUP_SEPARATOR)
    if len(gtin) != 14 or not gtin.isdigit():
        raise DuplicateChzError("В коде не найден корректный 14-значный GTIN (AI 01).")
    if len(serial) < serial_length:
        raise DuplicateChzError("В коде не найден полный серийный номер (AI 21).")
    return f"01{gtin}21{serial[:serial_length]}"


def _parse_variable_ai_tail(tail):
    parts = []
    rest = tail

    while rest:
        if len(rest) < 2 or not rest[:2].isdigit():
            raise DuplicateChzError("Не удалось распознать AI в хвосте кода ЧЗ.")

        ai = rest[:2]
        rest = rest[2:]

        separator_index = rest.find(GROUP_SEPARATOR)
        if separator_index >= 0:
            value = rest[:separator_index]
            rest = rest[separator_index + 1:]
        elif ai == "21" and "91" in rest:
            marker = rest.find("91", 1)
            value = rest[:marker]
            rest = rest[marker:]
        elif ai == "91" and "92" in rest:
            marker = rest.find("92", 1)
            value = rest[:marker]
            rest = rest[marker:]
        else:
            value = rest
            rest = ""

        if not value:
            raise DuplicateChzError(f"AI {ai} без значения.")
        parts.append(f"({ai}){value}")

    return "".join(parts)


def create_duplicate_chz_pdf(raw_code, output_path, product_info=None):
    try:
        import treepoem
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError as error:
        raise DuplicateChzError(
            "Не установлены библиотеки для генерации PDF/GS1 DataMatrix. Установите зависимости из requirements.txt."
        ) from error

    gs1_data = to_bwipp_gs1_data(raw_code)
    output_path = Path(output_path)

    try:
        barcode = treepoem.generate_barcode(
            barcode_type="gs1datamatrix",
            data=gs1_data,
            options={"parsefnc": True},
        )
        barcode = barcode.convert("RGB")
    except Exception as error:
        details = str(error)
        if "GS1badChecksum" in details or "Bad checksum" in details:
            raise DuplicateChzError(
                "Не удалось сгенерировать GS1 DataMatrix: в GTIN неверная контрольная цифра."
            ) from error
        raise DuplicateChzError(
            "Не удалось сгенерировать GS1 DataMatrix. Проверьте формат полного кода ЧЗ и наличие Ghostscript."
        ) from error

    image_path = output_path.with_suffix(".png")
    barcode.save(image_path)

    page_width, page_height = 58 * mm, 40 * mm
    qr_size = 27 * mm
    qr_x = 3 * mm
    qr_y = 7 * mm
    details_x = 32 * mm
    details_y = page_height - 7 * mm
    details_width = page_width - details_x - 2 * mm

    pdf = canvas.Canvas(str(output_path), pagesize=(page_width, page_height))
    pdf.setTitle("Дубликат ЧЗ")
    pdf.setFillColor(colors.black)
    pdf.drawImage(str(image_path), qr_x, qr_y, width=qr_size, height=qr_size, preserveAspectRatio=True, mask="auto")
    font_name = register_label_font(pdfmetrics, TTFont)
    draw_product_details(pdf, product_info or {}, short_code_text(raw_code), details_x, details_y, details_width, mm, font_name)
    pdf.showPage()
    pdf.save()

    try:
        image_path.unlink()
    except OSError:
        pass

    return output_path


def create_duplicate_chz_75x120_pdf(raw_code, output_path, product_info=None):
    """Создать товарную этикетку ЧЗ 75×120 мм по макету МойСклад."""
    try:
        import treepoem
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        from PIL import Image
    except ModuleNotFoundError as error:
        raise DuplicateChzError(
            "Не установлены библиотеки для генерации PDF/штрихкодов. Установите зависимости из requirements.txt."
        ) from error

    gs1_data = to_bwipp_gs1_data(raw_code)
    output_path = Path(output_path)
    datamatrix_path = output_path.with_name(f"{output_path.stem}_datamatrix.png")
    ean_path = output_path.with_name(f"{output_path.stem}_ean13.png")
    honest_sign_path = output_path.with_name(f"{output_path.stem}_honest_sign.png")
    eac_path = output_path.with_name(f"{output_path.stem}_eac.png")
    temporary_images = (datamatrix_path, ean_path, honest_sign_path, eac_path)
    product_info = dict(product_info or {})
    ean13 = str(product_info.get("ean13") or "").strip()
    if not ean13:
        gtin = extract_gtin(raw_code)
        if len(gtin) == 14 and gtin.startswith("0"):
            ean13 = gtin[1:]

    try:
        datamatrix = treepoem.generate_barcode(
            barcode_type="gs1datamatrix",
            data=gs1_data,
            options={"parsefnc": True},
        ).convert("RGB")
        datamatrix.save(datamatrix_path)

        if len(ean13) == 13 and ean13.isdigit():
            ean_barcode = treepoem.generate_barcode(
                barcode_type="ean13",
                data=ean13,
            ).convert("RGB")
            ean_barcode.save(ean_path)
        else:
            ean13 = ""

        prepare_logo_image(Image, HONEST_SIGN_LOGO_PATH, honest_sign_path)
        prepare_logo_image(Image, EAC_LOGO_PATH, eac_path)
    except Exception as error:
        for image_path in temporary_images:
            try:
                image_path.unlink()
            except OSError:
                pass
        if isinstance(error, DuplicateChzError):
            raise
        details = str(error)
        if "GS1badChecksum" in details or "Bad checksum" in details:
            raise DuplicateChzError("Не удалось сгенерировать штрихкод: неверная контрольная цифра.") from error
        raise DuplicateChzError(
            "Не удалось сгенерировать штрихкод. Проверьте код ЧЗ, EAN-13 и наличие Ghostscript."
        ) from error

    page_width, page_height = 75 * mm, 120 * mm
    pdf = canvas.Canvas(str(output_path), pagesize=(page_width, page_height))
    pdf.setTitle("Дубликат ЧЗ 75×120")
    pdf.setFillColor(colors.black)
    bold_font = register_label_font(pdfmetrics, TTFont)
    regular_font = register_regular_label_font(pdfmetrics, TTFont)

    draw_marking_logos(pdf, page_height, mm, honest_sign_path, eac_path)
    draw_75x120_product_details(
        pdf,
        product_info,
        raw_code,
        page_width,
        page_height,
        mm,
        bold_font,
        regular_font,
    )

    pdf.drawImage(
        str(datamatrix_path),
        48 * mm,
        8 * mm,
        width=22 * mm,
        height=22 * mm,
        preserveAspectRatio=True,
        mask="auto",
    )
    if ean13:
        pdf.drawImage(
            str(ean_path),
            3 * mm,
            7 * mm,
            width=43 * mm,
            height=17.5 * mm,
            preserveAspectRatio=False,
            mask="auto",
        )
        pdf.setFont(regular_font, label_75x120_font_size(6.2))
        pdf.drawCentredString(24.5 * mm, 25.5 * mm, ean13)

    draw_rotated_footer(pdf, product_info, page_width, mm, bold_font)
    pdf.showPage()
    pdf.save()

    for image_path in temporary_images:
        try:
            image_path.unlink()
        except OSError:
            pass

    return output_path


def short_code_text(raw_code, limit=31):
    return normalize_chz_text(raw_code).replace(GROUP_SEPARATOR, "<GS>")[:limit]


def register_label_font(pdfmetrics, tt_font):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/Library/Fonts/Arial Unicode Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]

    for font_path in candidates:
        if Path(font_path).exists():
            pdfmetrics.registerFont(tt_font("MarkingLabelFont", font_path))
            return "MarkingLabelFont"
    return "Helvetica-Bold"


def register_regular_label_font(pdfmetrics, tt_font):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for font_path in candidates:
        if Path(font_path).exists():
            pdfmetrics.registerFont(tt_font("MarkingLabelRegularFont", font_path))
            return "MarkingLabelRegularFont"
    return "Helvetica"


def prepare_logo_image(image_module, source_path, output_path):
    if not source_path.exists():
        raise DuplicateChzError(f"Не найден файл логотипа: {source_path.name}")

    with image_module.open(source_path) as image:
        source = image.convert("RGBA")
    grayscale = source.convert("L")
    alpha = grayscale.point(lambda value: 255 if value < 180 else 0)
    bounds = alpha.getbbox()
    if not bounds:
        raise DuplicateChzError(f"Изображение логотипа пустое: {source_path.name}")
    source.putalpha(alpha)
    source.crop(bounds).save(output_path)


def draw_marking_logos(pdf, page_height, mm, honest_sign_path, eac_path):
    pdf.drawImage(
        str(honest_sign_path),
        47 * mm,
        page_height - 11 * mm,
        width=16.5 * mm,
        height=5.8 * mm,
        preserveAspectRatio=False,
        mask="auto",
    )
    pdf.drawImage(
        str(eac_path),
        65 * mm,
        page_height - 10.8 * mm,
        width=7 * mm,
        height=5.5 * mm,
        preserveAspectRatio=False,
        mask="auto",
    )


def draw_75x120_product_details(
    pdf, product_info, raw_code, page_width, page_height, mm, bold_font, regular_font
):
    model = str(product_info.get("model_name") or "").strip()
    size = str(product_info.get("size") or "").strip()
    color = str(product_info.get("color") or "").strip()
    article = str(product_info.get("article") or "").strip()
    country = str(product_info.get("country") or "").strip()
    composition = str(product_info.get("composition") or "").strip()
    customer = str(product_info.get("customer") or MARKING_LABEL_CUSTOMER).strip()
    manufacturer = str(product_info.get("manufacturer") or MARKING_LABEL_MANUFACTURER).strip()

    draw_text_block(
        pdf, model, 4 * mm, page_height - 18 * mm, 30 * mm,
        bold_font, label_75x120_font_size(8), 3.5 * mm, 2,
    )
    draw_inline_field(pdf, "Цвет:", color, 36 * mm, page_height - 19 * mm, 35 * mm, mm, bold_font)
    draw_inline_field(pdf, "Размер:", size, 4 * mm, page_height - 31 * mm, 27 * mm, mm, bold_font)
    draw_inline_field(pdf, "Артикул:", article, 36 * mm, page_height - 31 * mm, 35 * mm, mm, bold_font)
    draw_inline_field(pdf, "Страна:", country, 4 * mm, page_height - 43 * mm, 40 * mm, mm, bold_font)
    draw_inline_field(pdf, "Состав:", composition, 4 * mm, page_height - 51 * mm, 67 * mm, mm, bold_font)

    pdf.setFont(bold_font, label_75x120_font_size(7.2))
    pdf.drawString(4 * mm, page_height - 61 * mm, "Заказчик:")
    draw_text_block(
        pdf, customer, 20 * mm, page_height - 59 * mm, page_width - 24 * mm,
        regular_font, label_75x120_font_size(5.5), 2.5 * mm, 4,
    )

    pdf.setFont(bold_font, label_75x120_font_size(7.2))
    pdf.drawString(4 * mm, page_height - 74 * mm, "Производитель:")
    draw_text_block(
        pdf, manufacturer, 29 * mm, page_height - 72 * mm, page_width - 33 * mm,
        regular_font, label_75x120_font_size(5.2), 2.4 * mm, 6,
    )

    try:
        code = extract_short_marking_code(raw_code)
    except DuplicateChzError:
        code = short_code_text(raw_code, limit=31)
    pdf.setFont(regular_font, label_75x120_font_size(7.2))
    pdf.drawCentredString(59 * mm, 34 * mm, code[:16])
    pdf.drawCentredString(59 * mm, 31 * mm, code[16:])


def draw_inline_field(pdf, label, value, x, y, width, mm, font_name):
    font_size = label_75x120_font_size(7.5)
    pdf.setFont(font_name, font_size)
    pdf.drawString(x, y, label)
    label_width = pdf.stringWidth(label, font_name, font_size) + 2 * mm
    draw_text_block(pdf, value, x + label_width, y, width - label_width, font_name, font_size, 3 * mm, 2)


def draw_text_block(pdf, value, x, y, width, font_name, font_size, leading, max_lines):
    pdf.setFont(font_name, font_size)
    lines = wrap_text_for_font(pdf, value, width, font_name, font_size)
    for index, line in enumerate(lines[:max_lines]):
        pdf.drawString(x, y - index * leading, line)


def wrap_text_for_font(pdf, value, width, font_name, font_size):
    words = str(value or "").split()
    if not words:
        return [""]
    wrapped_words = []
    for word in words:
        wrapped_words.extend(split_word_for_font(pdf, word, width, font_name, font_size))
    lines = []
    current = ""
    for word in wrapped_words:
        candidate = f"{current} {word}".strip()
        if not current or pdf.stringWidth(candidate, font_name, font_size) <= width:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def split_word_for_font(pdf, word, width, font_name, font_size):
    if pdf.stringWidth(word, font_name, font_size) <= width:
        return [word]

    parts = []
    current = ""
    for character in word:
        candidate = current + character
        if current and pdf.stringWidth(candidate, font_name, font_size) > width:
            parts.append(current)
            current = character
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def draw_rotated_footer(pdf, product_info, page_width, mm, font_name):
    model = str(product_info.get("model_name") or "").strip()
    size = str(product_info.get("size") or "").strip()
    pdf.saveState()
    pdf.translate(page_width - 4 * mm, 2.5 * mm)
    pdf.rotate(180)
    pdf.setFont(font_name, label_75x120_font_size(6.5))
    pdf.drawString(0, 0, model[:36])
    pdf.drawString(40 * mm, 0, f"Размер: {size}")
    pdf.restoreState()


def draw_product_details(pdf, product_info, short_code, x, y, width, mm, font_name):
    lines = [
        str(product_info.get("model_name") or "").strip(),
        f"Размер: {str(product_info.get('size') or '').strip()}",
        f"Страна производства: {str(product_info.get('country') or '').strip()}",
        short_code,
    ]

    pdf.setFont(font_name, 5.2)
    line_height = 3.4 * mm
    current_y = y
    for index, line in enumerate(lines):
        wrapped = wrap_label_text(line, width, pdf)
        if index > 0:
            current_y -= 0.5 * mm
        for part in wrapped:
            pdf.drawString(x, current_y, part)
            current_y -= line_height


def wrap_label_text(value, width, pdf):
    words = str(value or "").split()
    if not words:
        return [""]

    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or pdf.stringWidth(candidate) <= width:
            current = candidate
            continue
        lines.extend(split_oversized_word(current, width, pdf))
        current = word

    if current:
        lines.extend(split_oversized_word(current, width, pdf))
    return lines


def split_oversized_word(value, width, pdf):
    if pdf.stringWidth(value) <= width:
        return [value]

    result = []
    current = ""
    for char in value:
        candidate = current + char
        if current and pdf.stringWidth(candidate) > width:
            result.append(current)
            current = char
        else:
            current = candidate
    if current:
        result.append(current)
    return result
