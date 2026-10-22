"""Isolated real MongoDB fixtures for phase two; never touch business databases."""
import os
import unittest
from copy import deepcopy
from uuid import uuid4

from app import create_app
from database import create_client, initialize_database
from users import build_user_document


class StageTwoFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017")
        cls.mongo = create_client(cls.uri)
        cls.addClassCleanup(cls.mongo.close)
        cls.mongo.admin.command("ping")
        cls.user_docs = [build_user_document(name, "phase-two-password")
                         for name in ("owner", "other", "third")]

    def setUp(self):
        self.name = "survey_system_test_" + uuid4().hex
        self.database = self.mongo[self.name]
        self.addCleanup(self.clean_database)
        initialize_database(self.database)
        self.database.users.insert_many(deepcopy(self.user_docs))
        self.owner, self.other, self.third = [u["_id"] for u in self.user_docs]
        self.app = create_app({"TESTING": True, "SECRET_KEY": "phase-two-test",
                               "RESPONSE_IDENTITY_KEY": "phase-two-identity-test",
                               "MONGODB_URI": self.uri, "MONGODB_DB_NAME": self.name})
        self.client = self.app.test_client()
        self.login(self.owner)

    def login(self, user_id):
        with self.client.session_transaction() as state:
            state.clear()
            state.update(user_id=str(user_id), csrf_token="phase-two-csrf")

    def post(self, path, **data):
        return self.client.post(path, data={"csrf_token": "phase-two-csrf", **data})

    def clean_database(self):
        if not self.name.startswith("survey_system_test_"):
            raise RuntimeError("Refusing to clean a non-test database")
        self.mongo.drop_database(self.name)
