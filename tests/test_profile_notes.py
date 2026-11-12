"""The Profile notes in the record folder: dashboards Obsidian shows and search leaves out.

Each test runs in its own temporary home. The person is the made-up humanities graduate of
support/people.py; her files, employers and modules are made up.
"""
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.profiles import CV, LETTER, NOTES, TRANSCRIPT, ev, humanities_profile

from jobhunt import db, profile, record
from jobhunt.core import paths
from jobhunt.profile import store

NOW = "2026-11-05T09:00:00Z"


class Notes(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.folder = self.home / "Record"

    def test_the_profile_notes_are_dashboards_without_hidden_items(self):
        with closing(db.connect()) as con:
            humanities_profile(con)
            store.add_found(con, "job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust",
                                         "start": "October 2023", "current": True},
                            ev(LETTER, "letter", "since October 2023"))
            written = profile.write_notes(self.folder, con)
        names = sorted(p.name for p in written)
        self.assertEqual(names, ["Education.md", "Inconsistencies.md", "Profile.md", "Projects.md", "Skills.md",
                                 "Work history.md"])
        for path in written:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.name):
                self.assertFalse(record.should_index(text), "search and the AI skip the profile notes")
                self.assertNotIn("Paper round", text)
                self.assertTrue(text.isascii())
        work = (self.folder / "Profile" / "Work history.md").read_text(encoding="utf-8")
        self.assertIn("## Collections volunteer, Fenwick Lantern Museum Trust", work)
        self.assertIn(f"- From:\n  - [[{CV[:-3]}]]", work)
        education = (self.folder / "Profile" / "Education.md").read_text(encoding="utf-8")
        self.assertIn("| Early Modern Letters | 72 |", education)
        self.assertIn("Archival research", (self.folder / "Profile" / "Skills.md").read_text(encoding="utf-8"))
        self.assertIn("October 2023", (self.folder / "Profile" / "Inconsistencies.md").read_text(encoding="utf-8"))

    def test_the_module_table_has_one_row_per_module_code_and_no_lecture_titles(self):
        degree = {"qualification": "BA History", "institution": "University of Wexley", "period": "2022-25"}
        with closing(db.connect()) as con:
            store.add_found(con, "education", {**degree, "modules": [
                {"name": "HIS2010", "mark": "72"}, {"name": "Early Modern Letters (HIS2010)", "mark": ""}]},
                ev(TRANSCRIPT, "transcript", "HIS2010 72"))
            store.add_found(con, "education", {**degree, "modules": [{"name": "Week 3 lecture: parish registers"}]},
                            ev(NOTES, "notes", "lecture notes"))
            profile.write_notes(self.folder, con)
        education = (self.folder / "Profile" / "Education.md").read_text(encoding="utf-8")
        self.assertIn("| Early Modern Letters (HIS2010) | 72 |", education)
        self.assertEqual(education.count("| HIS2010"), 0)
        self.assertNotIn("Week 3", education)

    def test_notes_whose_text_is_the_same_are_not_written_again(self):
        with closing(db.connect()) as con:
            store.add_found(con, "skill", {"name": "Cataloguing"}, ev(CV, "cv", "catalogued 400 letters"))
            self.assertEqual(len(profile.write_notes(self.folder, con)), 6)
            self.assertEqual(profile.write_notes(self.folder, con), [])
            store.add_found(con, "skill", {"name": "Transcription"}, ev(CV, "cv", "transcribed a diary"))
            self.assertEqual([p.name for p in profile.write_notes(self.folder, con)], ["Profile.md", "Skills.md"])


if __name__ == "__main__":
    unittest.main()
