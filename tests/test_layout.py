from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from stage2_support import StageTwoFixture
from jumps import question_revision
from questions import build_question
from surveys import add_question, change_question_layout, create_survey, publish_survey, save_jump_rules


class LayoutTests(StageTwoFixture):
    def setUp(self):
        super().setUp()
        self.sid = create_survey(self.database, self.owner, "布局")
        self.questions = [build_question("fill_blank", str(i), input_type="number") for i in range(3)]
        for q in self.questions:
            add_question(self.database, self.sid, self.owner, q)
        rules = [{"condition": {"operator": "gt", "value": 0},
                  "target_question_id": self.questions[2]["id"]}]
        save_jump_rules(self.database, self.sid, self.owner, self.questions[0]["id"], rules,
                        question_revision(self.questions))

    def record(self):
        return self.database.surveys.find_one({"_id": self.sid})

    def route(self, question=0):
        return f"/surveys/{self.sid}/questions/{self.questions[question]['id']}/layout"

    def test_move_preserves_ids_and_content_clears_all_jumps(self):
        old = self.record()
        response = self.post(self.route(), action="down", revision=question_revision(old["questions"]))
        self.assertEqual(response.status_code, 303)
        new = self.record()["questions"]
        self.assertEqual([q["id"] for q in new], [self.questions[i]["id"] for i in (1, 0, 2)])
        self.assertTrue(all(not q["jump_rules"] for q in new))
        self.assertEqual(new[1]["validation"], old["questions"][0]["validation"])
        self.assertIn("全部跳转规则已清空", self.client.get(response.location).get_data(as_text=True))

    def test_delete_clears_rules_including_rules_targeting_deleted_question(self):
        old = self.record()
        self.assertEqual(self.post(self.route(2), action="delete", revision=question_revision(old["questions"])).status_code, 303)
        self.assertEqual(len(self.record()["questions"]), 2)
        self.assertTrue(all(not q["jump_rules"] for q in self.record()["questions"]))
        for q in list(self.record()["questions"]):
            self.assertTrue(change_question_layout(self.database, self.sid, self.owner, q["id"], "delete", question_revision(self.record()["questions"])))
        self.assertEqual(self.record()["questions"], [])

    def test_invalid_actions_boundaries_stale_revision_and_csrf_do_not_write(self):
        old = self.record()
        rev = question_revision(old["questions"])
        for action, index in (("up", 0), ("down", 2), ("bad", 1)):
            self.assertEqual(self.post(self.route(index), action=action, revision=rev).status_code, 400)
        self.assertEqual(self.post(self.route(), action="down", revision="old").status_code, 409)
        self.assertEqual(self.client.post(self.route(), data={"action": "delete", "revision": rev}).status_code, 400)
        self.assertEqual(self.record(), old)

    def test_published_and_other_owner_cannot_change_layout(self):
        old = self.record()
        self.login(self.other)
        self.assertEqual(self.post(self.route(), action="delete", revision=question_revision(old["questions"])).status_code, 404)
        self.login(self.owner)
        publish_survey(self.database, self.sid, self.owner)
        frozen = deepcopy(self.record())
        self.assertEqual(self.post(self.route(), action="down", revision=question_revision(old["questions"])).status_code, 409)
        self.assertEqual(self.record(), frozen)

    def test_concurrent_moves_from_same_revision_only_one_wins(self):
        rev = question_revision(self.record()["questions"])
        with ThreadPoolExecutor(2) as pool:
            answers = list(pool.map(lambda _: change_question_layout(self.database, self.sid, self.owner, self.questions[0]["id"], "down", rev), range(2)))
        self.assertEqual(sorted(answers), [False, True])
