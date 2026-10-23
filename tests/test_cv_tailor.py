"""Adjusting the CV for one job: No, A little and More, the original file turning it off, and the
change list's Undo, Redo and approval. The CV is the made-up humanities graduate's of `test_cv_suggest`.
"""
from __future__ import annotations

import json
import unittest

from support.fake_ai import fake_ai
from test_cv_suggest import PROFILE, TABLE, CvCase

from jobhunt.core import settings
from jobhunt.cvstudio import store, suggest, tailor

JOB = {"id": "job-archive-1", "title": "Archive Assistant", "company": "Fenmoor Heritage Trust",
       "requirements": ["Confident with Excel and NVivo for survey data", "Experience of archives"]}
NEW_PROFILE = "History graduate (2:1) who catalogued parish letters for a records office and uses Excel for surveys."
NEW_TABLE = "Ran the local history table, choosing stock and writing weekly recommendations"


def tailoring_ai(req) -> str:
    """Rewords the profile with evidence, and one bullet in each section it is shown."""
    lines = json.loads(req.user)["lines"]
    if PROFILE in lines:
        before = next(x.split("\t", 1)[1] for x in lines.splitlines() if x.startswith(PROFILE))
        return json.dumps({"target": PROFILE, "op": "replace", "kind": "Wording", "title": "Lead with the archive work",
                           "why": "The advert asks for archival research.", "before": before, "afters": [NEW_PROFILE],
                           "evidence": ["Advert: \"Experience of archives\""]})
    if TABLE in lines:
        return json.dumps({"target": TABLE, "op": "replace", "kind": "Wording", "title": "Say what you chose",
                           "why": "The advert asks for choosing material.",
                           "before": "Ran the local history table and wrote weekly staff recommendations",
                           "afters": [NEW_TABLE], "evidence": ["Your CV"]})
    return json.dumps({"none": True})


