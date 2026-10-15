"""网页表单、登录会话与真实 MongoDB 之间的集成测试。"""

import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import ServerSelectionTimeoutError

from app import create_app
from database import create_client, initialize_database
from users import verify_user_password


class AccountFlowTests(unittest.TestCase):
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
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "isolated-account-test-secret",
            "MONGODB_URI": self.uri, "MONGODB_DB_NAME": self.name,
        })
        self.client = self.app.test_client()

    def clean_database(self):
        if not self.name.startswith("survey_system_test_"):
            raise RuntimeError("拒绝清理非测试数据库。")
        self.mongo.drop_database(self.name)

    def token(self, path="/login", client=None):
        browser = client or self.client
        response = browser.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def register(self, username=" Alice_01 ", password=" Real Pass "):
        return self.client.post("/register", data={
            "csrf_token": self.token("/register"), "username": username,
            "password": password, "confirm_password": password,
        })

    def login(self, username="ALICE_01", password=" Real Pass "):
        return self.client.post("/login", data={
            "csrf_token": self.token(), "username": username, "password": password,
        })

    def test_public_pages_and_stylesheet_are_accessible(self):
        for path in ("/", "/register", "/login", "/static/style.css"):
            with self.subTest(path=path):
                with self.client.get(path) as response:
                    self.assertEqual(response.status_code, 200)
        html = self.client.get("/register").get_data(as_text=True)
        self.assertIn('for="username"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.assertIn('name="confirm_password"', html)

    def test_register_login_account_and_logout_end_to_end(self):
        response = self.register()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/login")
        stored = self.database.users.find_one({"username": "alice_01"})
        self.assertEqual(set(stored), {"_id", "username", "password_hash", "created_at"})
        self.assertTrue(verify_user_password(stored, " Real Pass "))
        self.assertEqual(self.database.surveys.count_documents({}), 0)
        self.assertEqual(self.database.responses.count_documents({}), 0)
        response = self.login()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/account")
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        account = self.client.get("/account")
        self.assertEqual(account.status_code, 200)
        self.assertIn("alice_01", account.get_data(as_text=True))
        self.assertIn("北京时间", account.get_data(as_text=True))
        self.assertNotIn(stored["password_hash"], account.get_data(as_text=True))
        with self.client.session_transaction() as state:
            self.assertEqual(state["user_id"], str(stored["_id"]))
            self.assertNotIn("password", state)
            self.assertNotIn("password_hash", state)
            logout_token = state["csrf_token"]
        self.assertEqual(self.client.post("/logout", data={"csrf_token": logout_token}).status_code, 303)
        self.assertEqual(self.client.get("/account").location, "/login")
        with self.client.session_transaction() as state:
            self.assertNotIn("user_id", state)

    def test_duplicate_normalized_username_is_rejected(self):
        self.register()
        response = self.register("ALICE_01")
        self.assertEqual(response.status_code, 409)
        self.assertIn("已被使用", response.get_data(as_text=True))
        self.assertEqual(self.database.users.count_documents({}), 1)

    def test_registration_enforces_username_rules_and_escapes_input(self):
        for name in ("ab", "a" * 31, "ali ce", "用户甲", "Kelvin", '<script>alert(1)</script>'):
            with self.subTest(name=name):
                response = self.register(name)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("<script>alert(1)</script>", response.get_data(as_text=True))
        self.assertEqual(self.database.users.count_documents({}), 0)

    def test_username_boundaries_are_measured_after_trimming(self):
        for name in (" Ab_ ", " " + "B" * 30 + " "):
            with self.subTest(name=name):
                self.assertEqual(self.register(name).status_code, 303)
        self.assertEqual(self.database.users.count_documents({}), 2)

    def test_password_confirmation_and_empty_password_are_rejected(self):
        response = self.client.post("/register", data={
            "csrf_token": self.token("/register"), "username": "alice",
            "password": "NeverEchoThisPassword", "confirm_password": "different",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("不一致", response.get_data(as_text=True))
        self.assertNotIn("NeverEchoThisPassword", response.get_data(as_text=True))
        self.assertEqual(self.register(password="").status_code, 400)
        self.assertEqual(self.database.users.count_documents({}), 0)

    def test_wrong_password_and_unknown_account_have_the_same_message(self):
        self.register()
        for name, password in (("alice_01", "wrong"), ("unknown", " Real Pass ")):
            with self.subTest(name=name):
                response = self.login(name, password)
                self.assertEqual(response.status_code, 401)
                self.assertIn("用户名或密码错误", response.get_data(as_text=True))
                with self.client.session_transaction() as state:
                    self.assertNotIn("user_id", state)

    def test_login_uses_same_username_rules_and_preserves_password_spaces(self):
        self.register()
        for password in ("Real Pass", " real pass "):
            self.assertEqual(self.login(password=password).status_code, 401)
        self.assertEqual(self.login(" \tALICE_01\n").status_code, 303)

    def test_invalid_login_username_is_rejected(self):
        self.assertEqual(self.login("{'$ne':null}").status_code, 400)

    def test_csrf_missing_wrong_and_unicode_tokens_are_rejected(self):
        for path in ("/register", "/login", "/logout"):
            self.token("/login")
            for token in (None, "wrong-token", "无效"):
                with self.subTest(path=path, token=token):
                    data = {"username": "alice", "password": "pass", "confirm_password": "pass"}
                    if token is not None:
                        data["csrf_token"] = token
                    self.assertEqual(self.client.post(path, data=data).status_code, 400)
        self.assertEqual(self.database.users.count_documents({}), 0)

    def test_csrf_token_cannot_be_shared_between_browsers(self):
        other = self.app.test_client()
        other_token = self.token(client=other)
        self.token()
        response = self.client.post("/login", data={
            "csrf_token": other_token, "username": "alice", "password": "pass",
        })
        self.assertEqual(response.status_code, 400)

    def test_login_rotates_session_and_old_form_cannot_log_out(self):
        self.register()
        old_token = self.token()
        with self.client.session_transaction() as state:
            state["old_marker"] = "remove on login"
        self.assertEqual(self.login().status_code, 303)
        with self.client.session_transaction() as state:
            self.assertNotEqual(state["csrf_token"], old_token)
            self.assertNotIn("old_marker", state)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": old_token}).status_code, 400)
        self.assertEqual(self.client.get("/account").status_code, 200)

    def test_guest_and_other_browser_cannot_access_account(self):
        self.assertEqual(self.client.get("/account").location, "/login")
        self.register()
        self.login()
        self.assertEqual(self.app.test_client().get("/account").location, "/login")
        for path in ("/", "/register", "/login"):
            self.assertEqual(self.client.get(path).location, "/account")

    def test_invalid_and_deleted_users_lose_access(self):
        for user_id in ("broken", {"$ne": None}, str(ObjectId())):
            with self.subTest(user_id=user_id):
                with self.client.session_transaction() as state:
                    state["user_id"] = user_id
                self.assertEqual(self.client.get("/account").location, "/login")
                with self.client.session_transaction() as state:
                    self.assertNotIn("user_id", state)
        self.register()
        self.login()
        self.database.users.delete_one({"username": "alice_01"})
        self.assertEqual(self.client.get("/account").location, "/login")

    def test_get_logout_does_not_end_session(self):
        self.register()
        self.login()
        response = self.client.get("/logout")
        self.assertEqual(response.status_code, 405)
        self.assertIn("POST", response.headers["Allow"])
        self.assertEqual(self.client.get("/account").status_code, 200)

    def test_database_failure_has_friendly_response(self):
        token = self.token("/register")
        with patch("auth.get_request_database", side_effect=ServerSelectionTimeoutError("internal database details")), self.assertLogs(self.app.logger, level="ERROR"):
            response = self.client.post("/register", data={
                "csrf_token": token, "username": "alice", "password": "pass", "confirm_password": "pass",
            })
        self.assertEqual(response.status_code, 503)
        self.assertIn("暂时无法连接数据服务", response.get_data(as_text=True))
        self.assertNotIn("internal database details", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
