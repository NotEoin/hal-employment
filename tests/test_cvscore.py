"""Scoring a CV: reading it back, judging bullets and finding the evidence a job asks for.

Moved by class from tests/test_apply.py; the test bodies are unchanged.
"""
import tempfile
import unittest
from pathlib import Path

from support import legacy

from jobhunt import cv, cvscore

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class Scoring(unittest.TestCase):
    """Hal's CV layout read back as a parser reads it, and the CV graded by rule."""

    def cv(self, body: str) -> dict:
        base = cv.printable(cv.site())
        base["projects"] = [{**base["projects"][0], "body": body}]
        base["experience"] = []
        return base

    def render(self, data: dict) -> Path:
        return cv.render(data, Path(tempfile.mkdtemp()))

    def test_bullets_and_marks_read_back_in_order(self):
        pdf = self.render(self.cv("- Built a thing that answers from notes.\n- Wrote 113 tests that run anywhere."))
        text = cvscore.pdf_text(pdf)
        self.assertIn("- Built a thing that answers from notes.", text)
        self.assertRegex(text, r"Selected modules \(out of 100\): [^\n]*\d")
        self.assertEqual(cvscore.parsing(self.cv(""), pdf)[4]["score"], 1.0)  # tables read back in order

    def test_bullets_with_results_outscore_a_paragraph_of_duties(self):
        duties = self.cv("Responsible for the backend. Helped with various features and worked on testing.")
        results = self.cv("- Built the backend in Flask, serving 3 clients.\n- Cut test time from 9 to 2 minutes "
                          "by running suites in parallel.")
        worse, better = cvscore.score(duties, self.render(duties)), cvscore.score(results, self.render(results))
        self.assertGreater(better["mark"], worse["mark"])
        verbs = [next(c["score"] for g in r["groups"] for c in g["checks"] if c["name"].startswith("Lines open"))
                 for r in (worse, better)]
        self.assertLess(verbs[0], verbs[1])

    def test_a_cv_no_parser_can_read_is_capped(self):
        empty = Path(tempfile.mkdtemp()) / "image.pdf"
        empty.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.assertLessEqual(cvscore.score(self.cv("- Built it."), empty)["mark"], 10)

    def test_a_short_readable_cv_is_not_capped_as_unreadable(self):
        """A CV under 250 words still has a text layer; its length is for the length checks to judge."""
        short = self.cv("- Built a parser in Python, with 40 tests.")
        short.update(education=[{"qualification": "Degree", "grade": "Grade",
                                  "meta": "University | Sep 2023 - Jun 2026", "detail": ""}],
                     skills=[], additional=[], moreProjects="", summary="")
        pdf = self.render(short)
        self.assertLess(len(cvscore.pdf_text(pdf).split()), 250)
        mark = cvscore.score(short, pdf)
        readable = next(c for g in mark["groups"] for c in g["checks"] if c["name"] == "Text can be read back")
        self.assertEqual((readable["score"], mark["capped"]), (1.0, []))

    def test_a_labelled_line_is_judged_by_its_verb(self):
        self.assertTrue(cvscore.strong_start("Dissertation, A Title: tested whether models segment images"))
        self.assertTrue(cvscore.strong_start("Built a pipeline over four datasets: results cached by config"))
        self.assertFalse(cvscore.strong_start("Responsible for: testing"))

    def test_evidence_the_cv_does_not_hold_counts_as_missing(self):
        job = {"title": "Engineer", "company": "Acme", "description": "You have strong Python. A 2:1 degree is required."}
        replies = iter([{"requirements": [
                            {"need": "Python", "kind": "must", "weight": 3, "quote": "You have strong Python."},
                            {"need": "Degree", "kind": "knockout", "weight": 3, "quote": "A 2:1 degree is required"},
                            {"need": "Rust", "kind": "must", "weight": 2, "quote": "made up by the model"}]},
                        {"evidence": [{"id": 1, "match": "direct", "quote": "Built a parser in Python"},
                                      {"id": 2, "match": "direct", "quote": "a 2:1 in Physics"}]}])
        saved = cvscore.llm.chat_json, cvscore.gpu.busy
        cvscore.llm.chat_json, cvscore.gpu.busy = lambda *a, **k: next(replies), lambda: None
        try:
            out = cvscore.match(job, "- Built a parser in Python.\nDegree, Grade")
        finally:
            cvscore.llm.chat_json, cvscore.gpu.busy = saved
        # The requirement the advert never stated is dropped; the degree evidence is not in the CV.
        self.assertEqual([r["need"] for r in out["requirements"]], ["Python", "Degree"])
        self.assertEqual([r["match"] for r in out["requirements"]], ["direct", "missing"])
        self.assertEqual((out["label"], out["knockouts_unmet"]), ("Long shot", ["Degree"]))

    def test_evidence_found_only_in_the_record_is_listed_but_not_counted(self):
        """A requirement the record meets and the CV does not is reported as in the record, and lowers coverage."""
        job = {"title": "Research engineer", "company": "Acme",
               "description": "You have strong Python. Research ethics training is required."}
        replies = iter([{"requirements": [
                            {"need": "Python", "kind": "must", "weight": 3, "quote": "You have strong Python."},
                            {"need": "Ethics training", "kind": "knockout", "weight": 3,
                             "quote": "Research ethics training is required"}]},
                        {"evidence": [{"id": 1, "match": "direct", "quote": "Built a parser in Python"},
                                      {"id": 2, "match": "direct",
                                       "quote": "Human-subjects research training, completed 5 March 2026"}]}])
        saved = cvscore.llm.chat_json, cvscore.gpu.busy, cvscore.record.context
        cvscore.llm.chat_json, cvscore.gpu.busy = lambda *a, **k: next(replies), lambda: None
        cvscore.record.context = lambda queries, **k: ("[Certificates/CITI.md > CITI]\n"
                                                       "Human-subjects research training, completed 5 March 2026.")
        try:
            out = cvscore.match(job, "- Built a parser in Python.")
        finally:
            cvscore.llm.chat_json, cvscore.gpu.busy, cvscore.record.context = saved
        self.assertEqual([r["where"] for r in out["requirements"]], ["cv", "record"])
        self.assertEqual((out["in_record"], out["knockouts_unmet"]), (["Ethics training"], ["Ethics training"]))
        self.assertEqual(out["coverage"], 50)


if __name__ == "__main__":
    unittest.main()
