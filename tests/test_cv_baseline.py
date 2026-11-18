"""An application's documents follow the CV they are built from (`cvstudio/choose.py`, `tailor.prepare`,
`pack.build`): its facts reach the letter, its lines make the job's CV, the choice is kept with the job's CV,
Hal's own pick is made again by a rewrite and the person's pick is kept. Every CV here is made up.
"""
from __future__ import annotations

import unittest
from unittest import mock

import support  # noqa: F401 - the temporary home, before any jobhunt import
from test_rewrite import Written

from jobhunt.apply import queries, rewrite
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


if __name__ == "__main__":
    unittest.main()
