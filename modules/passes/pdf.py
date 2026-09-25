from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


CAPACITY_LABELS = {
    "up_to_3_5": "до 3,5 т. (газель, бычок, фургон)",
    "from_3_5_to_10": "от 3,5 до 10 т. (грузовик)",
    "from_10_to_20": "от 10 до 20 т. (крупнотоннажный грузовик)",
    "over_20": "свыше 20 т. (фура)",
}

MONTHS_GENITIVE = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def format_pass_date(day):
    return f"«{day.day:02d}» {MONTHS_GENITIVE[day.month]} {day.year}г."


def _register_fonts():
    regular_candidates = [
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Times New Roman.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ]
    bold_candidates = [
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "/Library/Fonts/Times New Roman Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ]

    def register(name, candidates):
        if name in pdfmetrics.getRegisteredFontNames():
            return name
        for candidate in candidates:
            if Path(candidate).exists():
                pdfmetrics.registerFont(TTFont(name, candidate))
                return name
        return "Times-Bold" if name.endswith("Bold") else "Times-Roman"

    return register("VisitorPassRegular", regular_candidates), register("VisitorPassBold", bold_candidates)


def _fit_font(text, font_name, start_size, max_width, minimum=6.5):
    size = float(start_size)
    while size > minimum and pdfmetrics.stringWidth(str(text), font_name, size) > max_width:
        size -= 0.25
    return size


def _draw_fitted(pdf, text, x, y, max_width, font_name, size=10, *, centered=False):
    text = str(text or "")
    fitted = _fit_font(text, font_name, size, max_width)
    pdf.setFont(font_name, fitted)
    if centered:
        pdf.drawCentredString(x + max_width / 2, y, text)
    else:
        pdf.drawString(x, y, text)


def _draw_multiline(pdf, text, x, top_y, width, font_name, size=8.2, leading=3.5 * mm):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and pdfmetrics.stringWidth(candidate, font_name, size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    pdf.setFont(font_name, size)
    for index, line in enumerate(lines):
        pdf.drawString(x, top_y - index * leading, line)


def _draw_vehicle_type_choice(
    pdf,
    vehicle_type,
    x,
    y,
    width,
    font_name,
    size=9.5,
    *,
    feminine=False,
    centered=False,
):
    """Оставляет оба варианта в бланке и подчёркивает выбранный."""
    text = "(легковая/грузовая)" if feminine else "Легковой / грузовой"
    selected = {
        (False, "passenger"): "Легковой",
        (False, "cargo"): "грузовой",
        (True, "passenger"): "легковая",
        (True, "cargo"): "грузовая",
    }.get((feminine, vehicle_type))
    fitted = _fit_font(text, font_name, size, width)
    text_width = pdfmetrics.stringWidth(text, font_name, fitted)
    start_x = x + (width - text_width) / 2 if centered else x
    pdf.setFont(font_name, fitted)
    pdf.drawString(start_x, y, text)
    if selected:
        selected_at = text.index(selected)
        prefix_width = pdfmetrics.stringWidth(text[:selected_at], font_name, fitted)
        selected_width = pdfmetrics.stringWidth(selected, font_name, fitted)
        underline_y = y - 0.75 * mm
        pdf.line(start_x + prefix_width, underline_y, start_x + prefix_width + selected_width, underline_y)


def create_visitor_pass_pdf(data):
    """Создаёт одностраничный печатный бланк без служебной подсветки."""
    regular, bold = _register_fonts()
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    width, height = A4
    left, right = 12 * mm, 198 * mm
    full_name = " ".join(
        [data.get("surname", ""), data.get("first_name", ""), data.get("patronymic") or "-"]
    ).strip()
    pass_date = format_pass_date(data["date"])
    has_vehicle = bool(data.get("has_vehicle"))
    vehicle_make = data.get("vehicle_make", "") if has_vehicle else ""
    license_plate = data.get("license_plate", "") if has_vehicle else ""
    vehicle_type = data.get("vehicle_type", "") if has_vehicle else ""
    capacity = CAPACITY_LABELS.get(data.get("capacity_code", ""), "") if vehicle_type == "cargo" else ""

    pdf.setTitle("Разовый пропуск")
    pdf.setAuthor("HMMLS Warehouse Bot")
    pdf.setLineWidth(0.55)
    pdf.setFont(regular, 9)
    pdf.drawRightString(right, height - 12 * mm, "БЦ «Красный богатырь»")

    pdf.setFont(bold, 13)
    title_y = height - 20 * mm
    title_prefix = "ЗАЯВКА на "
    title_width = pdfmetrics.stringWidth(title_prefix + pass_date, bold, 13)
    title_x = (width - title_width) / 2
    pdf.drawString(title_x, title_y, title_prefix)
    pdf.drawString(title_x + pdfmetrics.stringWidth(title_prefix, bold, 13), title_y, pass_date)
    pdf.setFont(bold, 12.5)
    pdf.drawCentredString(width / 2, title_y - 5.5 * mm, "на оформление разового пропуска на посетителя")
    pdf.drawCentredString(
        width / 2,
        title_y - 11 * mm,
        "или автотранспортное средство, прибывшего в ИП Ахмед Дамир Султанович",
    )
    pdf.setFont(regular, 7.5)
    pdf.drawCentredString(width * 0.73, title_y - 15 * mm, "(наименование организации)")

    # Основная таблица заявления.
    x0, x1, x2, x3 = left, 26 * mm, 109 * mm, right
    table_top = title_y - 17 * mm
    rows = [9.5, 5, 11, 6.5, 5, 2.5, 6, 13, 9, 6, 8, 5, 5, 8, 5]
    row_ys = [table_top]
    for row_height in rows:
        row_ys.append(row_ys[-1] - row_height * mm)
    table_bottom = row_ys[-1]
    pdf.rect(x0, table_bottom, x3 - x0, table_top - table_bottom)
    pdf.line(x1, table_bottom, x1, table_top)
    pdf.line(x2, table_bottom, x2, table_top)
    for y in row_ys[1:-1]:
        pdf.line(x0, y, x3, y)
    pdf.setFillGray(0.55)
    pdf.rect(x0, row_ys[6], x3 - x0, rows[5] * mm, fill=1, stroke=1)
    pdf.setFillGray(0)

    def row_center(index):
        return (row_ys[index] + row_ys[index + 1]) / 2

    pdf.setFont(bold, 9)
    pdf.drawCentredString((x0 + x1) / 2, row_center(0) + 1.5 * mm, "№№")
    pdf.drawCentredString((x0 + x1) / 2, row_center(0) - 2 * mm, "ПП")
    pdf.drawCentredString((x1 + x2) / 2, row_center(0) + 1.5 * mm, "Наименование")
    pdf.drawCentredString((x1 + x2) / 2, row_center(0) - 2 * mm, "позиций заявки")
    pdf.drawCentredString((x2 + x3) / 2, row_center(0) + 1.5 * mm, "Поля")
    pdf.drawCentredString((x2 + x3) / 2, row_center(0) - 2 * mm, "для заполнения требуемых сведений")

    labels = [
        (1, "1.", "Фамилия, имя и отчество посетителя"),
        (2, "2.", "Наименование организации, в которую\nследует посетитель"),
        (3, "2.1-3", "Строение / этаж / офис"),
        (4, "2.4.", "ИНН организации"),
        (6, "3.", "Марка автомобиля"),
        (7, "4.", "Тип трансп./ср. (мотоцикл, мопед, скутер);\nлегковой / грузовой (в т.ч. автобус) / прицеп"),
        (8, "5.", "Грузоподъёмность трансп./ср. (только для\nгрузовых), тонн"),
        (9, "6.", "Государственный регистр. номер"),
        (10, "7.", "С посетителем следуют дети до 14 лет"),
        (11, "7.1.", "Имя ребёнка"),
        (12, "7.2.", "Возраст ребёнка"),
        (13, "7.3.", "Ф.И.О. владельца постоянного пропуска,\nсопровождающего ребёнка до 14 лет"),
        (14, "7.3.1.", "№ постоянного пропуска"),
    ]
    for index, number, label in labels:
        pdf.setFont(regular, 8.6)
        pdf.drawCentredString((x0 + x1) / 2, row_center(index) - 1.2 * mm, number)
        _draw_multiline(pdf, label, x1 + 2 * mm, row_ys[index] - 3.4 * mm, x2 - x1 - 4 * mm, regular)

    _draw_fitted(pdf, full_name, x2 + 2 * mm, row_center(1) - 1.2 * mm, x3 - x2 - 4 * mm, regular, 9.5)
    _draw_fitted(pdf, "ИП Ахмед Дамир Султанович", x2 + 2 * mm, row_center(2), x3 - x2 - 4 * mm, bold, 9)
    pdf.setFont(bold, 9)
    pdf.drawCentredString((x2 + x3) / 2, row_center(3) - 1.2 * mm, "4        /        1        /        9")
    _draw_fitted(pdf, vehicle_make, x2 + 2 * mm, row_center(6) - 1.2 * mm, x3 - x2 - 4 * mm, regular, 9.5)
    _draw_vehicle_type_choice(
        pdf,
        vehicle_type,
        x2 + 2 * mm,
        row_center(7) + 1.5 * mm,
        x3 - x2 - 4 * mm,
        regular,
    )
    _draw_fitted(pdf, capacity, x2 + 2 * mm, row_center(8) - 1.2 * mm, x3 - x2 - 4 * mm, regular, 8.8)
    _draw_fitted(pdf, license_plate, x2 + 2 * mm, row_center(9) - 1.2 * mm, x3 - x2 - 4 * mm, regular, 9.5)
    pdf.setFont(regular, 9)
    pdf.drawString(x2 + 2 * mm, row_center(14) - 1.2 * mm, "25311")

    # Подпись организации и дата заявления.
    signature_y = table_bottom - 5 * mm
    pdf.setFont(regular, 8.5)
    pdf.drawString(left + 2 * mm, signature_y, "Руководитель организации")
    pdf.drawString(left + 2 * mm, signature_y - 4.5 * mm, "(или уполномоченное лицо")
    pdf.drawString(left + 2 * mm, signature_y - 9 * mm, "от организации)")
    pdf.line(103 * mm, signature_y - 2 * mm, 128 * mm, signature_y - 2 * mm)
    pdf.drawCentredString(115.5 * mm, signature_y - 6 * mm, "(подпись)")
    pdf.line(151 * mm, signature_y - 2 * mm, 196 * mm, signature_y - 2 * mm)
    pdf.drawCentredString(173.5 * mm, signature_y - 6 * mm, "(Фамилия И.О.)")
    _draw_fitted(pdf, pass_date.replace("г.", "г."), 153 * mm, signature_y - 11 * mm, 43 * mm, regular, 8.5, centered=True)
    pdf.setFont(regular, 9)
    pdf.drawRightString(right, signature_y - 16 * mm, "МП")

    separator_y = signature_y - 24 * mm
    pdf.setDash(2, 1)
    pdf.line(left, separator_y, right, separator_y)
    pdf.setDash()
    pdf.setFont(bold, 11)
    pdf.drawCentredString(width / 2, separator_y - 6 * mm, "Разовый пропуск №____________")

    # Отрывная часть пропуска.
    ticket_top = separator_y - 8 * mm
    ticket_bottom = 18 * mm
    stub_right, coupon_left = 37 * mm, 164 * mm
    pdf.rect(left, ticket_bottom, right - left, ticket_top - ticket_bottom)
    pdf.line(stub_right, ticket_bottom, stub_right, ticket_top)
    pdf.line(coupon_left, ticket_bottom, coupon_left, ticket_top)

    pdf.setFont(regular, 8)
    _draw_multiline(pdf, "Корешок пропуска №________", left + 1.5 * mm, ticket_top - 4 * mm, stub_right - left - 3 * mm, regular, 7.8)

    center_x = stub_right + 2 * mm
    center_width = coupon_left - stub_right - 4 * mm
    cursor_y = ticket_top - 5 * mm
    pdf.setFont(regular, 8.5)
    pdf.drawString(center_x, cursor_y, "Разрешаю________________")
    _draw_fitted(pdf, pass_date, center_x + 50 * mm, cursor_y, center_width - 50 * mm, regular, 8.5, centered=True)
    pdf.setFont(regular, 6.5)
    pdf.drawString(center_x + 25 * mm, cursor_y - 4 * mm, "(подпись)")
    pdf.drawString(center_x + 83 * mm, cursor_y - 4 * mm, "(дата)")

    cursor_y -= 12 * mm
    fio_ticket = f"Ф. {data.get('surname', '')}  И. {data.get('first_name', '')}  О. {data.get('patronymic') or '-'}"
    _draw_fitted(pdf, fio_ticket, center_x, cursor_y, center_width, regular, 9.2)
    cursor_y -= 9 * mm
    pdf.setFont(regular, 8.5)
    pdf.drawString(center_x, cursor_y, "Куда")
    _draw_fitted(pdf, "ИП Ахмед Дамир Султанович", center_x + 13 * mm, cursor_y, center_width - 13 * mm, bold, 9)
    cursor_y -= 9 * mm
    pdf.setFont(regular, 8.5)
    pdf.drawString(center_x, cursor_y, "Выдано по ______________________________________________")
    cursor_y -= 7 * mm
    car_line = "Автомашина"
    if has_vehicle:
        car_line += f"  {vehicle_make}    гос/№ {license_plate}"
    else:
        car_line += " __________________  гос/№ ____________"
    _draw_fitted(pdf, car_line, center_x, cursor_y, center_width, regular, 8.5)
    cursor_y -= 5 * mm
    _draw_vehicle_type_choice(
        pdf,
        vehicle_type,
        center_x,
        cursor_y,
        center_width,
        regular,
        size=8.5,
        feminine=True,
        centered=True,
    )
    cursor_y -= 6 * mm
    pdf.drawString(center_x, cursor_y, "Проверил Д.Б.П.")
    pdf.line(center_x + 29 * mm, cursor_y - 0.8 * mm, center_x + center_width, cursor_y - 0.8 * mm)
    cursor_y -= 12 * mm
    pdf.drawCentredString(center_x + 23 * mm, cursor_y, "МП")
    pdf.drawString(center_x + 61 * mm, cursor_y, "Время прибытия")
    pdf.line(center_x + 91 * mm, cursor_y - 0.8 * mm, center_x + center_width, cursor_y - 0.8 * mm)
    cursor_y -= 8 * mm
    pdf.line(center_x, cursor_y, center_x + center_width, cursor_y)
    cursor_y -= 7 * mm
    pdf.drawString(center_x + 50 * mm, cursor_y, "Убыл «")
    pdf.line(center_x + 64 * mm, cursor_y - 0.8 * mm, center_x + 76 * mm, cursor_y - 0.8 * mm)
    pdf.drawString(center_x + 77 * mm, cursor_y, "»")
    pdf.line(center_x + 81 * mm, cursor_y - 0.8 * mm, center_x + 105 * mm, cursor_y - 0.8 * mm)
    pdf.drawString(center_x + 106 * mm, cursor_y, "20")
    pdf.line(center_x + 113 * mm, cursor_y - 0.8 * mm, center_x + 118 * mm, cursor_y - 0.8 * mm)
    pdf.drawString(center_x + 119 * mm, cursor_y, "г.")
    cursor_y -= 6 * mm
    pdf.line(center_x + 50 * mm, cursor_y - 0.8 * mm, center_x + 79 * mm, cursor_y - 0.8 * mm)
    pdf.drawString(center_x + 80 * mm, cursor_y, "час.")
    pdf.line(center_x + 89 * mm, cursor_y - 0.8 * mm, center_x + 114 * mm, cursor_y - 0.8 * mm)
    pdf.drawString(center_x + 115 * mm, cursor_y, "мин.")
    signature_line_y = ticket_bottom + 5 * mm
    pdf.drawCentredString(center_x + 23 * mm, signature_line_y, "МП")
    pdf.drawString(center_x + 39 * mm, signature_line_y, "Подпись")
    pdf.line(center_x + 56 * mm, signature_line_y - 0.8 * mm, center_x + 81 * mm, signature_line_y - 0.8 * mm)
    pdf.drawCentredString(center_x + 84 * mm, signature_line_y, "/")
    pdf.line(center_x + 87 * mm, signature_line_y - 0.8 * mm, center_x + center_width, signature_line_y - 0.8 * mm)

    coupon_x = coupon_left + 2 * mm
    coupon_width = right - coupon_left - 4 * mm
    _draw_multiline(pdf, "Контрольный талон пропуска №__________", coupon_x, ticket_top - 4 * mm, coupon_width, regular, 7.8)
    coupon_y = ticket_top - 20 * mm
    _draw_fitted(pdf, f"Ф. {data.get('surname', '')}", coupon_x, coupon_y, coupon_width, regular, 8.5)
    _draw_fitted(pdf, f"И. {data.get('first_name', '')}", coupon_x, coupon_y - 6 * mm, coupon_width, regular, 8.5)
    _draw_fitted(pdf, f"О. {data.get('patronymic') or '-'}", coupon_x, coupon_y - 12 * mm, coupon_width, regular, 8.5)
    _draw_multiline(pdf, "Куда  ИП Ахмед Дамир Султанович", coupon_x, coupon_y - 22 * mm, coupon_width, regular, 7.8)

    pdf.showPage()
    pdf.save()
    output.seek(0)
    return output.getvalue()
