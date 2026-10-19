"""问卷草稿数据规则及真实 MongoDB 保存、隔离和并发测试。"""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId

from database import create_client, initialize_database
from questions import build_question
from surveys import (
    add_question, build_survey_document, create_survey, get_owned_survey,
    list_owned_surveys,
)
from users import build_user_document


class SurveyDocumentTests(unittest.TestCase):
    def test_complete_normalized_draft_with_utc_dates(self):
        owner_id = ObjectId()
        before = datetime.now(timezone.utc)
        document = build_survey_document(owner_id, "  满意度调查  ", "  问卷说明\n", True, True)
        after = datetime.now(timezone.utc)
        self.assertEqual(set(document), {
            "_id", "owner_id", "title", "description", "status", "is_anonymous",
            "allow_repeat", "questions", "created_at", "updated_at", "published_at", "deadline",
        })
        self.assertIsInstance(document["_id"], ObjectId)
        self.assertEqual(document["owner_id"], owner_id)
        self.assertEqual(document["title"], "满意度调查")
        self.assertEqual(document["description"], "问卷说明")
        self.assertEqual(document["status"], "draft")
        self.assertTrue(document["is_anonymous"])
        self.assertTrue(document["allow_repeat"])
        self.assertEqual(document["questions"], [])
        self.assertIsNone(document["published_at"])
        self.assertIsNone(document["deadline"])
        self.assertEqual(document["created_at"], document["updated_at"])
        self.assertEqual(document["created_at"].tzinfo, timezone.utc)
        self.assertLessEqual(before, document["created_at"])
        self.assertLessEqual(document["created_at"], after)

    def test_defaults_and_documents_are_independent(self):
        first = build_survey_document(ObjectId(), "第一个")
        second = build_survey_document(ObjectId(), "第二个")
        self.assertEqual(first["description"], "")
        self.assertFalse(first["is_anonymous"])
        self.assertFalse(first["allow_repeat"])
        self.assertNotEqual(first["_id"], second["_id"])
        first["questions"].append("test")
        self.assertEqual(second["questions"], [])

    def test_title_length_limits_apply_after_trimming(self):
        for title in ("题", "  " + "题" * 200 + "\n"):
            self.assertEqual(build_survey_document(ObjectId(), title)["title"], title.strip())
        for title in ("", " \n", "题" * 201, None, 123, {"$ne": ""}):
            with self.subTest(title=title), self.assertRaises(ValueError):
                build_survey_document(ObjectId(), title)

    def test_description_length_and_type(self):
        self.assertEqual(build_survey_document(ObjectId(), "标题", " " + "文" * 5000 + " ")["description"], "文" * 5000)
        for description in ("文" * 5001, None, 123, []):
            with self.subTest(description=description), self.assertRaises(ValueError):
                build_survey_document(ObjectId(), "标题", description)

    def test_owner_requires_object_id(self):
        for owner_id in (None, str(ObjectId()), "bad", {"$ne": None}, 1):
            with self.subTest(owner_id=owner_id), self.assertRaises(ValueError):
                build_survey_document(owner_id, "标题")

    def test_settings_require_real_booleans(self):
        for value in (None, 0, 1, "true", "false", []):
            for field in ("is_anonymous", "allow_repeat"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    build_survey_document(ObjectId(), "标题", **{field: value})


class SurveyStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_client(os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017"))
        cls.addClassCleanup(cls.client.close)
        cls.client.admin.command("ping")
        # 每个用例使用独立用户文档，避免把耗时哈希重复当成问卷测试。
        cls.owner_document = build_user_document("owner", "test-password")
        cls.other_document = build_user_document("other", "test-password")

    def setUp(self):
        self.database_name = "survey_system_test_" + uuid4().hex
        self.assertNotIn(self.database_name, self.client.list_database_names())
        self.database = self.client[self.database_name]
        self.addCleanup(self.remove_test_database)
        initialize_database(self.database)
        self.database.users.insert_many([dict(self.owner_document), dict(self.other_document)])
        self.owner_id, self.other_id = self.owner_document["_id"], self.other_document["_id"]

    def remove_test_database(self):
        if not self.database_name.startswith("survey_system_test_"):
            raise RuntimeError("拒绝清理非测试数据库。")
        self.client.drop_database(self.database_name)

    def test_create_persists_complete_draft_and_leaves_other_collections_unchanged(self):
        survey_id = create_survey(self.database, self.owner_id, "  我的问卷  ", "说明", True, True)
        document = get_owned_survey(self.database, survey_id, self.owner_id)
        self.assertEqual(document["_id"], survey_id)
        self.assertEqual(document["title"], "我的问卷")
        self.assertEqual(document["description"], "说明")
        self.assertEqual(document["status"], "draft")
        self.assertTrue(document["is_anonymous"])
        self.assertTrue(document["allow_repeat"])
        self.assertEqual(document["questions"], [])
        self.assertIsInstance(document["created_at"], datetime)
        self.assertEqual(document["created_at"].utcoffset(), timezone.utc.utcoffset(None))
        self.assertEqual(self.database.users.count_documents({}), 2)
        self.assertEqual(self.database.responses.count_documents({}), 0)

    def test_invalid_creation_or_missing_owner_never_writes(self):
        for owner_id, title, settings in (
            (ObjectId(), "不存在的用户", {}),
            (self.owner_id, " ", {}),
            (self.owner_id, "标题", {"is_anonymous": "true"}),
            (str(self.owner_id), "标题", {}),
        ):
            with self.subTest(owner=owner_id, title=title), self.assertRaises(ValueError):
                create_survey(self.database, owner_id, title, **settings)
        self.assertEqual(self.database.surveys.count_documents({}), 0)

    def test_lookup_and_lists_are_scoped_to_owner(self):
        mine = create_survey(self.database, self.owner_id, "我的")
        theirs = create_survey(self.database, self.other_id, "别人的")
        self.assertIsNone(get_owned_survey(self.database, mine, self.other_id))
        self.assertIsNone(get_owned_survey(self.database, theirs, self.owner_id))
        self.assertEqual(get_owned_survey(self.database, str(mine), str(self.owner_id))["_id"], mine)
        self.assertEqual([item["_id"] for item in list_owned_surveys(self.database, self.owner_id)], [mine])
        self.assertEqual([item["_id"] for item in list_owned_surveys(self.database, self.other_id)], [theirs])

    def test_invalid_ids_cannot_broaden_queries(self):
        mine = create_survey(self.database, self.owner_id, "我的")
        question = build_question("fill_blank", "问题", input_type="text")
        for value in (None, "bad", 1, {"$ne": None}, []):
            with self.subTest(value=value):
                self.assertIsNone(get_owned_survey(self.database, value, self.owner_id))
                self.assertIsNone(get_owned_survey(self.database, mine, value))
                self.assertEqual(list_owned_surveys(self.database, value), [])
                self.assertFalse(add_question(self.database, value, self.owner_id, question))
                self.assertFalse(add_question(self.database, mine, value, question))

    def test_lists_sort_newest_first_with_stable_id_tiebreaker(self):
        ids = [create_survey(self.database, self.owner_id, f"问卷 {i}") for i in range(3)]
        fixed_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.database.surveys.update_many({}, {"$set": {"created_at": fixed_date}})
        expected = sorted(ids, reverse=True)
        self.assertEqual([item["_id"] for item in list_owned_surveys(self.database, self.owner_id)], expected)
        self.database.surveys.update_one({"_id": expected[-1]}, {"$set": {"created_at": datetime(2026, 2, 1, tzinfo=timezone.utc)}})
        self.assertEqual(list_owned_surveys(self.database, self.owner_id)[0]["_id"], expected[-1])

    def test_add_question_normalizes_and_preserves_order_and_created_time(self):
        survey_id = create_survey(self.database, self.owner_id, "问卷")
        before = get_owned_survey(self.database, survey_id, self.owner_id)
        first = build_question("fill_blank", "第一题", input_type="text")
        second = build_question("fill_blank", "第二题", input_type="number")
        first["title"] = "  第一题  "
        for question in (first, second):
            self.assertTrue(add_question(self.database, survey_id, self.owner_id, question))
        stored = get_owned_survey(self.database, survey_id, self.owner_id)
        self.assertEqual([q["title"] for q in stored["questions"]], ["第一题", "第二题"])
        self.assertEqual(stored["created_at"], before["created_at"])
        self.assertGreaterEqual(stored["updated_at"], before["updated_at"])
        summaries = list_owned_surveys(self.database, self.owner_id)
        self.assertEqual(summaries[0]["questions"], [{"id": first["id"]}, {"id": second["id"]}])

    def test_invalid_question_does_not_change_document(self):
        survey_id = create_survey(self.database, self.owner_id, "问卷")
        before = get_owned_survey(self.database, survey_id, self.owner_id)
        question = build_question("fill_blank", "数字", input_type="number")
        question["validation"].update({"min_value": 10, "max_value": 1})
        with self.assertRaises(ValueError):
            add_question(self.database, survey_id, self.owner_id, question)
        self.assertEqual(get_owned_survey(self.database, survey_id, self.owner_id), before)

    def test_add_requires_existing_owned_draft(self):
        survey_id = create_survey(self.database, self.owner_id, "问卷")
        question = build_question("fill_blank", "问题", input_type="text")
        self.assertFalse(add_question(self.database, survey_id, self.other_id, question))
        self.assertFalse(add_question(self.database, ObjectId(), self.owner_id, question))
        for status in ("published", "closed"):
            self.database.surveys.update_one({"_id": survey_id}, {"$set": {"status": status}})
            self.assertFalse(add_question(self.database, survey_id, self.owner_id, question))
        self.assertEqual(get_owned_survey(self.database, survey_id, self.owner_id)["questions"], [])

    def test_duplicate_question_id_is_rejected_without_changes(self):
        survey_id = create_survey(self.database, self.owner_id, "问卷")
        question = build_question("fill_blank", "问题", input_type="text")
        self.assertTrue(add_question(self.database, survey_id, self.owner_id, question))
        before = get_owned_survey(self.database, survey_id, self.owner_id)
        self.assertFalse(add_question(self.database, survey_id, self.owner_id, question))
        self.assertEqual(get_owned_survey(self.database, survey_id, self.owner_id), before)

    def test_concurrent_appends_do_not_lose_questions(self):
        survey_id = create_survey(self.database, self.owner_id, "问卷")
        questions = [build_question("fill_blank", f"题目 {i}", input_type="text") for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda question: add_question(self.database, survey_id, self.owner_id, question), questions))
        self.assertTrue(all(results))
        stored = get_owned_survey(self.database, survey_id, self.owner_id)["questions"]
        self.assertEqual(len(stored), len(questions))
        self.assertEqual({q["id"] for q in stored}, {q["id"] for q in questions})

    def test_concurrent_duplicate_append_succeeds_exactly_once(self):
        survey_id = create_survey(self.database, self.owner_id, "问卷")
        question = build_question("fill_blank", "唯一题目", input_type="text")
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _: add_question(self.database, survey_id, self.owner_id, question), range(12)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(get_owned_survey(self.database, survey_id, self.owner_id)["questions"], [question])


if __name__ == "__main__":
    unittest.main()
