"""Readers of a section's sources: the generic document reader's title, date and text, and the stubs that say
which phase brings them. Every file is made up."""
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.sections import readers


def source(*files: tuple[str, str]) -> tuple[readers.Source, tempfile.TemporaryDirectory]:
    """A document source holding `files` [(name, text)], kept in a temporary folder."""
    tmp = tempfile.TemporaryDirectory()
    kept = []
    for name, text in files:
        path = Path(tmp.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        kept.append(readers.SourceFile(name, path))
    return readers.Source(1, "document", "other", "", "upload", files=tuple(kept)), tmp


class Readers(unittest.TestCase):
    def test_the_document_reader_takes_the_heading_and_first_date_and_no_facts(self):
        src, tmp = source(("first-aid_cert.md", "# First aid at work\n\nCompleted on 12th Jan. 2024 by Avery Quillon."))
        self.addCleanup(tmp.cleanup)
        got = readers.get("document").read(src)
        self.assertEqual((got.title, got.date, got.facts), ("First aid at work", "2024-01", []))
        self.assertIn("Avery Quillon", got.text)

    def test_a_date_of_birth_is_never_a_documents_date(self):
        """Regression: three transcripts were dated by the date of birth at their top (2026-11-12)."""
        for text in ("Date of Birth   6th October 2002\nStudied From September 2023 To June 2026",
                     "DOB: 06/10/2002\nIssued September 2023", "Born 6 October 2002. Awarded September 2023."):
            with self.subTest(text=text):
                self.assertEqual(readers.date_in(text), "2023-09")

    def test_a_document_with_no_heading_is_named_by_its_file_and_one_with_no_date_has_none(self):
        src, tmp = source(("dbs_check-letter.txt", "A made-up basic check, no date given."))
        self.addCleanup(tmp.cleanup)
        got = readers.get("document").read(src)
        self.assertEqual((got.title, got.date), ("dbs check letter", ""))

    def test_several_files_are_read_under_their_own_headings(self):
        src, tmp = source(("Awards/a.md", "One award, 2025-06-01."), ("Awards/b.md", "Another award."))
        self.addCleanup(tmp.cleanup)
        got = readers.get("document").read(src)
        self.assertIn("### Awards/a.md", got.text)
        self.assertIn("### Awards/b.md", got.text)
        self.assertEqual(got.date, "2025-06")

    def test_a_reader_not_written_yet_says_which_phase_brings_it(self):
        src, tmp = source(("cv.md", "# CV"))
        self.addCleanup(tmp.cleanup)
        self.addCleanup(readers.register, readers.register(readers.Stub("cv", 2)))
        with self.assertRaises(readers.NotReadYet) as caught:
            readers.get("cv").read(src)
        self.assertEqual((caught.exception.reader, caught.exception.phase), ("cv", 2))
        self.assertIsInstance(caught.exception, NotImplementedError)
        self.assertEqual((readers.ready("cv"), readers.ready("document")), (False, True))

    def test_a_later_phase_puts_its_reader_in_place_and_can_be_put_back(self):
        made = readers.Stub("repo", 99)
        before = readers.register(made)
        self.addCleanup(readers.register, before)
        self.assertIs(readers.get("repo"), made)
        with self.assertRaises(LookupError):
            readers.get("telepathy")


if __name__ == "__main__":
    unittest.main()
