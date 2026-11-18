"""An application's documents follow the CV they are built from (`cvstudio/choose.py`, `tailor.prepare`,
`pack.build`): its facts reach the letter, its lines make the job's CV, the choice is kept with the job's CV,
Hal's own pick is made again by a rewrite and the person's pick is kept. Every CV here is made up.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

import support  # noqa: F401 - the temporary home, before any jobhunt import
from support.actions import finished
from test_pack import WAIT_S
from test_rewrite import MY_HEADLINE, MY_LETTER, Written

from jobhunt import db, documents
from jobhunt.apply import actions, queries, rewrite
from jobhunt.cvstudio import choose, cv, model, store, tailor

REAL_FACTS = cv.facts_text           # `as_applicant` stands canned facts in; these tests read the real CV
MUSEUM = {"title": "Museum guide", "summary": "Guided visitors round a made-up harbour museum.",
          "skills": [], "projects": [], "experience": [], "education": [], "additional": [], "moreProjects": "",
          "closing": ""}
MUSEUM_FACT = "made-up harbour museum"


class Baseline(Written):
    """The made-up applicant's Library CV first in their order, then a Museum CV."""

    def setUp(self):
        super().setUp()
        self.first = store.create("Library CV", store.current(self.con)[1], origin="uploaded",
                                  what="Your file: library.pdf", by="baseline", con=self.con)
        self.museum = store.create("Museum CV", model.from_v1(MUSEUM), origin="uploaded",
                                   what="Your file: museum.pdf", by="baseline", con=self.con)
        store.reorder([self.first, self.museum], con=self.con)

    def hal_picks(self, doc_id: str, reason: str = "Covers 3 of the 4 skills this advert asks for."):
        """Hal's chooser stood in for: it picks `doc_id`."""
        def pick(con, job):
            return choose.Choice(doc_id, store.current(con, doc_id)[0], reason=reason, pick=doc_id,
                                 scores=[{"doc_id": doc_id, "name": "x", "fit": 80, "have": 3, "of": 4}])
        return mock.patch.object(choose, "choose", side_effect=pick)

    def base_doc(self) -> str | None:
        return choose.base_doc(self.job["id"], self.con)


class Follows(Baseline):
    """What is written for the application follows the CV it is built from."""

    def test_the_letter_and_the_jobs_cv_are_written_from_the_cv_hal_picked(self):
        with self.hal_picks(self.museum), mock.patch.object(cv, "facts_text", REAL_FACTS):
            self.again()
        self.assertEqual(self.base_doc(), self.museum)
        self.assertEqual(tailor.v1(self.job["id"], self.con)["title"], "Museum guide")
        self.assertTrue(any(MUSEUM_FACT in e["user"] for e in self.writer.log), "the drafts read the Museum CV")
        base = queries.application(self.con, self.job["id"])["docs"]["cv"]["base"]
        self.assertEqual((base["name"], base["label"], base["by"], base["several"]),
                         ("Museum CV", "your Museum CV", "hal", True))
        self.assertEqual(base["reason"], "Covers 3 of the 4 skills this advert asks for.")

    def test_a_rewrite_chooses_again_when_hal_chose(self):
        with self.hal_picks(self.museum):
            self.again()
        with self.hal_picks(self.first):
            self.again()
        self.assertEqual(self.base_doc(), self.first)

    def test_a_cv_you_picked_is_kept_by_a_rewrite_on_its_newest_version(self):
        tailor.prepare(self.job, base=choose.by_you(self.con, self.job, self.museum), con=self.con)
        newer = store.new_version(model.from_v1({**MUSEUM, "title": "Museum and archive guide"}), origin="manual",
                                  what="You edited your CV", by="you", doc_id=self.museum, con=self.con)
        with self.hal_picks(self.first) as hal:
            self.again()
        hal.assert_not_called()
        kept = choose.kept(self.con, self.job["id"])
        self.assertEqual((kept.doc_id, kept.version_id, kept.by, kept.reason),
                         (self.museum, newer, "you", choose.YOU_CHOSE))

    def test_ticking_what_the_job_cares_about_keeps_the_cv_it_was_built_from(self):
        with self.hal_picks(self.museum):
            self.again()
        with self.hal_picks(self.first) as hal:
            tailor.prepare(self.job, priorities=["Writing reports"], con=self.con)
        hal.assert_not_called()
        self.assertEqual(self.base_doc(), self.museum)

    def test_a_change_to_another_cv_does_not_put_the_documents_out_of_date(self):
        with self.hal_picks(self.museum):
            self.again()
        store.new_version(model.from_v1({**MUSEUM, "summary": "Other words."}), origin="manual", what="Edit",
                          by="you", doc_id=self.first, con=self.con)
        self.assertEqual(rewrite.out_of_date(self.con, [self.job["id"]]), {})
        store.new_version(model.from_v1({**MUSEUM, "summary": "Newer words."}), origin="manual", what="Edit",
                          by="you", doc_id=self.museum, con=self.con)
        self.assertIn("CV changed", rewrite.out_of_date(self.con, [self.job["id"]])[self.job["id"]])

    def test_copy_to_my_main_cv_copies_into_the_cv_the_application_was_built_from(self):
        with self.hal_picks(self.museum):
            self.again()
        opened = tailor.editor(self.job["id"], self.con)
        self.assertEqual((opened["main_doc_id"], opened["main_version_id"], opened["main_label"]),
                         (self.museum, store.current(self.con, self.museum)[0], "your Museum CV"))

    def test_picking_hals_own_pick_again_makes_it_hals_choice_once_more(self):
        with self.hal_picks(self.museum, reason="Hal's made-up reason."):
            self.again()
        mine = choose.by_you(self.con, self.job, self.first)
        tailor.prepare(self.job, base=mine, con=self.con)
        back = choose.by_you(self.con, self.job, self.museum)
        self.assertEqual((back.by, back.reason), ("hal", "Hal's made-up reason."))


