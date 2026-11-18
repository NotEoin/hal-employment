"""Adjusting the CV for one job: No, A little and More, the original file turning it off, and the
change list's Undo, Redo and approval. The CV is the made-up humanities graduate's of `test_cv_suggest`.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from support.fake_ai import fake_ai
from test_cv_suggest import PROFILE, TABLE, CvCase

from jobhunt import documents
from jobhunt.apply import actions
from jobhunt.core import settings
from jobhunt.cvstudio import cv, store, suggest, tailor

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


class JobCase(CvCase):
    """The made-up CV and the made-up job it is adjusted for."""

    def setUp(self) -> None:
        super().setUp()
        self.con.execute("INSERT INTO jobs (id, source, company, title, first_seen, last_seen) "
                         "VALUES (?, 'manual', ?, ?, "
                         "'2026-10-20T09:00:00Z', '2026-10-20T09:00:00Z')", (JOB["id"], JOB["company"], JOB["title"]))
        self.con.commit()


class Tailoring(JobCase):
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

    def test_a_job_cv_never_gains_an_outcome_the_cv_does_not_have(self):
        # Claude's change for a made-up Collections Assistant job (2026-10-23), kind "Results", added a claim
        added = "Ran the local history table and wrote weekly staff recommendations, which raised sales"
        def outcome(req) -> str:
            out = json.loads(tailoring_ai(req))
            if out.get("target") == TABLE:
                out.update(kind="Results", afters=[added])
            return json.dumps(out)
        with fake_ai({"cv_suggest": outcome}):
            doc, changes = tailor.for_job(JOB, level="more")
        self.assertNotIn(TABLE, [c["target"] for c in changes])
        self.assertNotIn(added, self.bullets(doc))
        self.assertIn(PROFILE, [c["target"] for c in changes])

    def test_a_locked_line_is_never_touched_for_a_job(self):
        self.con.execute("UPDATE cv_documents SET layout = ?", (json.dumps({"locked_lines": [TABLE, PROFILE]}),))
        self.con.commit()
        with fake_ai({"cv_suggest": tailoring_ai}):
            _, changes = tailor.for_job(JOB, level="more")
        self.assertEqual([c["kind"] for c in changes], ["Moved"])

    def test_the_title_line_chosen_by_the_advert_is_a_change_that_can_be_undone(self):
        job = {**JOB, "description": "Catalogue the archive and answer archive enquiries."}
        with mock.patch.object(cv, "TRACKS", {"archives": ("Archives graduate", r"\barchive")}):
            shown = tailor.prepare(job, level="little")
        title = next(c for c in shown["changes"] if c["target"] == "headline")
        self.assertEqual((title["kind"], title["after"]), ("Reworded", "Archives graduate"))
        self.assertEqual(tailor.document(JOB["id"]).headline, "Archives graduate")
        self.assertEqual(tailor.v1(JOB["id"])["title"], "Archives graduate")
        tailor.set_change(JOB["id"], title["id"], False)
        self.assertEqual(tailor.document(JOB["id"]).headline, store.current()[1].headline)


class Applications(JobCase):
    """An application's CV through the documents and the approval, kept in `job_cvs`."""

    def setUp(self) -> None:
        super().setUp()
        self.printed: list[dict] = []
        self.enterContext(mock.patch.object(cv, "render", self.draw))
        self.enterContext(mock.patch.object(actions, "start", lambda *a, **kw: "action-made-up"))

    def draw(self, variant: dict, out: Path) -> Path:
        """A made-up PDF maker: keeps what it was asked to print."""
        self.printed.append(variant)
        out.mkdir(parents=True, exist_ok=True)
        (out / "cv.pdf").write_bytes(b"%PDF-1.4 made-up\n%%EOF\n")
        return out / "cv.pdf"

    def job(self):
        return self.con.execute("SELECT * FROM jobs WHERE id = ?", (JOB["id"],)).fetchone()

    def test_a_new_application_prints_its_kept_cv_and_undo_prints_it_again(self):
        with fake_ai({"cv_suggest": tailoring_ai}):
            shown = tailor.prepare(JOB, level="little", con=self.con)
        sent = documents.current_cv(self.job(), {})
        self.assertEqual(sent, cv.v1_of(tailor.document(JOB["id"])))
        self.assertEqual(sent["summary"], NEW_PROFILE)
        reworded = next(c for c in shown["changes"] if c["kind"] == "Reworded")
        undone = actions.set_change(self.con, JOB["id"], reworded["id"], False)
        self.assertEqual(undone, [reworded["id"]])
        self.assertNotEqual(self.printed[-1]["summary"], NEW_PROFILE, "the PDF is made again without it")
        self.assertNotIn("undone_changes", documents.load(self.con, self.job()))

    def test_approving_saves_the_jobs_version_on_the_application_and_its_folder(self):
        with fake_ai({"cv_suggest": tailoring_ai}):
            tailor.prepare(JOB, level="little", con=self.con)
        actions.approve_documents(self.con, JOB["id"], {})
        vid = tailor.version_of(JOB["id"], self.con)
        self.assertEqual(self.con.execute("SELECT origin, job_id FROM cv_versions WHERE id = ?", (vid,)).fetchone()[:],
                         ("tailored", JOB["id"]))
        self.assertEqual(self.printed[-1], cv.v1_of(store.get(vid)))
        found = self.con.execute("SELECT cv_version_id FROM applications WHERE job_id = ?", (JOB["id"],)).fetchone()
        self.assertEqual(found["cv_version_id"], vid)
        found = self.con.execute("SELECT cv_version_id FROM app_documents WHERE job_id = ? AND kind = 'cv_pdf'",
                                 (JOB["id"],)).fetchone()
        self.assertEqual(found["cv_version_id"], vid)
        actions.reopen_documents(self.con, JOB["id"])
        self.assertIsNone(tailor.version_of(JOB["id"], self.con), "taking the approval back lets the CV change")

    def test_an_approved_jobs_cv_is_never_made_again_over_the_approval(self):
        # review, 2026-11-18: a rewrite still running when the person approved replaced the approved row with an
        # unapproved one
        with fake_ai({"cv_suggest": tailoring_ai}):
            tailor.prepare(JOB, level="little", con=self.con)
            actions.approve_documents(self.con, JOB["id"], {})
            vid = tailor.version_of(JOB["id"], self.con)
            with self.assertRaisesRegex(RuntimeError, "approved"):
                tailor.prepare(JOB, level="more", con=self.con)
        self.assertEqual(tailor.version_of(JOB["id"], self.con), vid)
        self.assertEqual(tailor.shown(JOB["id"], self.con)["level"], "little")

    def test_an_application_written_before_keeps_the_older_path_until_sent(self):
        older = {"title": "Older", "summary": "Reordered the older way."}
        with mock.patch.object(cv, "tailor", lambda text: (dict(older), None)):
            self.assertEqual(documents.current_cv(self.job(), {})["summary"], "Reordered the older way.")
            undone = actions.set_change(self.con, JOB["id"], "summary", False)
        self.assertEqual(undone, ["summary"])
        self.assertEqual(documents.load(self.con, self.job())["undone_changes"], ["summary"])
        self.assertFalse(tailor.kept(JOB["id"]))


if __name__ == "__main__":
    unittest.main()
