"""The Checks tab: the scanner score, the checks of each CV type, fixes, a recruiter's read and one job.

Every CV here belongs to a made-up person from `support.people`; the AI is the fake one.
"""
from __future__ import annotations

import json
import unittest

from support.api import HalServer
from support.fake_ai import fake_ai
from support.people import HUMANITIES, as_person
from support.world import World
from test_cv_types import humanities_cv, profile_of

from jobhunt.core import runner
from jobhunt.cvstudio import checks, checks_actions, model, store
from jobhunt.cvstudio import types as cvtypes

STAR = chr(0x2605)      # a symbol icon fonts and some CV makers put in skills lines
ARROW = chr(0x2192)
B = model.Bullet


class ScannerScore(unittest.TestCase):
    def test_the_scanner_weights_add_up_to_100(self):
        self.assertEqual(sum(p.points for p in checks.SCANNER), 100)
        self.assertEqual([(p.name, p.points) for p in checks.SCANNER],
                         [("Text can be read", 30), ("Standard headings", 20), ("Dates on every entry", 15),
                          ("Contact details found", 15), ("One column", 10), ("Symbols scanners keep", 10)])
        result = checks.scanner(humanities_cv(), profile_of(HUMANITIES))
        self.assertEqual(sum(p["max"] for p in result["parts"]), 100)
        self.assertEqual(result["score"], 100)

    def test_the_scanner_score_is_the_same_every_time_for_the_same_cv(self):
        cv = humanities_cv()
        cv.sections[4].entries[0].items.append(f"{STAR} Cataloguing")
        cv.sections[3].entries[0].start = cv.sections[3].entries[0].end = ""
        first = checks.scanner(cv, profile_of(HUMANITIES))
        again = [checks.scanner(model.from_json(model.to_json(cv)), profile_of(HUMANITIES)) for _ in range(3)]
        self.assertTrue(all(a == first for a in again))
        self.assertLess(first["score"], 100)
        self.assertEqual({p["id"]: p["got"] for p in first["parts"]}["dates"], 10)

    def test_two_columns_and_odd_headings_lose_their_points(self):
        cv = humanities_cv()
        cv.sections[2].title = "Giving back"
        two = checks.scanner(cv, profile_of(HUMANITIES), columns=2)
        parts = {p["id"]: p for p in two["parts"]}
        self.assertEqual(parts["columns"]["got"], 0)
        self.assertEqual(parts["headings"]["fix_id"], "headings")
        self.assertIn("Giving back", parts["headings"]["note"])

    def test_no_contact_details_are_named_in_the_note(self):
        result = checks.scanner(humanities_cv(), {"name": "Marnie Quillfeather", "links": {}})
        contact = next(p for p in result["parts"] if p["id"] == "contact")
        self.assertEqual(contact["got"], 0)
        self.assertIn("Settings, About you", contact["note"])


