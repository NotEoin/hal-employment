"""Hal's base CV: edits, rearranged skills and each application's own copy.

Moved by class from tests/test_apply.py; the test bodies are unchanged.
"""
import json
import tempfile
import unittest
from pathlib import Path

from support import legacy

from jobhunt import cv, documents

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class BaseCv(unittest.TestCase):
    """Hal's base CV: the website's, with the applicant's changes laid over it."""

    SITE = {"title": "Graduate", "summary": "Builds things.", "moreProjects": "", "closing": "",
            "education": [{"qualification": "BSc", "meta": "City", "detail": "New dissertation wording.",
                           "modules": [{"year": "Final", "rows": [["Security", 80]]}]}],
            "projects": [{"name": "Hal", "stack": "Python", "body": "An assistant."},
                         {"name": "Hatch", "stack": "C#", "body": "A mood tracker."}],
            "experience": [], "skills": [["Languages", ["Python", "C++"]]], "additional": [["Interests", "Running"]]}

    def setUp(self):
        self.site = json.loads(json.dumps(self.SITE))
        saved = cv.site, cv.OVERRIDES, cv.ORIGIN
        def restore():
            cv.site, cv.OVERRIDES, cv.ORIGIN = saved
        self.addCleanup(restore)
        cv.site = lambda: json.loads(json.dumps(self.site))
        tmp = Path(tempfile.mkdtemp())
        cv.OVERRIDES, cv.ORIGIN = tmp / "cv-overrides.json", tmp / "cv-origin"

    def test_an_edit_reaches_hals_cv_and_survives_the_website_changing(self):
        mine = cv.for_editing()
        mine["education"][0]["detail"] = "Old dissertation wording."
        mine["projects"][1]["hidden"] = True
        mine["skills"][0][1] = ["Python"]
        self.assertEqual(len(cv.save_base(mine)), 3)
        # The website later adds a project and rewords its summary; Hal's CV takes both, and keeps its own changes.
        self.site["projects"].insert(0, {"name": "Lidar", "stack": "C++", "body": "A car."})
        self.site["summary"] = "Builds careful things."
        base = cv.load()
        self.assertEqual(base["education"][0]["detail"], "Old dissertation wording.")
        self.assertEqual(base["education"][0]["modules"], self.SITE["education"][0]["modules"])
        self.assertEqual([p["name"] for p in base["projects"]], ["Lidar", "Hal"])
        self.assertEqual((base["skills"][0][1], base["summary"]), (["Python"], "Builds careful things."))
        self.assertNotIn("_key", base["projects"][0])

    def test_skill_groups_can_be_rearranged(self):
        mine = cv.for_editing()
        mine["skills"] = [["Languages", ["Python"]], ["Tools", ["Git"]]]
        cv.save_base(mine)
        self.site["skills"].append(["Web", ["React"]])
        self.assertEqual(cv.load()["skills"], [["Languages", ["Python"]], ["Tools", ["Git"]]])

    def test_a_renamed_entry_is_still_matched_to_the_websites(self):
        mine = cv.for_editing()
        mine["projects"][0]["name"] = "Hal, a local assistant"
        cv.save_base(mine)
        self.assertEqual([p["name"] for p in cv.load()["projects"]], ["Hal, a local assistant", "Hatch"])

    def test_an_application_keeps_its_own_edit_of_the_cv(self):
        job = {"title": "Engineer", "company": "Acme", "location": "", "salary": "", "description": "Python"}
        self.assertEqual(documents.current_cv(job, {})["education"][0]["detail"], "New dissertation wording.")
        mine = {"cv": {**cv.load(), "summary": "For Acme."}}
        self.assertEqual(documents.current_cv(job, mine)["summary"], "For Acme.")

    def test_hals_changes_to_an_applications_cv_are_listed_against_the_base(self):
        base = cv.load()
        variant = json.loads(json.dumps(base))
        variant["title"] = "Graduate - security"
        variant["projects"].reverse()
        variant["projects"][0]["body"] = "A careful assistant."
        variant["skills"] = []
        found = {(c["path"], c["kind"]) for c in cv.changes(base, variant)}
        self.assertEqual(found, {("title", "text"), ("projects", "order"), ("projects.0.body", "text"),
                                 ("skills", "left out")})
        self.assertEqual(next(c for c in cv.changes(base, variant) if c["path"] == "title")["before"], "Graduate")
        self.assertEqual(cv.changes(base, base), [])


if __name__ == "__main__":
    unittest.main()
