"""Reading PDFs with pypdfium2: text, pages, type size, last-page fill and page pictures.

The made-up CVs here are drawn with Hal's own PDF writer. Where poppler is installed, pdfread is compared
with it on each of them, as `scripts/dev/compare_pdf_readers.py` does on real application PDFs.
"""
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import cv, cvscore, pdfdraw
from jobhunt.cvstudio import pdfread

POPPLER_TIMEOUT_S = 30
WORD_TOLERANCE = 0.02      # the same words within 2% of characters
SIZE_TOLERANCE = 0.5       # body size within half a point
FILL_TOLERANCE = 0.05      # last-page fill within 0.05

# Three made-up people, each with a different kind of CV.
PEOPLE = [
    ("Robin Testwell", "robin.testwell@example.org", "07700 900001"),
    ("Zo\u00eb M\u00fcller", "zoe.muller@example.org", "07700 900002"),
    ("Sam Placeholder", "sam.placeholder@example.org", "07700 900003"),
]


def made_up_cvs() -> list[dict]:
    """Three CVs for invented people: software, history and a school leaver with no degree."""
    software = {
        "title": "Graduate", "summary": "Builds small tools and tests them well.",
        "contact": {"location": "Leeds", "github": "github.com/robin-example",
                    "linkedin": "linkedin.com/in/robin-example"},
        "education": [{"qualification": "Degree", "grade": "Grade",
                       "meta": "Made-up University | Sep 2022 - Jun 2025",
                       "detail": "- Wrote a dissertation on parsing timetables, marked 78.",
                       "modules": [{"year": "Final year",
                                    "rows": [["Compilers", 81], ["Databases", 74], ["Networks", 66]]}]}],
        "projects": [{"name": "Timetable reader - parses bus timetables", "stack": "Python, SQLite",
                      "body": "- Built a parser for 40 timetable layouts, with 120 tests.\n"
                              "- Cut parse time from 9 s to 2 s."}],
        "experience": [{"role": "Tutor", "org": "Made-up Learning", "dates": "Sep 2023 - Jun 2025",
                        "body": "- Taught 12 pupils Python each week, with 3 reaching a top grade."}],
        "skills": [["Languages", ["Python", "SQL"]], ["Tools", ["Git", "Docker"]]],
        "additional": [["Interests", "Fell running and chess"]], "moreProjects": "", "closing": ""}
    history = {
        "title": "History graduate", "summary": "Catalogues archives and writes clearly for the public.",
        "contact": {"location": "York", "linkedin": "linkedin.com/in/zoe-example"},
        "education": [{"qualification": "BA (Hons) History", "grade": "2:1",
                       "meta": "Made-up University | Sep 2021 - Jun 2024",
                       "detail": "- Wrote a dissertation on council housing in the 1950s, marked 72."}],
        "projects": [], "skills": [["Research", ["Archives", "Cataloguing", "Oral history"]]],
        "experience": [{"role": "Archive volunteer", "org": "Made-up Record Office", "dates": "Sep 2023 - Present",
                        "body": "- Catalogued 400 items from a parish collection.\n- Gave 15 tours to school groups."}],
        "additional": [], "moreProjects": "", "closing": ""}
    school = {
        "title": "School leaver seeking an apprenticeship", "summary": "",
        "contact": {"location": "Hull"},
        "education": [{"qualification": "A levels", "grade": "B, B, C",
                       "meta": "Made-up Sixth Form College | Sep 2023 - Jun 2025",
                       "detail": "- Studied Maths, Physics and Business."}],
        "projects": [], "skills": [["Skills", ["Customer service", "Excel"]]],
        "experience": [{"role": "Shop assistant", "org": "Made-up Grocers", "dates": "Jun 2024 - Present",
                        "body": "- Served about 200 customers each Saturday.\n- Trained 2 new starters on the till."}],
        "additional": [], "moreProjects": "", "closing": ""}
    return [software, history, school]


class Person:
    """Draws PDFs as one made-up person, by assigning the identity pdfdraw would read from the settings."""

    def __init__(self, test: unittest.TestCase, who: tuple[str, str, str]):
        test.addCleanup(self.restore)
        pdfdraw.NAME, pdfdraw.EMAIL, pdfdraw.PHONE = who

    @staticmethod
    def restore():
        for name in ("NAME", "EMAIL", "PHONE"):
            vars(pdfdraw).pop(name, None)


def draw(test: unittest.TestCase, data: dict, who=PEOPLE[0]) -> Path:
    """`data` drawn as a CV PDF in a fresh folder, as `who`."""
    Person(test, who)
    return cv.render(data, Path(tempfile.mkdtemp()))


