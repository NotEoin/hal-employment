"""Reading a CV back, and finding the evidence a job asks for.

Moved by class from tests/test_apply.py. The CV is a made-up person's, drawn by Hal's own PDF writer, and the
AI is the fake one, answering by purpose.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import legacy
from support.fake_ai import fake_ai
from test_pdfread import PEOPLE, Person, made_up_cvs

from jobhunt import cv, cvscore

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class ReadBack(unittest.TestCase):
    """Hal's CV layout read back as a parser reads it."""

    def setUp(self):
        Person(self, PEOPLE[0])

    def test_bullets_and_marks_read_back_in_order(self):
        data = cv.printable(made_up_cvs()[0])
        data["projects"] = [{**data["projects"][0],
                             "body": "- Built a thing that answers from notes.\n- Wrote 113 tests that run anywhere."}]
        data["experience"] = []
        text = cvscore.pdf_text(cv.render(data, Path(tempfile.mkdtemp())))
        self.assertIn("- Built a thing that answers from notes.", text)
        self.assertRegex(text, r"Selected modules \(out of 100\): [^\n]*\d")


class Requirements(unittest.TestCase):
    """The advert's requirements, and the CV's evidence for each, checked against the text."""

    def test_evidence_the_cv_does_not_hold_counts_as_missing(self):
        job = {"title": "Engineer", "company": "Acme",
               "description": "You have strong Python. A 2:1 degree is required."}
        replies = {"cv_needs": json.dumps({"requirements": [
                       {"need": "Python", "kind": "must", "weight": 3, "quote": "You have strong Python."},
                       {"need": "Degree", "kind": "knockout", "weight": 3, "quote": "A 2:1 degree is required"},
                       {"need": "Rust", "kind": "must", "weight": 2, "quote": "made up by the model"}]}),
                   "cv_evidence": json.dumps({"evidence": [
                       {"id": 1, "match": "direct", "quote": "Built a parser in Python"},
                       {"id": 2, "match": "direct", "quote": "a 2:1 in Physics"}]})}
        with fake_ai(replies):
            found, why_not = cvscore.requirements(
                job, "- Built a parser in Python.\nDegree, Grade")
        # The requirement the advert never stated is dropped; the degree evidence is not in the CV.
        self.assertEqual(why_not, "")
        self.assertEqual([r["need"] for r in found], ["Python", "Degree"])
        self.assertEqual([(r["match"], r["kind"]) for r in found], [("direct", "must"), ("missing", "knockout")])

    def test_evidence_found_only_in_the_record_is_marked_as_in_the_record(self):
        """A requirement the record meets and the CV does not says so, so the job page can tell them apart."""
        job = {"title": "Research engineer", "company": "Acme",
               "description": "You have strong Python. Research ethics training is required."}
        replies = {"cv_needs": json.dumps({"requirements": [
                       {"need": "Python", "kind": "must", "weight": 3, "quote": "You have strong Python."},
                       {"need": "Ethics training", "kind": "knockout", "weight": 3,
                        "quote": "Research ethics training is required"}]}),
                   "cv_evidence": json.dumps({"evidence": [
                       {"id": 1, "match": "direct", "quote": "Built a parser in Python"},
                       {"id": 2, "match": "direct",
                        "quote": "Human-subjects research training, completed 5 March 2026"}]})}
        kept = "[Certificates/Ethics.md > Ethics]\nHuman-subjects research training, completed 5 March 2026."
        with fake_ai(replies), mock.patch.object(cvscore.record, "context", lambda queries, **k: kept):
            found, _ = cvscore.requirements(job, "- Built a parser in Python.")
        self.assertEqual([r["where"] for r in found], ["cv", "record"])


class TypeKeywords(unittest.TestCase):
    """Against a job, the CV type's keywords replace the old fixed list of software skills."""

    def test_against_a_job_uses_the_types_keywords_and_aliases(self):
        found = cvscore.keywords("You will need archival research and good spreadsheets.",
                                 "Catalogued letters; archival research; Excel.",
                                 terms=["archival research", "Excel", "NVivo"], aliases={"excel": ["spreadsheets"]})
        self.assertEqual(sorted(found["have"]), ["archival research", "excel"])
        self.assertEqual(found["missing"], [])

    def test_without_a_type_the_built_in_list_is_used(self):
        found = cvscore.keywords("We use Python and Docker.", "Built it in Python.")
        self.assertEqual(found["have"], ["python"])
        self.assertEqual(found["missing"], ["docker"])


if __name__ == "__main__":
    unittest.main()
