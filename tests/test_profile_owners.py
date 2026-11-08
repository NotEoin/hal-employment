"""Which card each file speaks for: files that list projects and files that are one project's own.

Each test runs in its own temporary home. The projects and files are made up.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import db
from jobhunt.core import paths
from jobhunt.profile import owners, store

NOW = "2026-11-08T09:00:00Z"


def own(rel: str, doc_type: str = "repository") -> dict:
    """Evidence from one of the person's own made-up files."""
    return {"source_rel": rel, "quote": "", "field": "", "doc_type": doc_type, "role": "own"}


class Owners(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def add(self, name: str, rel: str, doc_type: str = "repository", **fields) -> int:
        return store.add_found(self.con, "project", {"name": name, **fields}, own(rel, doc_type))

    def test_names_match_with_case_and_separators_aside(self):
        self.assertEqual(owners.key("quill-employment"), owners.key("Quill_Employment"))
        self.assertEqual(owners.file_name("Sources/Code/quill-employment/README.md"), "quill-employment")
        self.assertEqual(owners.file_name("Sources/Code/Tide gauge.md"), "Tide gauge")

    def test_a_repository_named_as_a_card_is_that_cards_own(self):
        quill = self.add("Quill", "Sources/Code/quill/README.md")
        later = self.add("Quill Employment", "Sources/Code/later/README.md",
                         link="https://example.org/avery/quill-employment")
        self.add("Quill", "Sources/Code/quill-employment/README.md")
        view = owners.Files.of(self.con)
        self.assertEqual(view.owners["Sources/Code/quill-employment/README.md"], later, "by the end of its link")
        self.assertFalse(view.belongs(quill, "Sources/Code/quill-employment/README.md"))
        self.assertTrue(view.belongs(quill, "Sources/Code/quill/README.md"))
        self.assertNotIn("Sources/Code/quill-employment/README.md", view.listings,
                         "a file backing two cards that is one's own is no listing")

    def test_only_a_whole_name_owns_a_file(self):
        # a dossier named "Portfolio ..." is no more the "Portfolio" card's than any other's
        self.add("Portfolio", "Sources/Code/site/README.md")
        self.add("Tide gauge", "Sources/Code/tide/README.md")
        for name in ("Portfolio", "Tide gauge"):
            self.add(name, "Sources/Projects/Portfolio overview.md", "project")
        view = owners.Files.of(self.con)
        self.assertNotIn("Sources/Projects/Portfolio overview.md", view.owners)
        self.assertIn("Sources/Projects/Portfolio overview.md", view.listings)


if __name__ == "__main__":
    unittest.main()
