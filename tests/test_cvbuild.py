"""Building a CV from the applicant's own material.

Moved by class from tests/test_apply.py. The applicant and their material are made up; the AI is the fake
one, answering by purpose, and the record is a folder of its own with a private folder in it.
"""
import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from support import legacy
from support.fake_ai import fake_ai
from test_pdfread import PEOPLE, Person

from jobhunt import cv, cvbuild, record
from jobhunt.core import naming

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class BuildingCv(unittest.TestCase):
    """A CV written from the applicant's material, in the shape Hal's PDF layout takes."""

    FACTS: ClassVar[dict] = {"education": [{"qualification": "Degree",
                                            "institution": "Made-up University",
                            "dates": "2023 - 2026", "grade": "First", "points": ["Dissertation on timetables"],
                            "modules": [{"year": "Final year", "mark": 75,
                                         "rows": [["Cryptography", 80], ["Compilers", "n/a"], ["Security", 79]]}]}],
             "projects": [{"name": "Timetable reader", "stack": "Python", "points": ["Reads bus timetables"]}]}
    DRAFT: ClassVar[dict] = {"title": "Graduate", "summary": "A recent graduate.",
             "education": [{"qualification": "Degree", "grade": "Grade",
                            "meta": "Made-up University | 2023 - 2026",
                            "detail": "**Dissertation:** timetables \u2014 12 layouts."}],
             "projects": [{"name": "Timetable reader - bus times", "stack": "Python",
                           "body": "**Reader:** parses timetables."},
                          {"name": "Chess clock", "stack": "C#", "body": "A clock."}],
             "skills": [["Languages", ["Python", ""]]], "experience": [{"role": "Tutor", "org": "", "dates": "", "body": "x"}],
             "additional": [["Interests", "Running"]], "moreProjects": "", "closing": ""}

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        Person(self, PEOPLE[0])
        for name, value in (("SOURCES", tmp / "sources"), ("PREVIEW", tmp / "preview")):
            patch = mock.patch.object(cvbuild, name, value, create=True)
            patch.start()
            self.addCleanup(patch.stop)
        for p in (mock.patch.object(cv, "BUILT", tmp / "cv-built.json", create=True),
                  mock.patch.object(record, "VAULT", tmp / "record", create=True),
                  mock.patch.object(record, "INDEX", tmp / "record-index.json", create=True)):
            p.start()
            self.addCleanup(p.stop)
        self.replies = {"cv_extract": json.dumps(self.FACTS), "cv_compose": json.dumps(self.DRAFT),
                        "verify": json.dumps({"unsupported": ["tutoring experience"]})}

    def test_material_is_kept_as_plain_text(self):
        name = cvbuild.add("Dissertation summary", "I studied timetables.")
        self.assertEqual(name, "dissertation-summary")
        self.assertEqual(cvbuild.add("notes.md", data=b"More notes"), "notes")
        self.assertEqual([x["name"] for x in cvbuild.sources()], ["notes", "dissertation-summary"])
        cvbuild.remove("notes")
        self.assertEqual(len(cvbuild.sources()), 1)

    def test_builds_a_cv_the_pdf_builder_accepts_and_lists_what_to_check(self):
        cvbuild.add("transcript", "Cryptography 80, Security 79")
        with fake_ai(self.replies) as ai:
            out = cvbuild.build({"website": False, "record": False, "projects": 1, "modules_min": 0})
        built = out["cv"]
        # Marks come from the material as it stands; a mark that is not a number is dropped.
        self.assertEqual(built["education"][0]["modules"][0]["rows"], [["Cryptography", 80], ["Security", 79]])
        self.assertEqual([p["slug"] for p in built["projects"]], ["timetable-reader-bus-times"])
        self.assertEqual((built["experience"], built["skills"]), ([], [["Languages", ["Python"]]]))
        self.assertEqual(built["closing"], "References available on request.")
        self.assertNotIn("\u2014", built["education"][0]["detail"])
        self.assertIn("a number no source states: 12", out["flags"])
        self.assertIn("not backed by your material: tutoring experience", out["flags"])
        self.assertTrue((cvbuild.PREVIEW / naming.file_name("cv")).exists())
        self.assertEqual(json.loads(cv.BUILT.read_text())["cv"], built)
        self.assertEqual([r.purpose for r in ai.log], ["cv_extract", "cv_compose", "verify"])

    def test_a_cv_without_module_marks_builds(self):
        """Many people have no transcript to give: the CV is built without a marks line."""
        cvbuild.add("notes", "I volunteered at an archive.")
        self.replies["cv_extract"] = json.dumps({"education": [{"qualification": "BA History", "modules": []}]})
        with fake_ai(self.replies), mock.patch.object(cv, "site", cv.starter):
            built = cvbuild.build({"website": False, "record": False})["cv"]
        self.assertNotIn("modules", built["education"][0])
        self.assertTrue((cvbuild.PREVIEW / naming.file_name("cv")).exists())

    def test_notes_in_a_private_folder_are_never_read_for_the_cv(self):
        for rel, text in (("Jobs/Archive.md", "# Archive\n\nCatalogued 400 items."),
                          ("Private/Health.md", "# Health\n\nA private note for the canary test.")):
            path = record.VAULT / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        with fake_ai(self.replies) as ai:
            cvbuild.build({"website": False, "record": True})
        sent = "\n".join(r.user for r in ai.log)
        self.assertIn("Catalogued 400 items", sent)
        self.assertNotIn("canary", sent)

    def test_nothing_is_built_without_material(self):
        with fake_ai(self.replies), self.assertRaisesRegex(RuntimeError, "add some material"):
            cvbuild.build({"website": False, "record": False})


if __name__ == "__main__":
    unittest.main()
