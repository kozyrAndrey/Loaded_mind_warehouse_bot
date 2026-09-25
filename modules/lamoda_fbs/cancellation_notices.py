import re
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


SHIPMENT = "SHIPMENT"
RETURNS = "RETURNS"
OUTBOUND_ACTIVE_STATUSES = {
    "NEW",
    "NEW_TO_BE_CONFIRMED",
    "CONFIRMED",
    "CREATED",
    "PENDING",
    "READY_FOR_ASSEMBLY",
    "AWAITING_SHIPMENT",
    "READY_FOR_SHIPMENT",
}


def normalize_status(value):
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").strip().upper()).strip("_")


def count_outbound_orders(orders):
    return sum(
        normalize_status(order.get("status")) in OUTBOUND_ACTIVE_STATUSES
        for order in orders or []
    )


def count_ready_returns(return_items):
    return sum(
        normalize_status(item.get("status")) == "READY_TO_RETURN"
        for item in return_items or []
    )


def cancellation_notice(service_type, service_date):
    date_text = service_date.strftime("%d.%m.%Y")
    if service_type == SHIPMENT:
        title = "Требуется отмена отгрузочной машины Lamoda"
        text = (
            f"⚠️ {title}\n\n"
            f"Дата машины: {date_text}\n"
            "Причина: нет заказов для отгрузки.\n\n"
            "PDF с шаблонным текстом отмены приложен к сообщению."
        )
    elif service_type == RETURNS:
        title = "Требуется отмена возвратной машины Lamoda"
        text = (
            f"⚠️ {title}\n\n"
            f"Дата машины: {date_text}\n"
            "Причина: нет товаров, готовых к возврату.\n\n"
            "PDF с шаблонным текстом отмены приложен к сообщению."
        )
    else:
        raise ValueError(f"Неизвестный тип отмены: {service_type}")
    return title, text


def cancellation_document(service_type, service_date):
    date_text = service_date.strftime("%d.%m.%Y")
    if service_type == SHIPMENT:
        title = "Уведомление об отмене отгрузочной машины"
        text = (
            "В связи с отсутствием заказов для отгрузки просим отменить "
            f"подачу отгрузочной машины на {date_text}."
        )
        filename = f"otmena_otgruzochnoy_mashiny_{service_date:%Y-%m-%d}.pdf"
    elif service_type == RETURNS:
        title = "Уведомление об отмене возвратной машины"
        text = (
            "В связи с отсутствием товаров, готовых к возврату, просим отменить "
            f"подачу возвратной машины на {date_text}."
        )
        filename = f"otmena_vozvratnoy_mashiny_{service_date:%Y-%m-%d}.pdf"
    else:
        raise ValueError(f"Неизвестный тип отмены: {service_type}")

    output = BytesIO()
    font_name = _cancellation_pdf_font()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=25 * mm,
        bottomMargin=25 * mm,
        title=title,
        author="HMMLS",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CancellationTitle",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=15,
        leading=19,
        spaceAfter=14 * mm,
    )
    body_style = ParagraphStyle(
        "CancellationBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=11,
        leading=17,
    )
    document.build(
        [
            Paragraph(title, title_style),
            Paragraph("Добрый день!", body_style),
            Spacer(1, 7 * mm),
            Paragraph(text, body_style),
            Spacer(1, 14 * mm),
            Paragraph("С уважением,<br/>HMMLS", body_style),
        ]
    )
    return filename, output.getvalue()


def _cancellation_pdf_font():
    font_name = "LamodaCancellationFont"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    for font_path in candidates:
        if Path(font_path).exists():
            pdfmetrics.registerFont(TTFont(font_name, font_path))
            return font_name
    return "Helvetica"
