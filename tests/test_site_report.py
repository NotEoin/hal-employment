"""'Something wrong here?': a report becomes a note the person sees before it is saved, the words
are kept when there is no AI, the survey never carries a private question, and the three ways out work.

The AI is the made-up one (`support.fake_ai`), and the form filling it would start is stood in for, so no
browser opens. The applicant, the employer and the site are made up.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer
from support.fake_ai import fake_ai

from jobhunt import db
from jobhunt.ai import LLMUnavailable
from jobhunt.core import runner
from jobhunt.sites import notes

WAIT_S = 10
JOB = "manual:civic-example"
FORM_URL = "https://apply.workable.com/civic-example/j/AB12CD/apply/"
WORDS = "The Location box only takes the city. It refused \"Leeds, United Kingdom\"."
PRIVATE_LABEL = "Do you consider yourself to have a disability?"
FORM = {"url": FORM_URL, "title": "Research Assistant",
        "fields": [{"hid": "1", "label": "First name", "kind": "text", "value": "Robin"},
                   {"hid": "2", "label": "Location", "kind": "text", "value": "Leeds, United Kingdom"},
                   {"hid": "3", "label": PRIVATE_LABEL, "kind": "radio", "value": "Prefer not to say"},
                   {"hid": "4", "label": "Password", "kind": "text", "type": "password", "value": "x"}],
        "controls": [{"name": "Save and continue"}, {"name": "Back"}]}
DRAFT = {"kind": "field_transform", "cond": {"label": "location"}, "act": {"transform": "city_only"},
         "text": "In \"Location\", type only the city (for example \"Leeds\")"}
NO_AI = {"site_note": LLMUnavailable("The made-up AI is switched off.")}


class Report(unittest.TestCase):
    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        self.con = self.enterContext(contextlib.closing(db.connect()))
        self.con.execute("INSERT INTO jobs (id, source, company, title, url, status, first_seen, last_seen) "
                         "VALUES (?, 'manual', 'Civic Example Trust', 'Research Assistant', ?, 'applying', "
                         "'2026-10-20', '2026-10-20')", (JOB, FORM_URL))
        self.con.execute("INSERT INTO applications (job_id, stage, form, updated) VALUES (?, 'form', ?, '2026-10-20')",
                         (JOB, json.dumps(FORM)))
        self.con.commit()

    def report(self, replies: dict, what: str = "This answer is wrong", words: str = WORDS) -> dict:
        with fake_ai(replies) as backend:
            r = self.c.post("/api/v2/sites/report", {"job_id": JOB, "site": "", "what": what, "words": words})
            self.assertEqual(r.status, 200, r.text)
            done = runner.wait(r.json()["action_id"], WAIT_S)
        self.assertEqual(done["state"], "done", done.get("error"))
        self.backend = backend
        return {**done["result"], "report_id": r.json()["report_id"]}

    def active(self) -> list[notes.Note]:
        return notes.for_page(FORM_URL, con=self.con).all("field_transform", label="Location")

    def test_a_report_becomes_a_note_shown_before_saving(self) -> None:
        out = self.report({"site_note": json.dumps(DRAFT)})
        self.assertEqual(out["draft"]["text"], DRAFT["text"])
        self.assertEqual([s["label"] for s in out["draft"]["scopes"]],
                         ["Just this site", "All Workable sites", "Everywhere"])
        self.assertEqual(out["draft"]["scopes"][0]["help"], "The safe choice.")
        self.assertEqual(self.active(), [], "nothing is used before the person saves it")

        r = self.c.post(f"/api/v2/sites/reports/{out['report_id']}/save", {"scope": "site", "text": ""})
        self.assertEqual(r.status, 200, r.text)
        note = self.active()[0]
        self.assertEqual((note.id, note.scope_kind, note.scope_key, note.act, note.origin),
                         (r.json()["note_id"], "site", "workable.com", {"transform": "city_only"}, "you"))
        row = self.con.execute("SELECT said, status FROM site_notes WHERE id = ?", (note.id,)).fetchone()
        self.assertEqual(tuple(row), (WORDS, "active"))
        why = self.con.execute("SELECT why FROM activity WHERE kind = 'sites.note_saved'").fetchone()["why"]
        self.assertEqual(why, f"You told Hal: {WORDS}")

    def test_the_person_can_pick_every_workable_site_and_change_the_wording(self) -> None:
        out = self.report({"site_note": json.dumps(DRAFT)})
        r = self.c.post(f"/api/v2/sites/reports/{out['report_id']}/save",
                        {"scope": "ats", "text": "Workable: city only in Location"})
        note = notes.for_page("https://apply.workable.com/moss-row/j/1/", con=self.con).first(
            "field_transform", label="Location")
        self.assertEqual((note.id, note.scope_kind, note.scope_key, note.text),
                         (r.json()["note_id"], "ats", "workable", "Workable: city only in Location"))

    def test_with_no_ai_the_words_are_saved_as_a_note(self) -> None:
        for replies in (NO_AI, {"site_note": "I am not sure what you mean."},
                        {"site_note": json.dumps({**DRAFT, "act": {"press_enter": True}})}):
            with self.subTest(replies=str(replies)[:40]):
                out = self.report(replies)
                self.assertTrue(out["saved_as_note"])
                stored = next(s for s in notes.stored(self.con) if s.note.id == out["note_id"])
                self.assertEqual((stored.note.kind, stored.note.text, stored.said, stored.status),
                                 ("note", " ".join(WORDS.split()), WORDS, "active"))
                self.assertEqual(notes.for_page(FORM_URL, con=self.con).first("note").kind, "note")
                state = self.con.execute("SELECT state, note_id FROM site_reports WHERE id = ?",
                                         (out["report_id"],)).fetchone()
                self.assertEqual(tuple(state), ("saved", out["note_id"]))
        self.assertEqual(self.active(), [], "words alone never change what Hal types")

    def test_the_report_survey_leaves_out_private_questions(self) -> None:
        self.report({"site_note": json.dumps(DRAFT)})
        sent = self.backend.requests("site_note")
        self.assertEqual(len(sent), 1)
        user = json.loads(sent[0].user)
        self.assertEqual(user["page"]["questions"], ["First name", "Location"])
        self.assertEqual(user["page"]["buttons"], ["Save and continue", "Back"])
        page = json.dumps(user["page"])
        for never in ("disability", "Prefer not to say", "Robin", "Password", "Leeds, United Kingdom"):
            self.assertNotIn(never, page)
        self.assertNotIn("disability", sent[0].user.lower())
        self.assertEqual(sent[0].priority, 0)

    def test_the_three_ways_out_after_a_report(self) -> None:
        out = self.report(NO_AI)
        rid = out["report_id"]
        filled = []
        with mock.patch("jobhunt.apply.driver.act_auto", lambda con, job_id, *a: filled.append((job_id, a)) or "ok"):
            r = self.c.post(f"/api/v2/sites/reports/{rid}/next", {"choice": "retry"})
            self.assertEqual(r.status, 200, r.text)
            done = runner.wait(r.json()["action_id"], WAIT_S)
        self.assertEqual((done["kind"], done["state"]), ("apply.fill", "done"), done.get("error"))
        self.assertEqual(filled, [(JOB, (False, True))], "a fresh fill of the same application")

        r = self.c.post(f"/api/v2/sites/reports/{rid}/next", {"choice": "myself"})
        self.assertEqual(r.json(), {"route": "docs"})
        self.assertEqual(self.con.execute("SELECT by_hand FROM applications WHERE job_id = ?", (JOB,)).fetchone()[0], 1)

        closed = []
        with mock.patch("jobhunt.apply.queries._close_quietly", closed.append):
            r = self.c.post(f"/api/v2/sites/reports/{rid}/next", {"choice": "skip"})
            runner.POOL.submit(lambda: None).result(WAIT_S)
        self.assertEqual(r.json(), {"route": f"app-{JOB}"})
        self.assertEqual(closed, [JOB])
        job = self.con.execute("SELECT status FROM jobs WHERE id = ?", (JOB,)).fetchone()
        self.assertEqual(job["status"], "applying", "the application stays in Applications")
        outcome = self.con.execute("SELECT outcome FROM site_reports WHERE id = ?", (rid,)).fetchone()[0]
        self.assertEqual(outcome, "skip")
        self.assertEqual(self.c.post(f"/api/v2/sites/reports/{rid}/next", {"choice": "send"}).status, 409)

    def test_a_report_needs_words_or_a_choice(self) -> None:
        r = self.c.post("/api/v2/sites/report", {"job_id": JOB, "what": "Something else", "words": " "})
        self.assertEqual(r.status, 400)
        r = self.c.post("/api/v2/sites/report",
                        {"job_id": "manual:nobody", "what": "This answer is wrong", "words": "x"})
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    unittest.main()
