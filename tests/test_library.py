from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure

from stage2_support import StageTwoFixture
from database import initialize_database
from jumps import question_revision
from questions import build_question, validate_question
from question_library import (LibraryError, bank_revision, change_bank, create_bank, create_item,
    create_version, fork_version, get_bank, get_item, get_version, import_version, list_items,
    list_versions, save_survey_question, share_item, usage, version_difference)
from survey_schema import PRE_LIBRARY_SURVEY_VALIDATOR
from surveys import add_question, create_survey, publish_survey


class LibraryTests(StageTwoFixture):
    def setUp(self):
        super().setUp()
        self.question = build_question("fill_blank", "年龄", input_type="number", required=True,
                                       validation={"min_value": 0, "max_value": 120, "integer_only": True})
        self.iid = create_item(self.database, self.owner, self.question)
        self.v1 = get_item(self.database, self.iid, self.owner)["current_version_id"]

    def test_import_creates_independent_instances_with_exact_version_and_options(self):
        a = create_survey(self.database, self.owner, "A")
        b = create_survey(self.database, self.owner, "B")
        ids = [import_version(self.database, sid, self.owner, self.v1) for sid in (a, a, b)]
        self.assertEqual(len(set(ids)), 3)
        for sid in (a, b):
            for q in self.database.surveys.find_one({"_id": sid})["questions"]:
                self.assertEqual(q["source"], {"question_id": self.iid, "version_id": self.v1})
                self.assertEqual(q["validation"], self.question["validation"])
                self.assertFalse(q["jump_rules"])
                self.assertEqual(validate_question(q), q)
                self.assertEqual(len(question_revision([q])), 64)

    def test_version_edit_and_restore_never_change_published_survey_or_old_versions(self):
        sid = create_survey(self.database, self.owner, "旧问卷")
        import_version(self.database, sid, self.owner, self.v1)
        publish_survey(self.database, sid, self.owner)
        frozen = deepcopy(self.database.surveys.find_one({"_id": sid}))
        old_version = deepcopy(get_version(self.database, self.v1, self.owner)[1])
        changed = deepcopy(self.question); changed["validation"]["max_value"] = 100
        v2 = create_version(self.database, self.iid, self.owner, self.v1, changed, note="年龄范围调整")
        v3 = create_version(self.database, self.iid, self.owner, self.v1, restore=True)
        restored = get_version(self.database, v3, self.owner)[1]
        self.assertEqual(restored["number"], 3)
        self.assertEqual(restored["restored_from_version_id"], self.v1)
        self.assertEqual(restored["parent_version_id"], self.v1)
        self.assertEqual(restored["previous_version_id"], v2)
        self.assertEqual(restored["content"], self.question)
        self.assertEqual(get_version(self.database, self.v1, self.owner)[1], old_version)
        self.assertEqual(self.database.surveys.find_one({"_id": sid}), frozen)
        self.assertEqual(get_item(self.database, self.iid, self.owner)["current_version_id"], v3)

    def test_versions_from_different_bases_coexist_and_show_semantic_differences(self):
        changed = deepcopy(self.question);changed["title"] = "年龄（周岁）"
        changed["validation"]["max_value"] = 100
        v2 = create_version(self.database, self.iid, self.owner, self.v1, changed)
        v3 = create_version(self.database, self.iid, self.owner, self.v1, self.question)
        self.assertEqual([x["number"] for x in list_versions(self.database, self.iid, self.owner)], [3, 2, 1])
        diff = version_difference(self.question, get_version(self.database, v2, self.owner)[1]["content"])
        self.assertEqual({x["field"] for x in diff}, {"题目内容", "最大值"})
        self.assertEqual(version_difference(self.question, get_version(self.database, v3, self.owner)[1]["content"]), [])

    def test_concurrent_version_numbers_are_unique_and_default_never_regresses(self):
        with ThreadPoolExecutor(4) as pool:
            versions = list(pool.map(lambda _: create_version(self.database, self.iid, self.owner, self.v1, self.question), range(6)))
        self.assertEqual(len(set(versions)), 6)
        rows = list_versions(self.database, self.iid, self.owner)
        self.assertEqual([x["number"] for x in rows], list(range(7, 0, -1)))
        item = get_item(self.database, self.iid, self.owner)
        self.assertEqual(item["current_version_id"], rows[0]["_id"])

    def test_share_fork_revoke_preserve_imported_survey_and_independent_fork(self):
        with self.assertRaises(LibraryError):get_version(self.database, self.v1, self.other)
        share_item(self.database, self.iid, self.owner, " OTHER ")
        self.assertEqual(len(list_items(self.database, self.other)), 1)
        sid = create_survey(self.database, self.other, "对方问卷")
        import_version(self.database, sid, self.other, self.v1)
        fork = fork_version(self.database, self.v1, self.other)
        self.assertEqual(get_item(self.database, fork, self.other)["forked_from_version_id"], self.v1)
        with self.assertRaises(LibraryError):create_version(self.database, self.iid, self.other, self.v1, self.question)
        old = deepcopy(self.database.surveys.find_one({"_id": sid}))
        share_item(self.database, self.iid, self.owner, "other", revoke=True)
        with self.assertRaises(LibraryError):get_version(self.database, self.v1, self.other)
        with self.assertRaises(LibraryError):import_version(self.database, sid, self.other, self.v1)
        self.assertEqual(self.database.surveys.find_one({"_id": sid}), old)
        self.assertEqual(len(list_items(self.database, self.other)), 1)  # own fork remains

    def test_sharing_is_owner_only_and_invalid_users_do_not_change_grants(self):
        for uid, username in [(self.other, "third"), (self.owner, "missing"), (self.owner, "owner")]:
            with self.assertRaises(LibraryError):share_item(self.database, self.iid, uid, username)
        self.assertEqual(get_item(self.database, self.iid, self.owner)["shared_with"], [])

    def test_cross_item_base_and_bad_content_rejected_without_allocating_version(self):
        other_item = create_item(self.database, self.owner, self.question)
        foreign = get_item(self.database, other_item, self.owner)["current_version_id"]
        before = get_item(self.database, self.iid, self.owner)
        with self.assertRaises(LibraryError):create_version(self.database, self.iid, self.owner, foreign, self.question)
        bad = deepcopy(self.question);bad["validation"]["max_value"] = -1
        with self.assertRaises(ValueError):create_version(self.database, self.iid, self.owner, self.v1, bad)
        self.assertEqual(get_item(self.database, self.iid, self.owner), before)

    def test_saving_legacy_question_strips_jumps_without_rewriting_source(self):
        sid = create_survey(self.database, self.owner, "旧草稿")
        add_question(self.database, sid, self.owner, self.question)
        old = self.database.surveys.find_one({"_id": sid})
        iid = save_survey_question(self.database, sid, self.owner, self.question["id"])
        self.assertIsNotNone(get_item(self.database, iid, self.owner))
        self.assertEqual(self.database.surveys.find_one({"_id": sid}), old)
        with self.assertRaises(LibraryError):save_survey_question(self.database, sid, self.other, self.question["id"])
        # Legacy question bodies have no 200-character cap; the short library name is only a label.
        long_question = dict(self.question, title="较长的题目内容" * 40)
        long_item = create_item(self.database, self.owner, long_question)
        entry = get_item(self.database, long_item, self.owner)
        self.assertEqual(len(entry["name"]), 200)
        self.assertEqual(get_version(self.database, entry["current_version_id"], self.owner)[1]["content"]["title"], long_question["title"])

    def test_import_into_published_or_other_survey_is_rejected(self):
        sid = create_survey(self.database, self.owner, "已发布")
        add_question(self.database, sid, self.owner, self.question)
        publish_survey(self.database, sid, self.owner)
        with self.assertRaises(LibraryError):import_version(self.database, sid, self.owner, self.v1)
        other_sid = create_survey(self.database, self.other, "他人")
        with self.assertRaises(LibraryError):import_version(self.database, other_sid, self.owner, self.v1)

    def test_usage_lists_other_survey_metadata_without_granting_answer_access(self):
        sid = create_survey(self.database, self.owner, "可见名称")
        import_version(self.database, sid, self.owner, self.v1)
        share_item(self.database, self.iid, self.owner, "other")
        private_sid = create_survey(self.database, self.other, "其他用户的引用问卷")
        import_version(self.database, private_sid, self.other, self.v1)
        result = usage(self.database, self.iid, self.owner)
        self.assertEqual([s["title"] for s in result["surveys"]], ["可见名称"])
        self.assertEqual(result["other_count"], 1)
        self.assertEqual(result["other_surveys"], [{"_id":private_sid,"title":"其他用户的引用问卷","status":"draft"}])

    def test_bank_pins_versions_and_requires_explicit_update(self):
        bid = create_bank(self.database, self.owner, "常用题")
        change_bank(self.database, bid, self.owner, "put", self.v1, bank_revision(get_bank(self.database, bid, self.owner)))
        old = get_bank(self.database, bid, self.owner)
        v2 = create_version(self.database, self.iid, self.owner, self.v1, self.question)
        self.assertEqual(get_bank(self.database, bid, self.owner), old)
        change_bank(self.database, bid, self.owner, "put", v2, bank_revision(old))
        self.assertEqual(get_bank(self.database, bid, self.owner)["entries"], [{"question_id": self.iid, "version_id": v2}])
        with self.assertRaises(LibraryError):change_bank(self.database, bid, self.owner, "remove", v2, bank_revision(old))
        with self.assertRaises(LibraryError):get_bank(self.database, bid, self.other)
        change_bank(self.database, bid, self.owner, "remove", v2, bank_revision(get_bank(self.database, bid, self.owner)))
        self.assertEqual(get_bank(self.database, bid, self.owner)["entries"], [])

    def test_bank_cannot_use_unshared_version_but_can_remove_after_revoke(self):
        bid = create_bank(self.database, self.other, "共享题库")
        rev = bank_revision(get_bank(self.database, bid, self.other))
        with self.assertRaises(LibraryError):change_bank(self.database, bid, self.other, "put", self.v1, rev)
        share_item(self.database, self.iid, self.owner, "other")
        change_bank(self.database, bid, self.other, "put", self.v1, rev)
        share_item(self.database, self.iid, self.owner, "other", revoke=True)
        change_bank(self.database, bid, self.other, "remove", self.v1, bank_revision(get_bank(self.database, bid, self.other)))

    def test_known_phase_one_validator_upgrade_preserves_documents_and_hashes(self):
        sid = create_survey(self.database, self.owner, "旧结构")
        add_question(self.database, sid, self.owner, self.question)
        old = deepcopy(self.database.surveys.find_one({"_id": sid}))
        revision = question_revision(old["questions"])
        self.database.command("collMod", "surveys", validator=PRE_LIBRARY_SURVEY_VALIDATOR)
        initialize_database(self.database)
        initialize_database(self.database)
        self.assertEqual(self.database.surveys.find_one({"_id": sid}), old)
        self.assertEqual(question_revision(old["questions"]), revision)
        import_version(self.database, sid, self.owner, self.v1)

    def test_new_database_constraints_reject_bad_sources_and_duplicate_versions(self):
        version = deepcopy(get_version(self.database, self.v1, self.owner)[1]);version["_id"] = ObjectId()
        with self.assertRaises(DuplicateKeyError):self.database.question_versions.insert_one(version)
        version["number"] = 2;version["content"]["jump_rules"] = [{"condition": {}, "target_question_id": "x"}]
        with self.assertRaises(OperationFailure):self.database.question_versions.insert_one(version)
        bad = deepcopy(self.question);bad["source"] = {"question_id": "bad", "version_id": ObjectId()}
        with self.assertRaises(ValueError):validate_question(bad)
        sid = create_survey(self.database, self.owner, "坏来源")
        with self.assertRaises(OperationFailure):self.database.surveys.update_one({"_id": sid}, {"$push": {"questions": bad}})