def _poppler(*args: str) -> str:
    return subprocess.run(["pdftotext", *args], capture_output=True, text=True, timeout=POPPLER_TIMEOUT_S).stdout


class Reading(unittest.TestCase):
    def test_the_text_pages_and_body_size_of_a_drawn_cv_read_back(self):
        pdf = draw(self, made_up_cvs()[0])
        text = pdfread.text(pdf)
        self.assertIn("Robin Testwell", text)
        self.assertIn("- Built a parser for 40 timetable layouts, with 120 tests.", text)
        self.assertEqual((pdfread.pages(pdf), pdfread.body_size(pdf)), (1, 10.0))
        self.assertTrue(0.1 < pdfread.last_page_fill(pdf) < 1.0)

    def test_side_by_side_text_keeps_its_gap_when_laid_out(self):
        doc = pdfdraw.Doc()
        doc.text("Compilers", pdfdraw.ML, 700, pdfdraw.F_REG, 10)
        doc.text("81", pdfdraw.ML + 300, 700, pdfdraw.F_REG, 10)
        out = Path(tempfile.mkdtemp()) / "table.pdf"
        Person(self, PEOPLE[0])
        pdfdraw.OUT = out
        pdfdraw.write_pdf(doc.finish())
        self.assertRegex(pdfread.text(out, layout=True), r"Compilers {6,}81")

    def test_a_file_that_is_not_a_pdf_reads_as_empty(self):
        broken = Path(tempfile.mkdtemp()) / "image.pdf"
        broken.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.assertEqual((pdfread.text(broken), pdfread.pages(broken), pdfread.body_size(broken),
                          pdfread.last_page_fill(broken)), ("", 0, None, 0.0))

    def test_a_page_picture_is_a_png_of_the_page(self):
        pdf = draw(self, made_up_cvs()[1], PEOPLE[1])
        png = pdfread.page_png(pdf, 1, scale=0.5)
        self.assertTrue(png.startswith(pdfread.PNG_SIGNATURE))
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (298, 421))   # A4 at 36 dots per inch
        start = png.index(b"IDAT") + 4
        raw = zlib.decompress(png[start:start + struct.unpack(">I", png[start - 8:start - 4])[0]])
        self.assertEqual(len(raw), height * (1 + width * 3))
        with self.assertRaises(IndexError):
            pdfread.page_png(pdf, 2)

    @unittest.skipUnless(shutil.which("pdftotext") and shutil.which("pdfinfo"), "poppler is not installed")
    def test_pdf_reading_matches_poppler_on_the_made_up_cvs(self):
        for who, data in zip(PEOPLE, made_up_cvs(), strict=True):
            with self.subTest(person=who[0]):
                pdf = draw(self, data, who)
                ours = pdfread.text(pdf).split()
                theirs = _poppler(str(pdf), "-").split()
                differ = sum(1 for a, b in zip(ours, theirs, strict=False) if a != b) + abs(len(ours) - len(theirs))
                self.assertLessEqual(differ, WORD_TOLERANCE * max(1, len(theirs)))
                info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True,
                                      timeout=POPPLER_TIMEOUT_S).stdout
                self.assertEqual(pdfread.pages(pdf), int(re.search(r"Pages:\s+(\d+)", info).group(1)))
                self.assertAlmostEqual(pdfread.body_size(pdf), 10.0, delta=SIZE_TOLERANCE)
                bbox = _poppler("-bbox", str(pdf), "-")
                height = float(re.search(r'<page width="[\d.]+" height="([\d.]+)"', bbox).group(1))
                tops = [float(y) for y in re.findall(r'<word[^>]*yMax="([\d.]+)"', bbox)]
                fill = max(t for t in tops if t < height - 40) / (height - 80)
                self.assertAlmostEqual(pdfread.last_page_fill(pdf), fill, delta=FILL_TOLERANCE)


class Checks(unittest.TestCase):
    def test_cv_checks_need_no_poppler(self):
        """The CV checks read the PDF themselves: with no poppler on the path the mark is unchanged."""
        pdf = draw(self, made_up_cvs()[0])
        with_path = cvscore.score(made_up_cvs()[0], pdf)
        empty = tempfile.mkdtemp()
        from unittest import mock
        with mock.patch.dict("os.environ", {"PATH": empty}), \
                mock.patch.object(cvscore.subprocess, "run", side_effect=FileNotFoundError("no programs")):
            without = cvscore.score(made_up_cvs()[0], pdf)
        self.assertEqual(without["groups"][0], with_path["groups"][0])     # parsing reads the same text
        self.assertGreater(without["mark"], 50)
        readable = next(c for c in without["groups"][0]["checks"] if c["name"] == "Text can be read back")
        self.assertEqual(readable["score"], 1.0)


if __name__ == "__main__":
    unittest.main()