class Tailoring(CvCase):
    def setUp(self) -> None:
        super().setUp()
        self.con.execute("INSERT INTO jobs (id, source, company, title, first_seen, last_seen) "
                         "VALUES (?, 'manual', ?, ?, "
                         "'2026-10-20T09:00:00Z', '2026-10-20T09:00:00Z')", (JOB["id"], JOB["company"], JOB["title"]))
        self.con.commit()

    def bullets(self, doc) -> list[str]:
        return [b.text for s in doc.sections for e in s.entries for b in e.bullets]

    def test_adjust_a_little_only_reorders_and_changes_the_profile_line(self):
        base = store.current()[1]
        with fake_ai({"cv_suggest": tailoring_ai}):
            doc, changes = tailor.for_job(JOB, level="little")
        kinds = [(c["kind"], c["target"]) for c in changes]
        self.assertIn(("Moved", "s:skills"), kinds)
        self.assertIn(("Reworded", PROFILE), kinds)
        self.assertEqual({k for k, _ in kinds}, {"Moved", "Reworded"})
        self.assertEqual(self.bullets(doc), self.bullets(base))
        skills = next(s for s in doc.sections if s.id == "skills")
        self.assertEqual(skills.entries[0].heading, "Tools")
        self.assertEqual(suggest.shown_at(doc, "replace", PROFILE), NEW_PROFILE)
        moved = next(c for c in changes if c["kind"] == "Moved")
        self.assertIn("excel", moved["why"].lower())
        reworded = next(c for c in changes if c["kind"] == "Reworded")
        self.assertIn("Experience of archives", reworded["ev"])

    def test_adjust_more_also_rewrites_bullets_from_evidence(self):
        with fake_ai({"cv_suggest": tailoring_ai}):
            doc, changes = tailor.for_job(JOB, level="more")
        self.assertIn(TABLE, [c["target"] for c in changes])
        self.assertIn(NEW_TABLE, self.bullets(doc))
        self.assertLessEqual(sum(1 for c in changes if "/b:" in c["target"]), tailor.MAX_BULLETS)

    def test_adjust_no_gives_the_current_version_as_it_is(self):
        _, base = store.current()
        doc, changes = tailor.for_job(JOB, level="no")
        self.assertEqual((doc, changes), (base, []))

    def test_without_an_ai_a_little_still_reorders(self):
        _, changes = tailor.for_job(JOB, level="little")
        self.assertEqual([c["kind"] for c in changes], ["Moved"])

    def test_sending_the_original_turns_adjusting_off_and_says_why(self):
        self.con.execute("UPDATE cv_documents SET send = 'original'")
        self.con.commit()
        settings.set("cvdocs.tailor", "more", by="you")
        with fake_ai({"cv_suggest": tailoring_ai}) as backend:
            doc, changes = tailor.for_job(JOB)
            shown = tailor.prepare(JOB)
        self.assertEqual(changes, [])
        self.assertEqual(doc, store.current()[1])
        self.assertEqual(backend.requests("cv_suggest"), [])
        self.assertEqual(tailor.level_for(), ("no", tailor.ORIGINAL_WHY))
        self.assertEqual((shown["level"], shown["why_no"]), ("no", tailor.ORIGINAL_WHY))
        self.assertIn("off while you send your original", shown["why_no"])

    def test_the_person_picks_what_the_job_cares_about(self):
        _, changes = tailor.for_job(JOB, level="little", priorities=["Report writing"])
        self.assertEqual(changes, [])
        _, changes = tailor.for_job(JOB, level="little", priorities=["Oral history interviews"])
        self.assertEqual(changes[0]["after"].split(", ")[0], "Research")

    def test_undo_and_redo_a_change_then_approve_saves_a_version_for_the_job(self):
        base_vid = store.current()[0]
        with fake_ai({"cv_suggest": tailoring_ai}):
            shown = tailor.prepare(JOB, level="little")
        ids = {c["kind"]: c["id"] for c in shown["changes"]}
        after = tailor.set_change(JOB["id"], ids["Reworded"], False)
        self.assertFalse(next(c for c in after["changes"] if c["id"] == ids["Reworded"])["on"])
        doc = tailor.document(JOB["id"])
        self.assertNotEqual(suggest.shown_at(doc, "replace", PROFILE), NEW_PROFILE)
        tailor.set_change(JOB["id"], ids["Reworded"], True)
        self.assertEqual(suggest.shown_at(tailor.document(JOB["id"]), "replace", PROFILE), NEW_PROFILE)
        vid = tailor.approve(JOB["id"], JOB)
        row = self.con.execute("SELECT origin, job_id, what FROM cv_versions WHERE id = ?", (vid,)).fetchone()
        self.assertEqual(tuple(row),
                         ("tailored", JOB["id"], "Adjusted for Archive Assistant at Fenmoor Heritage Trust"))
        self.assertEqual(store.current()[0], base_vid)
        with self.assertRaises(RuntimeError):
            tailor.set_change(JOB["id"], ids["Moved"], False)

    def test_a_job_cv_never_carries_a_gap_in_the_ais_own_words(self):
        # Claude's gaps on 2026-10-23 read "[what outcome?]", which the old gap pattern let through to a job's CV
        def asks(req) -> str:
            out = json.loads(tailoring_ai(req))
            if out.get("afters"):
                out["afters"] = [out["afters"][0].rstrip(".") + ", achieving [what outcome?]"]
            return json.dumps(out)
        with fake_ai({"cv_suggest": asks}):
            _, changes = tailor.for_job(JOB, level="more")
        self.assertEqual([c["kind"] for c in changes], ["Moved"])
        self.assertNotIn("[what outcome?]", json.dumps(changes))

    def test_a_locked_line_is_never_touched_for_a_job(self):
        self.con.execute("UPDATE cv_documents SET layout = ?", (json.dumps({"locked_lines": [TABLE, PROFILE]}),))
        self.con.commit()
        with fake_ai({"cv_suggest": tailoring_ai}):
            _, changes = tailor.for_job(JOB, level="more")
        self.assertEqual([c["kind"] for c in changes], ["Moved"])


if __name__ == "__main__":
    unittest.main()
