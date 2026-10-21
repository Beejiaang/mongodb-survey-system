"""发布、截止、关闭、分享与登录回跳的真实 MongoDB 集成测试。"""

import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId

from app import create_app
from database import create_client, initialize_database
from questions import build_question
from surveys import (add_question, availability, close_survey, create_survey,
                     get_owned_survey, publish_survey, set_survey_deadline)
from users import build_user_document


class PublishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017")
        cls.mongo = create_client(cls.uri)
        cls.addClassCleanup(cls.mongo.close)
        cls.mongo.admin.command("ping")
        cls.user = build_user_document("publisher", "test-pass")
        cls.reader = build_user_document("reader", "test-pass")

    def setUp(self):
        self.name = "survey_system_test_" + uuid4().hex
        self.assertNotIn(self.name, self.mongo.list_database_names())
        self.db = self.mongo[self.name]
        self.addCleanup(self.cleanup)
        initialize_database(self.db)
        self.db.users.insert_many([dict(self.user), dict(self.reader)])
        self.owner = self.user["_id"]
        self.app = create_app({"TESTING": True, "SECRET_KEY": "publishing-test-only",
                               "MONGODB_URI": self.uri, "MONGODB_DB_NAME": self.name})
        self.client = self.app.test_client()
        self.login_as(self.owner)
        self.sid = create_survey(self.db, self.owner, "测试发布", "说明")
        add_question(self.db, self.sid, self.owner,
                     build_question("fill_blank", "你的建议", input_type="text"))
        self.path = f"/surveys/{self.sid}"
        self.share = f"/survey/{self.sid}"

    def cleanup(self):
        if not self.name.startswith("survey_system_test_"):
            raise RuntimeError("拒绝清理非测试数据库")
        self.mongo.drop_database(self.name)

    def login_as(self, user_id):
        with self.client.session_transaction() as state:
            state.clear()
            state["user_id"] = str(user_id)

    def token(self, client=None, path="/login"):
        browser = client or self.client
        response = browser.get(path if client else self.path)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)

    def post(self, action, **fields):
        return self.client.post(self.path + "/" + action,
                                data={"csrf_token": self.token(), **fields})

    def stored(self):
        return self.db.surveys.find_one({"_id": self.sid})

    def test_publish_sets_dates_locks_questions_and_keeps_share_url_stable(self):
        before = self.stored()
        self.assertEqual(self.post("publish").status_code, 303)
        stored = self.stored()
        self.assertEqual(stored["status"], "published")
        self.assertEqual(stored["created_at"], before["created_at"])
        self.assertEqual(stored["questions"], before["questions"])
        self.assertIsInstance(stored["published_at"], datetime)
        self.assertEqual(stored["published_at"], stored["updated_at"])
        html = self.client.get(self.path).get_data(as_text=True)
        self.assertIn("http://localhost" + self.share, html)
        self.assertIn('id="copy-share"', html)
        self.assertEqual(self.client.get(self.path + "/questions/new").status_code, 409)
        self.assertEqual(self.post("publish").status_code, 409)
        self.assertEqual(self.stored(), stored)

        # 只模拟不同请求Host，不建立局域网连接；真实跨设备验收另见手工N01。
        for origin in ("http://192.168.1.50:5002", "http://10.0.0.50:8080"):
            with self.subTest(origin=origin):
                publisher = self.app.test_client()
                with publisher.session_transaction(base_url=origin) as state:
                    state["user_id"] = str(self.owner)
                page = publisher.get(self.path, base_url=origin)
                self.assertEqual(page.status_code, 200)
                self.assertIn(f'id="share-url" readonly value="{origin}{self.share}"', page.get_data(as_text=True))
                reader = self.app.test_client()
                redirect = reader.get(self.share, base_url=origin)
                self.assertEqual(redirect.status_code, 302)
                self.assertEqual(redirect.location, "/login?next=" + self.share)
                login = reader.get(redirect.location, base_url=origin)
                token = re.search(r'name="csrf_token" value="([^"]+)"', login.get_data(as_text=True)).group(1)
                result = reader.post(redirect.location, base_url=origin,
                    data={"csrf_token": token, "username": "reader", "password": "test-pass"})
                self.assertEqual(result.status_code, 303)
                self.assertEqual(result.location, self.share)
                shared = reader.get(result.location, base_url=origin)
                self.assertEqual(shared.status_code, 200)
                self.assertIn("开始或继续填写", shared.get_data(as_text=True))

    def test_empty_or_invalid_question_cannot_publish(self):
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": []}})
        self.assertEqual(self.post("publish").status_code, 400)
        q = build_question("fill_blank", "长度", input_type="text")
        q["validation"] = {"min_length": 10, "max_length": 1}
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"questions": [q]}})
        self.assertEqual(self.post("publish").status_code, 400)
        self.assertEqual(self.stored()["status"], "draft")
        self.assertIsNone(self.stored()["published_at"])

    def test_beijing_deadline_converts_to_utc_and_can_be_removed(self):
        with patch("surveys.utcnow", return_value=datetime(2030, 1, 1, tzinfo=timezone.utc)):
            self.assertEqual(self.post("deadline", deadline="2030-01-02T18:30").status_code, 303)
            self.assertEqual(self.stored()["deadline"], datetime(2030, 1, 2, 10, 30, tzinfo=timezone.utc))
            html = self.client.get(self.path).get_data(as_text=True)
            self.assertIn('value="2030-01-02T18:30"', html)
            self.assertEqual(self.post("deadline", deadline="").status_code, 303)
            self.assertIsNone(self.stored()["deadline"])

    def test_bad_past_or_equal_deadlines_do_not_change_document(self):
        before = self.stored()
        with patch("surveys.utcnow", return_value=datetime(2030, 1, 1, tzinfo=timezone.utc)):
            for raw in ("bad", "0001-01-01T00:00", "2030-02-30T10:00", "2030-01-01T08:00", "2029-12-31T23:59",
                        "2030-01-02T08:00+08:00"):
                with self.subTest(raw=raw):
                    self.assertEqual(self.post("deadline", deadline=raw).status_code, 400)
                    self.assertEqual(self.stored(), before)
        with self.assertRaises(ValueError):
            set_survey_deadline(self.db, self.sid, self.owner, datetime(2030, 1, 2))

    def test_draft_with_elapsed_deadline_cannot_publish(self):
        past = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"deadline": past}})
        self.assertEqual(self.post("publish").status_code, 400)
        self.assertEqual(self.stored()["status"], "draft")

    def test_deadline_exact_boundary_blocks_shared_content_without_state_write(self):
        publish_survey(self.db, self.sid, self.owner)
        end = datetime(2030, 1, 2, tzinfo=timezone.utc)
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"deadline": end}})
        before = self.stored()
        for now, status in ((end - timedelta(milliseconds=1), 200), (end, 410),
                            (end + timedelta(milliseconds=1), 410)):
            with self.subTest(now=now), patch("surveys.utcnow", return_value=now):
                response = self.client.get(self.share)
                self.assertEqual(response.status_code, status)
                if status == 410:
                    self.assertNotIn("你的建议", response.get_data(as_text=True))
                    self.assertIn("已截止", self.client.get("/surveys").get_data(as_text=True))
        self.assertEqual(self.stored(), before)

    def test_extending_expired_published_survey_reopens_entry(self):
        publish_survey(self.db, self.sid, self.owner)
        self.db.surveys.update_one({"_id": self.sid}, {"$set": {"deadline": datetime(2000, 1, 1, tzinfo=timezone.utc)}})
        self.assertEqual(self.client.get(self.share).status_code, 410)
        self.assertEqual(self.post("deadline", deadline="").status_code, 303)
        self.assertEqual(self.client.get(self.share).status_code, 200)

    def test_close_blocks_entry_preserves_data_and_cannot_reopen(self):
        self.assertEqual(self.post("close").status_code, 409)
        publish_survey(self.db, self.sid, self.owner)
        before = self.stored()
        self.assertEqual(self.post("close").status_code, 303)
        closed = self.stored()
        self.assertEqual(closed["status"], "closed")
        for key in ("questions", "published_at", "created_at", "deadline"):
            self.assertEqual(closed[key], before[key])
        response = self.client.get(self.share)
        self.assertEqual(response.status_code, 410)
        self.assertNotIn("你的建议", response.get_data(as_text=True))
        for action, fields in (("publish", {}), ("close", {}), ("deadline", {"deadline": ""})):
            self.assertEqual(self.post(action, **fields).status_code, 409)
        self.assertEqual(self.stored(), closed)

    def test_only_owner_can_manage_even_if_other_reader_has_share_link(self):
        publish_survey(self.db, self.sid, self.owner)
        self.login_as(self.reader["_id"])
        self.assertEqual(self.client.get(self.share).status_code, 200)
        token = self.token(client=self.client, path="/surveys/new")
        for action in ("publish", "close", "deadline"):
            response = self.client.post(self.path + "/" + action, data={"csrf_token": token})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.stored()["status"], "published")

    def test_draft_and_unknown_share_links_are_hidden(self):
        for browser in (self.client, self.app.test_client()):
            for path in (self.share, "/survey/bad", f"/survey/{ObjectId()}"):
                self.assertEqual(browser.get(path).status_code, 404)

    def test_share_login_returns_to_same_questionnaire(self):
        publish_survey(self.db, self.sid, self.owner)
        browser = self.app.test_client()
        response = browser.get(self.share)
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=/survey/", response.location)
        login_path = response.location
        token = self.token(client=browser, path=login_path)
        response = browser.post(login_path, data={"csrf_token": token,
                                "username": "reader", "password": "test-pass"})
        self.assertEqual(response.location, self.share)
        html = browser.get(self.share).get_data(as_text=True)
        self.assertIn("你的建议", html)
        self.assertNotIn(str(self.owner), html)
        self.assertNotIn("发布问卷", html)
        self.assertIn("开始或继续填写", html)

    def test_registration_preserves_share_destination(self):
        browser = self.app.test_client()
        path = "/register?next=" + self.share
        token = self.token(client=browser, path=path)
        html = browser.get(path).get_data(as_text=True)
        self.assertIn('action="/register?next=' + self.share, html)
        response = browser.post(path, data={"csrf_token": token, "username": "new_reader",
                                "password": "test-pass", "confirm_password": "test-pass"})
        self.assertEqual(response.location, "/login?next=" + self.share)

    def test_external_and_malformed_login_destinations_are_rejected(self):
        for target in ("https://evil.example", "//evil.example", "/\\evil.example", "/survey/bad",
                       self.share + "?redirect=bad"):
            with self.subTest(target=target):
                browser = self.app.test_client()
                token = self.token(client=browser)
                response = browser.post("/login", query_string={"next": target},
                                        data={"csrf_token": token, "username": "reader", "password": "test-pass"})
                self.assertEqual(response.location, "/account")

    def test_mutations_require_post_and_csrf(self):
        before = self.stored()
        for action in ("publish", "deadline", "close"):
            self.assertEqual(self.client.get(self.path + "/" + action).status_code, 405)
            for token in (None, "wrong", "无效"):
                self.assertEqual(self.client.post(self.path + "/" + action,
                    data={} if token is None else {"csrf_token": token}).status_code, 400)
        self.assertEqual(self.stored(), before)

    def test_concurrent_publish_only_records_one_first_publication(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: publish_survey(self.db, self.sid, self.owner), range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.stored()["status"], "published")

    def test_changed_questions_during_publish_require_retry(self):
        original = get_owned_survey
        def changed(database, survey_id, owner_id):
            snapshot = original(database, survey_id, owner_id)
            add_question(database, survey_id, owner_id,
                         build_question("fill_blank", "并发题目", input_type="text"))
            return snapshot
        with patch("surveys.get_owned_survey", side_effect=changed):
            self.assertFalse(publish_survey(self.db, self.sid, self.owner))
        self.assertEqual(self.stored()["status"], "draft")
        self.assertEqual(len(self.stored()["questions"]), 2)

    def test_invalid_ids_and_foreign_owner_cannot_change_lifecycle(self):
        for sid, owner in (("bad", self.owner), (self.sid, "bad"),
                           (self.sid, self.reader["_id"]), (ObjectId(), self.owner)):
            self.assertFalse(publish_survey(self.db, sid, owner))
            self.assertFalse(close_survey(self.db, sid, owner))
            self.assertFalse(set_survey_deadline(self.db, sid, owner, None))
        self.assertEqual(availability(self.stored()), "draft")


if __name__ == "__main__":
    unittest.main()
