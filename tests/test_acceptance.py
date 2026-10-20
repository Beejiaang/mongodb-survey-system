"""第一阶段验收：全部配置与操作经真实网页路由，数据写入隔离MongoDB。"""

from datetime import datetime, timedelta, timezone
import os
import re
import unittest
from uuid import uuid4

from app import create_app
from database import create_client, initialize_database
from survey_results import owned_results


class FirstStageAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017")
        cls.mongo = create_client(cls.uri)
        cls.addClassCleanup(cls.mongo.close)
        cls.mongo.admin.command("ping")

    def setUp(self):
        self.name = "survey_system_test_acceptance_" + uuid4().hex
        self.assertNotIn(self.name, self.mongo.list_database_names())
        self.db = self.mongo[self.name]
        self.addCleanup(lambda: self.mongo.drop_database(self.name))
        initialize_database(self.db)
        self.config = {"TESTING": True, "SECRET_KEY": "acceptance-only-session",
                       "RESPONSE_IDENTITY_KEY": "acceptance-only-identity",
                       "MONGODB_URI": self.uri, "MONGODB_DB_NAME": self.name}
        self.app = create_app(self.config)
        self.owner = self.app.test_client()
        self.reader = self.app.test_client()

    def post(self, browser, path, form_path=None, **fields):
        page = browser.get(form_path or path)
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        data = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))
        data.update(fields)
        return browser.post(path, data=data)

    def register_and_login(self, browser, username):
        self.assertEqual(self.post(browser, "/register", username=username, password="AcceptanceOnly!",
                                   confirm_password="AcceptanceOnly!").status_code, 303)
        self.assertEqual(self.post(browser, "/login", username=username.upper(),
                                   password="AcceptanceOnly!").status_code, 303)

    def test_first_stage_web_lifecycle_and_three_configured_jump_types(self):
        self.register_and_login(self.owner, "accept_owner")
        self.register_and_login(self.reader, "accept_reader")
        result = self.post(self.owner, "/surveys/new", title="第一阶段验收问卷", description="配置与填写验收",
                           is_anonymous="on", allow_repeat="on")
        self.assertEqual(result.status_code, 303)
        manage = result.location
        survey = self.db.surveys.find_one({"title": "第一阶段验收问卷"})
        sid, owner_id = survey["_id"], survey["owner_id"]
        forms = [
            {"kind": "number", "title": "年龄", "required": "on", "min_value": "0", "max_value": "120", "integer_only": "on"},
            {"kind": "single_choice", "title": "身份", "required": "on", "options_text": "学生\n其他"},
            {"kind": "multiple_choice", "title": "参与活动", "required": "on", "options_text": "A\nB\nC", "min_choices": "2", "max_choices": "3"},
            {"kind": "text", "title": "补充说明", "required": "on", "min_length": "2", "max_length": "100"},
            {"kind": "text", "title": "最终建议", "required": "on", "min_length": "2", "max_length": "100"},
        ]
        for fields in forms:
            response = self.post(self.owner, manage + "/questions/new",
                                 form_path=manage + "/questions/new?kind=" + fields["kind"], **fields)
            self.assertEqual(response.status_code, 303)
        questions = self.db.surveys.find_one({"_id": sid})["questions"]
        qids = [q["id"] for q in questions]
        single_ids = [o["id"] for o in questions[1]["options"]]
        multi_ids = [o["id"] for o in questions[2]["options"]]
        rules = [
            (0, {"operator": "lt", "number": "18", "target": qids[2]}),
            (1, {"operator": "equals", "value": single_ids[1], "target": qids[3]}),
            (2, {"operator": "equals_set", "value": multi_ids[:2], "target": qids[4]}),
            (2, {"operator": "contains_all", "value": multi_ids[:2], "target": qids[3]}),
        ]
        for index, fields in rules:
            self.assertEqual(self.post(self.owner, manage + f"/questions/{qids[index]}/jumps",
                                       action="add", **fields).status_code, 303)
        deadline = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        self.assertEqual(self.post(self.owner, manage + "/deadline", form_path=manage, deadline=deadline).status_code, 303)
        self.assertEqual(self.post(self.owner, manage + "/publish", form_path=manage).status_code, 303)
        share = f"/survey/{sid}"
        self.assertEqual(self.reader.get(share).status_code, 200)
        self.assertEqual(self.reader.get(manage).status_code, 404)
        self.assertEqual(self.reader.get(manage + "/results").status_code, 404)

        # 数字跳转后恰好AB，直接到最终建议，跳过两道必答题。
        start = self.post(self.reader, share + "/start", form_path=share)
        path = start.location
        self.assertEqual(self.post(self.reader, path, action="next", answer="121").status_code, 400)
        for value in ("16", multi_ids[:2], "路径一建议"):
            self.assertEqual(self.post(self.reader, path, action="next", answer=value).status_code, 303)
        self.assertIn("确认你的回答", self.reader.get(path).get_data(as_text=True))
        self.assertEqual(self.post(self.reader, path, action="submit").status_code, 303)
        self.assertIn("提交成功", self.reader.get(path).get_data(as_text=True))

        # 同一填写者再次填写：包含ABC，需要补充说明，随后按顺序继续。
        path2 = self.post(self.reader, share + "/start", form_path=share).location
        self.assertNotEqual(path, path2)
        for value in ("20", single_ids[0], multi_ids, "更多场次", "路径二建议"):
            self.assertEqual(self.post(self.reader, path2, action="next", answer=value).status_code, 303)
        self.assertEqual(self.post(self.reader, path2, action="submit").status_code, 303)

        # 第三次验证单选跳转，略过必答多选题；新应用实例仍能读到已保存进度。
        path3 = self.post(self.reader, share + "/start", form_path=share).location
        for value in ("30", single_ids[1]):
            self.assertEqual(self.post(self.reader, path3, action="next", answer=value).status_code, 303)
        fresh = create_app(self.config).test_client()
        self.assertEqual(self.post(fresh, "/login", username="accept_reader", password="AcceptanceOnly!").status_code, 303)
        self.assertIn("补充说明", fresh.get(path3).get_data(as_text=True))
        for value in ("补充内容", "路径三建议"):
            self.assertEqual(self.post(fresh, path3, action="next", answer=value).status_code, 303)
        self.assertEqual(self.post(fresh, path3, action="submit").status_code, 303)

        records = list(self.db.responses.find({"survey_id": sid, "state": "submitted"}).sort("created_at", 1))
        self.assertEqual(len(records), 3)
        self.assertEqual([r["path"] for r in records], [[qids[0], qids[2], qids[4]], qids,
                                                       [qids[0], qids[1], qids[3], qids[4]]])
        self.assertTrue(all(r["user_id"] is None for r in records))
        _, stats = owned_results(self.db, sid, owner_id)
        self.assertEqual(stats["total"], 3)
        self.assertEqual([r["answered"] for r in stats["questions"]], [3, 2, 2, 2, 3])
        self.assertEqual(stats["questions"][0]["average"], "22.00")
        self.assertEqual([o["count"] for o in stats["questions"][2]["options"]], [2, 2, 1])
        self.assertEqual(self.owner.get(manage + "/results").status_code, 200)
        answers = self.owner.get(manage + f"/results/questions/{qids[4]}").get_data(as_text=True)
        for text in ("路径一建议", "路径二建议", "路径三建议"):
            self.assertIn(text, answers)
        for record in records:
            self.assertNotIn(record["respondent_key"], answers)
        self.assertEqual(self.post(self.owner, manage + "/close", form_path=manage).status_code, 303)
        self.assertEqual(self.reader.get(share).status_code, 410)
        self.assertEqual(self.owner.get(manage + "/results").status_code, 200)
        self.assertEqual(self.post(self.reader, "/logout", form_path="/account").status_code, 303)
        self.assertEqual(self.reader.get("/account").status_code, 302)
