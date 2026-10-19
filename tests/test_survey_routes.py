"""问卷创建、逐题保存、权限隔离与真实 MongoDB 的网页集成测试。"""

import os
import re
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import ServerSelectionTimeoutError

from app import create_app
from database import create_client, initialize_database
from users import create_user


class SurveyRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017")
        cls.mongo = create_client(cls.uri)
        cls.addClassCleanup(cls.mongo.close)
        cls.mongo.admin.command("ping")

    def setUp(self):
        self.name = "survey_system_test_" + uuid4().hex
        self.assertNotIn(self.name, self.mongo.list_database_names())
        self.database = self.mongo[self.name]
        self.addCleanup(self.clean_database)
        initialize_database(self.database)
        self.owner = create_user(self.database, "survey_owner", "owner password")
        self.other = create_user(self.database, "other_owner", "other password")
        self.config = {
            "TESTING": True,
            "SECRET_KEY": "isolated-survey-route-test-secret",
            "MONGODB_URI": self.uri,
            "MONGODB_DB_NAME": self.name,
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.assume_user(self.owner)

    def clean_database(self):
        if not self.name.startswith("survey_system_test_"):
            raise RuntimeError("拒绝清理非测试数据库。")
        self.mongo.drop_database(self.name)

    def assume_user(self, user_id, client=None):
        with (client or self.client).session_transaction() as state:
            state.clear()
            state["user_id"] = str(user_id)

    def token(self, path="/surveys/new", client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def create_draft(self, **fields):
        data = {
            "title": "校园生活调查", "description": "了解校园生活体验。",
            "csrf_token": self.token(),
        }
        data.update(fields)
        return self.client.post("/surveys/new", data=data)

    def saved_draft(self, **fields):
        response = self.create_draft(**fields)
        self.assertEqual(response.status_code, 303)
        self.assertRegex(response.location, r"^/surveys/[a-f0-9]{24}$")
        survey_id = ObjectId(response.location.rsplit("/", 1)[1])
        return self.database.surveys.find_one({"_id": survey_id})

    def add_question(self, survey_id, kind="text", **fields):
        path = f"/surveys/{survey_id}/questions/new"
        data = {
            "csrf_token": self.token(f"{path}?kind={kind}"),
            "kind": kind, "title": "你有什么建议？",
        }
        data.update(fields)
        return self.client.post(path, data=data)

    def test_create_draft_saves_complete_metadata_and_ignores_owner_spoof(self):
        survey = self.saved_draft(
            title="  校园生活调查  ", description="  了解校园生活体验。\n",
            owner_id=str(self.other), status="published", questions="fake questions",
            created_at="1900-01-01", published_at="1900-01-01", deadline="1900-01-01",
        )
        self.assertEqual(set(survey), {
            "_id", "owner_id", "title", "description", "status", "is_anonymous",
            "allow_repeat", "questions", "created_at", "updated_at", "published_at", "deadline",
        })
        self.assertEqual(survey["owner_id"], self.owner)
        self.assertEqual(survey["title"], "校园生活调查")
        self.assertEqual(survey["description"], "了解校园生活体验。")
        self.assertEqual(survey["status"], "draft")
        self.assertFalse(survey["is_anonymous"])
        self.assertFalse(survey["allow_repeat"])
        self.assertEqual(survey["questions"], [])
        self.assertIsNone(survey["published_at"])
        self.assertIsNone(survey["deadline"])
        self.assertIsInstance(survey["created_at"], datetime)
        self.assertEqual(survey["created_at"].utcoffset().total_seconds(), 0)
        self.assertEqual(survey["created_at"], survey["updated_at"])
        self.assertLessEqual(survey["created_at"], datetime.now(timezone.utc))
        self.assertEqual(self.database.responses.count_documents({}), 0)

    def test_draft_accepts_optional_flags_and_empty_description(self):
        survey = self.saved_draft(description="", is_anonymous="on", allow_repeat="on")
        self.assertEqual(survey["description"], "")
        self.assertIs(survey["is_anonymous"], True)
        self.assertIs(survey["allow_repeat"], True)

    def test_metadata_boundaries_are_enforced_before_writing(self):
        for fields in ({"title": ""}, {"title": " \t\n"}, {"title": "题" * 201},
                       {"description": "说" * 5001}, {"is_anonymous": "false"},
                       {"allow_repeat": "yes"}):
            with self.subTest(fields={key: value[:30] for key, value in fields.items()}):
                self.assertEqual(self.create_draft(**fields).status_code, 400)
                self.assertEqual(self.database.surveys.count_documents({}), 0)
        survey = self.saved_draft(title="题" * 200, description="说" * 5000)
        self.assertEqual(len(survey["title"]), 200)
        self.assertEqual(len(survey["description"]), 5000)

    def test_invalid_metadata_preserves_input_and_escapes_html(self):
        unsafe = '<script>alert("description")</script>'
        response = self.create_draft(title="", description=unsafe)
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn(unsafe, html)
        self.assertEqual(self.database.surveys.count_documents({}), 0)

    def test_list_and_detail_show_only_the_current_owners_surveys(self):
        owned = self.saved_draft(title="ONLY_FIRST_OWNER")
        self.assume_user(self.other)
        other = self.saved_draft(title="ONLY_SECOND_OWNER")
        other_html = self.client.get("/surveys").get_data(as_text=True)
        self.assertIn("ONLY_SECOND_OWNER", other_html)
        self.assertNotIn("ONLY_FIRST_OWNER", other_html)
        self.assertEqual(self.client.get(f"/surveys/{owned['_id']}").status_code, 404)
        self.assume_user(self.owner)
        response = self.client.get("/surveys")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ONLY_FIRST_OWNER", response.get_data(as_text=True))
        self.assertNotIn("ONLY_SECOND_OWNER", response.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/surveys/{other['_id']}").status_code, 404)
        detail = self.client.get(f"/surveys/{owned['_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("ONLY_FIRST_OWNER", detail.get_data(as_text=True))

    def test_guests_must_log_in_before_viewing_or_writing_surveys(self):
        survey = self.saved_draft()
        guest = self.app.test_client()
        guest_token = self.token("/login", client=guest)
        paths = ("/surveys", "/surveys/new", f"/surveys/{survey['_id']}",
                 f"/surveys/{survey['_id']}/questions/new")
        for path in paths:
            with self.subTest(path=path):
                response = guest.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, "/login")
        for path in ("/surveys/new", f"/surveys/{survey['_id']}/questions/new"):
            with self.subTest(path=path):
                response = guest.post(path, data={"csrf_token": guest_token, "title": "unauthorized"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, "/login")
        self.assertEqual(self.database.surveys.count_documents({}), 1)
        self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), survey)

    def test_invalid_missing_and_foreign_survey_ids_cannot_be_read_or_changed(self):
        survey = self.saved_draft()
        self.assume_user(self.other)
        token = self.token()
        for survey_id in ("not-an-object-id", str(ObjectId()), str(survey["_id"])):
            with self.subTest(survey_id=survey_id):
                self.assertEqual(self.client.get(f"/surveys/{survey_id}").status_code, 404)
                path = f"/surveys/{survey_id}/questions/new"
                self.assertEqual(self.client.get(path).status_code, 404)
                self.assertEqual(self.client.post(path, data={
                    "csrf_token": token, "kind": "text", "title": "unauthorized",
                    "owner_id": str(self.owner),
                }).status_code, 404)
        self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), survey)

    def test_all_question_kinds_can_be_added_and_read_after_new_app_login(self):
        survey = self.saved_draft(is_anonymous="on", allow_repeat="on")
        survey_id = survey["_id"]
        forms = (
            ("single_choice", {"title": "年级", "options_text": "一年级\n二年级", "required": "on"}),
            ("multiple_choice", {"title": "兴趣", "options_text": "音乐\n运动\n阅读",
                                 "min_choices": "1", "max_choices": "2"}),
            ("text", {"title": "建议", "min_length": "2", "max_length": "100"}),
            ("number", {"title": "年龄", "min_value": "0", "max_value": "120", "integer_only": "on"}),
        )
        previous_questions = []
        for kind, fields in forms:
            with self.subTest(kind=kind):
                response = self.add_question(survey_id, kind, **fields)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.location, f"/surveys/{survey_id}")
                current = self.database.surveys.find_one({"_id": survey_id})
                self.assertEqual(current["questions"][:-1], previous_questions)
                self.assertEqual(current["questions"][-1]["jump_rules"], [])
                previous_questions = current["questions"]
                for key in survey.keys() - {"questions", "updated_at"}:
                    self.assertEqual(current[key], survey[key])
                self.assertGreaterEqual(current["updated_at"], survey["updated_at"])
        questions = previous_questions
        self.assertEqual([q["type"] for q in questions], [
            "single_choice", "multiple_choice", "fill_blank", "fill_blank",
        ])
        self.assertEqual(len({q["id"] for q in questions}), 4)
        self.assertTrue(all(isinstance(q["id"], str) and q["id"] for q in questions))
        self.assertIs(questions[0]["required"], True)
        self.assertIs(questions[1]["required"], False)
        self.assertEqual([o["text"] for o in questions[0]["options"]], ["一年级", "二年级"])
        for question in questions[:2]:
            ids = [option["id"] for option in question["options"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(all(isinstance(option_id, str) and option_id for option_id in ids))
        self.assertEqual(questions[1]["validation"], {"min_choices": 1, "max_choices": 2})
        self.assertEqual(questions[2]["input_type"], "text")
        self.assertEqual(questions[2]["validation"], {"min_length": 2, "max_length": 100})
        self.assertEqual(questions[3]["input_type"], "number")
        self.assertEqual(questions[3]["validation"], {
            "min_value": 0, "max_value": 120, "integer_only": True,
        })
        restarted_app = create_app(self.config)
        browser = restarted_app.test_client()
        login = browser.post("/login", data={
            "csrf_token": self.token("/login", client=browser),
            "username": "SURVEY_OWNER", "password": "owner password",
        })
        self.assertEqual(login.status_code, 303)
        detail = browser.get(f"/surveys/{survey_id}")
        self.assertEqual(detail.status_code, 200)
        html = detail.get_data(as_text=True)
        for label in ("校园生活调查", "年级", "一年级", "兴趣", "音乐", "建议", "年龄"):
            self.assertIn(label, html)
        self.assertEqual(self.database.surveys.find_one({"_id": survey_id})["questions"], questions)
        self.assertEqual(self.database.responses.count_documents({}), 0)

    def test_unset_question_bounds_remain_null_and_numbers_allow_decimals(self):
        survey_id = self.saved_draft()["_id"]
        for kind, fields in (("multiple_choice", {"options_text": "甲\n乙"}), ("text", {}),
                             ("number", {}), ("number", {"min_value": "-1.5", "max_value": "2.75"})):
            self.assertEqual(self.add_question(survey_id, kind, **fields).status_code, 303)
        questions = self.database.surveys.find_one({"_id": survey_id})["questions"]
        self.assertEqual(questions[0]["validation"], {"min_choices": None, "max_choices": None})
        self.assertEqual(questions[1]["validation"], {"min_length": None, "max_length": None})
        self.assertEqual(questions[2]["validation"], {
            "min_value": None, "max_value": None, "integer_only": False,
        })
        self.assertEqual(questions[3]["validation"], {
            "min_value": -1.5, "max_value": 2.75, "integer_only": False,
        })

    def test_invalid_question_configurations_do_not_change_saved_survey(self):
        survey = self.saved_draft()
        cases = (
            ("text", {"title": " \t"}),
            ("single_choice", {"options_text": "一个选项"}),
            ("single_choice", {"options_text": "\n\n"}),
            ("multiple_choice", {"options_text": "甲\n乙", "min_choices": "3"}),
            ("multiple_choice", {"options_text": "甲\n乙", "min_choices": "2", "max_choices": "1"}),
            ("multiple_choice", {"options_text": "甲\n乙", "required": "on", "max_choices": "0"}),
            ("multiple_choice", {"options_text": "甲\n乙", "min_choices": "1.5"}),
            ("text", {"min_length": "-1"}),
            ("text", {"min_length": "abc"}),
            ("text", {"min_length": "3", "max_length": "2"}),
            ("text", {"required": "on", "max_length": "0"}),
            ("text", {"required": "false"}),
            ("number", {"min_value": "NaN"}),
            ("number", {"max_value": "Infinity"}),
            ("number", {"min_value": "abc"}),
            ("number", {"min_value": "5", "max_value": "4"}),
            ("number", {"min_value": "0.2", "max_value": "0.8", "integer_only": "on"}),
            ("number", {"integer_only": "true"}),
        )
        for kind, fields in cases:
            with self.subTest(kind=kind, fields=fields):
                self.assertEqual(self.add_question(survey["_id"], kind, **fields).status_code, 400)
                self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), survey)

    def test_invalid_question_form_keeps_entered_fields_for_correction(self):
        survey_id = self.saved_draft()["_id"]
        response = self.add_question(survey_id, "multiple_choice", title="请保留这个标题",
                                     options_text="苹果\n香蕉", min_choices="7", max_choices="8")
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        for text in ("请保留这个标题", "苹果", "香蕉", 'value="7"', 'value="8"'):
            self.assertIn(text, html)

    def test_unknown_question_kind_is_rejected_on_get_and_post(self):
        survey = self.saved_draft()
        path = f"/surveys/{survey['_id']}/questions/new"
        self.assertEqual(self.client.get(f"{path}?kind=unsupported").status_code, 400)
        response = self.client.post(path, data={
            "csrf_token": self.token(), "kind": "unsupported", "title": "无效类型",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), survey)

    def test_published_and_closed_surveys_reject_question_changes(self):
        survey = self.saved_draft()
        path = f"/surveys/{survey['_id']}/questions/new"
        token = self.token()
        for status in ("published", "closed"):
            with self.subTest(status=status):
                self.database.surveys.update_one({"_id": survey["_id"]}, {"$set": {"status": status}})
                before = self.database.surveys.find_one({"_id": survey["_id"]})
                self.assertEqual(self.client.get(path).status_code, 409)
                response = self.client.post(path, data={
                    "csrf_token": token, "kind": "text", "title": "不能保存",
                })
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), before)

    def test_missing_wrong_and_cross_browser_csrf_cannot_create_or_add(self):
        survey = self.saved_draft()
        other_browser = self.app.test_client()
        self.assume_user(self.owner, client=other_browser)
        foreign_token = self.token(client=other_browser)
        self.token()
        for path in ("/surveys/new", f"/surveys/{survey['_id']}/questions/new"):
            for token in (None, "wrong-token", "无效令牌", foreign_token):
                with self.subTest(path=path, token=token):
                    data = {"title": "不可保存", "kind": "text"}
                    if token is not None:
                        data["csrf_token"] = token
                    self.assertEqual(self.client.post(path, data=data).status_code, 400)
        self.assertEqual(self.database.surveys.count_documents({}), 1)
        self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), survey)

    def test_saved_survey_question_and_option_text_are_html_escaped(self):
        unsafe = '<script>alert("xss")</script>'
        survey_id = self.saved_draft(title=unsafe, description=unsafe)["_id"]
        self.assertEqual(self.add_question(survey_id, "single_choice", title=unsafe,
                                          options_text=f"{unsafe}\n安全选项").status_code, 303)
        for path in ("/surveys", f"/surveys/{survey_id}"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn("&lt;script&gt;", html)
                self.assertNotIn(unsafe, html)
        response = self.add_question(survey_id, "single_choice", title=unsafe, options_text="只有一个选项")
        self.assertEqual(response.status_code, 400)
        self.assertIn("&lt;script&gt;", response.get_data(as_text=True))
        self.assertNotIn(unsafe, response.get_data(as_text=True))

    def test_get_requests_do_not_create_documents_or_questions(self):
        for path in ("/surveys", "/surveys/new"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.database.surveys.count_documents({}), 0)
        survey = self.saved_draft()
        for kind in ("single_choice", "multiple_choice", "text", "number"):
            path = f"/surveys/{survey['_id']}/questions/new?kind={kind}"
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), survey)

    def test_database_failure_returns_friendly_503_without_partial_write(self):
        survey = self.saved_draft()
        token = self.token()
        operations = (
            ("GET", "/surveys", {}),
            ("GET", f"/surveys/{survey['_id']}", {}),
            ("POST", "/surveys/new", {"csrf_token": token, "title": "不能保存"}),
            ("POST", f"/surveys/{survey['_id']}/questions/new",
             {"csrf_token": token, "kind": "text", "title": "不能保存"}),
        )
        for method, path, data in operations:
            with self.subTest(method=method, path=path):
                with patch("survey_views.get_request_database", side_effect=ServerSelectionTimeoutError("private connection details")), self.assertLogs(self.app.logger, level="ERROR"):
                    response = self.client.open(path, method=method, data=data)
                self.assertEqual(response.status_code, 503)
                self.assertIn("暂时无法连接数据服务", response.get_data(as_text=True))
                self.assertNotIn("private connection details", response.get_data(as_text=True))
        self.assertEqual(self.database.surveys.count_documents({}), 1)
        self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), survey)


if __name__ == "__main__":
    unittest.main()
