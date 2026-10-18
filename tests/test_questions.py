"""题目及答案规则：正常、边界、非法输入和存储格式往返。"""

import unittest
from copy import deepcopy

from bson import BSON

from questions import build_question, validate_answer, validate_question, validate_questions


OPTIONS = [{"id": key, "text": label} for key, label in
           (("a", "苹果"), ("b", "香蕉"), ("c", "西瓜"), ("d", "葡萄"))]


def choice(kind="single_choice", **kwargs):
    return build_question(kind, "喜欢的水果", options=OPTIONS, **kwargs)


def blank(subtype="text", **kwargs):
    return build_question("fill_blank", "请输入", input_type=subtype, **kwargs)


class QuestionTests(unittest.TestCase):
    def assert_invalid_answers(self, question, values):
        for value in values:
            with self.subTest(answer=value), self.assertRaises(ValueError):
                validate_answer(question, value)

    def test_all_types_have_defaults_and_independent_stable_ids(self):
        questions = [choice(), choice("multiple_choice"), blank(), blank("number")]
        self.assertEqual(len({q["id"] for q in questions}), 4)
        for question in questions:
            self.assertEqual(validate_question(question), question)
            self.assertFalse(question["required"])
            self.assertEqual(question["jump_rules"], [])
        edited = choice(question_id=questions[0]["id"])
        self.assertEqual(edited["id"], questions[0]["id"])

    def test_bson_round_trip_preserves_question_configuration(self):
        original = {"questions": [choice(), choice("multiple_choice"), blank(),
                                  blank("number", validation={"min_value": 0.5})]}
        stored = BSON(BSON.encode(original)).decode()
        self.assertEqual(validate_questions(stored["questions"]), original["questions"])

    def test_normalization_does_not_mutate_inputs(self):
        source = choice()
        source["title"] = "  喜欢的水果  "
        source["options"][0]["text"] = " 苹果 "
        before = deepcopy(source)
        clean = validate_question(source)
        self.assertEqual(source, before)
        self.assertEqual(clean["title"], "喜欢的水果")
        self.assertEqual(clean["options"][0]["text"], "苹果")
        clean["options"][0]["text"] = "改变"
        self.assertEqual(source, before)

    def test_bson_int64_limits_and_answers_remain_valid_after_loading(self):
        q = blank("number", validation={"max_value": 2**63 - 1, "integer_only": True})
        document = BSON(BSON.encode({"question": q, "answer": 13800138000})).decode()
        self.assertEqual(validate_answer(document["question"], document["answer"]),
                         13800138000)
        text = blank(validation={"max_length": 2**32})
        loaded = BSON(BSON.encode(text)).decode()
        self.assertEqual(validate_answer(loaded, "中文"), "中文")

    def test_question_list_preserves_order_and_detects_duplicate_ids(self):
        first, second = choice(), blank()
        self.assertEqual(validate_questions([second, first]), [second, first])
        self.assertEqual(validate_questions([]), [])
        for value in ([first, first], {}, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_questions(value)

    def test_invalid_question_fields_types_and_missing_fields(self):
        mutations = [("id", " "), ("title", ""), ("title", 1), ("required", 1),
                     ("type", "unknown"), ("type", []), ("validation", []),
                     ("extra", "value")]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_question(dict(choice(), **{key: value}))
        for key in choice():
            q = choice()
            del q[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_question(q)
        with self.assertRaises(ValueError):
            validate_question(None)

    def test_options_require_unique_ids_and_nonempty_labels(self):
        invalid = [None, {}, [], OPTIONS[:1], [OPTIONS[0], OPTIONS[0]],
                   [{"id": "a", "text": " "}, OPTIONS[1]],
                   [{"id": [], "text": "苹果"}, OPTIONS[1]],
                   [{"id": "a", "text": "苹果", "other": 1}, OPTIONS[1]]]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_question(dict(choice(), options=options))

    def test_wrong_subtype_or_cross_type_configuration_is_rejected(self):
        for subtype in (None, "email", [], 1):
            with self.subTest(subtype=subtype), self.assertRaises(ValueError):
                blank(subtype)
        for kind, kwargs in (
            ("single_choice", {"options": OPTIONS, "input_type": "text"}),
            ("single_choice", {"options": OPTIONS, "validation": {"min_choices": 1}}),
            ("fill_blank", {"input_type": "text", "options": OPTIONS}),
            ("fill_blank", {"input_type": "text", "validation": {"min_value": 1}}),
            ("fill_blank", {"input_type": "text", "validation": []}),
        ):
            with self.subTest(kind=kind, kwargs=kwargs), self.assertRaises(ValueError):
                build_question(kind, "题目", **kwargs)

    def test_pending_jump_rules_are_explicitly_rejected(self):
        for rules in (None, {}, [{"target": "q2"}]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                validate_question(dict(choice(), jump_rules=rules))

    def test_invalid_count_and_length_limits(self):
        for make, lower, upper in (
            (lambda **kw: choice("multiple_choice", **kw), "min_choices", "max_choices"),
            (blank, "min_length", "max_length"),
        ):
            for key in (lower, upper):
                for value in (-1, 1.5, True, "2", [], 2**63):
                    with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                        make(validation={key: value})
            with self.assertRaises(ValueError):
                make(validation={lower: 3, upper: 2})
            with self.assertRaises(ValueError):
                make(required=True, validation={upper: 0})

    def test_choice_limits_cannot_exceed_options(self):
        for key in ("min_choices", "max_choices"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                choice("multiple_choice", validation={key: 5})

    def test_invalid_numeric_rules_and_impossible_integer_interval(self):
        for key in ("min_value", "max_value"):
            for value in (True, "1", float("nan"), float("inf"), -float("inf"),
                          [], 2**63):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    blank("number", validation={key: value})
        for rules in ({"min_value": 2, "max_value": 1}, {"integer_only": 1},
                      {"min_value": 0.1, "max_value": 0.9, "integer_only": True}):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                blank("number", validation=rules)

    def test_required_and_optional_absence_for_every_type(self):
        for q in (choice(), choice("multiple_choice"), blank(), blank("number")):
            self.assertIsNone(validate_answer(q, None))
            q["required"] = True
            self.assert_invalid_answers(q, [None])
        for q, empty in ((choice("multiple_choice"), []), (blank(), " \t\n")):
            self.assertIsNone(validate_answer(q, empty))
            q["required"] = True
            self.assert_invalid_answers(q, [empty])

    def test_single_choice_valid_id_and_wrong_shapes(self):
        q = choice(required=True)
        self.assertEqual(validate_answer(q, "a"), "a")
        self.assert_invalid_answers(q, ["", "苹果", "unknown", ["a"], {}, True, 1])

    def test_multiple_choice_inclusive_limits_and_wrong_shapes(self):
        q = choice("multiple_choice", required=True,
                   validation={"min_choices": 2, "max_choices": 3})
        for value in (["a", "b"], ["a", "b", "c"]):
            self.assertEqual(validate_answer(q, value), value)
        self.assert_invalid_answers(q, [[], ["a"], ["a", "b", "c", "d"],
                                       ["a", "a"], ["a", "unknown"],
                                       ["a", {}], "ab", {}, True])

    def test_exact_choice_count(self):
        q = choice("multiple_choice", validation={"min_choices": 2, "max_choices": 2})
        self.assertEqual(validate_answer(q, ["a", "d"]), ["a", "d"])
        self.assert_invalid_answers(q, [["a"], ["a", "b", "c"]])

    def test_optional_omission_bypasses_minimum_but_supplied_answer_does_not(self):
        q = choice("multiple_choice", validation={"min_choices": 2})
        self.assertIsNone(validate_answer(q, []))
        self.assert_invalid_answers(q, [["a"]])
        q = blank(validation={"min_length": 2})
        self.assertIsNone(validate_answer(q, " "))
        self.assert_invalid_answers(q, ["字"])

    def test_required_multiple_choice_requires_selection_even_when_minimum_is_zero(self):
        q = choice("multiple_choice", required=True, validation={"min_choices": 0})
        self.assert_invalid_answers(q, [[]])
        self.assertEqual(validate_answer(q, ["a"]), ["a"])

    def test_text_length_counts_unicode_codepoints_and_preserves_content(self):
        q = blank(required=True, validation={"min_length": 2, "max_length": 3})
        for text in ("中文", "中A。", "😀a", " a "):
            self.assertEqual(validate_answer(q, text), text)
        self.assert_invalid_answers(q, ["中", "abcd", "   ", 12, [], {}])

    def test_unlimited_text_and_numbers(self):
        self.assertEqual(validate_answer(blank(), "字" * 10000), "字" * 10000)
        q = blank("number")
        for value in (-12.25, 0, 120.5, 2**63 - 1, -(2**63)):
            self.assertEqual(validate_answer(q, value), value)

    def test_numeric_range_is_inclusive_and_zero_is_a_valid_answer(self):
        q = blank("number", required=True,
                  validation={"min_value": 0, "max_value": 120, "integer_only": True})
        for value in (0, 120, 18, 18.0):
            self.assertEqual(validate_answer(q, value), value)
        self.assert_invalid_answers(q, [-1, 121, 18.5])

    def test_decimal_limits_and_answers(self):
        q = blank("number", validation={"min_value": -0.5, "max_value": 1.5})
        for value in (-0.5, 0, 0.25, 1.5):
            self.assertEqual(validate_answer(q, value), value)
        self.assert_invalid_answers(q, [-0.51, 1.51])

    def test_numbers_reject_nonfinite_bool_strings_and_bson_overflow(self):
        self.assert_invalid_answers(blank("number"), [True, False, "18", "", [], {},
                                    float("nan"), float("inf"), -float("inf"),
                                    2**63, -(2**63) - 1])

    def test_answer_result_does_not_share_input_list(self):
        answer = ["a", "b"]
        result = validate_answer(choice("multiple_choice"), answer)
        result.append("c")
        self.assertEqual(answer, ["a", "b"])

    def test_answer_ids_survive_reordering_and_option_label_editing(self):
        q = choice()
        q["options"].reverse()
        next(option for option in q["options"] if option["id"] == "a")["text"] = "红苹果"
        self.assertEqual(validate_answer(q, "a"), "a")


if __name__ == "__main__":
    unittest.main()
