"""Critique as a recruiter: the CV and letter of one application read against its advert, with grades per
criterion, line comments that quote real words, the requirements mapped to evidence and the top three fixes.

The made-up humanities graduate of `support.people` applies for the made-up collections job; the recruiter's
read comes from the fake AI.
"""
import json
import unittest
from unittest import mock

from support import people
from support.fake_ai import fake_ai
from test_pack import FACTS, add_job, as_applicant

from jobhunt import cv, documents, pack
from jobhunt.ai.base import LLMUnavailable
from jobhunt.writing import critique

CV_READ = {"screen": {"who": "A history graduate", "wants": "collections work", "read_on": True,
                      "why": "The dissertation is near the top."},
           "requirements": [{"requirement": "Writing reports", "evidence": "Dissertation on river trade",
                             "strength": 4},
                            {"requirement": "Answering enquiries", "evidence": "Ran a made-up help desk",
                             "strength": 5}],
           "summary": "Your dissertation is the strongest thing. Say what came of your guiding.",
           "grades": [{"criterion": "Impact", "grade": 2, "why": "Duties, not results.", "fix": "Say how many "
                       "visitors you guided.", "needs_fact": True},
                      {"criterion": "Fit to advert", "grade": 9, "why": "Reports are shown.", "fix": ""}],
           "lines": [{"quote": "Volunteer guide", "comment": "Say what came of it."},
                     {"quote": "Words that are not in the CV", "comment": "Dropped."}],
           "top_fixes": ["Say how many visitors you guided.", "Put the dissertation first."]}
LETTER_READ = {"requirements": [], "summary": "A short letter that names the dissertation.",
               "grades": [{"criterion": "Why them", "grade": 2, "why": "Nothing about the Trust.",
                           "fix": "Name one thing from the advert about the Trust."}],
               "lines": [{"quote": "I would bring", "comment": "Start with the role."}],
               "top_fixes": ["Name one thing from the advert about the Trust."]}


def applied(person=people.HUMANITIES):
    """A made-up world with `person`'s pack drafted for their made-up job."""
    return as_applicant(person)


class Critique(unittest.TestCase):
    def read(self, replies: dict) -> tuple[dict, list]:
        with applied() as a, fake_ai(replies) as backend, \
                mock.patch.object(cv, "facts_text_of", lambda d: FACTS["humanities_grad"]):
            job = add_job(a.con, "humanities_grad", status="applying")
            pack.build(a.con, job["id"], use_web=False)
            out = critique.critique(a.con, job)
            letter = documents.load(a.con, job)["letter"]
        return out, backend.log, letter

    def test_a_critique_grades_each_document_quotes_real_lines_and_names_three_fixes(self):
        out, _log, letter = self.read({"cv_judge": json.dumps(CV_READ), "letter_judge": json.dumps(LETTER_READ)})
        self.assertEqual([d["document"] for d in out["documents"]], ["cv", "letter"])
        cv_doc, letter_doc = out["documents"]
        self.assertEqual([(g["criterion"], g["grade"]) for g in cv_doc["grades"]],
                         [("Impact", 2), ("Fit to advert", 5)], "a grade outside 1 to 5 is brought inside")
        self.assertTrue(cv_doc["grades"][0]["needs_fact"])
        self.assertEqual([x["quote"] for x in cv_doc["lines"] if x["by"] == "ai"], ["Volunteer guide"],
                         "a quote that is not in the CV is dropped")
        self.assertIn("I would bring", letter)
        self.assertEqual(letter_doc["grades"][0]["criterion"], "Why them")
        self.assertEqual(len(out["top_fixes"]), critique.TOP_FIXES)
        self.assertEqual(out["top_fixes"][:2], ["Say how many visitors you guided.",
                                                "Name one thing from the advert about the Trust."])
        self.assertTrue(out["screen"]["read_on"])
        self.assertEqual([(r["requirement"], r["evidence"]) for r in out["requirements"]],
                         [("Writing reports", "Dissertation on river trade"), ("Answering enquiries", "")],
                         "evidence the CV does not hold counts as none")
        self.assertEqual(out["field"], "Humanities, heritage and the written word")
        self.assertTrue(out["ai"])

    def test_the_recruiter_reads_the_top_of_the_cv_apart_and_never_sees_your_name(self):
        _out, log, _letter = self.read({"cv_judge": json.dumps(CV_READ), "letter_judge": json.dumps(LETTER_READ)})
        cv_request = next(r for r in log if r.purpose == "cv_judge")
        self.assertIn("<<<TOP\n", cv_request.user)
        self.assertIn("<<<ADVERT\n", cv_request.user)
        self.assertIn("Collections Assistant at Riverside Heritage Trust", cv_request.user)
        for r in log:
            if r.purpose in ("cv_judge", "letter_judge"):
                for name in people.HUMANITIES.names:
                    self.assertNotIn(name, r.user)
        self.assertIn("Never comment on age", " ".join(cv_request.system.split()))
        self.assertIn("Fit to advert", cv_request.system)
        self.assertIn("Voice", cv_request.system)

    def test_without_an_ai_the_checkers_comments_still_come_with_a_note(self):
        out, _log, _letter = self.read({"cv_judge": LLMUnavailable("The made-up AI is switched off."),
                                        "letter_judge": LLMUnavailable("The made-up AI is switched off.")})
        self.assertFalse(out["ai"])
        self.assertIn("The made-up AI is switched off.", out["note"])
        self.assertTrue(all(d["grades"] == [] for d in out["documents"]))

    def test_the_checkers_findings_are_line_comments_with_their_quotes(self):
        hits = critique._checker_lines("I am a passionate team player.", "letter")
        self.assertEqual([(x["quote"], x["by"]) for x in hits][:2], [("passionate", "rules"), ("team player", "rules")])

    def test_your_name_and_contact_details_are_replaced_before_an_ai_reads_them(self):
        profile = {"name": "Marnie Quillfeather", "email": "marnie.quillfeather@example.org",
                   "phone": "07700 900461", "city": "Harrowgate Vale"}
        text = critique.anonymised("Marnie Quillfeather, Harrowgate Vale, 07700 900461, "
                                   "marnie.quillfeather@example.org. Marnie guided visitors.", profile)
        self.assertEqual(text, "[name], [city], [phone], [email]. [name] guided visitors.")


if __name__ == "__main__":
    unittest.main()
