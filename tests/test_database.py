"""在独立临时数据库中验证集合、约束和用户保存行为。"""

import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure

from app import app
from database import COLLECTION_NAMES, create_client, initialize_database
from users import build_user_document, create_user, find_user_by_username, verify_user_password
from questions import build_question, validate_answer, validate_questions
from survey_schema import SURVEY_VALIDATOR
from surveys import build_survey_document


class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_client(os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017"))
        cls.addClassCleanup(cls.client.close)
        # 连接失败就是测试失败，不能将数据库验证悄悄跳过。
        cls.client.admin.command("ping")

    def setUp(self):
        self.database_name = "survey_system_test_" + uuid4().hex
        self.assertNotIn(self.database_name, self.client.list_database_names())
        self.database = self.client[self.database_name]
        self.addCleanup(self.remove_test_database)
        initialize_database(self.database)

    def remove_test_database(self):
        # 只清理本用例刚创建且名字经过核对的临时数据库。
        if not self.database_name.startswith("survey_system_test_"):
            raise RuntimeError("拒绝清理非测试数据库。")
        self.client.drop_database(self.database_name)

    def test_creates_three_empty_collections(self):
        self.assertEqual(set(self.database.list_collection_names()), set(COLLECTION_NAMES))
        for name in COLLECTION_NAMES:
            self.assertEqual(self.database[name].count_documents({}), 0)

    def test_embedded_questions_round_trip_through_mongodb(self):
        options = [{"id": "a", "text": "苹果"}, {"id": "b", "text": "香蕉"}]
        questions = [
            build_question("single_choice", "单选", options=options),
            build_question("multiple_choice", "多选", options=options,
                           validation={"min_choices": 2, "max_choices": 2}),
            build_question("fill_blank", "文本", input_type="text"),
            build_question("fill_blank", "数字", input_type="number",
                           validation={"integer_only": True, "max_value": 2**63 - 1}),
        ]
        document = build_survey_document(ObjectId(), "四种题目存储测试")
        document["questions"] = questions
        survey_id = self.database.surveys.insert_one(document).inserted_id
        loaded = self.database.surveys.find_one({"_id": survey_id})["questions"]
        self.assertEqual(validate_questions(loaded), questions)
        for question, answer in zip(loaded, ["a", ["a", "b"], "填写内容", 13800138000]):
            self.assertEqual(validate_answer(question, answer), answer)
        self.assertEqual(self.database.users.count_documents({}), 0)
        self.assertEqual(self.database.responses.count_documents({}), 0)

    def test_initialization_preserves_existing_records(self):
        user_id = create_user(self.database, "  Alice_01  ", " Secret Case ")
        survey = build_survey_document(user_id, "已有问卷")
        self.database.surveys.insert_one(survey)
        saved_survey = self.database.surveys.find_one({"_id": survey["_id"]})
        initialize_database(self.database)
        stored = find_user_by_username(self.database, " ALICE_01 ")
        self.assertEqual(stored["_id"], user_id)
        self.assertTrue(verify_user_password(stored, " Secret Case "))
        self.assertFalse(verify_user_password(stored, "Secret Case"))
        self.assertEqual(self.database.users.count_documents({}), 1)
        self.assertEqual(self.database.surveys.find_one({"_id": survey["_id"]}), saved_survey)

    def test_empty_legacy_surveys_collection_gets_validator_and_index(self):
        self.database.command("collMod", "surveys", validator={})
        initialize_database(self.database)
        self.assertEqual(self.database.surveys.options()["validator"], SURVEY_VALIDATOR)
        index = self.database.surveys.index_information()["owner_created_at"]
        self.assertEqual(index["key"], [("owner_id", 1), ("created_at", -1), ("_id", -1)])
        self.assertEqual(self.database.surveys.count_documents({}), 0)

    def test_nonempty_legacy_surveys_collection_requires_migration(self):
        self.database.command("collMod", "surveys", validator={})
        legacy = {"_id": ObjectId(), "legacy_title": "必须保留"}
        self.database.surveys.insert_one(legacy)
        with self.assertRaisesRegex(ValueError, "surveys"):
            initialize_database(self.database)
        self.assertEqual(self.database.surveys.find_one({"_id": legacy["_id"]}), legacy)
        self.assertEqual(self.database.surveys.options().get("validator", {}), {})

    def test_different_surveys_validator_is_not_overwritten(self):
        legacy_validator = {"title": {"$type": "string"}}
        self.database.command("collMod", "surveys", validator=legacy_validator)
        with self.assertRaisesRegex(ValueError, "surveys"):
            initialize_database(self.database)
        self.assertEqual(self.database.surveys.options()["validator"], legacy_validator)

    def test_surveys_validator_rejects_missing_fields_and_wrong_values(self):
        valid = build_survey_document(ObjectId(), "问卷")
        invalid_documents = []
        for field in set(valid) - {"_id"}:
            document = dict(valid)
            del document[field]
            invalid_documents.append(document)
        for field, value in (
            ("_id", "bad"), ("owner_id", "bad"), ("title", "  "),
            ("title", "题" * 201), ("description", "文" * 5001),
            ("status", "unknown"), ("is_anonymous", 1), ("allow_repeat", "false"),
            ("questions", {}), ("created_at", None), ("updated_at", "today"),
            ("published_at", 1), ("deadline", "tomorrow"), ("unexpected", True),
        ):
            invalid_documents.append(dict(valid, **{field: value}))
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(OperationFailure) as error:
                    self.database.surveys.insert_one(document)
                self.assertEqual(error.exception.code, 121)
        self.assertEqual(self.database.surveys.count_documents({}), 0)

    def test_surveys_validator_rejects_mismatched_question_structure(self):
        from copy import deepcopy

        valid_question = build_question("fill_blank", "文本", input_type="text")
        invalid_questions = [{"title": "缺少配置"}]
        for field, value in (
            ("type", "single_choice"), ("required", "false"),
            ("input_type", "number"), ("validation", {"min_length": -1, "max_length": None}),
            ("validation", {"min_length": 1.5, "max_length": None}),
            ("validation", {"min_length": None, "max_length": None, "unknown": 1}),
            ("jump_rules", [{"target": "q2"}]), ("unknown", True),
        ):
            invalid_questions.append(dict(deepcopy(valid_question), **{field: value}))
        choices = build_question("single_choice", "单选", options=[
            {"id": "a", "text": "A"}, {"id": "b", "text": "B"},
        ])
        choices["options"].pop()
        invalid_questions.append(choices)
        for question in invalid_questions:
            with self.subTest(question=question):
                document = build_survey_document(ObjectId(), "非法题目")
                document["questions"] = [question]
                with self.assertRaises(OperationFailure) as error:
                    self.database.surveys.insert_one(document)
                self.assertEqual(error.exception.code, 121)
        self.assertEqual(self.database.surveys.count_documents({}), 0)

    def test_normalized_username_is_unique(self):
        create_user(self.database, " Alice ", "first password")
        with self.assertRaises(DuplicateKeyError):
            create_user(self.database, "ALICE", "second password")
        self.assertEqual(self.database.users.count_documents({}), 1)

    def test_cli_initializes_the_configured_database(self):
        with patch.dict(os.environ, {
            "MONGODB_URI": os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017"),
            "MONGODB_DB_NAME": self.database_name,
        }):
            result = app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(self.database_name, result.output)
        self.assertEqual(set(self.database.list_collection_names()), set(COLLECTION_NAMES))

    def test_existing_different_validator_is_not_overwritten(self):
        user_id = create_user(self.database, "alice", "test password")
        self.database.command("collMod", "users", validator={})
        with self.assertRaises(ValueError):
            initialize_database(self.database)
        self.assertIsNotNone(self.database.users.find_one({"_id": user_id}))
        self.assertEqual(self.database.users.options().get("validator", {}), {})

    def test_missing_fields_are_rejected(self):
        valid = build_user_document("alice", "test password")
        for field in ("username", "password_hash", "created_at"):
            with self.subTest(field=field):
                document = dict(valid)
                del document[field]
                with self.assertRaises(OperationFailure) as error:
                    self.database.users.insert_one(document)
                self.assertEqual(error.exception.code, 121)

    def test_invalid_document_values_are_rejected(self):
        valid = build_user_document("alice", "test password")
        invalid_values = (
            ("_id", "not-an-object-id"),
            ("username", "Alice"),
            ("username", "ab"),
            ("username", "a" * 31),
            ("username", "abc\n"),
            ("username", "a b"),
            ("username", "用户名"),
            ("username", {"$ne": None}),
            ("password_hash", "plaintext password"),
            ("created_at", "2026-10-15"),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                document = dict(valid, **{field: value})
                with self.assertRaises(OperationFailure) as error:
                    self.database.users.insert_one(document)
                self.assertEqual(error.exception.code, 121)

    def test_plaintext_password_field_is_rejected(self):
        document = build_user_document("alice", "test password")
        document["password"] = "test password"
        with self.assertRaises(OperationFailure) as error:
            self.database.users.insert_one(document)
        self.assertEqual(error.exception.code, 121)

    def test_saved_user_has_bson_date_and_no_plaintext(self):
        before = datetime.now(timezone.utc)
        user_id = create_user(self.database, " Alice ", "real test password")
        after = datetime.now(timezone.utc)
        stored = self.database.users.find_one({"_id": user_id})
        self.assertEqual(set(stored), {"_id", "username", "password_hash", "created_at"})
        self.assertEqual(stored["username"], "alice")
        self.assertNotIn("real test password", stored.values())
        self.assertIsInstance(stored["created_at"], datetime)
        self.assertEqual(stored["created_at"].utcoffset(), timezone.utc.utcoffset(None))
        # BSON Date 的精度为毫秒。
        earliest = before.replace(microsecond=(before.microsecond // 1000) * 1000)
        self.assertLessEqual(earliest, stored["created_at"])
        self.assertLessEqual(stored["created_at"], after)


if __name__ == "__main__":
    unittest.main()
