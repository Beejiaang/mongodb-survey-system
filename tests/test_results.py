"""真实MongoDB：统计口径、分页、精度、权限及匿名保护。"""

from copy import deepcopy
from datetime import timedelta
import os
import json
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from pymongo.errors import AutoReconnect

from app import create_app
from database import create_client, initialize_database
from questions import build_question
from responses import start_response, update_progress, submit_response
from survey_results import answer_page, owned_results, summarize
from surveys import add_question, close_survey, create_survey, publish_survey, utcnow
from users import build_user_document


class ResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017")
        cls.mongo = create_client(cls.uri)
        cls.addClassCleanup(cls.mongo.close)
        cls.mongo.admin.command("ping")
        cls.owner = build_user_document("result_owner", "test-pass")
        cls.reader = build_user_document("result_reader", "test-pass")
        cls.secret = "result-test-identity"

    def setUp(self):
        self.name = "survey_system_test_" + uuid4().hex
        self.assertNotIn(self.name, self.mongo.list_database_names())
        self.db = self.mongo[self.name]
        self.addCleanup(lambda: self.mongo.drop_database(self.name))
        initialize_database(self.db)
        self.db.users.insert_many([deepcopy(self.owner), deepcopy(self.reader)])
        self.uid = self.owner["_id"]
        self.sid = create_survey(self.db, self.uid, "统计体验", is_anonymous=True, allow_repeat=True)
        options = [{"id": x, "text": x.upper()} for x in "abc"]
        self.questions = [
            build_question("single_choice", "单选", options=options, question_id="q1"),
            build_question("multiple_choice", "多选", options=options, question_id="q2"),
            build_question("fill_blank", "文本", input_type="text", question_id="q3"),
            build_question("fill_blank", "数字", input_type="number", question_id="q4"),
        ]
        for q in self.questions:
            add_question(self.db, self.sid, self.uid, q)
        self.questions[0]["jump_rules"] = [{"condition": {"operator": "equals", "value": "a"}, "target_question_id": "q3"}]
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": self.questions}})
        publish_survey(self.db, self.sid, self.uid)
        self.app = create_app({"TESTING": True, "SECRET_KEY": "results-web-test",
            "RESPONSE_IDENTITY_KEY": self.secret, "MONGODB_URI": self.uri, "MONGODB_DB_NAME": self.name})
        self.client = self.app.test_client()
        self.login(self.uid)
        self.url = f"/surveys/{self.sid}/results"

    def login(self, uid):
        with self.client.session_transaction() as state:
            state.clear()
            state["user_id"] = str(uid)

    def submit(self, values, uid=None):
        uid = uid or self.uid
        rid = start_response(self.db, self.sid, uid, self.secret)
        for value in values:
            record = self.db.responses.find_one({"_id": rid})
            update_progress(self.db, self.sid, rid, uid, self.secret, record["version"], "next", value)
        record = self.db.responses.find_one({"_id": rid})
        submit_response(self.db, self.sid, rid, uid, self.secret, record["version"])
        return rid

    def sample(self):
        self.submit(["a", "hello", 0])
        self.submit(["b", ["a", "b"], None, 10])
        self.submit([None, ["b"], "world", 20])
        self.submit(["a", None, None])
        start_response(self.db, self.sid, self.uid, self.secret)

    def summary(self):
        return summarize(self.db, self.db.surveys.find_one({"_id": self.sid}))

    def test_exact_counts_skip_blank_multichoice_denominator_and_average(self):
        self.sample()
        summary = self.summary()
        self.assertEqual(summary["total"], 4)
        q1, q2, q3, q4 = summary["questions"]
        self.assertEqual((q1["answered"], q1["blank"], q1["skipped"]), (3, 1, 0))
        self.assertEqual([(o["count"], o["percent"]) for o in q1["options"]], [(2, "66.7"), (1, "33.3"), (0, "0.0")])
        self.assertEqual((q2["answered"], q2["blank"], q2["skipped"]), (2, 0, 2))
        self.assertEqual([(o["count"], o["percent"]) for o in q2["options"]], [(1, "50.0"), (2, "100.0"), (0, "0.0")])
        self.assertEqual((q3["answered"], q3["blank"], q3["skipped"]), (2, 2, 0))
        self.assertEqual((q4["answered"], q4["blank"], q4["average"]), (3, 1, "10.00"))

    def test_single_choice_people_deduplicated_separate_from_submission_counts(self):
        self.sample()  # 同一人3份有效单选和1份留空，另有草稿。
        self.submit(["b", [], None, 30], self.reader["_id"])
        row = self.summary()["questions"][0]
        self.assertEqual((row["respondents"], row["answered"], row["blank"]), (2, 4, 1))
        self.assertEqual([o["count"] for o in row["options"]], [2, 2, 0])
        for suffix in ("", "/questions/q1"):
            html = self.client.get(self.url + suffix).get_data(as_text=True)
            self.assertIn("总回答人数：<strong>2</strong> 人", html)
            self.assertIn("有效回答：4 份", html)
            self.assertIn("同一账号重复填写只计一人", html)

    def test_single_choice_people_exclude_skipped_blank_draft_and_other_survey(self):
        # 用第二道单选验证按实际路径分别去重，不能使用全卷人数代替。
        qs = deepcopy(self.questions)
        qs[1] = build_question("single_choice", "第二单选", options=qs[0]["options"], question_id="q2")
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": qs}})
        self.submit(["a", None, None])  # 跳过q2
        self.submit(["b", None, None, None])  # q2留空
        rid = start_response(self.db, self.sid, self.uid, self.secret)
        for version, answer in enumerate(("b", "a")):
            update_progress(self.db, self.sid, rid, self.uid, self.secret, version, "next", answer)
        q1, q2 = self.summary()["questions"][:2]
        self.assertEqual((q1["respondents"], q2["respondents"]), (1, 0))
        self.assertIn("总回答人数：<strong>0</strong> 人", self.client.get(self.url + "/questions/q2").get_data(as_text=True))
        self.submit(["b", "a", None, None], self.reader["_id"])
        self.assertEqual([r["respondents"] for r in self.summary()["questions"][:2]], [2, 1])
        self.assertTrue(all(r["user_id"] is None for r in self.db.responses.find()))
        # 不同问卷使用同样的题目编号，不得串入人数。
        original = self.sid
        self.sid = create_survey(self.db, self.uid, "另一份", allow_repeat=True)
        for q in qs:
            copy = deepcopy(q)
            copy["jump_rules"] = []
            add_question(self.db, self.sid, self.uid, copy)
        publish_survey(self.db, self.sid, self.uid)
        self.submit(["b", "b", None, None])
        self.sid = original
        self.assertEqual(self.summary()["questions"][1]["respondents"], 1)

    def test_empty_and_only_draft_progress_never_count(self):
        self.assertEqual(self.summary()["total"], 0)
        rid = start_response(self.db, self.sid, self.uid, self.secret)
        update_progress(self.db, self.sid, rid, self.uid, self.secret, 0, "next", "b")
        summary = self.summary()
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["questions"][0]["respondents"], 0)
        for row in summary["questions"]:
            self.assertEqual(row["answered"], 0)
            self.assertIsNone(row["average"])
        html = self.client.get(self.url).get_data(as_text=True)
        self.assertIn("还没有正式提交", html)
        self.assertIn("0.0%", html)
        empty = self.client.get(self.url + "/questions/q4").get_data(as_text=True)
        self.assertIn("暂无填写内容", empty)
        self.assertIn("—", empty)

    def test_repeat_submissions_count_each_and_fresh_reads_include_new_results(self):
        self.submit(["a", "same", 12])
        self.assertEqual(self.summary()["total"], 1)
        self.submit(["a", "same", 24])
        self.assertEqual(self.summary()["total"], 2)
        self.assertEqual(self.summary()["questions"][3]["average"], "18.00")

    def test_other_survey_and_other_users_are_handled_independently(self):
        self.submit(["a", "own", 10])
        self.submit(["a", "reader", 30], self.reader["_id"])
        original_sid = self.sid
        self.sid = create_survey(self.db, self.uid, "another", allow_repeat=True)
        for q in self.questions:
            copy = deepcopy(q); copy["jump_rules"] = []
            add_question(self.db, self.sid, self.uid, copy)
        publish_survey(self.db, self.sid, self.uid)
        self.submit(["b", ["c"], "exclude-me", 1000])
        self.sid = original_sid
        self.assertEqual(self.summary()["total"], 2)
        self.assertEqual(self.summary()["questions"][3]["average"], "20.00")
        page = answer_page(self.db, self.sid, "q3", 1)
        self.assertEqual({r["value"] for r in page["items"]}, {"own", "reader"})

    def test_decimal_negative_zero_and_large_int64_average_are_precise(self):
        for number in (0.1, 0.2, -0.3):
            self.submit(["a", None, number])
        self.assertEqual(self.summary()["questions"][3]["average"], "0.00")
        self.db.responses.delete_many({})
        for number in (2**63 - 1, 2**63 - 3):
            self.submit(["a", None, number])
        self.assertEqual(self.summary()["questions"][3]["average"], "9223372036854775806.00")
        page = answer_page(self.db, self.sid, "q4", 1)
        self.assertEqual({r["value"] for r in page["items"]}, {2**63 - 1, 2**63 - 3})

    def test_large_finite_float_average_does_not_overflow(self):
        for number in (1e308, 1e308):
            self.submit(["a", None, number])
        average = self.summary()["questions"][3]["average"]
        self.assertEqual(average, "1" + "0" * 308 + ".00")
        self.assertNotIn("inf", self.client.get(self.url).get_data(as_text=True))

    def test_pagination_stable_order_no_duplicates_and_excludes_blank_and_skipped(self):
        ids = [self.submit(["a", f"text-{i}", i]) for i in range(21)]
        # 强制提交时间相同，验证用_id稳定排序。
        fixed = utcnow()
        self.db.responses.update_many({}, {"$set": {"submitted_at": fixed}})
        first = answer_page(self.db, self.sid, "q3", 1)
        second = answer_page(self.db, self.sid, "q3", 2)
        expected = [self.db.responses.find_one({"_id": rid})["answers"][1]["value"] for rid in sorted(ids, reverse=True)]
        self.assertEqual([r["value"] for r in first["items"] + second["items"]], expected)
        self.assertTrue(first["has_next"])
        self.assertFalse(second["has_next"])
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(second["items"]), 1)
        self.assertEqual(second["offset"], 20)
        page1 = self.client.get(self.url + "/questions/q3").get_data(as_text=True)
        page2 = self.client.get(self.url + "/questions/q3?page=2").get_data(as_text=True)
        self.assertIn("下一页", page1)
        self.assertIn("上一页", page2)
        self.assertIn("第 2 页", page2)

    def test_text_and_numeric_answer_pages_only_return_nonblank_actual_path(self):
        self.sample()
        text = answer_page(self.db, self.sid, "q3", 1)
        self.assertEqual({r["value"] for r in text["items"]}, {"hello", "world"})
        numbers = answer_page(self.db, self.sid, "q4", 1)
        self.assertEqual({r["value"] for r in numbers["items"]}, {0, 10, 20})
        for item in text["items"] + numbers["items"]:
            self.assertEqual(set(item), {"value", "submitted_at"})

    def test_anonymous_and_real_results_never_expose_response_identity(self):
        rid = self.submit(["a", "private-answer", 23], self.reader["_id"])
        record = self.db.responses.find_one({"_id": rid})
        for anonymous in (True, False):
            self.db.responses.update_one({"_id": rid}, {"$set": {"is_anonymous": anonymous,
                "user_id": None if anonymous else self.reader["_id"]}})
            for path in (self.url, self.url + "/questions/q3", self.url + "/questions/q4"):
                html = self.client.get(path).get_data(as_text=True)
                for private in (str(rid), str(self.reader["_id"]), record["respondent_key"], "result_reader"):
                    self.assertNotIn(private, html)

    def test_owner_only_including_individual_question_and_invalid_ids(self):
        self.sample()
        self.login(self.reader["_id"])
        with patch("survey_results.summarize") as aggregate:
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.get(self.url + "/questions/q3").status_code, 404)
            self.assertIsNone(owned_results(self.db, self.sid, self.reader["_id"]))
            aggregate.assert_not_called()
        self.login(self.uid)
        self.assertEqual(self.client.get("/surveys/invalid/results").status_code, 404)
        self.assertEqual(self.client.get(self.url + "/questions/missing").status_code, 404)
        with self.client.session_transaction() as state: state.clear()
        self.assertIn("/login", self.client.get(self.url).headers["Location"])
        self.assertIn("/login", self.client.get(self.url + "/questions/q3").headers["Location"])

    def test_html_escaping_for_title_option_and_text_answer(self):
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {
            "title": "<script>title()</script>", "questions.0.options.0.text": "<img src=x onerror=bad()>"}})
        self.submit(["a", "<script>answer()</script>\n第二行", 18])
        html = self.client.get(self.url).get_data(as_text=True)
        self.assertIn("&lt;script&gt;title()", html)
        self.assertIn("&lt;img", html)
        detail = self.client.get(self.url + "/questions/q3").get_data(as_text=True)
        self.assertIn("&lt;script&gt;answer()", detail)
        self.assertIn("第二行", detail)
        self.assertNotIn("<script>answer()", detail)

    def test_draft_closed_and_expired_statistics_remain_available(self):
        self.submit(["a", "saved", 42])
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"deadline": utcnow() - timedelta(seconds=1)}})
        self.assertIn("已截止", self.client.get(self.url).get_data(as_text=True))
        close_survey(self.db, self.sid, self.uid)
        self.assertEqual(self.client.get(self.url + "/questions/q4").status_code, 200)
        self.assertIn("已关闭", self.client.get(self.url).get_data(as_text=True))
        draft = create_survey(self.db, self.uid, "empty draft")
        html = self.client.get(f"/surveys/{draft}/results").get_data(as_text=True)
        self.assertIn("还没有添加题目", html)
        self.assertIn("草稿", html)

    def test_invalid_page_parameters_methods_and_no_writes(self):
        self.sample()
        before = list(self.db.responses.find())
        survey = self.db.surveys.find_one({"_id": self.sid})
        for raw in ("0", "-1", "abc", "1.5", "", "99999999", "０", "%2B1"):
            self.assertEqual(self.client.get(self.url + "/questions/q3?page=" + raw).status_code, 400)
        for suffix in ("/questions/q3?page=2", "/questions/q1?page=2"):
            self.assertEqual(self.client.get(self.url + suffix).status_code, 404)
        for suffix in ("", "/questions/q1", "/questions/q2", "/questions/q3", "/questions/q4"):
            self.assertEqual(self.client.get(self.url + suffix).status_code, 200)
        self.assertEqual(self.db.surveys.find_one({"_id": self.sid}), survey)
        self.assertEqual(list(self.db.responses.find()), before)
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_detail_entry_and_database_failure(self):
        html = self.client.get(f"/surveys/{self.sid}").get_data(as_text=True)
        self.assertIn(self.url, html)
        self.assertIn("查看统计", html)
        with patch("result_views.owned_results", side_effect=AutoReconnect("secret-connection")):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("secret-connection", response.get_data(as_text=True))

    def chart_data(self, html):
        return [json.loads(value) for value in re.findall(
            r'<script type="application/json" class="chart-data">(.*?)</script>', html, re.S)]

    def test_choice_chart_series_matches_original_counts_and_switch_options(self):
        self.sample()
        summary = self.summary()
        self.assertEqual(summary["questions"][0]["chart"]["series"], [
            {"label": "A", "count": 2}, {"label": "B", "count": 1}, {"label": "C", "count": 0}])
        self.assertEqual([i["count"] for i in summary["questions"][1]["chart"]["series"]], [1, 2, 0])
        html = self.client.get(self.url + "/questions/q2").get_data(as_text=True)
        self.assertEqual(self.chart_data(html), [summary["questions"][1]["chart"]])
        for kind in ("bars", "columns", "pie", "donut", "table"):
            self.assertIn(f'<option value="{kind}">', html)
        self.assertIn("/static/result-charts.js", html)
        self.assertIn("100.0%", html)  # 原有回答人数占比仍可查。

    def test_numeric_chart_uses_all_answers_not_first_page(self):
        for i in range(21):
            self.submit(["a", None, i - 10])
        html = self.client.get(self.url + "/questions/q4").get_data(as_text=True)
        chart = self.chart_data(html)[0]
        self.assertEqual(chart["kind"], "number")
        self.assertEqual(sum(i["count"] for i in chart["series"]), 21)
        self.assertLessEqual(len(chart["series"]), 10)
        self.assertIn('value="bars"', html)
        self.assertIn('value="table"', html)
        self.assertNotIn('value="columns"', html)
        self.assertNotIn('value="line"', html)
        self.assertNotIn('value="pie"', html)
        self.assertEqual(len(answer_page(self.db, self.sid, "q4", 1)["items"]), 20)
        self.assertIn("0.00", html)

    def test_numeric_single_value_zero_negative_and_large_integer_labels(self):
        for value in (0, 0):
            self.submit(["a", None, value])
        self.assertEqual(self.summary()["questions"][3]["chart"]["series"], [{"label": "0", "count": 2}])
        self.db.responses.delete_many({})
        for value in (-20, 2**63 - 1):
            self.submit(["a", None, value])
        series = self.summary()["questions"][3]["chart"]["series"]
        self.assertEqual(sum(i["count"] for i in series), 2)
        self.assertIn("9223372036854775807", repr(series))
        self.assertIn("-20", repr(series))
        self.assertTrue(all(isinstance(i["label"], str) for i in series))

    def test_text_has_no_chart_and_keeps_counts_and_paginated_answers(self):
        self.sample()
        row = self.summary()["questions"][2]
        self.assertNotIn("chart", row)
        self.assertEqual((row["answered"], row["blank"], row["skipped"]), (2, 2, 0))
        overview = self.client.get(self.url).get_data(as_text=True)
        self.assertEqual(len(self.chart_data(overview)), 3)
        html = self.client.get(self.url + "/questions/q3").get_data(as_text=True)
        for removed in ('data-result-visual', 'chart-kind', 'chart-data', 'result-charts.js', '启用JavaScript后可切换图表'):
            self.assertNotIn(removed, html)
        for retained in ('有效回答：2 份', '选答留空：2 份', '填写内容', 'hello', 'world'):
            self.assertIn(retained, html)

    def test_empty_chart_data_and_javascript_unavailable_table_fallback(self):
        summary = self.summary()
        self.assertEqual(summary["questions"][3]["chart"]["series"], [])
        self.assertEqual(sum(i["count"] for i in summary["questions"][0]["chart"]["series"]), 0)
        html = self.client.get(self.url).get_data(as_text=True)
        self.assertIn("<div data-chart-table>", html)
        self.assertIn("启用JavaScript后可切换图表", html)
        with self.client.get("/static/result-charts.js") as script:
            self.assertEqual(script.status_code, 200)

    def test_chart_json_escapes_script_end_and_never_contains_identity(self):
        attack = '</script><img src=x onerror="alert(1)">'
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions.0.options.0.text": attack}})
        rid = self.submit(["a", "private text", 0], self.reader["_id"])
        record = self.db.responses.find_one({"_id": rid})
        html = self.client.get(self.url).get_data(as_text=True)
        data = self.chart_data(html)
        self.assertEqual(data[0]["series"][0]["label"], attack)
        self.assertNotIn(attack, html)
        payload = json.dumps(data)
        for private in (record["respondent_key"], str(rid), str(self.reader["_id"]), "private text"):
            self.assertNotIn(private, payload)