class TypeChecks(unittest.TestCase):
    def test_a_humanities_cv_is_checked_by_humanities_rules(self):
        cv, profile = humanities_cv(), profile_of(HUMANITIES)
        humanities = checks.type_checks(cv, cvtypes.load("humanities_grad"), profile)
        ids = [i["id"] for i in humanities["items"]]
        self.assertEqual(ids, list(cvtypes.load("humanities_grad").checks))
        self.assertNotIn("projects_with_evidence", ids)
        by_id = {i["id"]: i for i in humanities["items"]}
        self.assertTrue(by_id["dissertation_shown"]["ok"])
        self.assertEqual(by_id["pages_within"]["name"], "Two pages or fewer")
        self.assertEqual(humanities["passed"], humanities["total"], [i for i in humanities["items"] if not i["ok"]])
        # Strong modules are 65 and over for humanities, 70 and over for the technical type.
        shown = cvtypes.arrange(cv, cvtypes.load("humanities_grad")).sections[1].entries[0].modules
        self.assertEqual([m.mark for m in shown], ["68", "66"])
        self.assertEqual(cvtypes.arrange(cv, cvtypes.load("technical_grad")).sections[1].entries[0].modules, [])
        # The same CV under the technical type is held to the technical checks, and a mark chosen by hand is weak.
        cv.sections[1].entries[0].show_marks_from = 50
        technical = {i["id"]: i for i in checks.type_checks(cv, cvtypes.load("technical_grad"), profile)["items"]}
        self.assertEqual(list(technical), [k for k in cvtypes.load("technical_grad").checks])
        self.assertFalse(technical["modules_strong"]["ok"])
        self.assertIn("Under 70", technical["modules_strong"]["note"])
        self.assertNotIn("dissertation_shown", technical)

    def test_a_profile_of_qualities_and_duties_fail_with_their_reasons(self):
        cv = humanities_cv()
        cv.sections[0].text = "Passionate and hard-working graduate."
        cv.sections[3].entries[0].bullets = [B("b9", "Responsible for the till")]
        items = {i["id"]: i for i in checks.type_checks(cv, cvtypes.load("humanities_grad"),
                                                         profile_of(HUMANITIES))["items"]}
        self.assertFalse(items["profile_evidence"]["ok"])
        self.assertIn("passionate", items["profile_evidence"]["note"])
        self.assertFalse(items["entry_says_what"]["ok"])

    def test_a_check_weighted_0_is_switched_off(self):
        t = cvtypes.load("humanities_grad")
        off = cvtypes.CVType(**{**t.__dict__, "checks": {**t.checks, "contact_at_top": 0}})
        ids = [i["id"] for i in checks.type_checks(humanities_cv(), off, profile_of(HUMANITIES))["items"]]
        self.assertNotIn("contact_at_top", ids)

    def test_a_student_type_asks_when_you_are_free(self):
        cv = humanities_cv()
        items = {i["id"]: i for i in checks.type_checks(cv, cvtypes.load("student"), profile_of(HUMANITIES))["items"]}
        self.assertFalse(items["availability_line"]["ok"])
        cv.sections[0].text += " Available for a 12-month placement from July 2027."
        items = {i["id"]: i for i in checks.type_checks(cv, cvtypes.load("student"), profile_of(HUMANITIES))["items"]}
        self.assertTrue(items["availability_line"]["ok"])


class Fixes(unittest.TestCase):
    def test_the_symbols_fix_replaces_symbols_and_leaves_locked_lines_alone(self):
        cv = humanities_cv()
        cv.sections[4].entries[0].items.append(f"{STAR} Cataloguing")
        cv.sections[2].entries[0].bullets.append(B("b7", f"Moved {ARROW} new shelves", locked=True))
        fixed, what = checks.fix("symbols", cv, cvtypes.load("humanities_grad"))
        self.assertIn("Cataloguing", fixed.sections[4].entries[0].items)
        self.assertEqual(fixed.sections[2].entries[0].bullets[-1].text, f"Moved {ARROW} new shelves")
        self.assertIn("2 symbols", what)
        self.assertEqual(checks.symbols_in("\n".join(checks.doc_lines(fixed, {}))), [ARROW])

    def test_the_headings_fix_uses_the_types_titles(self):
        cv = humanities_cv()
        cv.sections[2].title = "Giving back"
        fixed, what = checks.fix("headings", cv, cvtypes.load("humanities_grad"))
        self.assertEqual(fixed.sections[2].title, "Volunteering")
        self.assertIn("Giving back to Volunteering", what)


