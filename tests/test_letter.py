"""Cover letters drawn in the CV's style, with the header of the CV sent with them."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_pdfread import PEOPLE, Person, draw, made_up_cvs

from jobhunt import cv, letter
from jobhunt.cvstudio import pdfread

BODY = "Dear Hiring Team,\n\nI catalogued 400 items for a record office.\n\nKind regards,\nZo\u00eb M\u00fcller"


class Letters(unittest.TestCase):
    def header(self, pdf: Path) -> list[str]:
        return [line.strip() for line in pdfread.text(pdf).splitlines()[:4]]

    def test_the_letter_header_matches_the_cv_sent_with_it(self):
        sent = made_up_cvs()[1]
        sent["title"] = "History graduate - archives and collections"
        cv_pdf = draw(self, sent, PEOPLE[1])
        out = Path(tempfile.mkdtemp()) / "letter.pdf"
        # The main CV differs from the one sent: the letter must follow the one sent.
        with mock.patch.object(cv, "load", lambda: made_up_cvs()[0]):
            letter.render(BODY, out, ["Made-up Record Office", "York"], "Archive assistant",
                          when=date(2026, 10, 22), cv_sent=sent)
        self.assertEqual(self.header(out), self.header(cv_pdf))
        self.assertIn("22 October 2026", pdfread.text(out))

    def test_without_a_cv_the_header_is_the_main_cvs(self):
        Person(self, PEOPLE[0])
        out = Path(tempfile.mkdtemp()) / "letter.pdf"
        with mock.patch.object(cv, "load", lambda: cv.printable(made_up_cvs()[0])):
            letter.render(BODY, out, ["Made-up Ltd"], "Graduate developer")
        self.assertEqual(self.header(out)[:2], ["Robin Testwell", "Graduate"])

    def test_a_letter_longer_than_a_page_is_refused(self):
        Person(self, PEOPLE[2])
        with self.assertRaisesRegex(ValueError, "shorten it to one"):
            letter.render("\n\n".join(["A paragraph of the letter that goes on."] * 80),
                          Path(tempfile.mkdtemp()) / "l.pdf", [], "Too long", cv_sent=made_up_cvs()[2])


if __name__ == "__main__":
    unittest.main()
