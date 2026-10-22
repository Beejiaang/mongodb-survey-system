from copy import deepcopy

from stage2_support import StageTwoFixture
from library_results import summarize_version
from question_library import create_item, create_version, get_item, import_version, share_item
from questions import build_question
from responses import start_response, update_progress, submit_response
from surveys import add_question, create_survey, publish_survey, save_jump_rules
from jumps import question_revision


class LibraryResultTests(StageTwoFixture):
    secret = "phase-two-identity-test"

    def item(self, question):
        iid = create_item(self.database, self.owner, question)
        return iid, get_item(self.database, iid, self.owner)["current_version_id"]

    def survey(self, version, owner=None, copies=1):
        owner = self.owner if owner is None else owner
        sid = create_survey(self.database, owner, "统计问卷", allow_repeat=True, is_anonymous=True)
        if owner != self.owner:
            version_row = self.database.question_versions.find_one({"_id":version})
            share_item(self.database,version_row["question_id"],self.owner,"other",survey_id=sid,version_id=version)
        for _ in range(copies):import_version(self.database, sid, owner, version)
        publish_survey(self.database, sid, owner)
        return sid

    def submit(self, sid, values):
        rid = start_response(self.database, sid, self.third, self.secret)
        for value in values:
            record = self.database.responses.find_one({"_id": rid})
            update_progress(self.database, sid, rid, self.third, self.secret, record["version"], "next", value)
        record = self.database.responses.find_one({"_id": rid})
        submit_response(self.database, sid, rid, self.third, self.secret, record["version"])
        return rid

    def test_numeric_cross_survey_totals_exclude_other_versions_owners_and_drafts(self):
        q = build_question("fill_blank", "年龄", input_type="number")
        iid, v1 = self.item(q)
        a, b = self.survey(v1), self.survey(v1)
        self.submit(a, [0]);self.submit(a, [10]);self.submit(b, [20])
        v2 = create_version(self.database, iid, self.owner, v1, dict(q, title="新版年龄"))
        self.submit(self.survey(v2), [90])
        self.submit(self.survey(v1, self.other), [1000])
        rid = start_response(self.database, a, self.third, self.secret)
        update_progress(self.database, a, rid, self.third, self.secret, 0, "next", 100)
        result = summarize_version(self.database, v1, self.owner)
        self.assertEqual((result["survey_count"],result["submission_count"],result["answered"]), (2,3,3))
        self.assertEqual(result["average"], "10.00")
        self.assertEqual(sum(x["count"] for x in result["chart"]["series"]), 3)
        self.assertEqual(summarize_version(self.database, v2, self.owner)["average"], "90.00")

    def test_choice_counts_and_html_chart_payload_follow_the_pinned_options(self):
        options = [{"id":"a","text":"<script>甲</script>"},{"id":"b","text":"乙"}]
        for kind, values, expected in [("single_choice",["a","b","a"],[2,1]),
                                       ("multiple_choice",[["a","b"],["b"],["b"]],[1,3])]:
            with self.subTest(kind=kind):
                _, vid = self.item(build_question(kind, "选择", options=options))
                sid = self.survey(vid)
                for value in values:self.submit(sid,[value])
                result = summarize_version(self.database, vid, self.owner)
                self.assertEqual([x["count"] for x in result["options"]], expected)
                self.assertEqual(result["answered"],3)
                html = self.client.get(f"/library/versions/{vid}/results").get_data(as_text=True)
                self.assertIn("有效回答 3 次",html)
                self.assertNotIn("<script>甲</script>",html)
                self.assertIn("&lt;script&gt;甲",html)

    def test_text_pagination_preserves_all_values_without_charts_or_identities(self):
        _, vid = self.item(build_question("fill_blank","文本",input_type="text"))
        sid = self.survey(vid)
        for i in range(25):self.submit(sid,[f"内容{i:02}"])
        self.submit(sid,["<script>alert(1)</script>"])
        first = summarize_version(self.database,vid,self.owner)
        second = summarize_version(self.database,vid,self.owner,page=2)
        self.assertEqual((first["answered"],len(first["answers"]),len(second["answers"])),(26,20,6))
        self.assertTrue(first["has_next"]);self.assertFalse(second["has_next"])
        self.assertEqual(first["answers"][0]["value"],"<script>alert(1)</script>")
        html = self.client.get(f"/library/versions/{vid}/results").get_data(as_text=True)
        self.assertNotIn('class="chart-kind"',html)
        self.assertNotIn('result-charts.js',html)
        self.assertNotIn('respondent_key',html)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;alert(1)',html)

    def test_skipped_and_optional_blank_are_separate_and_do_not_enter_average(self):
        _, vid = self.item(build_question("fill_blank","选答数字",input_type="number"))
        sid = create_survey(self.database,self.owner,"跳转",allow_repeat=True)
        control = build_question("fill_blank","控制",input_type="number",required=True)
        tail = build_question("fill_blank","末题",input_type="text")
        add_question(self.database,sid,self.owner,control)
        import_version(self.database,sid,self.owner,vid)
        add_question(self.database,sid,self.owner,tail)
        questions=self.database.surveys.find_one({"_id":sid})["questions"]
        save_jump_rules(self.database,sid,self.owner,control["id"],
                         [{"condition":{"operator":"gt","value":0},"target_question_id":tail["id"]}],
                         question_revision(questions))
        publish_survey(self.database,sid,self.owner)
        self.submit(sid,[1,"结束"]);self.submit(sid,[0,None,"结束"])
        summary=summarize_version(self.database,vid,self.owner)
        self.assertEqual((summary["answered"],summary["blank"],summary["skipped"]),(0,1,1))
        self.assertIsNone(summary["average"])

    def test_repeated_instances_count_answers_but_not_duplicate_submission_totals(self):
        _,vid=self.item(build_question("fill_blank","数字",input_type="number"))
        sid=self.survey(vid,copies=2);self.submit(sid,[10,20])
        result=summarize_version(self.database,vid,self.owner)
        self.assertEqual((result["survey_count"],result["submission_count"],result["answered"]),(1,1,2))
        self.assertEqual(result["average"],"15.00")

    def test_revocation_preserves_own_statistics_and_never_opens_other_answers(self):
        iid,vid=self.item(build_question("fill_blank","数字",input_type="number"))
        sid=self.survey(vid,self.other);self.submit(sid,[7])
        self.assertEqual(summarize_version(self.database,vid,self.owner)["answered"],0)
        share_item(self.database,iid,self.owner,"other",survey_id=sid,version_id=vid,revoke=True)
        self.login(self.other)
        response=self.client.get(f"/library/versions/{vid}/results")
        self.assertEqual(response.status_code,200)
        self.assertIn("7.00",response.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/library/items/{iid}").status_code,404)
        self.assertEqual(self.client.get(f"/surveys/{sid}/results").status_code,200)

    def test_invalid_pages_versions_and_private_versions_are_rejected(self):
        _,vid=self.item(build_question("fill_blank","数字",input_type="number"))
        for page in ("0","-1","bad","100001"):
            self.assertEqual(self.client.get(f"/library/versions/{vid}/results?page={page}").status_code,400)
        self.assertEqual(self.client.get("/library/versions/bad/results").status_code,404)
        self.login(self.other)
        self.assertEqual(self.client.get(f"/library/versions/{vid}/results").status_code,404)

    def test_saved_legacy_question_includes_original_answers_without_changing_revision(self):
        from question_library import save_survey_question, usage
        q = build_question("fill_blank", "第一阶段数字", input_type="number")
        sid = create_survey(self.database,self.owner,"原问卷",allow_repeat=True)
        add_question(self.database,sid,self.owner,q)
        publish_survey(self.database,sid,self.owner)
        self.submit(sid,[12])
        frozen = deepcopy(self.database.surveys.find_one({"_id":sid}))
        iid = save_survey_question(self.database,sid,self.owner,q["id"])
        vid = get_item(self.database,iid,self.owner)["current_version_id"]
        self.assertEqual(summarize_version(self.database,vid,self.owner)["average"],"12.00")
        self.assertEqual([s["_id"] for s in usage(self.database,iid,self.owner)["surveys"]],[sid])
        self.assertEqual(self.database.surveys.find_one({"_id":sid}),frozen)
