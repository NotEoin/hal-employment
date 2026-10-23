"""Exports of Hal's copy of the CV: PDF, Word, plain text, JSON Resume and the website's cv.json,
each downloaded through `/cv/export`, each opened again, each named by the `cvdocs.file_name` setting.

Without `HAL_TEST_BROWSER=1` the PDF comes from Hal's simple printer, so the default run starts no browser.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import people
from support.api import HalServer
from support.ui import browser_tests_on
from test_cv_printer import NoBrowsersHere
from test_cv_render import made_up_cv, profile_of, type_for

from jobhunt.cvstudio import export, pdfread, printer, render, store

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@contextlib.contextmanager
def printing():
    """Real printing with browser tests on; otherwise Hal's simple printer."""
    if browser_tests_on():
        yield
        return
    with mock.patch.object(printer, "_has_playwright", lambda: False), \
            mock.patch.object(printer, "_platform", NoBrowsersHere):
        yield


def docx_text(data: bytes) -> tuple[str, list[str]]:
    """The Word file's words and the paragraph styles it uses."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        self_check = z.testzip()
        if self_check is not None:
            raise AssertionError(f"broken member {self_check}")
        root = ElementTree.fromstring(z.read("word/document.xml"))
        ElementTree.fromstring(z.read("word/styles.xml"))
        ElementTree.fromstring(z.read("word/numbering.xml"))
    words = " ".join(t.text or "" for t in root.iter(f"{W}t"))
    styles = [s.get(f"{W}val") for s in root.iter(f"{W}pStyle")]
    return words, styles


class Exports(unittest.TestCase):
    def test_every_export_opens(self):
        person = people.HUMANITIES
        with HalServer() as hal, people.as_person(person), printing():
            store.new_version(made_up_cv(person.key), origin="imported", what="Made-up CV", by="baseline")
            client = hal.signed_in()
            got = {}
            for fmt in export.FORMATS:
                reply = client.get(f"/api/v2/cv/export?format={fmt}")
                self.assertEqual(reply.status, 200, f"{fmt}: {reply.text[:200]}")
                self.assertIn("attachment", reply.headers["Content-Disposition"])
                got[fmt] = reply
        name = person.you["name"]

        pdf = Path(tempfile.mkdtemp()) / "cv.pdf"
        pdf.write_bytes(got["pdf"].body)
        self.assertGreaterEqual(pdfread.pages(pdf), 1)
        self.assertIn("".join(name.split()), "".join(pdfread.text(pdf).split()))
        self.assertIn(f'filename="{name} CV.pdf"', got["pdf"].headers["Content-Disposition"])

        words, styles = docx_text(got["docx"].body)
        self.assertIn(name, words)
        self.assertIn("Collections volunteer", words)
        self.assertTrue({"Heading1", "ListBullet"} <= set(styles))
        self.assertIn(f'filename="{name} CV.docx"', got["docx"].headers["Content-Disposition"])

        self.assertIn(name, got["txt"].body.decode("utf-8"))

        resume = got["jsonresume"].json()
        self.assertEqual(resume["basics"]["name"], name)
        self.assertEqual(resume["basics"]["location"], {"city": person.you["city"], "countryCode": "GB"})
        self.assertEqual(resume["work"][0]["startDate"], "2023-09")
        self.assertNotIn("endDate", resume["work"][0])            # "Present"
        self.assertEqual(resume["education"][0]["studyType"], "BA (Hons) History and Imaginary Studies")

        self.assertEqual(got["website"].json()["title"], made_up_cv(person.key).headline)
        for body in (got["jsonresume"].body, got["website"].body):
            body.decode("ascii")                                 # JSON keeps its ASCII escapes

    def test_the_word_file_holds_the_same_words_in_the_same_order_as_the_page(self):
        person = people.TECHNICAL
        doc, type_ = made_up_cv(person.key), type_for(person.key)
        view = render.view(doc, type_, profile_of(person))
        words, _ = docx_text(export.to_docx(view))
        titles = [s.title for s in view.sections]
        self.assertEqual(sorted(titles, key=words.index), titles)
        self.assertEqual(export.to_docx(view), export.to_docx(view))   # the same CV gives the same bytes

    def test_word_files_open_in_a_document_reader(self):
        try:
            from markitdown import MarkItDown
        except ImportError:
            self.skipTest("markitdown is in the documents extra")
        person = people.CHANGER
        data = export.to_docx(render.view(made_up_cv(person.key), type_for(person.key), profile_of(person)))
        path = Path(tempfile.mkdtemp()) / "cv.docx"
        path.write_bytes(data)
        text = MarkItDown().convert(str(path)).text_content
        self.assertIn("Priya Ashdown-Vell", text)
        self.assertIn("Planned three refits", text)

    def test_accents_are_kept_in_the_file_and_stripped_only_from_its_name(self):
        person = people.HUMANITIES
        accented = {**person.you, "name": "Zo\u00eb M\u00fcller"}
        with HalServer() as hal, people.as_person(people.Person(person.key, accented, person.goal, person.leaks)):
            store.new_version(made_up_cv(person.key), origin="imported", what="Made-up CV", by="baseline")
            reply = hal.signed_in().get("/api/v2/cv/export?format=txt")
        self.assertIn('filename="Zoe Muller CV.txt"', reply.headers["Content-Disposition"])
        self.assertIn("Zo\u00eb M\u00fcller", reply.body.decode("utf-8"))

    def test_an_unknown_format_is_refused_in_plain_words(self):
        with HalServer() as hal:
            store.new_version(made_up_cv("technical_grad"), origin="imported", what="Made-up CV", by="baseline")
            reply = hal.signed_in().get("/api/v2/cv/export?format=odt")
        self.assertEqual(reply.status, 400)
        self.assertIn("cannot export a CV as 'odt'", reply.json()["error"]["message"])

    def test_the_looks_route_lists_the_gallery_and_saves_a_choice(self):
        with HalServer() as hal:
            client = hal.signed_in()
            looks = client.get("/api/v2/cv/looks").json()
            self.assertEqual([x["id"] for x in looks], ["classic", "modern", "compact", "two"])
            self.assertEqual([x["chosen"] for x in looks], [True, False, False, False])
            chosen = client.request("PUT", "/api/v2/cv/look", {"id": "compact"})
            self.assertEqual(chosen.status, 200)
            # the reply is My CV's header, and the look shows by name, not by id
            self.assertEqual(chosen.json()["look"], {"id": "compact", "name": "Compact"})
            self.assertEqual(client.get("/api/v2/cv").json()["look"]["name"], "Compact")
            self.assertEqual(store.doc()["look_id"], "compact")
            self.assertTrue(client.get("/api/v2/cv/looks").json()[2]["chosen"])
            bad = client.request("PUT", "/api/v2/cv/look", {"id": "glitter"})
            self.assertEqual((bad.status, bad.json()["error"]["field"]), (400, "id"))


if __name__ == "__main__":
    unittest.main()
