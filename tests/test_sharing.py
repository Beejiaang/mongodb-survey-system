"""Survey-scoped question grants and real request/approval flows."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure

from stage2_support import StageTwoFixture
from question_library import (LibraryError, create_item, create_version, decide_share, fork_version,
    get_item, get_version, import_version, list_items, list_versions, request_share,
    save_survey_question, share_item, share_records)
from questions import build_question
from surveys import create_survey, publish_survey


class SharingTests(StageTwoFixture):
    def setUp(self):
        super().setUp()
        self.q = build_question("fill_blank", "私人数字题", input_type="number")
        self.iid = create_item(self.database, self.owner, self.q)
        self.v1 = get_item(self.database, self.iid, self.owner)["current_version_id"]
        self.sid = create_survey(self.database, self.other, "获准目标")
        self.second = create_survey(self.database, self.other, "未授权目标")

    def request(self):
        return request_share(self.database, self.iid, self.other, self.sid, message="教学使用")

    def grant(self):
        return share_item(self.database, self.iid, self.owner, " OTHER ", survey_id=self.sid, version_id=self.v1)

    def test_pending_request_denies_content_use_and_only_owner_can_approve(self):
        rid = self.request()
        self.assertEqual(self.database.question_shares.find_one({"_id":rid})["status"], "pending")
        with self.assertRaises(LibraryError):get_version(self.database, self.v1, self.other)
        with self.assertRaises(LibraryError):import_version(self.database, self.sid, self.other, self.v1)
        for uid in (self.other, self.third):
            with self.assertRaises(LibraryError):decide_share(self.database, rid, uid, "approve")
        decide_share(self.database, rid, self.owner, "approve")
        import_version(self.database, self.sid, self.other, self.v1)
        self.assertEqual(self.database.surveys.find_one({"_id":self.sid})["questions"][0]["source"]["version_id"], self.v1)

    def test_grant_does_not_cover_other_surveys_or_future_versions(self):
        self.grant()
        v2 = create_version(self.database, self.iid, self.owner, self.v1, dict(self.q,title="新私密题干"))
        with self.assertRaises(LibraryError):import_version(self.database, self.second, self.other, self.v1)
        with self.assertRaises(LibraryError):get_version(self.database, v2, self.other)
        self.assertEqual([v["_id"] for v in list_versions(self.database,self.iid,self.other)], [self.v1])
        self.assertFalse(list_items(self.database,self.other,survey_id=self.second))
        self.login(self.other)
        html = self.client.get(f"/library/items/{self.iid}").get_data(as_text=True)
        self.assertIn("私人数字题",html)
        self.assertNotIn("新私密题干",html)
        self.assertIn(str(self.sid),html)
        self.assertNotIn(f'<option value="{self.second}">',html)
        self.assertNotIn("私人数字题",self.client.get(f"/library/select/{self.second}").get_data(as_text=True))
        self.assertEqual(self.post(f"/library/select/{self.second}",version_id=str(self.v1)).status_code,404)

    def test_direct_grant_validates_recipient_survey_version_and_draft(self):
        with self.assertRaises(LibraryError):share_item(self.database,self.iid,self.owner,"third",survey_id=self.sid,version_id=self.v1)
        with self.assertRaises(LibraryError):share_item(self.database,self.iid,self.other,"third",survey_id=self.sid,version_id=self.v1)
        other = create_item(self.database,self.owner,self.q)
        with self.assertRaises(LibraryError):share_item(self.database,self.iid,self.owner,"other",survey_id=self.sid,
            version_id=get_item(self.database,other,self.owner)["current_version_id"])
        rid = self.grant()
        self.assertEqual(self.database.question_shares.find_one({"_id":rid})["origin"],"direct")
        import_version(self.database,self.sid,self.other,self.v1)
        publish_survey(self.database,self.sid,self.other)
        with self.assertRaises(LibraryError):self.request()
        with self.assertRaises(LibraryError):self.grant()

    def test_reject_reapply_and_stale_decision_preserve_history(self):
        first = self.request()
        decide_share(self.database,first,self.owner,"reject")
        with self.assertRaises(LibraryError):decide_share(self.database,first,self.owner,"approve")
        second = self.request()
        self.assertNotEqual(first,second)
        decide_share(self.database,second,self.owner,"approve")
        with self.assertRaises(LibraryError):decide_share(self.database,second,self.owner,"reject")
        self.assertEqual(self.database.question_shares.find_one({"_id":first})["status"],"rejected")
        self.assertEqual(self.database.question_shares.find_one({"_id":second})["decided_by"],self.owner)

    def test_revocation_blocks_new_imports_preserves_snapshot_and_other_grants(self):
        first = self.grant()
        second = share_item(self.database,self.iid,self.owner,"other",survey_id=self.second,version_id=self.v1)
        import_version(self.database,self.sid,self.other,self.v1)
        frozen = deepcopy(self.database.surveys.find_one({"_id":self.sid}))
        decide_share(self.database,first,self.owner,"revoke")
        with self.assertRaises(LibraryError):import_version(self.database,self.sid,self.other,self.v1)
        import_version(self.database,self.second,self.other,self.v1)
        self.assertEqual(self.database.surveys.find_one({"_id":self.sid}),frozen)
        self.assertEqual(self.database.question_shares.find_one({"_id":second})["status"],"approved")

    def test_shared_question_cannot_be_forked_saved_or_reshared(self):
        self.grant()
        qid = import_version(self.database,self.sid,self.other,self.v1)
        for operation in (lambda:fork_version(self.database,self.v1,self.other),
                lambda:save_survey_question(self.database,self.sid,self.other,qid),
                lambda:share_item(self.database,self.iid,self.other,"third",survey_id=self.sid,version_id=self.v1)):
            with self.assertRaises(LibraryError):operation()
        self.login(self.other)
        self.assertEqual(self.post(f"/library/from-survey/{self.sid}/{qid}").status_code,404)
        self.assertNotIn("保存为常用题目",self.client.get(f"/surveys/{self.sid}").get_data(as_text=True))
        self.assertEqual(self.database.question_items.count_documents({"owner_id":self.other}),0)

    def test_old_user_wide_grants_no_longer_authorize_any_import(self):
        self.database.question_items.update_one({"_id":self.iid},{"$set":{"shared_with":[self.other]}})
        with self.assertRaises(LibraryError):get_item(self.database,self.iid,self.other)
        with self.assertRaises(LibraryError):import_version(self.database,self.sid,self.other,self.v1)
        self.assertFalse(list_items(self.database,self.other))
        self.assertEqual(get_item(self.database,self.iid,self.owner)["shared_with"],[self.other])

    def test_concurrent_duplicate_requests_and_direct_grants_have_one_active_record(self):
        with ThreadPoolExecutor(4) as pool:
            ids = list(pool.map(lambda _:self.request(),range(6)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(self.grant(),ids[0])
        self.assertEqual(self.grant(),ids[0])
        self.assertEqual(self.database.question_shares.count_documents({"status":"approved"}),1)
        self.assertEqual(self.database.question_shares.count_documents({}),1)

    def test_request_validation_rejects_foreign_survey_self_missing_and_bad_inputs(self):
        for iid, uid, sid, number, message in ((self.iid,self.third,self.sid,1,""),
                (self.iid,self.owner,create_survey(self.database,self.owner,"本人"),1,""),
                (ObjectId(),self.other,self.sid,1,""),(self.iid,self.other,self.sid,0,""),
                (self.iid,self.other,self.sid,999,""),(self.iid,self.other,self.sid,1,"文"*501)):
            with self.assertRaises(LibraryError):request_share(self.database,iid,uid,sid,version_number=number,message=message)
        self.assertEqual(self.database.question_shares.count_documents({}),0)

    def test_web_request_approval_rejection_revocation_csrf_login_and_escaping(self):
        self.login(self.other)
        result = self.post('/library/sharing',item_id=str(self.iid),survey_id=str(self.sid),
                           version_number='1',message='<script>申请</script>')
        self.assertEqual(result.status_code,303)
        rid = self.database.question_shares.find_one({})["_id"]
        path = f'/library/sharing/{rid}/decision'
        self.assertEqual(self.client.get(path).status_code,405)
        self.assertEqual(self.client.post(path,data={"action":"approve"}).status_code,400)
        self.assertEqual(self.post(path,action='approve').status_code,404)
        self.login(self.owner)
        html = self.client.get('/library/sharing').get_data(as_text=True)
        self.assertIn('获准目标',html);self.assertIn('&lt;script&gt;申请',html)
        self.assertNotIn('<script>申请</script>',html)
        self.assertEqual(self.post(path,action='approve').status_code,303)
        self.assertEqual(self.post(path,action='approve').status_code,409)
        self.assertEqual(self.post(path,action='revoke').status_code,303)
        with self.client.session_transaction() as state:state.clear()
        self.assertEqual(self.client.get('/library/sharing').status_code,302)

    def test_participants_only_and_grants_never_open_answers(self):
        self.grant()
        self.assertEqual(len(share_records(self.database,self.owner)),1)
        self.assertEqual(len(share_records(self.database,self.other)),1)
        self.assertFalse(share_records(self.database,self.third))
        self.assertEqual(self.client.get(f'/surveys/{self.sid}/results').status_code,404)
        self.login(self.other)
        self.assertEqual(self.client.get(f'/library/versions/{self.v1}/results').status_code,200)
        self.assertEqual(self.post(f'/library/items/{self.iid}/share',username='third',action='share',
            survey_id=str(self.sid),version_id=str(self.v1)).status_code,404)

    def test_share_schema_unique_active_index_and_published_pending_approval(self):
        rid = self.request()
        row = self.database.question_shares.find_one({"_id":rid})
        duplicate = dict(row,_id=ObjectId())
        with self.assertRaises(DuplicateKeyError):self.database.question_shares.insert_one(duplicate)
        invalid = dict(duplicate,status="unknown")
        with self.assertRaises(OperationFailure):self.database.question_shares.insert_one(invalid)
        from surveys import add_question
        add_question(self.database,self.sid,self.other,self.q)
        publish_survey(self.database,self.sid,self.other)
        with self.assertRaises(LibraryError):decide_share(self.database,rid,self.owner,"approve")
        self.assertEqual(self.database.question_shares.find_one({"_id":rid})["status"],"pending")
        decide_share(self.database,rid,self.owner,"reject")