class ChecksRoutes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.enterContext(as_person(HUMANITIES))
        self.vid = store.new_version(humanities_cv(), origin="imported", what="Your CV, made up for a test",
                                     by="baseline")
        from jobhunt import db

        con = db.connect()
        con.execute("UPDATE cv_documents SET type_id = 'humanities_grad' WHERE id = 'main'")
        con.commit()
        con.close()
        self.client = self.hal.signed_in()

    def test_the_checks_tab_shows_the_score_and_the_types_checks_from_the_cache(self):
        first = self.client.get("/api/v2/cv/checks").json()
        self.assertEqual(first["scanner"]["score"], 100)
        self.assertEqual(first["type"]["name"], "Humanities and social science graduate")
        self.assertIsNone(first["recruiter"])
        again = self.client.get("/api/v2/cv/checks").json()
        self.assertEqual(again["scanner"], first["scanner"])
        from jobhunt import db

        con = db.connect()
        try:
            row = con.execute("SELECT scanner FROM cv_checks WHERE version_id = ?", (self.vid,)).fetchone()
            score = con.execute("SELECT scanner_score FROM cv_versions WHERE id = ?", (self.vid,)).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(json.loads(row["scanner"])["score"], 100)
        self.assertEqual(score, 100)

    def test_fix_it_makes_a_new_version(self):
        cv = humanities_cv()
        cv.sections[4].entries[0].items.append(f"{STAR} Cataloguing")
        store.new_version(cv, origin="edited", what="A made-up edit", by="you")
        parts = {p["id"]: p for p in self.client.get("/api/v2/cv/checks").json()["scanner"]["parts"]}
        self.assertEqual(parts["symbols"]["fix_id"], "symbols")
        out = self.client.post("/api/v2/cv/checks/fix/symbols").json()
        self.assertEqual(store.current()[0], out["version_id"])
        self.assertEqual(self.client.get("/api/v2/cv/checks").json()["scanner"]["score"], 100)
        self.assertEqual(self.client.post("/api/v2/cv/checks/fix/nothing").status, 404)

    def test_does_not_matter_leaves_a_word_out_of_the_match(self):
        from jobhunt import db

        con = db.connect()
        con.execute("INSERT INTO jobs (id, source, title, company, url, description, first_seen, last_seen, status)"
                    " VALUES ('made-up:1', 'test', 'Archive Assistant', 'Fenwick Trust', 'https://example.org/1',"
                    " 'Essential: archival research and Excel. Desirable: NVivo.', ?, ?, 'new')", (db.now(), db.now()))
        con.commit()
        con.close()
        job = self.client.get("/api/v2/cv/checks?job_id=made-up%3A1").json()["job"]
        self.assertIn("archival research", job["have"])
        self.assertIn("nvivo", [m["k"] for m in job["missing"]])
        off = self.client.post("/api/v2/cv/keywords/off", {"job_id": "made-up:1", "term": "NVivo"})
        self.assertTrue(off.json()["ok"])
        job = self.client.get("/api/v2/cv/checks?job_id=made-up%3A1").json()["job"]
        self.assertNotIn("nvivo", [m["k"] for m in job["missing"]])


class RecruiterRead(unittest.TestCase):
    def test_a_recruiters_read_is_the_ais_opinion_as_a_recruiter_for_the_type(self):
        reply = json.dumps({"grades": [{"criterion": "Impact", "grade": 3,
                                        "why": "The dissertation is the strongest thing.",
                                        "fix": "Move it up."}]})
        with World(), fake_ai({"cv_judge": reply}) as ai_log:
            done = runner.run("cv.recruiter", params=checks_actions._example(None))
            self.assertEqual(done["state"], "done")
            prompt = ai_log.log[-1].user
            self.assertIn(cvtypes.load("humanities_grad").fits, prompt)
            from jobhunt import db

            con = db.connect()
            try:
                row = con.execute("SELECT recruiter FROM cv_checks WHERE version_id = 'v-example'").fetchone()
            finally:
                con.close()
            self.assertEqual(json.loads(row["recruiter"])["text"], "The dissertation is the strongest thing.")

    def test_without_an_ai_the_read_ends_and_says_why(self):
        with World():
            done = runner.run("cv.recruiter", params=checks_actions._example(None))
        self.assertEqual(done["state"], "done")
        self.assertFalse(done["result"]["ok"])


if __name__ == "__main__":
    unittest.main()
