"""跳转语义、路径、配置页面、并发保护及旧结构兼容性。"""

from copy import deepcopy
import os
import re
import unittest
from uuid import uuid4

from pymongo.errors import OperationFailure

from app import create_app
from database import create_client, initialize_database
from jumps import evaluate_path, next_question_id, question_revision
from questions import build_question, validate_question, validate_questions
from surveys import add_question, create_survey, publish_survey, save_jump_rules
from survey_schema import SURVEY_VALIDATOR, PRE_JUMP_SURVEY_VALIDATOR
from users import build_user_document


def rule(op, value, target="q3"):
    return {"condition": {"operator": op, "value": value}, "target_question_id": target}


def questions(kind="multiple_choice", required=False):
    options = [{"id": x, "text": x.upper()} for x in "abc"]
    if kind in ("single_choice", "multiple_choice"):
        first = build_question(kind, "选择项目", options=options, required=required, question_id="q1")
    else:
        first = build_question("fill_blank", "数字或文本", input_type=kind, required=required, question_id="q1")
    return [first] + [build_question("fill_blank", f"后续题{i}", input_type="text",
                                     question_id=f"q{i}", required=True) for i in (2, 3, 4)]


class JumpLogicTests(unittest.TestCase):
    def test_contains_all_allows_extras_but_requires_every_specified_option(self):
        qs = questions()
        qs[0]["jump_rules"] = [rule("contains_all", ["a", "b"])]
        for answer, target in ((["a", "b"], "q3"), (["c", "b", "a"], "q3"),
                               (["a"], "q2"), (["b", "c"], "q2"), ([], "q2")):
            self.assertEqual(next_question_id(qs, "q1", answer), target)

    def test_equals_set_rejects_both_missing_and_extra_options_and_ignores_order(self):
        qs = questions()
        qs[0]["jump_rules"] = [rule("equals_set", ["a", "b"])]
        for answer, target in ((["a", "b"], "q3"), (["b", "a"], "q3"),
                               (["a", "b", "c"], "q2"), (["a"], "q2"), ([], "q2")):
            self.assertEqual(next_question_id(qs, "q1", answer), target)

    def test_first_matching_rule_wins_and_reordering_changes_result(self):
        qs = questions()
        qs[0]["jump_rules"] = [rule("contains_all", ["a"], "q3"), rule("equals_set", ["a", "b"], "q4")]
        self.assertEqual(next_question_id(qs, "q1", ["a", "b"]), "q3")
        qs[0]["jump_rules"].reverse()
        self.assertEqual(next_question_id(qs, "q1", ["a", "b"]), "q4")
        self.assertEqual(next_question_id(qs, "q1", ["a", "b", "c"]), "q3")

    def test_single_choice_and_optional_blank_default_to_next(self):
        qs = questions("single_choice")
        qs[0]["jump_rules"] = [rule("equals", "a")]
        for answer, expected in (("a", "q3"), ("b", "q2"), (None, "q2")):
            self.assertEqual(next_question_id(qs, "q1", answer), expected)

    def test_numeric_operators_including_zero_negative_and_inclusive_boundaries(self):
        cases = [("eq", 0, [(0, True), (1, False)]),
                 ("lt", 0, [(-1, True), (0, False)]),
                 ("lte", 0, [(0, True), (0.1, False)]),
                 ("gt", 0, [(0.1, True), (0, False)]),
                 ("gte", 0, [(0, True), (-0.1, False)]),
                 ("between", [-1, 2], [(-1, True), (2, True), (2.1, False), (-1.1, False)])]
        for op, value, answers in cases:
            qs = questions("number")
            qs[0]["jump_rules"] = [rule(op, value)]
            for answer, matched in answers:
                with self.subTest(op=op, answer=answer):
                    self.assertEqual(next_question_id(qs, "q1", answer), "q3" if matched else "q2")
            self.assertEqual(next_question_id(qs, "q1", None), "q2")

    def test_validate_answer_before_evaluating_condition(self):
        qs = questions(required=True)
        qs[0]["validation"] = {"min_choices": 2, "max_choices": 2}
        qs[0]["jump_rules"] = [rule("contains_all", ["a"])]
        for answer in ([], ["a"], ["a", "a"], ["a", "z"], ["a", "b", "c"]):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                next_question_id(qs, "q1", answer)
        qs = questions("number", required=True)
        qs[0]["validation"].update(min_value=0, max_value=120, integer_only=True)
        qs[0]["jump_rules"] = [rule("lt", 18)]
        for answer in (-1, 2.5, True, float("nan"), "12", None):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                next_question_id(qs, "q1", answer)

    def test_reject_bad_conditions_wrong_type_unknown_options_and_combinations(self):
        bad = [rule("equals_set", []), rule("contains_all", ["a", "a"]),
               rule("contains_all", ["z"]), rule("equals", "a"),
               rule("contains_all", "a"), rule([], ["a"]),
               {"condition": {"operator": "contains_all", "value": ["a"], "and": []}, "target_question_id": "q3"}]
        for value in bad:
            qs = questions()
            qs[0]["jump_rules"] = [value]
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_questions(qs)
        qs = questions("text")
        qs[0]["jump_rules"] = [rule("equals", "a")]
        with self.assertRaises(ValueError):
            validate_questions(qs)

    def test_invalid_numeric_rule_values(self):
        for op, value in (("eq", True), ("eq", float("inf")), ("eq", "1"),
                          ("between", [2, 1]), ("between", [0]), ("between", [0, float("nan")])):
            qs = questions("number")
            qs[0]["jump_rules"] = [rule(op, value)]
            with self.subTest(op=op, value=value), self.assertRaises(ValueError):
                validate_questions(qs)

    def test_target_must_exist_in_same_survey_and_be_forward(self):
        for target in ("unknown", "q1", " ", 1):
            qs = questions()
            qs[0]["jump_rules"] = [rule("contains_all", ["a"], target)]
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_questions(qs)
        qs = questions("number")
        qs[0]["jump_rules"] = [rule("eq", 1)]
        with self.assertRaises(ValueError):
            validate_questions(list(reversed(qs)))

    def test_impossible_multi_choice_conditions_are_rejected(self):
        qs = questions()
        qs[0]["validation"] = {"min_choices": 2, "max_choices": 2}
        for value in (rule("equals_set", ["a"]), rule("contains_all", ["a", "b", "c"])):
            qs[0]["jump_rules"] = [value]
            with self.assertRaises(ValueError):
                validate_questions(qs)

    def test_path_skips_required_questions_and_discards_their_stale_answers(self):
        qs = questions()
        qs[0]["jump_rules"] = [rule("contains_all", ["a"], "q4")]
        answers = {"q1": ["a"], "q2": {"bad": "type"}, "q3": "旧答案", "q4": "结束"}
        original = deepcopy(answers)
        result = evaluate_path(qs, answers, require_complete=True)
        self.assertEqual(result["path"], ["q1", "q4"])
        self.assertEqual(result["answers"], {"q1": ["a"], "q4": "结束"})
        self.assertTrue(result["complete"])
        self.assertEqual(answers, original)
        answers["q1"] = ["b"]
        with self.assertRaises(ValueError):
            evaluate_path(qs, answers, require_complete=True)

    def test_partial_path_stops_at_first_missing_and_rechecks_newly_required_questions(self):
        qs = questions()
        qs[0]["jump_rules"] = [rule("contains_all", ["a"], "q4")]
        self.assertEqual(evaluate_path(qs, {"q1": ["a"]})["next_question_id"], "q4")
        result = evaluate_path(qs, {"q1": ["b"], "q4": "旧内容"})
        self.assertEqual(result["next_question_id"], "q2")
        self.assertEqual(result["answers"], {"q1": ["b"]})
        with self.assertRaises(ValueError):
            evaluate_path(qs, {"q1": ["b"]}, require_complete=True)

    def test_last_question_ends_and_unknown_answers_are_rejected(self):
        qs = questions()
        self.assertIsNone(next_question_id(qs, "q4", "完成"))
        with self.assertRaises(ValueError):
            next_question_id(qs, "missing", "完成")
        for answers in ([], {"foreign": 1}):
            with self.assertRaises(ValueError):
                evaluate_path(qs, answers)

    def test_config_validation_and_path_results_do_not_mutate_input(self):
        qs = questions()
        qs[0]["jump_rules"] = [rule("contains_all", ["a"])]
        original = deepcopy(qs)
        validated = validate_question(qs[0])
        validated["jump_rules"].clear()
        next_question_id(qs, "q1", ["a"])
        self.assertEqual(qs, original)


class JumpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017")
        cls.mongo = create_client(cls.uri)
        cls.addClassCleanup(cls.mongo.close)
        cls.mongo.admin.command("ping")
        cls.owner_doc = build_user_document("jump_owner", "test-pass")
        cls.other_doc = build_user_document("jump_other", "test-pass")

    def setUp(self):
        self.name = "survey_system_test_" + uuid4().hex
        self.assertNotIn(self.name, self.mongo.list_database_names())
        self.db = self.mongo[self.name]
        self.addCleanup(self.cleanup)
        initialize_database(self.db)
        self.db.users.insert_many([dict(self.owner_doc), dict(self.other_doc)])
        self.owner = self.owner_doc["_id"]
        self.sid = create_survey(self.db, self.owner, "跳转问卷")
        for q in questions():
            add_question(self.db, self.sid, self.owner, q)
        self.app = create_app({"TESTING": True, "SECRET_KEY": "jump-tests",
                               "MONGODB_URI": self.uri, "MONGODB_DB_NAME": self.name})
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = str(self.owner)
        self.path = f"/surveys/{self.sid}/questions/q1/jumps"

    def cleanup(self):
        if not self.name.startswith("survey_system_test_"):
            raise RuntimeError("非测试库")
        self.mongo.drop_database(self.name)

    def stored(self):
        return self.db.surveys.find_one({"_id": self.sid})

    def form(self):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        return {key: re.search(f'name="{key}" value="([^"]+)"', html).group(1)
                for key in ("csrf_token", "revision")}

    def post(self, **fields):
        return self.client.post(self.path, data={**self.form(), **fields})

    def test_two_multichoice_modes_save_and_preview_differ(self):
        self.assertEqual(self.post(action="add", operator="equals_set", value=["a", "b"], target="q4").status_code, 303)
        self.assertEqual(self.post(action="add", operator="contains_all", value=["a", "b"], target="q3").status_code, 303)
        for answer, target in ((["a", "b"], "第4题"), (["a", "b", "c"], "第3题"), (["a"], "第2题")):
            response = self.post(action="test", test_answer=answer)
            self.assertEqual(response.status_code, 200)
            self.assertIn("下一步：" + target, response.get_data(as_text=True))
        self.assertEqual(self.db.responses.count_documents({}), 0)
        saved = self.stored()
        self.assertEqual(len(saved["questions"][0]["jump_rules"]), 2)
        self.assertTrue(publish_survey(self.db, self.sid, self.owner))

    def test_reorder_and_delete_persist_priority(self):
        self.post(action="add", operator="contains_all", value=["a"], target="q3")
        self.post(action="add", operator="equals_set", value=["a", "b"], target="q4")
        self.assertEqual(self.post(action="up", index="1").status_code, 303)
        self.assertIn("下一步：第4题", self.post(action="test", test_answer=["a", "b"]).get_data(as_text=True))
        self.assertEqual(self.post(action="down", index="0").status_code, 303)
        self.assertEqual(self.post(action="delete", index="0").status_code, 303)
        self.assertEqual(self.post(action="delete", index="0").status_code, 303)
        self.assertEqual(self.stored()["questions"][0]["jump_rules"], [])

    def test_invalid_conditions_targets_indices_and_unknown_actions_never_write(self):
        before = self.stored()
        for fields in ({"action": "add", "operator": "equals_set", "value": [], "target": "q3"},
                       {"action": "add", "operator": "contains_all", "value": ["a"], "target": "q1"},
                       {"action": "add", "operator": "contains_all", "value": ["z"], "target": "q3"},
                       {"action": "add", "operator": "equals", "value": "a", "target": "q3"},
                       {"action": "delete", "index": "-1"}, {"action": "up", "index": "bad"},
                       {"action": "unknown"}):
            with self.subTest(fields=fields):
                self.assertEqual(self.post(**fields).status_code, 400)
                self.assertEqual(self.stored(), before)

    def test_stale_form_does_not_delete_a_different_rule(self):
        self.post(action="add", operator="contains_all", value=["a"], target="q3")
        stale = self.form()
        self.post(action="add", operator="equals_set", value=["a", "b"], target="q4")
        before = self.stored()
        self.assertEqual(self.client.post(self.path, data={**stale, "action": "delete", "index": "0"}).status_code, 409)
        self.assertEqual(self.stored(), before)

    def test_ownership_csrf_and_published_lock(self):
        form = self.form()
        self.assertEqual(self.client.post(self.path, data={"action": "add"}).status_code, 400)
        self.assertEqual(self.app.test_client().get(self.path).location, "/login")
        with self.client.session_transaction() as state:
            state["user_id"] = str(self.other_doc["_id"])
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.client.post(self.path, data={**form, "action": "delete", "index": "0"}).status_code, 404)
        with self.client.session_transaction() as state:
            state["user_id"] = str(self.owner)
        publish_survey(self.db, self.sid, self.owner)
        self.assertEqual(self.client.get(self.path).status_code, 409)
        self.assertEqual(self.client.post(self.path, data={**form, "action": "delete", "index": "0"}).status_code, 409)

    def test_numeric_and_single_choice_routes(self):
        for kind, fields, testfields in (
            ("number", {"operator": "between", "number": "-1", "upper": "2"}, {"test_number": "0"}),
            ("single_choice", {"operator": "equals", "value": "a"}, {"test_answer": "a"}),
        ):
            self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": questions(kind)}})
            self.assertEqual(self.post(action="add", target="q3", **fields).status_code, 303)
            self.assertIn("下一步：第3题", self.post(action="test", **testfields).get_data(as_text=True))

    def test_text_and_last_question_offer_no_rule_form(self):
        response = self.client.get(f"/surveys/{self.sid}/questions/q4/jumps")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('value="add"', response.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/surveys/{self.sid}/questions/missing/jumps").status_code, 404)

    def test_database_rejects_wrong_operator_type_and_extra_condition_keys(self):
        for bad in (rule("equals", "a"), rule("equals_set", []), rule("contains_all", ["a", "a"])):
            with self.assertRaises(OperationFailure):
                self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions.0.jump_rules": [bad]}})

    def test_upgrade_known_old_schema_preserves_existing_documents_and_is_repeatable(self):
        before = self.stored()
        self.db.command("collMod", "surveys", validator=PRE_JUMP_SURVEY_VALIDATOR)
        initialize_database(self.db)
        initialize_database(self.db)
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.db.surveys.options()["validator"], SURVEY_VALIDATOR)
        self.assertEqual(self.post(action="add", operator="contains_all", value=["a"], target="q3").status_code, 303)

    def test_data_service_rejects_stale_snapshot_and_invalid_full_graph(self):
        qs = self.stored()["questions"]
        revision = question_revision(qs)
        add_question(self.db, self.sid, self.owner, build_question("fill_blank", "新题", input_type="text"))
        self.assertFalse(save_jump_rules(self.db, self.sid, self.owner, "q1", [rule("contains_all", ["a"])], revision))
        with self.assertRaises(ValueError):
            save_jump_rules(self.db, self.sid, self.owner, "q1", [rule("contains_all", ["a"], "missing")],
                            question_revision(self.stored()["questions"]))


if __name__ == "__main__":
    unittest.main()
