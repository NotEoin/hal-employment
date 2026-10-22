"""Hal's own PDF writer: accented names, the contact line, and text the fonts cannot draw."""
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_pdfread import PEOPLE, Person, draw, made_up_cvs

from jobhunt import cvpdf, pdfdraw
from jobhunt.cvstudio import pdfread


class Drawing(unittest.TestCase):
    def test_accented_names_are_kept_in_the_pdf(self):
        pdf = draw(self, made_up_cvs()[1], PEOPLE[1])
        self.assertTrue(pdfread.text(pdf).startswith("Zo\u00eb M\u00fcller"))
        # The file name employers see is plain letters, for sites that mangle accents.
        self.assertTrue(pdf.name.isascii())

    def test_the_contact_line_shows_only_the_links_given(self):
        Person(self, PEOPLE[1])
        line = "".join(text for text, _ in pdfdraw.contact_line({"location": "York", "linkedin": "linkedin.com/in/z"}))
        self.assertEqual(line, "zoe.muller@example.org | 07700 900002 | linkedin.com/in/z")
        Person(self, ("Sam Placeholder", "", ""))
        self.assertEqual(pdfdraw.contact_line({}), [])

    def test_typographic_characters_are_drawn_in_their_plain_forms(self):
        data = made_up_cvs()[2]
        data["summary"] = "Reliable \u2014 \u201cfriendly\u201d \u2026 \u2713"
        text = pdfread.text(draw(self, data, PEOPLE[2]))
        self.assertIn('Reliable, "friendly" ...', text)
        self.assertNotIn("\u2713", text)

    def test_a_cv_runs_to_a_second_page_with_the_name_in_its_footer(self):
        data = made_up_cvs()[0]
        data["experience"] = data["experience"] * 14
        pdf = draw(self, data)
        self.assertEqual(pdfread.pages(pdf), 2)
        self.assertIn("Robin Testwell - CV", pdfread.text(pdf).split("\f")[1])

    def test_text_the_fonts_cannot_draw_is_refused_with_its_place(self):
        with self.assertRaisesRegex(ValueError, r"U\+4E2D\) at cv.json.summary"):
            pdfdraw.check_ascii({"summary": "\u4e2d"})
        self.assertEqual(cvpdf._drawable({"a": ["\u4e2dx"]}), {"a": ["x"]})

    def test_the_writer_needs_no_folder_of_its_own(self):
        out = Path(tempfile.mkdtemp()) / "nested" / "cv.pdf"
        out.parent.mkdir()
        Person(self, PEOPLE[0])
        pdfdraw.OUT = out
        pdfdraw.write_pdf(pdfdraw.Doc().finish())
        self.assertEqual(pdfread.pages(out), 1)


if __name__ == "__main__":
    unittest.main()
