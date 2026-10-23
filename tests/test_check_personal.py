"""The personal-string scan (scripts/dev/check_personal.py): whole words only, and the list is never printed.

The list here is made up; no real personal string appears in a test.
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - must come before jobhunt

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "dev"
sys.path.insert(0, str(SCRIPTS))
import check_personal  # noqa: E402 - needs the path above


def listed(*words: str):
    """A temporary list file holding `words`, one per line; returns the compiled pattern."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "strings.txt"
        path.write_text("\n".join(words) + "\n", encoding="utf-8")
        return check_personal.patterns(path)


class WholeWords(unittest.TestCase):
    """A listed string matches as a word, not inside a longer one."""

    def test_the_name_alone_is_a_hit(self):
        self.assertTrue(listed("Sam").search("Prepared by Sam, 2026"))

    def test_a_public_handle_that_contains_the_name_is_not_a_hit(self):
        # a public handle may end in the listed first name
        pattern = listed("Sam")
        self.assertIsNone(pattern.search("Copyright (c) 2026 NotSam"))
        self.assertIsNone(pattern.search("https://github.com/NotSam/project"))

    def test_the_match_ignores_case(self):
        self.assertTrue(listed("Sam").search("SAM"))

    def test_a_string_with_spaces_and_dots_matches_literally(self):
        pattern = listed("12 Acacia Road", "a.b")
        self.assertTrue(pattern.search("lives at 12 Acacia Road, Town"))
        self.assertIsNone(pattern.search("axb"))

    def test_an_empty_list_has_no_pattern(self):
        self.assertIsNone(listed("# only a comment"))


class Report(unittest.TestCase):
    """The report names places, never strings."""

    def test_the_report_has_no_listed_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strings.txt"
            path.write_text("Zebulon\n", encoding="utf-8")
            rows = {"a.txt": [(3, "met Zebulon today"), (4, "nothing")]}
            out = io.StringIO()
            real = check_personal.tree_lines
            check_personal.tree_lines = lambda: rows
            try:
                with contextlib.redirect_stdout(out):
                    code = check_personal.main(["--tree", "--list", str(path)])
            finally:
                check_personal.tree_lines = real
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue().strip(), "a.txt:3")
        self.assertNotIn("Zebulon", out.getvalue())


if __name__ == "__main__":
    unittest.main()
