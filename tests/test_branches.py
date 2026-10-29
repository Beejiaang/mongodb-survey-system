"""Selectable grant scopes and immutable recipient-owned modification branches."""
from copy import deepcopy
from bson import BSON, ObjectId

from stage2_support import StageTwoFixture
from database import initialize_database
from library_schema import PRE_SCOPE_SHARE_VALIDATOR
from question_library import (LibraryError, create_item, create_version, decide_share, fork_version,
    get_item, get_version, import_version, list_items, list_versions, request_share,
    replace_survey_version, save_survey_question, share_item, usage, preview_referenced_survey)
from library_results import summarize_version
from questions import build_question
from responses import start_response, update_progress, submit_response
from surveys import add_question, close_survey, create_survey, publish_survey, save_jump_rules
from jumps import question_revision


class BranchTests(StageTwoFixture):
    def setUp(self):
        super().setUp()
        self.q = build_question("fill_blank", "原始年龄", input_type="number")
        self.iid = create_item(self.database,self.owner,self.q)
        self.v1 = get_item(self.database,self.iid,self.owner)["current_version_id"]
        self.a = create_survey(self.database,self.other,"问卷A",allow_repeat=True)
        self.b = create_survey(self.database,self.other,"问卷B",allow_repeat=True)

    def grant(self):
        return share_item(self.database,self.iid,self.owner,"other",version_id=self.v1,scope="user")

    def branch(self):
        self.grant()
        iid = fork_version(self.database,self.v1,self.other,dict(self.q,title="修改后年龄"),note="修改题干")
        return iid,get_item(self.database,iid,self.other)["current_version_id"]

    def test_user_wide_grant_covers_owned_current_and_future_surveys_only(self):
        rid = self.grant()
        self.assertIsNone(self.database.question_shares.find_one({"_id":rid})["survey_id"])
        future = create_survey(self.database,self.other,"以后创建")
        for sid in (self.a,self.b,future):import_version(self.database,sid,self.other,self.v1)
        third = create_survey(self.database,self.third,"第三人")
        with self.assertRaises(LibraryError):import_version(self.database,third,self.other,self.v1)
        v2 = create_version(self.database,self.iid,self.owner,self.v1,self.q)
        with self.assertRaises(LibraryError):get_version(self.database,v2,self.other)

    def test_selected_surveys_are_prevalidated_and_do_not_enable_branching(self):
        other = create_survey(self.database,self.third,"不可冒充")
        with self.assertRaises(LibraryError):share_item(self.database,self.iid,self.owner,"other",
            version_id=self.v1,survey_ids=[self.a,other])
        self.assertEqual(self.database.question_shares.count_documents({}),0)
        ids = share_item(self.database,self.iid,self.owner,"other",version_id=self.v1,survey_ids=[self.a,self.b])
        self.assertEqual(len(ids),2)
        for sid in (self.a,self.b):import_version(self.database,sid,self.other,self.v1)
        future = create_survey(self.database,self.other,"未指定")
        with self.assertRaises(LibraryError):import_version(self.database,future,self.other,self.v1)
        with self.assertRaises(LibraryError):fork_version(self.database,self.v1,self.other)
        for scope,ids in [("bad",None),("surveys",[]),("surveys",[self.a,self.a])]:
            with self.assertRaises(LibraryError):share_item(self.database,self.iid,self.owner,"other",
                version_id=self.v1,scope=scope,survey_ids=ids)

    def test_user_scope_request_needs_author_approval_and_keeps_scope_on_reapply(self):
        rid = request_share(self.database,self.iid,self.other,scope="user")
        with self.assertRaises(LibraryError):get_version(self.database,self.v1,self.other)
        with self.assertRaises(LibraryError):decide_share(self.database,rid,self.other,"approve")
        decide_share(self.database,rid,self.owner,"reject")
        second = request_share(self.database,self.iid,self.other,scope="user")
        decide_share(self.database,second,self.owner,"approve")
        self.assertNotEqual(rid,second)
        import_version(self.database,self.b,self.other,self.v1)
        self.assertEqual(self.grant(),second)

    def test_branch_versions_record_user_source_and_never_change_originals(self):
        root_v2 = create_version(self.database,self.iid,self.owner,self.v1,dict(self.q,title="原作者v2"))
        share_item(self.database,self.iid,self.owner,"other",version_id=root_v2,scope="user")
        originals = list(self.database.question_versions.find({"question_id":self.iid}).sort("number",1))
        iid = fork_version(self.database,root_v2,self.other,dict(self.q,title="其他用户修改"))
        item = get_item(self.database,iid,self.other)
        self.assertIn("other分支",item["name"])
        self.assertEqual(item["current_version_number"],3)
        v3 = get_version(self.database,item["current_version_id"],self.other)[1]
        self.assertEqual((v3["parent_version_id"],v3["created_by"]),(root_v2,self.other))
        v4 = create_version(self.database,iid,self.other,v3["_id"],dict(self.q,title="继续修改"))
        v5 = create_version(self.database,iid,self.other,v3["_id"],restore=True)
        restored = get_version(self.database,v5,self.other)[1]
        self.assertEqual((restored["number"],restored["previous_version_id"],restored["restored_from_version_id"]),(5,v4,v3["_id"]))
        self.assertEqual(list(self.database.question_versions.find({"question_id":self.iid}).sort("number",1)),originals)

    def test_revoking_user_scope_freezes_branches_but_keeps_existing_answers(self):
        iid,vid = self.branch()
        import_version(self.database,self.a,self.other,vid)
        publish_survey(self.database,self.a,self.other)
        share_item(self.database,self.iid,self.owner,"other",version_id=self.v1,scope="user",revoke=True)
        for operation in (lambda:import_version(self.database,self.b,self.other,vid),
            lambda:create_version(self.database,iid,self.other,vid,self.q),
            lambda:create_version(self.database,iid,self.other,vid,restore=True)):
            with self.assertRaises(LibraryError):operation()
        self.assertNotIn(iid,[i["_id"] for i in list_items(self.database,self.other,survey_id=self.b)])
        self.assertEqual(get_version(self.database,vid,self.other)[1]["content"]["title"],"修改后年龄")
        secret="phase-two-identity-test"
        rid=start_response(self.database,self.a,self.third,secret)
        update_progress(self.database,self.a,rid,self.third,secret,0,"next",9)
        submit_response(self.database,self.a,rid,self.third,secret,1)
        self.assertEqual(summarize_version(self.database,vid,self.other)["average"],"9.00")
        self.assertEqual(summarize_version(self.database,self.v1,self.owner)["answered"],0)

    def test_branch_cannot_be_copied_reshared_or_saved_to_remove_restrictions(self):
        iid,vid = self.branch()
        qid=import_version(self.database,self.a,self.other,vid)
        snapshot=self.database.surveys.find_one({"_id":self.a})["questions"][0]
        for operation in (lambda:fork_version(self.database,vid,self.other),
            lambda:save_survey_question(self.database,self.a,self.other,qid),
            lambda:create_item(self.database,self.other,snapshot),
            lambda:share_item(self.database,iid,self.other,"third",version_id=vid,scope="user"),
            lambda:request_share(self.database,iid,self.third,scope="user")):
            with self.assertRaises(LibraryError):operation()

    def test_replacing_a_draft_preserves_identity_clears_jumps_and_locks_publication(self):
        iid,vid=self.branch()
        qid=import_version(self.database,self.a,self.other,self.v1)
        tail=build_question("fill_blank","末题",input_type="text")
        add_question(self.database,self.a,self.other,tail)
        old=self.database.surveys.find_one({"_id":self.a})
        save_jump_rules(self.database,self.a,self.other,qid,[{"condition":{"operator":"gt","value":0},
            "target_question_id":tail["id"]}],question_revision(old["questions"]))
        old=self.database.surveys.find_one({"_id":self.a});revision=question_revision(old["questions"])
        replace_survey_version(self.database,self.a,self.other,qid,vid,revision)
        new=self.database.surveys.find_one({"_id":self.a})
        self.assertEqual(new["questions"][0]["id"],qid)
        self.assertEqual(new["questions"][0]["source"],{"question_id":iid,"version_id":vid})
        self.assertTrue(all(not q["jump_rules"] for q in new["questions"]))
        with self.assertRaises(LibraryError):replace_survey_version(self.database,self.a,self.other,qid,vid,revision)
        publish_survey(self.database,self.a,self.other)
        frozen=deepcopy(self.database.surveys.find_one({"_id":self.a}))
        with self.assertRaises(LibraryError):replace_survey_version(self.database,self.a,self.other,qid,vid,question_revision(new["questions"]))
        self.assertEqual(self.database.surveys.find_one({"_id":self.a}),frozen)

    def test_web_branch_editor_labels_source_and_explicit_draft_replacement(self):
        self.grant();import_version(self.database,self.a,self.other,self.v1)
        self.login(self.other)
        path=f"/library/versions/{self.v1}/fork"
        self.assertIn("创建我的修改分支",self.client.get(path).get_data(as_text=True))
        result=self.post(path,kind="number",input_type="number",title="我的年龄题",max_value="99",change_note="用户修改")
        self.assertEqual(result.status_code,303)
        html=self.client.get(result.location).get_data(as_text=True)
        self.assertIn("other分支 v2",html)
        self.assertIn("owner v1",html)
        self.assertIn("问卷A",html)
        item=self.database.question_items.find_one({"owner_id":self.other})
        old=self.database.surveys.find_one({"_id":self.a})
        replace=f"/library/versions/{item['current_version_id']}/replace"
        self.assertEqual(self.post(replace,survey_id=str(self.a),question_id=old["questions"][0]["id"],
            revision=question_revision(old["questions"])).status_code,303)
        self.assertEqual(self.database.surveys.find_one({"_id":self.a})["questions"][0]["title"],"我的年龄题")
        self.assertEqual(self.client.get(replace).status_code,405)

    def test_web_scope_forms_support_user_and_multiple_surveys_and_protect_approval(self):
        self.login(self.other)
        self.assertEqual(self.post('/library/sharing',item_id=str(self.iid),version_number='1',scope='user').status_code,303)
        rid=self.database.question_shares.find_one({})["_id"]
        self.login(self.owner)
        html=self.client.get('/library/sharing').get_data(as_text=True)
        self.assertIn("当前及未来的全部问卷",html)
        self.assertEqual(self.post(f'/library/sharing/{rid}/decision',action='approve').status_code,303)
        self.assertEqual(self.post(f'/library/items/{self.iid}/share',username='other',version_id=str(self.v1),
            scope='surveys',survey_ids=f'{self.a},\n{self.b}',action='share').status_code,303)
        self.assertEqual(self.database.question_shares.count_documents({"status":"approved"}),3)
        self.login(self.third)
        self.assertEqual(self.post(f'/library/sharing/{rid}/decision',action='approve').status_code,404)

    def test_known_scope_validator_upgrade_preserves_old_grants_and_survey_data(self):
        share_item(self.database,self.iid,self.owner,"other",version_id=self.v1,survey_id=self.a)
        grant=self.database.question_shares.find_one({})
        original=BSON.encode(self.database.surveys.find_one({"_id":self.a}))
        self.database.command('collMod','question_shares',validator=PRE_SCOPE_SHARE_VALIDATOR)
        initialize_database(self.database);initialize_database(self.database)
        self.assertEqual(self.database.question_shares.find_one({"_id":grant['_id']}),grant)
        self.assertEqual(BSON.encode(self.database.surveys.find_one({"_id":self.a})),original)
        self.grant()
        self.assertEqual(self.database.question_shares.count_documents({"survey_id":None}),1)

    def test_original_author_sees_open_branch_references_without_branch_answers(self):
        iid,vid=self.branch()
        import_version(self.database,self.a,self.other,vid)
        publish_survey(self.database,self.a,self.other)
        self.assertEqual(usage(self.database,self.iid,self.owner,self.v1)["other_count"],1)
        self.assertEqual(preview_referenced_survey(self.database,self.iid,self.owner,self.a)["_id"],self.a)
        with self.assertRaises(LibraryError):get_version(self.database,vid,self.owner)
        self.assertEqual(self.client.get(f'/surveys/{self.a}/results').status_code,404)
        close_survey(self.database,self.a,self.other)
        with self.assertRaises(LibraryError):preview_referenced_survey(self.database,self.iid,self.owner,self.a)

    def test_partial_and_user_grants_revoke_independently_and_restore_branch_editing(self):
        iid,vid=self.branch()
        partial=share_item(self.database,self.iid,self.owner,"other",version_id=self.v1,survey_id=self.a)
        share_item(self.database,self.iid,self.owner,"other",version_id=self.v1,scope="user",revoke=True)
        import_version(self.database,self.a,self.other,self.v1)
        with self.assertRaises(LibraryError):import_version(self.database,self.b,self.other,self.v1)
        with self.assertRaises(LibraryError):create_version(self.database,iid,self.other,vid,self.q)
        self.grant()
        new=create_version(self.database,iid,self.other,vid,self.q)
        self.assertEqual(get_version(self.database,new,self.other)[1]["number"],3)
        self.assertEqual(self.database.question_shares.find_one({"_id":partial})["status"],"approved")
