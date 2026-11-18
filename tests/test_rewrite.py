"""Write the documents again (`apply/rewrite.py`, `docs.prepare` with `again`): a real rewrite from the record
and the CV as they are now, which keeps the person's own edits unless asked to write everything fresh, moves an
older application to the line-by-line CV, stamps what it was written from, and never touches an approved, sent
or filling application.

The rewrites run the real pack with the recording fake AI of `test_pack.py`, as one of its made-up people.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from support import people
from support.actions import finished
from test_pack import WAIT_S, FakeWriter, add_job, as_applicant

from jobhunt import db, documents, record
from jobhunt.apply import actions, queries, rewrite
from jobhunt.core import runner
from jobhunt.cvstudio import store, tailor

OLD_LETTER = "I catalogued the river trade ledgers of a made-up port for my dissertation."
NEW_LETTER = "I guided visitors round a made-up heritage centre and wrote its short reports."
MY_LETTER = "Avery Quillon here: these are my own words for the letter."
MY_HEADLINE = "Collections helper in my own words"


def letter_of(con, job) -> str:
    return documents.load(con, job)["letter"]


class Written(unittest.TestCase):
    """A made-up applicant whose documents Hal has written once, with the helpers the tests below share."""

    def setUp(self):
        self.writer = FakeWriter(letter=OLD_LETTER)
        self.a = self.enterContext(as_applicant(people.HUMANITIES, self.writer))
        self.con = self.a.con
        self.job = add_job(self.con, "humanities_grad")
        done = finished(actions.start("docs.prepare", self.job["id"]), WAIT_S)
        self.assertEqual(done["state"], "done", done.get("error"))
        self.job = dict(queries.load_job(self.con, self.job["id"]))

    def again(self, **kw) -> dict:
        done = finished(actions.write_again(self.con, self.job["id"], **kw), WAIT_S)
        self.assertEqual(done["state"], "done", done.get("error"))
        return done

    def your_cv_edit(self) -> None:
        """One edit of the person's own in the job's CV, as the CV step's editor keeps it."""
        row = self.con.execute("SELECT changes FROM job_cvs WHERE job_id = ?", (self.job["id"],)).fetchone()
        changes = [*json.loads(row["changes"]),
                   {"id": "y1", "by": tailor.YOU, "op": "replace", "target": "headline", "value": MY_HEADLINE}]
        self.con.execute("UPDATE job_cvs SET changes = ? WHERE job_id = ?", (json.dumps(changes), self.job["id"]))
        self.con.commit()


class WriteAgain(Written):
    def test_writing_again_writes_a_new_letter_from_the_changed_record_and_stamps_it(self):
        self.assertIn(OLD_LETTER, letter_of(self.con, self.job))
        self.writer.letter = NEW_LETTER     # what the writer makes of the record once it has changed
        now = record.bump_revision()
        self.again()
        self.assertIn(NEW_LETTER, letter_of(self.con, self.job))
        self.assertEqual(documents.written_from(self.con, self.job["id"]), now)
        base = self.con.execute("SELECT base_version_id FROM job_cvs WHERE job_id = ?", (self.job["id"],)).fetchone()
        self.assertEqual(base[0], store.current(self.con)[0])

    def test_your_own_letter_and_cv_edits_are_kept_by_default(self):
        actions.save_documents(self.con, self.job["id"], {"letter": MY_LETTER}, by_you=True)
        self.your_cv_edit()
        self.assertTrue(rewrite.has_own_edits(self.con, self.job))
        self.writer.letter = NEW_LETTER
        done = self.again()
        self.assertEqual(letter_of(self.con, self.job), MY_LETTER)
        self.assertEqual(tailor.v1(self.job["id"], self.con)["title"], MY_HEADLINE)
        self.assertTrue(done["result"]["kept_edits"])

    def test_writing_everything_fresh_replaces_your_letter_and_your_cv_edits(self):
        actions.save_documents(self.con, self.job["id"], {"letter": MY_LETTER}, by_you=True)
        self.your_cv_edit()
        self.writer.letter = NEW_LETTER
        self.again(keep_edits=False)
        self.assertIn(NEW_LETTER, letter_of(self.con, self.job))
        self.assertEqual(tailor.shown(self.job["id"], self.con)["edits"], 0)
        self.assertNotEqual(tailor.v1(self.job["id"], self.con)["title"], MY_HEADLINE)
        self.assertFalse(rewrite.has_own_edits(self.con, self.job))

    def test_an_application_from_before_kept_cvs_moves_to_the_line_by_line_cv(self):
        self.con.execute("DELETE FROM job_cvs WHERE job_id = ?", (self.job["id"],))
        actions.save_documents(self.con, self.job["id"], {"undone_changes": ["c1"]})
        self.con.commit()
        self.assertEqual(queries.application(self.con, self.job["id"])["docs"]["cv"]["schema"], 1)
        self.again()
        self.assertEqual(queries.application(self.con, self.job["id"])["docs"]["cv"]["schema"], 2)
        self.assertNotIn("undone_changes", documents.load(self.con, self.job), "an undone change does not carry over")

    def test_approved_sent_and_closed_applications_are_never_written_again(self):
        job_id = self.job["id"]
        self.con.execute("UPDATE applications SET approved_at = ? WHERE job_id = ?", (db.now(), job_id))
        self.con.commit()
        with self.assertRaisesRegex(rewrite.NotNow, "Change the documents"):
            actions.write_again(self.con, job_id)
        db.set_status(self.con, job_id, "applied", "sent")
        self.con.commit()
        self.assertEqual(rewrite.refusal(self.con, job_id), rewrite.SENT)
        db.set_status(self.con, job_id, "skipped", "discarded")
        self.con.commit()
        self.assertEqual(rewrite.refusal(self.con, job_id), rewrite.CLOSED)

    def test_an_application_approved_while_its_rewrite_waited_is_left_as_it_is(self):
        before = letter_of(self.con, self.job)
        self.con.execute("UPDATE applications SET approved_at = ? WHERE job_id = ?", (db.now(), self.job["id"]))
        self.con.commit()
        self.writer.letter = NEW_LETTER
        done = finished(actions.start("docs.prepare", self.job["id"], params={"again": True}), WAIT_S)
        self.assertEqual(done["result"]["left"], True)
        self.assertEqual(letter_of(self.con, self.job), before)

    def test_a_form_hal_is_filling_is_never_stopped_by_a_rewrite(self):
        filling = {"id": "a1", "kind": "apply.fill"}
        with mock.patch.object(runner, "running_for", lambda subject: filling):
            with self.assertRaisesRegex(rewrite.NotNow, "filling in the form"):
                actions.write_again(self.con, self.job["id"])


