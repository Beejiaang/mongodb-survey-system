from bson import ObjectId

from stage2_support import StageTwoFixture
from question_library import (bank_revision, create_bank, create_item, create_version, get_bank,
                              get_item, import_version, share_item)
from questions import build_question
from surveys import add_question, create_survey, publish_survey


class LibraryWebTests(StageTwoFixture):
    def setUp(self):
        super().setUp()
        self.q = build_question("fill_blank", "年龄", input_type="number",
                                 validation={"min_value": 0, "max_value": 120, "integer_only": True})
        self.iid = create_item(self.database, self.owner, self.q)
        self.v1 = get_item(self.database, self.iid, self.owner)["current_version_id"]
        self.sid = create_survey(self.database, self.owner, "网页问卷")

    def test_all_question_forms_save_and_reuse_existing_validation(self):
        forms = [dict(kind="single_choice", title="单选", options_text="甲\n乙"),
                 dict(kind="multiple_choice", title="多选", options_text="甲\n乙\n丙", min_choices="2", max_choices="3"),
                 dict(kind="text", title="建议", input_type="text", min_length="2", max_length="20"),
                 dict(kind="number", title="数字", input_type="number", min_value="0", max_value="120", integer_only="on")]
        for data in forms:
            with self.subTest(kind=data["kind"]):
                response = self.post("/library/new", **data)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(self.client.get(response.location).status_code, 200)
        before = self.database.question_versions.count_documents({})
        response = self.post("/library/new", kind="text", input_type="text", title="坏题", min_length="5", max_length="1")
        self.assertEqual(response.status_code, 400)
        self.assertIn("最小限制不能大于最大限制", response.get_data(as_text=True))
        self.assertEqual(self.database.question_versions.count_documents({}), before)

    def test_library_editor_option_add_and_numeric_hint_do_not_save(self):
        html = self.client.get("/library/new").get_data(as_text=True)
        self.assertEqual(html.count('name="option_text"'), 2)
        count = self.database.question_versions.count_documents({})
        response = self.post("/library/new", kind="single_choice", title="未保存", options_text="甲\n乙", action="add_option")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True).count('name="option_text"'), 3)
        html = self.post("/library/new", kind="text", title="年龄", action="number_hint").get_data(as_text=True)
        self.assertIn('name="title" value="年龄（请使用阿拉伯数字回答）"', html)
        self.assertEqual(self.database.question_versions.count_documents({}), count)

    def test_version_editor_compare_restore_and_history_are_visible(self):
        path = f"/library/items/{self.iid}/versions/new?base={self.v1}"
        html = self.client.get(path).get_data(as_text=True)
        self.assertIn('name="max_value" value="120"', html)
        result = self.post(path, kind="number", input_type="number", title="年龄", max_value="100", min_value="0", integer_only="on", change_note="收紧范围")
        self.assertEqual(result.status_code, 303)
        html = self.client.get(result.location).get_data(as_text=True)
        for value in ("v1 → v2", "最大值", "120", "100", "收紧范围"):
            self.assertIn(value, html)
        restored = self.post(f"/library/versions/{self.v1}/restore")
        self.assertEqual(restored.status_code, 303)
        html = self.client.get(restored.location).get_data(as_text=True)
        self.assertIn("恢复自v1", html)
        self.assertIn("原默认版本：v2", html)
        self.assertEqual(get_item(self.database, self.iid, self.owner)["current_version_number"], 3)

    def test_picker_uses_selected_old_version_even_after_new_default(self):
        changed = dict(self.q, title="新年龄")
        create_version(self.database, self.iid, self.owner, self.v1, changed)
        path = f"/library/select/{self.sid}"
        html = self.client.get(path).get_data(as_text=True)
        self.assertIn("查看各版本内容与差异", html)
        self.assertIn(str(self.v1), html)
        self.assertEqual(self.post(path, version_id=str(self.v1)).status_code, 303)
        survey = self.database.surveys.find_one({"_id": self.sid})
        self.assertEqual(survey["questions"][0]["title"], "年龄")
        self.assertEqual(survey["questions"][0]["source"]["version_id"], self.v1)
        publish_survey(self.database, self.sid, self.owner)
        self.assertEqual(self.client.get(path).status_code, 409)

    def test_from_survey_saves_question_without_rewriting_published_survey(self):
        add_question(self.database, self.sid, self.owner, self.q)
        publish_survey(self.database, self.sid, self.owner)
        original = self.database.surveys.find_one({"_id": self.sid})
        result = self.post(f"/library/from-survey/{self.sid}/{self.q['id']}")
        self.assertEqual(result.status_code, 303)
        self.assertEqual(self.database.surveys.find_one({"_id": self.sid}), original)

    def test_share_recipient_can_use_target_survey_but_not_fork_or_modify_original(self):
        path = f"/library/items/{self.iid}"
        sid = create_survey(self.database, self.other, "授权问卷")
        self.assertEqual(self.post(path+"/share", action="share", username="other", survey_id=str(sid), version_id=str(self.v1)).status_code, 303)
        self.login(self.other)
        html = self.client.get(path).get_data(as_text=True)
        self.assertNotIn("派生为自己的题目", html)
        self.assertNotIn("共享使用权</button>", html)
        self.assertEqual(self.client.get(path+"/versions/new").status_code, 404)
        self.assertEqual(self.post(f"/library/versions/{self.v1}/restore").status_code, 404)
        self.assertEqual(self.post(f"/library/versions/{self.v1}/fork").status_code, 403)
        self.assertEqual(self.client.get(f"/library/items/{ObjectId()}").status_code, 404)

    def test_usage_shows_reference_metadata_without_management_links_or_answers(self):
        other_sid = create_survey(self.database, self.other, "引用问卷标题")
        share_item(self.database, self.iid, self.owner, "other", survey_id=other_sid, version_id=self.v1)
        import_version(self.database, other_sid, self.other, self.v1)
        html = self.client.get(f"/library/items/{self.iid}/usage").get_data(as_text=True)
        self.assertIn("1份", html)
        self.assertIn("引用问卷标题", html)
        self.assertIn(str(other_sid), html)
        self.assertNotIn(f'href="/surveys/{other_sid}', html)
        self.assertEqual(self.client.get(f"/surveys/{other_sid}/results").status_code,404)

    def test_banks_and_picker_pin_versions_and_reject_stale_or_foreign_bank(self):
        response = self.post("/library/banks", name="年龄题库", description="常用")
        self.assertEqual(response.status_code, 303)
        bid = ObjectId(response.location.rsplit("/",1)[1])
        bank = get_bank(self.database, bid, self.owner)
        self.assertEqual(self.post(response.location, action="put", version_id=str(self.v1), revision=bank_revision(bank)).status_code, 303)
        self.assertEqual(self.post(response.location, action="remove", version_id=str(self.v1), revision=bank_revision(bank)).status_code, 409)
        self.assertIn(str(self.v1), self.client.get(f"/library/select/{self.sid}?bank={bid}").get_data(as_text=True))
        self.login(self.other)
        self.assertEqual(self.client.get(response.location).status_code, 404)
        self.assertEqual(self.client.get(f"/library/select/{self.sid}").status_code, 404)

    def test_revoked_bank_entry_is_unavailable_without_disclosing_content(self):
        sid = create_survey(self.database, self.other, "授权问卷")
        share_item(self.database, self.iid, self.owner, "other", survey_id=sid, version_id=self.v1)
        self.login(self.other)
        bid = create_bank(self.database, self.other, "接收题库")
        self.post(f"/library/banks/{bid}", action="put", version_id=str(self.v1), revision=bank_revision(get_bank(self.database, bid, self.other)))
        share_item(self.database, self.iid, self.owner, "other", survey_id=sid, version_id=self.v1, revoke=True)
        html = self.client.get(f"/library/banks/{bid}").get_data(as_text=True)
        self.assertIn("该条目当前未获授权", html)
        self.assertNotIn("<h2>年龄 ·", html)

    def test_auth_csrf_methods_escaping_and_foreign_version_comparison(self):
        self.assertEqual(self.client.post("/library/new", data={"kind":"text","title":"x"}).status_code, 400)
        self.assertEqual(self.client.get(f"/library/versions/{self.v1}/restore").status_code, 405)
        other = create_item(self.database, self.owner, build_question("fill_blank", "<script>alert(1)</script>", input_type="text"))
        other_version = get_item(self.database, other, self.owner)["current_version_id"]
        self.assertEqual(self.client.get(f"/library/items/{self.iid}?compare={other_version}").status_code, 400)
        html = self.client.get(f"/library/items/{other}").get_data(as_text=True)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        with self.client.session_transaction() as state:state.clear()
        self.assertEqual(self.client.get("/library").status_code, 302)

    def test_rename_and_delete_bank_preserve_question_versions_and_surveys(self):
        bid = create_bank(self.database,self.owner,"旧题库")
        path=f"/library/banks/{bid}"
        old=get_bank(self.database,bid,self.owner)
        self.assertEqual(self.post(path,action="rename",name="新题库",description="新说明",revision=bank_revision(old)).status_code,303)
        self.assertEqual(self.post(path,action="delete",revision=bank_revision(old)).status_code,409)
        current=get_bank(self.database,bid,self.owner)
        self.assertEqual(current["name"],"新题库")
        self.assertEqual(self.post(path,action="delete",revision=bank_revision(current)).status_code,303)
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertIsNotNone(get_item(self.database,self.iid,self.owner))
        self.assertIsNotNone(self.database.surveys.find_one({"_id":self.sid}))
