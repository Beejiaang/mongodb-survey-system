"""阿拉伯数字输入格式及数值边界，不能由Python宽松转换替代。"""

import unittest

from numeric_input import parse_numeric_input
from questions import build_question, validate_answer


class NumericInputTests(unittest.TestCase):
    def test_ascii_numbers_sign_decimal_exponent_and_integer_precision(self):
        for raw, expected in (("0", 0), ("-18", -18), ("+18", 18), ("-2.5", -2.5),
                              (".5", .5), ("18.", 18), ("1e3", 1000), ("2E-2", .02),
                              (" 18 ", 18), (str(2**63 - 1), 2**63 - 1)):
            with self.subTest(raw=raw):
                self.assertEqual(parse_numeric_input(raw), expected)
        self.assertIsInstance(parse_numeric_input(str(2**63 - 1)), int)
        self.assertIsNone(parse_numeric_input("  "))

    def test_non_ascii_digits_words_units_separators_and_special_floats_rejected(self):
        for raw in ("十八", "１８", "١٨", "۱۸", "1８", "18岁", "1,000", "1_000",
                    "1 8", "nan", "NaN", "inf", "-Infinity", "1e", ".", "+", "−18"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "阿拉伯数字"):
                parse_numeric_input(raw)

    def test_valid_format_still_requires_finite_bson_number_and_question_limits(self):
        q = build_question("fill_blank", "数字", input_type="number", required=True)
        for raw in (str(2**63), str(-2**63 - 1), "1e309", "-1e309", ""):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_answer(q, parse_numeric_input(raw))
        q["validation"] = {"min_value": 0, "max_value": 120, "integer_only": True}
        for raw in ("18.5", "121", "-1"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_answer(q, parse_numeric_input(raw))
        self.assertEqual(validate_answer(q, parse_numeric_input("18.0")), 18)
