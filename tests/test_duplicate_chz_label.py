import unittest

from modules.marking.duplicate_chz import (
    draw_75x120_product_details,
    label_75x120_font_size,
    wrap_text_for_font,
)


class FakePdf:
    def __init__(self):
        self.drawn_strings = []

    @staticmethod
    def stringWidth(value, font_name, font_size):
        return len(value) * font_size

    @staticmethod
    def setFont(font_name, font_size):
        pass

    def drawString(self, x, y, value):
        self.drawn_strings.append(value)

    def drawCentredString(self, x, y, value):
        self.drawn_strings.append(value)


class DuplicateChzLabelTests(unittest.TestCase):
    def test_75x120_font_is_reduced_by_two_pixels(self):
        self.assertAlmostEqual(label_75x120_font_size(8), 6.5)
        self.assertAlmostEqual(label_75x120_font_size(5.5), 4.0)

    def test_long_value_without_spaces_stays_inside_available_width(self):
        pdf = FakePdf()

        lines = wrap_text_for_font(pdf, "FW26-27-DJSB-LONG-ARTICLE", 30, "Font", 5)

        self.assertGreater(len(lines), 1)
        self.assertTrue(all(pdf.stringWidth(line, "Font", 5) <= 30 for line in lines))

    def test_75x120_label_does_not_contain_manufacturing_date(self):
        pdf = FakePdf()

        draw_75x120_product_details(
            pdf,
            {},
            "010460123456789021ABCDEFGHIJKLM",
            page_width=75,
            page_height=120,
            mm=1,
            bold_font="Bold",
            regular_font="Regular",
        )

        self.assertFalse(any("Дата изготов" in value for value in pdf.drawn_strings))


if __name__ == "__main__":
    unittest.main()
