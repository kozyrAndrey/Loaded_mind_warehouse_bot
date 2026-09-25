from io import BytesIO
from pathlib import Path
from textwrap import wrap

from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

def merge_pdfs(parts):
    writer = PdfWriter()
    for content in parts:
        reader = PdfReader(BytesIO(content))
        for page in reader.pages:
            writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _font_name():
    name = "LamodaDejaVu"
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    candidates = [
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ]
    for path in candidates:
        if path.exists():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    return "Helvetica"


def _draw_lines(pdf, lines, *, title=None):
    font = _font_name()
    width, height = A4
    y = height - 18 * mm
    if title:
        pdf.setFont(font, 14)
        pdf.drawString(15 * mm, y, title)
        y -= 10 * mm
    pdf.setFont(font, 9)
    for line in lines:
        if y < 15 * mm:
            pdf.showPage()
            pdf.setFont(font, 9)
            y = height - 15 * mm
        pdf.drawString(15 * mm, y, str(line)[:125])
        y -= 5 * mm


def create_manifest_pdf(manifest, shipment_id=""):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    lines = []
    if shipment_id:
        lines.append(f"Отгрузка Lamoda: {shipment_id}")
    for cargo in manifest:
        lines.extend([
            "",
            f"Грузовое место №{cargo['local_number']} · Lamoda palletId: {cargo.get('pallet_id') or '—'}",
        ])
        for pack in cargo["packs"]:
            lines.append(
                f"  {pack['pack_number']} · заказ {pack['order_id']} · item {pack['item_id']} · "
                f"{pack.get('product_name') or 'без названия'} · {pack.get('size') or '—'} · {pack.get('sku') or '—'}"
            )
    _draw_lines(pdf, lines, title="Состав грузовых мест Lamoda FBS")
    pdf.save()
    return output.getvalue()


def create_picking_list_pdf(packs, session_id=None):
    grouped = {}
    for pack in packs:
        key = (
            str(pack.get("external_sku") or pack.get("sku") or "").strip(),
            str(pack.get("product_name") or "Без названия").strip(),
            str(pack.get("size") or "").strip(),
        )
        grouped.setdefault(key, []).append(str(pack.get("order_id") or "—"))

    lines = [
        f"Сборка №{session_id or '—'} · заказов: {len({str(row.get('order_id')) for row in packs})} "
        f"· товаров: {len(packs)}",
        "",
    ]
    for index, ((article, name, size), order_ids) in enumerate(
        sorted(grouped.items(), key=lambda row: (row[0][0].casefold(), row[0][1].casefold(), row[0][2].casefold())),
        1,
    ):
        name_lines = wrap(f"{index}. {name}", width=105, break_long_words=False) or [f"{index}. {name}"]
        lines.extend(name_lines)
        lines.append(
            f"   Артикул: {article or '—'} · Размер: {size or '—'} · Количество: {len(order_ids)}"
        )
        order_text = "   Заказы: " + ", ".join(order_ids)
        lines.extend(wrap(order_text, width=115, subsequent_indent="            ", break_long_words=False))
        lines.append("")

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    _draw_lines(pdf, lines, title="Лист подбора товаров Lamoda FBS")
    pdf.save()
    return output.getvalue()


EXPORT_HEADERS = [
    "Партия", "Отгрузка", "Дата", "Заказ", "itemId", "packNumber",
    "Товар", "Размер", "SKU", "GTIN", "КИЗ",
]
REINTRO_EXTRA_HEADERS = [
    "Дата вывода", "Дата приёмки", "Состояние", "Причина брака",
    "returnItemId", "Статус возврата",
]
WITHDRAWAL_HEADERS = [
    "Номер товарной этикетки", "Название товара", "Код маркировки", "Цена продажи",
]


def create_marking_xlsx(rows, batch_type):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Коды Lamoda"
    if batch_type == "WITHDRAWAL":
        sheet.append(WITHDRAWAL_HEADERS)
        for row in rows:
            sheet.append([
                row.get("item_id"), row.get("product_name"),
                row.get("short_code"), row.get("sale_price"),
            ])
        for cell in sheet["D"][1:]:
            cell.number_format = '#,##0.00 "₽"'
    else:
        sheet.append(EXPORT_HEADERS + REINTRO_EXTRA_HEADERS)
        for row in rows:
            values = [
                row.get("batch_id"), row.get("shipment_id"), row.get("date"), row.get("order_id"),
                row.get("item_id"), row.get("pack_number"), row.get("product_name"), row.get("size"),
                row.get("sku"), row.get("gtin"), row.get("raw_code"),
                row.get("withdrawn_at"), row.get("return_received_at"), row.get("condition"),
                row.get("defect_reason"), row.get("return_item_id"), row.get("return_status"),
            ]
            sheet.append(values)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(max(len(str(cell.value or "")) for cell in column) + 2, 45)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def create_marking_pdf(rows, batch_type):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    label = "Возврат в оборот" if batch_type == "REINTRODUCTION" else "Вывод из оборота"
    lines = []
    if batch_type == "WITHDRAWAL":
        for index, row in enumerate(rows, 1):
            price = row.get("sale_price")
            price_text = f"{price:,.2f} ₽".replace(",", " ") if price is not None else "—"
            lines.extend([
                f"{index}. Товарная этикетка: {row.get('item_id') or '—'}",
                f"   Товар: {row.get('product_name') or '—'}",
                f"   Код маркировки: {row.get('short_code') or '—'}",
                f"   Цена продажи: {price_text}",
                "",
            ])
        _draw_lines(pdf, lines, title=f"Lamoda · {label} · партия №{rows[0].get('batch_id') if rows else '—'}")
        pdf.save()
        return output.getvalue()

    for index, row in enumerate(rows, 1):
        lines.extend([
            f"{index}. Заказ {row.get('order_id')} · pack {row.get('pack_number')} · item {row.get('item_id')}",
            f"   {row.get('product_name') or '—'} · размер {row.get('size') or '—'} · SKU {row.get('sku') or '—'}",
            f"   GTIN {row.get('gtin') or '—'}",
        ])
        raw_code = str(row.get("raw_code") or "—")
        for offset in range(0, len(raw_code), 100):
            lines.append(("   КИЗ: " if offset == 0 else "        ") + raw_code[offset:offset + 100])
    _draw_lines(pdf, lines, title=f"Lamoda · {label} · партия №{rows[0].get('batch_id') if rows else '—'}")
    pdf.save()
    return output.getvalue()
