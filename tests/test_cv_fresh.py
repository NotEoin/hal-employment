"""Build me a fresh CV: the interview answers of made-up people become a CV of their type that passes
its checks, with module marks optional for everyone.
"""
from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

from support.stubs import cv_early_contracts

cv_early_contracts.install()

from jobhunt import db  # noqa: E402 - the CV stand-ins go in before any jobhunt.cvstudio import
from jobhunt.core import paths  # noqa: E402
from jobhunt.core.settings import store as settings_store  # noqa: E402
from jobhunt.cvstudio import checks, fresh, model, store, types  # noqa: E402

NO_MARKS = {
    "education": [{"qualification": "BA (Hons) History and Imaginary Studies", "org": "University of Nowhere Fen",
                   "start": "Sep 2023", "end": "Jun 2026", "grade": "2:1",
                   "dissertation": "Parish records of Fenmoor, 1850 to 1900",
                   "modules": [{"name": "Early Modern Fenland"}, {"name": "Archives and Memory"}]}],
    "jobs": [{"role": "Bookseller", "org": "Lantern Books", "start": "Sep 2024", "end": "Present",
              "did": ["Ran the local history table and wrote 12 weekly staff recommendations"]}],
    "volunteering": [{"role": "Archive Volunteer", "org": "Fenmoor Records Office", "start": "Jan 2024",
                      "end": "Present", "did": ["Catalogued 120 parish letters for the public search"]}],
    "skills": [{"group": "Research", "items": ["archival research", "oral history"]},
               {"group": "Tools", "items": ["Excel", "NVivo"]}],
    "interests": "Lead a monthly local history walk.",
}


class FreshCv(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-fresh-")))
        stack.enter_context(paths.set_override(tmp / "home"))
        settings_store.drop_cache()
        self.addCleanup(settings_store.drop_cache)

    def test_a_humanities_graduate_with_no_marks_gets_a_fresh_cv_that_passes_its_checks(self):
        out = fresh.make(NO_MARKS, "humanities_grad")
        self.assertEqual(out["failing"], [])
        self.assertGreater(out["checks"], 0)
        vid, doc = store.current()
        self.assertEqual(vid, out["version_id"])
        type_ = types.load("humanities_grad")
        self.assertEqual(model.validate(doc, type_), [])
        self.assertTrue(all(r["ok"] for r in checks.for_type(doc, type_)))
        self.assertEqual([s.id for s in doc.sections],
                         ["summary", "education", "volunteering", "experience", "skills", "interests"])
        self.assertEqual(doc.sections[0].title, "Profile")
        education = doc.sections[1].entries[0]
        self.assertEqual(education.modules, [])
        self.assertIn("Dissertation: Parish records of Fenmoor, 1850 to 1900", [b.text for b in education.bullets])
        self.assertIn("dissertation on Parish records", doc.sections[0].text)
        con = db.connect()
        try:
            row = con.execute("SELECT origin, by FROM cv_versions WHERE id = ?", (vid,)).fetchone()
        finally:
            con.close()
        self.assertEqual(tuple(row), ("generated", "hal"))

    def test_strong_modules_with_marks_are_shown_and_weak_ones_left_out(self):
        answers = {**NO_MARKS, "education": [{**NO_MARKS["education"][0], "modules": [
            {"name": "Early Modern Fenland", "mark": "72"}, {"name": "Archives and Memory", "mark": "58"}]}]}
        doc = fresh.build(answers, types.load("humanities_grad"))
        self.assertEqual([m.name for m in doc.sections[1].entries[0].modules], ["Early Modern Fenland"])

    def test_the_interview_asks_one_question_at_a_time(self):
        self.assertEqual([q["id"] for q in fresh.QUESTIONS],
                         ["education", "jobs", "volunteering", "skills", "interests"])

    def test_a_fresh_cv_with_a_failing_check_gets_a_suggestion_for_it(self):
        answers = {**NO_MARKS, "jobs": [{**NO_MARKS["jobs"][0], "did": ["Organized author events"]}]}
        out = fresh.make(answers, "humanities_grad")
        self.assertIn("One spelling throughout", out["failing"])
        self.assertGreaterEqual(out["suggestions"], 1)


if __name__ == "__main__":
    unittest.main()
