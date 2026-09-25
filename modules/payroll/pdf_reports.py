from pathlib import Path
import tempfile

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def _register_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]

    for font_path in candidates:
        if Path(font_path).exists():
            pdfmetrics.registerFont(TTFont("PayrollFont", font_path))
            return "PayrollFont"

    # Fallback. На некоторых системах кириллица может отображаться некорректно.
    return "Helvetica"


def create_payroll_pdf(text, filename="payroll_report.pdf"):
    font_name = _register_font()
    output_path = Path(tempfile.gettempdir()) / filename

    page_width, page_height = A4
    margin_x = 40
    margin_y = 40
    line_height = 14

    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.setFont(font_name, 10)

    y = page_height - margin_y

    for raw_line in text.splitlines():
        line = raw_line

        # Простая переноска слишком длинных строк.
        chunks = []
        while len(line) > 105:
            chunks.append(line[:105])
            line = line[105:]
        chunks.append(line)

        for chunk in chunks:
            if y < margin_y:
                c.showPage()
                c.setFont(font_name, 10)
                y = page_height - margin_y

            c.drawString(margin_x, y, chunk)
            y -= line_height

    c.save()
    return output_path
