from pathlib import Path
import tempfile

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from modules.consumables.storage import format_quantity


def register_consumables_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for font_path in candidates:
        if Path(font_path).exists():
            pdfmetrics.registerFont(TTFont("ConsumablesFont", font_path))
            return "ConsumablesFont"
    return "Helvetica"


def create_inventory_count_pdf(records, counted_by_name="", filename="consumables_inventory.pdf"):
    font_name = register_consumables_font()
    output_path = Path(tempfile.gettempdir()) / filename
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ConsumablesTitle",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=14,
        leading=17,
    )
    body_style = ParagraphStyle(
        "ConsumablesBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8,
        leading=10,
    )
    header_style = ParagraphStyle(
        "ConsumablesHeader",
        parent=body_style,
        textColor=colors.white,
        fontSize=7,
        leading=8,
    )
    title = "Пересчет расходников"
    if counted_by_name:
        title += f" — завершил: {counted_by_name}"
    rows = [
        [
            Paragraph("№", header_style),
            Paragraph("Расходник", header_style),
            Paragraph("Система", header_style),
            Paragraph("Факт", header_style),
            Paragraph("Разница", header_style),
            Paragraph("Считал", header_style),
        ]
    ]
    for index, record in enumerate(records, start=1):
        difference = float(record["difference"] or 0)
        sign = "+" if difference > 0 else ""
        unit = str(record.get("unit") or "шт")
        rows.append(
            [
                Paragraph(str(index), body_style),
                Paragraph(str(record.get("item_name") or "—"), body_style),
                Paragraph(f"{format_quantity(record.get('system_quantity'))} {unit}", body_style),
                Paragraph(f"{format_quantity(record.get('counted_quantity'))} {unit}", body_style),
                Paragraph(f"{sign}{format_quantity(difference)} {unit}", body_style),
                Paragraph(str(record.get("counted_by_name") or "—"), body_style),
            ]
        )
    table = Table(rows, colWidths=[9 * mm, 57 * mm, 27 * mm, 27 * mm, 28 * mm, 33 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5D50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8C2BE")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F7FAF8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F7FAF8"), colors.white]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    document.build([Paragraph(title, title_style), Spacer(1, 6 * mm), table])
    return output_path


def create_consumables_stock_pdf(items, filename="consumables_stock.pdf"):
    font_name = register_consumables_font()
    output_path = Path(tempfile.gettempdir()) / filename
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ConsumablesStockTitle",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=14,
        leading=17,
    )
    body_style = ParagraphStyle(
        "ConsumablesStockBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=11,
    )
    header_style = ParagraphStyle(
        "ConsumablesStockHeader",
        parent=body_style,
        textColor=colors.white,
        fontSize=8,
        leading=10,
    )
    rows = [
        [
            Paragraph("№", header_style),
            Paragraph("Расходник", header_style),
            Paragraph("Остаток", header_style),
            Paragraph("Ед.", header_style),
        ]
    ]
    for index, item in enumerate(items, start=1):
        rows.append(
            [
                Paragraph(str(index), body_style),
                Paragraph(str(item.get("name") or "—"), body_style),
                Paragraph(format_quantity(item.get("current_quantity")), body_style),
                Paragraph(str(item.get("unit") or "шт"), body_style),
            ]
        )
    table = Table(rows, colWidths=[12 * mm, 105 * mm, 28 * mm, 20 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5D50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8C2BE")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F7FAF8"), colors.white]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    document.build([Paragraph("Остатки расходников", title_style), Spacer(1, 6 * mm), table])
    return output_path
