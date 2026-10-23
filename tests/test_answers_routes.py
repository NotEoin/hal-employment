"""What Hal remembers, Saved answers, through the real server: listing, where each may be used, changes.

Each test runs a server in its own temporary home with made-up saved answers and one made-up job.
"""
import unittest
from contextlib import closing

from support.api import HalServer

from jobhunt import autofill, db

PATH = "/api/v2/answers"
JOB = {"id": "test:7", "source": "greenhouse", "company": "Made-up Heritage Trust", "title": "Archive Assistant",
       "location": "Leeds", "url": "https://heritage-madeup.example/jobs/7", "description": "", "salary": "",
       "posted_at": None, "board_size": None}
CANARY = "SYNTH-PRIVATE-4410 a made-up answer"


class SavedAnswers(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        with closing(db.connect()) as con:
            db.upsert(con, JOB)
            rows = [("Do you hold a full driving licence?", True, "everywhere", ""),
                    ("What is your notice period?", "Two weeks", "job", JOB["id"]),
                    ("Are you willing to travel?", "Yes, within the made-up county", "review", ""),
                    ("Do you consider yourself to have a disability?", CANARY, "everywhere", ""),
                    ("Which made-up tools do you use?", ["Quill", "Ledger"], "everywhere", "")]
            for label, answer, scope, key in rows:
                con.execute("""INSERT INTO answers2 (question, label, answer, scope, scope_key, updated)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (autofill._norm(label), label, db.json_text(answer), scope, key, db.now()))
            con.commit()

    def items(self) -> dict:
        reply = self.c.get(PATH)
        self.assertEqual(reply.status, 200)
        return {i["q"]: i for i in reply.json()}

    def put(self, item: dict, body: dict):
        return self.c.request("PUT", f"{PATH}/{item['id']}", body)

    def test_each_answer_says_where_hal_may_use_it(self):
        items = self.items()
        self.assertEqual(items["Do you hold a full driving licence?"]["scope_label"], "Everywhere")
        self.assertEqual(items["Do you hold a full driving licence?"]["a"], "Yes")
        notice = items["What is your notice period?"]
        self.assertEqual(notice["scope_label"], "Only for Archive Assistant at Made-up Heritage Trust")
        self.assertEqual([c["label"] for c in notice["choices"]],
                         ["Everywhere", "Only on heritage-madeup.example",
                          "Only for Archive Assistant at Made-up Heritage Trust", "Never reused"])
        self.assertEqual(items["Which made-up tools do you use?"]["a"], "Quill, Ledger")

    def test_answers_waiting_for_a_choice_come_first_and_say_so(self):
        first = self.c.get(PATH).json()[0]
        self.assertEqual((first["q"], first["needs_choice"], first["scope_label"]),
                         ("Are you willing to travel?", True, "Choose where to use it"))

    def test_the_tab_never_lists_a_private_answer(self):
        reply = self.c.get(PATH)
        self.assertNotIn(CANARY, reply.text)
        self.assertNotIn("disability", reply.text.lower())
        with closing(db.connect()) as con:
            private_id = con.execute("SELECT id FROM answers2 WHERE label LIKE '%disability%'").fetchone()[0]
        self.assertEqual(self.c.request("PUT", f"{PATH}/{private_id}", {"a": "No"}).status, 404)
        self.assertEqual(self.c.request("DELETE", f"{PATH}/{private_id}").status, 404)

    def test_choosing_where_to_use_an_answer_puts_it_to_use(self):
        travel = self.items()["Are you willing to travel?"]
        job = {"id": JOB["id"], "company": JOB["company"], "url": JOB["url"]}
        field = {"hid": "t", "kind": "text", "label": "Are you willing to travel?"}
        with closing(db.connect()) as con:
            self.assertIsNone(autofill._recall(con, dict(field), job))
            reply = self.put(travel, {"scope": "site", "scope_key": "Heritage-Madeup.example"})
            self.assertEqual(reply.status, 200)
            self.assertEqual(reply.json()["scope_label"], "Only on heritage-madeup.example")
            self.assertEqual(autofill._recall(con, dict(field), job), "Yes, within the made-up county")
            elsewhere = {**job, "id": "test:8", "url": "https://other-madeup.example/jobs/8"}
            self.assertIsNone(autofill._recall(con, dict(field), elsewhere))

    def test_an_answer_keeps_its_shape_when_its_words_change(self):
        items = self.items()
        self.assertEqual(self.put(items["Do you hold a full driving licence?"], {"a": "No"}).json()["a"], "No")
        self.assertEqual(self.put(items["Do you hold a full driving licence?"], {"a": "Maybe"}).status, 400)
        self.put(items["Which made-up tools do you use?"], {"a": "Quill, Abacus"})
        with closing(db.connect()) as con:
            stored = dict(con.execute("SELECT label, answer FROM answers2").fetchall())
        self.assertEqual(db.json_value(stored["Do you hold a full driving licence?"]), False)
        self.assertEqual(db.json_value(stored["Which made-up tools do you use?"]), ["Quill", "Abacus"])
        self.assertEqual(self.put(items["Which made-up tools do you use?"], {"a": "  "}).status, 400)

    def test_answers_hal_works_out_each_time_are_shown_but_cannot_be_changed(self):
        worked = {i["id"]: i for i in self.c.get(PATH).json() if i["auto"]}
        self.assertEqual(sorted(worked), ["auto:how_heard", "auto:salary", "auto:why_us"])
        self.assertEqual(worked["auto:why_us"]["scope_label"], "Never reused")
        self.assertEqual(self.c.request("PUT", f"{PATH}/auto:salary", {"a": "Made-up 1"}).status, 400)

    def test_moving_an_answer_onto_one_already_there_is_refused(self):
        with closing(db.connect()) as con:
            label = "Do you hold a full driving licence?"
            con.execute("""INSERT INTO answers2 (question, label, answer, scope, scope_key, updated)
                           VALUES (?, ?, 'false', 'never', '', ?)""", (autofill._norm(label), label, db.now()))
            con.commit()
        licence = self.items()["Do you hold a full driving licence?"]
        reply = self.put(licence, {"scope": "never" if licence["scope"] == "everywhere" else "everywhere"})
        self.assertEqual(reply.status, 409)
        self.assertIn("already have an answer", reply.json()["error"]["message"])

    def test_a_deleted_answer_is_gone(self):
        licence = self.items()["Do you hold a full driving licence?"]
        self.assertEqual(self.c.request("DELETE", f"{PATH}/{licence['id']}").json(), {"ok": True})
        self.assertNotIn("Do you hold a full driving licence?", self.items())
        self.assertEqual(self.c.request("DELETE", f"{PATH}/{licence['id']}").status, 404)


if __name__ == "__main__":
    unittest.main()