class OutOfDate(Written):
    """Out of date on the application page and the board, and the failed state, from the same real pack."""

    def page(self) -> dict:
        return queries.application(self.con, self.job["id"])["docs"]

    def card(self) -> dict:
        return next(c for col in queries.board(self.con)["columns"] for c in col["items"] if c["id"] == self.job["id"])

    def test_documents_are_up_to_date_until_the_record_changes_and_again_once_rewritten(self):
        self.assertEqual((self.page()["out_of_date"], self.card()["out_of_date"]), (None, ""))
        record.bump_revision()
        self.assertEqual(self.page()["out_of_date"], rewrite.RECORD_MOVED)
        self.assertEqual((self.card()["out_of_date"], self.card()["rewritable"]), (rewrite.RECORD_MOVED, True))
        self.again()
        self.assertIsNone(self.page()["out_of_date"])

    def test_a_new_version_of_your_cv_makes_the_documents_out_of_date(self):
        store.new_version(store.current(self.con)[1], origin="edited", what="A made-up change", by="you",
                          con=self.con)
        self.assertEqual(self.page()["out_of_date"], rewrite.CV_MOVED)
        record.bump_revision()
        self.assertEqual(self.card()["out_of_date"], rewrite.BOTH_MOVED)

    def test_documents_written_before_stamps_are_out_of_date_once_the_record_changes(self):
        self.con.execute("DELETE FROM meta WHERE k = ?", (documents.written_from_key(self.job["id"]),))
        self.con.commit()
        if record.revision(self.con) == 0:
            self.assertIsNone(self.page()["out_of_date"], "nothing has changed since the stamps began")
        record.bump_revision()
        self.assertEqual(self.page()["out_of_date"], rewrite.RECORD_MOVED)

    def test_an_approved_application_is_never_called_out_of_date(self):
        record.bump_revision()
        self.con.execute("UPDATE applications SET approved_at = ? WHERE job_id = ?", (db.now(), self.job["id"]))
        self.con.commit()
        self.assertIsNone(self.page()["out_of_date"])
        self.assertFalse(self.card()["rewritable"])

    def test_a_write_that_failed_shows_on_the_application_page_with_why(self):
        # a regression: the page said "ready" with empty tabs, and only the board showed the error (2026-11-18)
        from jobhunt import pack

        job = add_job(self.con, "technical_grad", status="applying")
        with mock.patch.object(pack, "build", side_effect=RuntimeError("Hal's AI did not answer")):
            done = finished(actions.start("docs.prepare", job["id"]), WAIT_S)
        self.assertEqual(done["state"], "error")
        docs = queries.application(self.con, job["id"])["docs"]
        self.assertEqual(docs["state"], "failed")
        self.assertIn("did not answer", docs["stopped"])


DRAFTED = {"letter": "Hal's letter.", "message": "Hal's message.", "message_to": "Ada Penhallow",
           "answers": [{"question": "Why this role?", "answer": "Hal's answer."},
                       {"question": "Your strengths?", "answer": "Hal's strengths."}], "approved": False}


class KeptEdits(unittest.TestCase):
    def test_only_what_differs_from_the_pack_counts_as_yours(self):
        docs = {**DRAFTED, "message": "My message.",
                "answers": [{"question": "Why this role?", "answer": "Hal's answer."},
                            {"question": "Your strengths?", "answer": "My strengths."}],
                "undone_changes": ["c2"]}
        self.assertEqual(rewrite.kept_edits(docs, DRAFTED),
                         {"message": "My message.", "answers": {"Your strengths?": "My strengths."}})

    def test_a_letter_hal_drafted_for_a_form_is_not_counted_as_yours(self):
        docs = {**DRAFTED, "letter": "Hal's form-time letter.", "letter_notes": {"tone": "plain"}}
        self.assertEqual(rewrite.kept_edits(docs, {**DRAFTED, "letter": ""}), {})

    def test_your_answers_go_back_in_and_one_the_new_drafts_no_longer_ask_is_kept_last(self):
        new = {**DRAFTED, "answers": [{"question": "Why this role?", "answer": "Hal's new answer."}]}
        kept = {"answers": {"Why this role?": "My answer.", "Your strengths?": "My strengths."}}
        out = rewrite.merged(new, kept)
        self.assertEqual(out["answers"], [{"question": "Why this role?", "answer": "My answer."},
                                          {"question": "Your strengths?", "answer": "My strengths."}])
        self.assertNotIn("approved", out)


if __name__ == "__main__":
    unittest.main()