LIBRARY = {**MUSEUM, "title": "Library assistant", "summary": "Shelved and catalogued made-up books.",
           "experience": [{"role": "Library assistant", "org": "Made-up Library", "dates": "2024",
                           "body": "- Shelved"}]}


class Switch(Baseline):
    """Building an application on another CV (`actions.change_base`, `docs.base_cv`)."""

    def setUp(self):
        super().setUp()
        store.new_version(model.from_v1(LIBRARY), origin="manual", what="Edit", by="you", doc_id=self.first,
                          con=self.con)
        with self.hal_picks(self.first):
            self.again()

    def switch(self, doc_id: str) -> dict:
        done = finished(actions.change_base(self.con, self.job["id"], doc_id), WAIT_S)
        self.assertEqual(done["state"], "done", done.get("error"))
        return done["result"]

    def entry_edit(self) -> dict:
        """An edit of the person's own on the Library CV's job entry, which the Museum CV does not have."""
        doc = store.current(self.con, self.first)[1]
        sec = next(s for s in doc.sections if s.entries)
        return {"id": "y2", "by": tailor.YOU, "op": "replace", "target": model.target_of(sec, sec.entries[0])
                + "/heading", "value": "Senior library assistant"}

    def test_switching_keeps_the_letter_and_the_edits_that_still_fit(self):
        actions.save_documents(self.con, self.job["id"], {"letter": MY_LETTER}, by_you=True)
        self.your_cv_edit()
        self.add_edit(self.entry_edit())
        self.assertEqual(tailor.lost_edits(self.job["id"], store.current(self.con, self.museum)[0], self.con), 1)
        got = self.switch(self.museum)
        self.assertEqual(got["lost"], 1)
        self.assertEqual(documents.load(self.con, self.job)["letter"], MY_LETTER)
        self.assertEqual(tailor.v1(self.job["id"], self.con)["title"], MY_HEADLINE)
        kept = choose.kept(self.con, self.job["id"])
        self.assertEqual((kept.doc_id, kept.by), (self.museum, "you"))

    def test_an_approved_or_sent_application_keeps_its_cv(self):
        self.con.execute("UPDATE applications SET approved_at = ? WHERE job_id = ?", (db.now(), self.job["id"]))
        self.con.commit()
        with self.assertRaises(rewrite.NotNow):
            actions.change_base(self.con, self.job["id"], self.museum)
        self.assertEqual(self.base_doc(), self.first)

    def test_only_a_cv_in_use_for_applications_can_be_chosen(self):
        store.star(self.museum, False, con=self.con)
        with self.assertRaisesRegex(ValueError, "CVs you use for applications"):
            actions.change_base(self.con, self.job["id"], self.museum)

    def test_an_older_application_moves_to_the_line_by_line_cv_on_the_cv_you_chose(self):
        self.con.execute("DELETE FROM job_cvs WHERE job_id = ?", (self.job["id"],))
        actions.save_documents(self.con, self.job["id"], {"undone_changes": ["c1"]})
        self.con.commit()
        chosen = next(c["id"] for c in choose.candidates(self.con, self.job["id"]) if c["chosen"])
        self.assertEqual(chosen, store.lead_id(self.con), "an older application was built from the lead CV")
        self.switch(self.museum)
        self.assertEqual(queries.application(self.con, self.job["id"])["docs"]["cv"]["schema"], 2)
        self.assertEqual(self.base_doc(), self.museum)
        self.assertNotIn("undone_changes", documents.load(self.con, self.job))

    def add_edit(self, edit: dict) -> None:
        row = self.con.execute("SELECT changes FROM job_cvs WHERE job_id = ?", (self.job["id"],)).fetchone()
        self.con.execute("UPDATE job_cvs SET changes = ? WHERE job_id = ?",
                         (json.dumps([*json.loads(row["changes"]), edit]), self.job["id"]))
        self.con.commit()


if __name__ == "__main__":
    unittest.main()
