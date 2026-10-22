"""真实 MongoDB：填写、跳转、校验、匿名、并发及幂等提交。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import OperationFailure
from werkzeug.datastructures import MultiDict

from app import create_app
from database import create_client, initialize_database
from jumps import question_revision
from questions import build_question
from responses import (ResponseError, respondent_key, start_response, update_progress,
                       submit_response, own_response)
from response_schema import RESPONSE_VALIDATOR
from surveys import add_question, create_survey, publish_survey, close_survey, utcnow
from users import build_user_document


class ResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017")
        cls.mongo = create_client(cls.uri)
        cls.addClassCleanup(cls.mongo.close)
        cls.mongo.admin.command("ping")
        cls.user = build_user_document("filler", "test-pass")
        cls.other = build_user_document("other_filler", "test-pass")
        cls.secret = "response-test-identity-key"

    def setUp(self):
        self.name = "survey_system_test_" + uuid4().hex
        self.assertNotIn(self.name, self.mongo.list_database_names())
        self.db = self.mongo[self.name]
        self.addCleanup(lambda: self.mongo.drop_database(self.name))
        initialize_database(self.db)
        self.db.users.insert_many([deepcopy(self.user), deepcopy(self.other)])
        self.uid = self.user["_id"]
        self.sid = create_survey(self.db, self.uid, "填写测试", "说明")
        opts = [{"id": x, "text": x.upper()} for x in "abc"]
        self.questions = [
            build_question("single_choice", "单选", options=opts, required=True, question_id="q1"),
            build_question("multiple_choice", "多选", options=opts, required=True,
                           validation={"min_choices": 2, "max_choices": 3}, question_id="q2"),
            build_question("fill_blank", "文本", input_type="text", required=True,
                           validation={"min_length": 2, "max_length": 10000}, question_id="q3"),
            build_question("fill_blank", "年龄", input_type="number", required=True,
                           validation={"min_value": 0, "max_value": 120, "integer_only": True}, question_id="q4"),
        ]
        for q in self.questions:
            add_question(self.db, self.sid, self.uid, q)
        self.questions[0]["jump_rules"] = [{"condition": {"operator": "equals", "value": "a"}, "target_question_id": "q3"}]
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": self.questions}})
        publish_survey(self.db, self.sid, self.uid)
        self.app = create_app({"TESTING": True, "SECRET_KEY": "response-web-test",
            "RESPONSE_IDENTITY_KEY": self.secret, "MONGODB_URI": self.uri, "MONGODB_DB_NAME": self.name})
        self.client = self.app.test_client()
        self.login(self.client, self.uid)
        self.share = f"/survey/{self.sid}"

    def login(self, client, uid):
        with client.session_transaction() as state:
            state.clear()
            state["user_id"] = str(uid)

    def start(self, uid=None):
        return start_response(self.db, self.sid, uid or self.uid, self.secret)

    def record(self, rid):
        return self.db.responses.find_one({"_id": rid})

    def step(self, rid, answer=None, action="next", uid=None):
        return update_progress(self.db, self.sid, rid, uid or self.uid, self.secret,
                               self.record(rid)["version"], action, answer)

    def complete(self, rid, uid=None):
        for answer in ("a", "有效回答", 18):
            self.step(rid, answer, uid=uid)

    def submit(self, rid, uid=None):
        return submit_response(self.db, self.sid, rid, uid or self.uid, self.secret, self.record(rid)["version"])

    def url(self, rid):
        return self.share + f"/fill/{rid}"

    def form(self, path, **fields):
        page = self.client.get(path)
        html = page.get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        version = re.search(r'name="version" value="([^"]+)"', html)
        data = {"csrf_token": token, **fields}
        if version:
            data["version"] = version.group(1)
        return data

    def test_four_types_roundtrip_and_drafts_excluded_from_submitted(self):
        rid = self.start()
        for answer in ("b", ["a", "b"], " 文本原样 ", 0):
            self.step(rid, answer)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 0)
        self.submit(rid)
        doc = self.record(rid)
        self.assertEqual(doc["state"], "submitted")
        self.assertEqual(doc["path"], ["q1", "q2", "q3", "q4"])
        self.assertEqual([a["value"] for a in doc["answers"]], ["b", ["a", "b"], " 文本原样 ", 0])
        self.assertEqual(doc["user_id"], self.uid)
        self.assertIsNotNone(doc["submitted_at"].tzinfo)
        initialize_database(self.db)
        self.assertEqual(self.record(rid), doc)

    def test_jump_skips_required_and_previous_answer_change_removes_old_tail(self):
        rid = self.start()
        self.complete(rid)
        self.assertEqual(self.record(rid)["path"], ["q1", "q3", "q4"])
        for _ in range(3):
            self.step(rid, action="back")
        self.step(rid, "b")
        doc = self.record(rid)
        self.assertEqual(doc["current_question_id"], "q2")
        self.assertEqual(doc["answers"], [{"question_id": "q1", "value": "b"}])
        with self.assertRaises(ResponseError):
            self.submit(rid)
        for value in (["a", "c"], "新文本", 24):
            self.step(rid, value)
        self.submit(rid)
        self.assertEqual(self.record(rid)["answers"][2]["value"], "新文本")

    def test_multichoice_exact_and_contains_follow_configured_priority(self):
        qs = deepcopy(self.questions)
        qs[1]["jump_rules"] = [
            {"condition": {"operator": "equals_set", "value": ["a", "b"]}, "target_question_id": "q4"},
            {"condition": {"operator": "contains_all", "value": ["a", "b"]}, "target_question_id": "q3"}]
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": qs}})
        for uid, answer, target in ((self.uid, ["b", "a"], "q4"), (self.other["_id"], ["a", "b", "c"], "q3")):
            rid = self.start(uid)
            self.step(rid, "b", uid=uid)
            self.step(rid, answer, uid=uid)
            self.assertEqual(self.record(rid)["current_question_id"], target)

    def test_invalid_answers_never_advance_or_write(self):
        rid = self.start()
        for bad in (None, "unknown", ["a"]):
            before = self.record(rid)
            with self.assertRaises(ValueError): self.step(rid, bad)
            self.assertEqual(self.record(rid), before)
        self.step(rid, "b")
        for bad in ([], ["a"], ["a", "a"], ["a", "z"]):
            with self.assertRaises(ValueError): self.step(rid, bad)
        self.step(rid, ["a", "b"])
        for bad in ("", "x", "x" * 10001):
            with self.assertRaises(ValueError): self.step(rid, bad)
        self.step(rid, "文本")
        for bad in (None, -1, 121, 1.5, True, float("nan"), float("inf"), "18"):
            with self.assertRaises(ValueError): self.step(rid, bad)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 0)

    def test_optional_blank_has_explicit_null_and_sequential_path(self):
        qs = deepcopy(self.questions)
        for q in qs: q["required"] = False
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": qs}})
        rid = self.start()
        for value in (None, [], "  ", None): self.step(rid, value)
        self.submit(rid)
        self.assertEqual([a["value"] for a in self.record(rid)["answers"]], [None] * 4)

    def test_progress_survives_new_app_and_never_uses_cookie_for_answers(self):
        rid = self.start()
        self.step(rid, "a")
        self.step(rid, "长" * 8000)
        restarted = create_app(dict(self.app.config)).test_client()
        self.login(restarted, self.uid)
        page = restarted.get(self.url(rid))
        self.assertEqual(page.status_code, 200)
        self.assertIn("年龄", page.get_data(as_text=True))
        with restarted.session_transaction() as state:
            self.assertNotIn("answers", state)
        self.assertEqual(self.start(), rid)

    def test_anonymous_stores_no_user_id_and_scopes_hash_by_survey(self):
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"is_anonymous": True}})
        rid = self.start()
        self.complete(rid)
        self.submit(rid)
        doc = self.record(rid)
        self.assertIsNone(doc["user_id"])
        self.assertNotIn(str(self.uid), repr(doc))
        self.assertNotEqual(doc["respondent_key"], respondent_key(self.secret, ObjectId(), self.uid))
        self.assertNotEqual(doc["respondent_key"], respondent_key("other-secret", self.sid, self.uid))
        with self.assertRaises(ResponseError): self.start()

    def test_ownership_unknown_ids_and_forged_identity(self):
        rid = self.start()
        for sid, response_id, uid in ((self.sid, rid, self.other["_id"]),
                                     (ObjectId(), rid, self.uid), (self.sid, "invalid", self.uid)):
            with self.assertRaises(ResponseError) as error:
                own_response(self.db, sid, response_id, uid, self.secret)
            self.assertEqual(error.exception.status, 404)
        other_client = self.app.test_client()
        self.login(other_client, self.other["_id"])
        self.assertEqual(other_client.get(self.url(rid)).status_code, 404)

    def test_stale_page_cannot_overwrite_or_submit(self):
        rid = self.start()
        self.step(rid, "a")
        before = self.record(rid)
        with self.assertRaises(ResponseError):
            update_progress(self.db, self.sid, rid, self.uid, self.secret, 0, "next", "b")
        self.assertEqual(self.record(rid), before)
        self.complete_remaining(rid)
        with self.assertRaises(ResponseError):
            submit_response(self.db, self.sid, rid, self.uid, self.secret, 0)

    def complete_remaining(self, rid):
        self.step(rid, "文本")
        self.step(rid, 18)

    def test_duplicate_submit_is_idempotent_even_after_close(self):
        rid = self.start()
        self.complete(rid)
        version = self.record(rid)["version"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: submit_response(self.db, self.sid, rid, self.uid, self.secret, version), range(6)))
        self.assertEqual(set(results), {rid})
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 1)
        close_survey(self.db, self.sid, self.uid)
        self.assertEqual(self.submit(rid), rid)
        self.assertIn("提交成功", self.client.get(self.url(rid)).get_data(as_text=True))

    def test_concurrent_completion_after_initial_read_returns_same_receipt(self):
        rid = self.start(); self.complete(rid)
        from responses import existing_submission as original
        def finish_before_duplicate_check(database, survey, key):
            self.db.responses.update_one({"_id": rid}, {"$set": {
                "state": "submitted", "submitted_at": utcnow()}})
            return original(database, survey, key)
        with patch("responses.existing_submission", side_effect=finish_before_duplicate_check):
            self.assertEqual(self.submit(rid), rid)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 1)

    def test_nonrepeat_concurrent_distinct_drafts_only_one_submits(self):
        rid = self.start()
        self.complete(rid)
        second = deepcopy(self.record(rid))
        second["_id"] = ObjectId()
        self.db.responses.insert_one(second)
        def send(value):
            try: self.submit(value); return True
            except ResponseError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, [rid, second["_id"]]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 1)

    def test_repeat_enabled_creates_new_attempt_after_submission(self):
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"allow_repeat": True}})
        for _ in range(2):
            rid = self.start(); self.complete(rid); self.submit(rid)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 2)

    def test_multiple_users_submit_independently(self):
        def fill_and_submit(uid):
            rid = self.start(uid)
            self.complete(rid, uid)
            self.submit(rid, uid)
            return rid
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(fill_and_submit, (self.uid, self.other["_id"])))
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 2)

    def test_closed_expired_draft_and_unknown_surveys_reject_without_submission(self):
        rid = self.start(); self.complete(rid)
        for fields in ({"status": "closed"}, {"status": "draft"},
                       {"status": "published", "deadline": utcnow() - timedelta(seconds=1)}):
            self.db.surveys.update_one({"_id": self.sid}, {"$set": fields})
            with self.assertRaises(ResponseError): self.submit(rid)
            with self.assertRaises(ResponseError): self.start()
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 0)
        with self.assertRaises(ResponseError): start_response(self.db, "bad", self.uid, self.secret)

    def test_close_between_validation_and_atomic_admission_rejects(self):
        rid = self.start(); self.complete(rid)
        from responses import evaluate_path as original
        def close_after_validation(*args, **kwargs):
            result = original(*args, **kwargs)
            close_survey(self.db, self.sid, self.uid)
            return result
        with patch("responses.evaluate_path", side_effect=close_after_validation):
            with self.assertRaises(ResponseError): self.submit(rid)
        self.assertEqual(self.record(rid)["state"], "draft")

    def test_expiry_between_validation_and_atomic_admission_rejects(self):
        rid = self.start(); self.complete(rid)
        from responses import evaluate_path as original
        def expire_after_validation(*args, **kwargs):
            result = original(*args, **kwargs)
            self.db.surveys.update_one({"_id": self.sid}, {"$set": {"deadline": utcnow() - timedelta(seconds=1)}})
            return result
        with patch("responses.evaluate_path", side_effect=expire_after_validation):
            with self.assertRaises(ResponseError): self.submit(rid)
        self.assertEqual(self.record(rid)["state"], "draft")

    def test_mutated_survey_and_incomplete_tampered_progress_are_rejected(self):
        rid = self.start()
        self.db.responses.update_one({"_id": rid}, {"$set": {"current_question_id": None}})
        with self.assertRaises(ValueError): self.submit(rid)
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions.0.title": "changed"}})
        with self.assertRaises(ResponseError): self.submit(rid)

    def test_response_schema_and_empty_legacy_upgrade(self):
        rid = self.start(); doc = self.record(rid)
        for change in ({"state": "bad"}, {"answers": {}}, {"version": -1},
                       {"is_anonymous": True, "user_id": self.uid},
                       {"state": "submitted"}, {"extra": True}):
            bad = dict(doc, _id=ObjectId(), **change)
            with self.assertRaises(OperationFailure): self.db.responses.insert_one(bad)
        self.db.responses.delete_many({})
        self.db.command("collMod", "responses", validator={})
        initialize_database(self.db)
        self.assertEqual(self.db.responses.options()["validator"], RESPONSE_VALIDATOR)
        self.assertTrue(self.db.responses.index_information()["one_submission_per_user"]["unique"])

    def test_nonempty_unknown_legacy_responses_preserved(self):
        self.db.command("collMod", "responses", validator={})
        legacy = {"_id": ObjectId(), "old": "keep"}
        self.db.responses.insert_one(legacy)
        with self.assertRaisesRegex(ValueError, "responses"): initialize_database(self.db)
        self.assertEqual(self.db.responses.find_one(), legacy)

    def test_web_full_flow_errors_review_receipt_and_no_skipped_answer(self):
        data = self.form(self.share)
        start = self.client.post(self.share + "/start", data=data)
        self.assertEqual(start.status_code, 303)
        path = start.headers["Location"]
        page = self.client.post(path, data=self.form(path, action="next", answer=""))
        self.assertEqual(page.status_code, 400)
        for value in ("a", "<script>alert(1)</script>", "18"):
            response = self.client.post(path, data=self.form(path, action="next", answer=value))
            self.assertEqual(response.status_code, 303)
        review = self.client.get(path).get_data(as_text=True)
        self.assertIn("确认你的回答", review)
        self.assertIn("&lt;script&gt;", review)
        self.assertNotIn("<script>alert", review)
        self.assertNotIn("多选", review)
        response = self.client.post(path, data=self.form(path, action="submit", user_id=str(self.other["_id"]), answers="forged"))
        self.assertEqual(response.status_code, 303)
        self.assertIn("提交成功", self.client.get(path).get_data(as_text=True))
        record = self.db.responses.find_one({"state": "submitted"})
        self.assertEqual(record["user_id"], self.uid)
        self.assertEqual(record["answers"][-1]["value"], 18)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 1)

    def test_web_back_and_bad_numeric_inputs_retain_progress(self):
        rid = self.start(); self.complete(rid)
        path = self.url(rid)
        self.assertEqual(self.client.post(path, data=self.form(path, action="back")).status_code, 303)
        for value in ("abc", "nan", "inf", "18.5", "121", "", "十八", "１８", "١٨", "18岁", "1_8"):
            before = self.record(rid)
            result = self.client.post(path, data=self.form(path, action="next", answer=value))
            self.assertEqual(result.status_code, 400)
            self.assertEqual(self.record(rid), before)
            if value in ("十八", "１８", "١٨", "18岁", "1_8"):
                html = result.get_data(as_text=True)
                self.assertIn("请使用阿拉伯数字回答", html)
                self.assertIn(f'value="{value}"', html)
        self.assertEqual(self.client.post(path, data=self.form(path, action="next", answer="0")).status_code, 303)

    def test_numeric_fill_has_browser_pattern_and_back_bypasses_invalid_number(self):
        from numeric_input import NUMBER_PATTERN
        rid = self.start(); self.step(rid, "a"); self.step(rid, "有效回答")
        path = self.url(rid)
        html = self.client.get(path).get_data(as_text=True)
        self.assertIn(f'pattern="{NUMBER_PATTERN}"', html)
        self.assertIn('inputmode="numeric"', html)
        self.assertNotIn('number-help', html)
        self.assertNotIn('最小值：', html)
        self.assertNotIn('最大值：', html)
        self.assertNotIn('系统会强制检查', html)
        response = self.client.post(path, data=self.form(path, action="back", answer="错误输入"))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.record(rid)["current_question_id"], "q3")

    def test_csrf_login_ownership_and_method_protection(self):
        guest = self.app.test_client()
        self.assertIn("/login?next=", guest.get(self.share).headers["Location"])
        self.assertEqual(self.client.get(self.share + "/start").status_code, 405)
        self.assertEqual(self.client.post(self.share + "/start").status_code, 400)
        rid = self.start()
        self.assertEqual(self.client.post(self.url(rid), data={"action": "submit"}).status_code, 400)
        data = self.form(self.url(rid), action="next", answer="a")
        data["version"] = "bad"
        self.assertEqual(self.client.post(self.url(rid), data=data).status_code, 400)
        self.assertEqual(self.db.responses.count_documents({"state": "submitted"}), 0)

    def test_duplicate_scalar_values_rejected_and_zero_and_int64_preserved(self):
        rid = self.start()
        path = self.url(rid)
        data = MultiDict(self.form(path, action="next")); data.add("answer", "a"); data.add("answer", "b")
        self.assertEqual(self.client.post(path, data=data).status_code, 400)
        self.assertEqual(self.record(rid)["version"], 0)
        qs = deepcopy(self.questions); qs[3]["validation"]["max_value"] = 2**63 - 1
        self.db.responses.delete_one({"_id": rid})
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": qs}})
        rid = self.start(); self.step(rid, "a"); self.step(rid, "文本")
        path = self.url(rid)
        self.assertEqual(self.client.post(path, data=self.form(path, action="next", answer=str(2**63 - 1))).status_code, 303)
        self.submit(rid)
        self.assertEqual(self.record(rid)["answers"][-1]["value"], 2**63 - 1)
