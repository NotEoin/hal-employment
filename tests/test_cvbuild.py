"""Building a CV from the applicant's own material.

Moved by class from tests/test_apply.py; the test bodies are unchanged.
"""
import json
import tempfile
import unittest
from pathlib import Path

from support import legacy

from jobhunt import cv, cvbuild

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class BuildingCv(unittest.TestCase):
    """A CV written from the applicant's material, in the shape the website's PDF builder takes."""

    FACTS = {"education": [{"qualification": "Degree", "institution": "University",
                            "dates": "2023 - 2026", "grade": "First", "points": ["Dissertation on a research topic"],
                            "modules": [{"year": "Final year", "mark": 75,
                                         "rows": [["Cryptography", 80], ["Compilers", "n/a"], ["Security", 79]]}]}],
             "projects": [{"name": "Hal", "stack": "Python", "points": ["Local assistant"]}]}
    DRAFT = {"title": "Graduate", "summary": "A recent graduate.",
             "education": [{"qualification": "Degree", "grade": "Grade",
                            "meta": "University | 2023 - 2026", "detail": "**Dissertation:** a research topic."}],
             "projects": [{"name": "Hal - local assistant", "stack": "Python", "body": "**Hal:** answers from notes."},
                          {"name": "Hatch", "stack": "C#", "body": "A mood tracker."}],
             "skills": [["Languages", ["Python", ""]]], "experience": [{"role": "Tutor", "org": "", "dates": "", "body": "x"}],
             "additional": [["Interests", "Running"]], "moreProjects": "", "closing": ""}

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        saved = (cvbuild.SOURCES, cvbuild.PREVIEW, cv.BUILT, cvbuild.gpu.busy, cvbuild.llm.chat_json)
        def restore():
            cvbuild.SOURCES, cvbuild.PREVIEW, cv.BUILT, cvbuild.gpu.busy, cvbuild.llm.chat_json = saved
        self.addCleanup(restore)
        cvbuild.SOURCES, cvbuild.PREVIEW, cv.BUILT = tmp / "sources", tmp / "preview", tmp / "cv-built.json"
        cvbuild.gpu.busy = lambda: None
        self.asked = []

        def model(system, user, **kw):
            self.asked.append(system)
            if system is cvbuild.EXTRACT:
                return self.FACTS
            if system is cvbuild.COMPOSE:
                return self.DRAFT
            return {"unsupported": ["tutoring experience"]}
        cvbuild.llm.chat_json = model

    def test_material_is_kept_as_plain_text(self):
        name = cvbuild.add("Dissertation summary", "I studied a research topic.")
        self.assertEqual(name, "dissertation-summary")
        self.assertEqual(cvbuild.add("notes.md", data=b"More notes"), "notes")
        self.assertEqual([x["name"] for x in cvbuild.sources()], ["notes", "dissertation-summary"])
        cvbuild.remove("notes")
        self.assertEqual(len(cvbuild.sources()), 1)

    def test_builds_a_cv_the_pdf_builder_accepts_and_lists_what_to_check(self):
        cvbuild.add("transcript", "Cryptography 80, Security 79")
        out = cvbuild.build({"website": False, "projects": 1, "modules_min": 0})
        built = out["cv"]
        # Marks come from the material as it stands; a mark that is not a number is dropped.
        self.assertEqual(built["education"][0]["modules"][0]["rows"], [["Cryptography", 80], ["Security", 79]])
        self.assertEqual([p["slug"] for p in built["projects"]], ["hal-local-assistant"])
        self.assertEqual((built["experience"], built["skills"]), ([], [["Languages", ["Python"]]]))
        self.assertEqual(built["closing"], "References available on request.")
        self.assertNotIn("—", built["education"][0]["detail"])
        self.assertIn("a number no source states: 12", out["flags"])
        self.assertIn("not backed by your material: tutoring experience", out["flags"])
        self.assertTrue((cvbuild.PREVIEW / "Firstname-Lastname-CV.pdf").exists())
        self.assertEqual(json.loads(cv.BUILT.read_text())["cv"], built)

    def test_nothing_is_built_without_material(self):
        with self.assertRaisesRegex(RuntimeError, "add some material"):
            cvbuild.build({"website": False})


if __name__ == "__main__":
    unittest.main()
