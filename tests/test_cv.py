"""Hal's base CV through the cv.json-shaped functions over the CV store: where it starts, each application's
own copy, and what an application's copy changes. The people and places are made up.
"""
import json
import unittest
from unittest import mock

from support import legacy
from test_cv_import import lay_out
from test_cv_store import cv_home

from jobhunt import cv, documents
from jobhunt.core import settings
from jobhunt.cvstudio import import_v1

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule

SITE = {"title": "Graduate", "summary": "Builds things.", "moreProjects": "", "closing": "", "contact": {},
        "education": [{"qualification": "BSc", "meta": "Made-up University", "detail": "New dissertation wording.",
                       "modules": [{"year": "Final", "rows": [["Security", 80]]}]}],
        "projects": [{"name": "Hal", "stack": "Python", "body": "An assistant."},
                     {"name": "Hatch", "stack": "C#", "body": "A mood tracker."}],
        "experience": [], "skills": [["Languages", ["Python", "C++"]]], "additional": [["Interests", "Running"]]}


class BaseCv(unittest.TestCase):
    """Hal's base CV: imported once from the website's, then changed only by the person."""

    def setUp(self):
        self.enterContext(cv_home())
        lay_out(import_v1.folder(), site=SITE, overrides=[], snapshots=False)

    def test_the_website_changing_later_changes_nothing(self):
        cv.load()
        (import_v1.folder() / "site-cv.json").write_text(json.dumps({**SITE, "summary": "Changed on the site."}))
        self.assertEqual(cv.load()["summary"], "Builds things.")

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
        title = next(c for c in cv.changes(base, variant) if c["path"] == "title")
        self.assertEqual((title["before"], title["after"]), ("Graduate", "Graduate - security"))
        # the application page said only "(changed)", never the new wording
        body = next(c for c in cv.changes(base, variant) if c["path"] == "projects.0.body")
        self.assertEqual(body["after"], "A careful assistant.")
        self.assertEqual(cv.changes(base, base), [])

    def test_an_undone_change_is_put_back_in_the_cv_that_is_sent(self):
        # Undo on the application page was recorded but never applied: the PDF kept Hal's change
        base = cv.load()
        variant = json.loads(json.dumps(base))
        variant["title"] = "Graduate - security"
        variant["projects"].reverse()
        variant["projects"][0]["body"] = "A careful assistant."
        variant["skills"] = []
        ids = {(c["path"], c["kind"]): c["id"] for c in cv.changes(base, variant)}
        sent = cv.revert(base, variant, [ids[("title", "text")], ids[("projects.0.body", "text")]])
        self.assertEqual(sent["title"], base["title"])
        self.assertEqual(sent["projects"][-1]["body"], base["projects"][0]["body"])
        self.assertEqual(sent["skills"], [], "a change still on stays")
        sent = cv.revert(base, variant, [ids[("projects", "order")], ids[("skills", "left out")]])
        self.assertEqual([p["name"] for p in sent["projects"]], [p["name"] for p in base["projects"]])
        self.assertEqual([s[0] for s in sent["skills"]], [s[0] for s in base["skills"]])
        self.assertEqual(cv.revert(base, variant, []), variant)


class StartingCv(unittest.TestCase):
    """With no CV to import, Hal's main CV starts from a starter made from the settings."""

    def test_the_starter_comes_from_your_settings(self):
        values = {"you.education.degree": "BA (Hons) History", "you.education.university": "Made-up University",
                  "you.city": "York", "you.links.linkedin": "https://www.linkedin.com/in/zoe-example/",
                  "goal.study_end": "2025"}
        with mock.patch.object(settings, "get", lambda key: values.get(key, "")):
            first = cv.starter()
        self.assertEqual(first["education"][0]["meta"], "Made-up University | 2025")
        self.assertEqual(first["contact"], {"location": "York", "linkedin": "linkedin.com/in/zoe-example"})
        self.assertNotIn("github", first["contact"])

    def test_the_right_to_work_reaches_drafts_in_plain_words_never_a_settings_label(self):
        # a letter said "as a British or Irish citizen", the form wording of the settings, for a person who is a
        # UK and Irish citizen (2026-11-07)
        values = {"you.right_to_work": "uk_no_visa", "you.work_status": "British or Irish citizen",
                  "you.nationality": "UK and Irish"}
        with mock.patch.object(settings, "get", lambda key: values.get(key, "")):
            line = cv.status_line()
        self.assertEqual(line, "Right to work: I have the right to work in the UK. Nationality: UK and Irish.")

    def test_the_cv_facts_say_nothing_the_settings_do_not(self):
        text = cv.facts_text_of(cv.printable(SITE))
        self.assertNotIn("Graduated", text)
        self.assertNotIn("citizen", text)


if __name__ == "__main__":
    unittest.main()
