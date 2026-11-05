"""Tests of the employment record: documents turned into notes, notes indexed and searched.

Each test builds its own record in a temporary folder, with its own index file, so nothing here reads the
person's real record. The people, places and certificates are made up. No test needs poppler or the AI:
search by words is pure Python, and search by meaning uses a stand-in AI with a bag-of-words embedding.
"""
import json
import re
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import FakeBackend, fake_ai

from jobhunt import ai, record
from jobhunt.cvstudio import pdfread

DIM = 512  # wide enough that the few words in a test rarely share a slot
CANARY = "CANARY-7f3a private note"


class WordVectors(FakeBackend):
    """The AI on this computer, embedding by words: one slot per word, so passages sharing words score highest."""

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        self.embedded = getattr(self, "embedded", []) + list(texts)
        out = []
        for text in texts:
            vec = [0.0] * DIM
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vec[zlib.crc32(word.encode()) % DIM] += 1.0
            out.append(vec)
        return out


def tiny_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with each line of text set in Helvetica, built by hand so no PDF library is needed."""
    text = "".join(f"BT /F1 11 Tf 40 {760 - 16 * i} Td ({line}) Tj ET\n" for i, line in enumerate(lines)).encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(text) + text + b"endstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


class Record(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp()) / "Employment Record"
        self.vault.mkdir()
        self.settings = {"record.search": "words", "record.private_folders": ["Private"], "record.ocr": True}
        for p in (mock.patch.object(record, "VAULT", self.vault, create=True),
                  mock.patch.object(record, "INDEX", self.vault.parent / "record-index.json", create=True),
                  mock.patch.object(record, "_MARKITDOWN", record._MARKITDOWN),
                  mock.patch.object(record, "_setting", self.setting)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(record._CACHE.clear)
        record._CACHE.clear()

    def setting(self, key, legacy_key=None, default=None):
        return self.settings.get(key, default)

    def note(self, rel: str, text: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_dropped_document_becomes_a_note_that_keeps_its_headings(self):
        self.note("Awards/prize.html", "<html><body><h1>Prizes</h1><h2>Dean's list</h2>"
                                       "<p>Named on the School of History dean's list for 2025.</p></body></html>")
        out = record.sync()
        self.assertEqual(out["converted"], ["Awards/prize (HTML).md"])
        text = (self.vault / "Awards/prize (HTML).md").read_text(encoding="utf-8")
        self.assertIn('source: "[[prize.html]]"', text)
        self.assertIn("## Dean's list", text)
        hit = record.search("dean's list 2025", min_score=0)[0]
        self.assertEqual((hit["path"], hit["heading"]), ("Awards/prize (HTML).md", "Dean's list"))
        # A second sync leaves the note alone, since the document has not changed.
        self.assertEqual(record.sync()["converted"], [])

    def test_a_pdf_table_is_read_in_columns_not_as_a_guessed_markdown_table(self):
        """A transcript's credits and mark stay apart: MarkItDown's guessed table ran them into one cell ("20 68")."""
        (self.vault / "transcript.pdf").write_bytes(tiny_pdf(["Code     Module Title          Credits  Mark",
                                                              "HIS3021  Cold War Diplomacy    20       68"]))
        guessed = "| HIS3021 Cold War Diplomacy | 20 68 |\n| --- | --- |\n"
        record._MARKITDOWN = type("Guess", (), {"convert_local": lambda self, p: type("R", (), {"markdown": guessed})()})()
        record.convert()
        text = (self.vault / "transcript (PDF).md").read_text(encoding="utf-8")
        self.assertNotIn("| 20 68 |", text)
        self.assertRegex(text, r"Cold War Diplomacy +20 +68")

    def test_a_word_document_is_read_without_markitdown(self):
        """Without MarkItDown (no C++ runtime on the laptop) a Word file is read from its XML, headings kept."""
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        xml = (f'<w:document {w}><w:body><w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
               '<w:r><w:t>First Aid</w:t></w:r>'
               '</w:p><w:p><w:r><w:t>Emergency First Aid at Work, St John Ambulance, March 2025.</w:t></w:r></w:p>'
               '</w:body></w:document>')
        with zipfile.ZipFile(self.vault / "certs.docx", "w") as z:
            z.writestr("word/document.xml", xml)
        record._MARKITDOWN = type("Broken", (), {"convert_local": lambda self, p: 1 / 0})()
        record.sync()
        self.assertIn("## First Aid", (self.vault / "certs (DOCX).md").read_text(encoding="utf-8"))
        self.assertEqual(record.search("first aid certificate", min_score=0.3)[0]["heading"], "First Aid")

    @unittest.skipUnless(record._tesseract(), "tesseract is not installed")
    def test_a_scanned_certificate_is_read_by_ocr(self):
        """A photo of a certificate has no text layer, so its words come from OCR."""
        image = self.vault / "Certificates" / "licence.png"
        image.parent.mkdir()
        image.write_bytes(pdfread.page_png(tiny_pdf(["Award for Personal Licence Holders", "Completed 2023"]), 1, 3.0))
        record.convert()
        self.assertIn("Personal Licence Holders",
                      (self.vault / "Certificates" / "licence (PNG).md").read_text(encoding="utf-8"))

    def test_each_heading_keeps_only_its_own_text(self):
        """One year's module marks never appear in a passage filed under another year's heading."""
        self.note("Education/BA.md", "# BA\n\n## Second year\n\n" + "Tudor England 70, Medieval Europe 68. " * 20
                  + "\n\n## Final year\n\nCold War diplomacy 80, Dissertation 75, Empire and memory 72.\n")
        record.sync()
        hits = record.search("cold war diplomacy mark", k=5, min_score=0)
        self.assertEqual(hits[0]["heading"], "Final year")
        self.assertNotIn("Tudor", hits[0]["text"])
        self.assertFalse([h for h in hits if h["heading"] == "Second year" and "Cold War" in h["text"]])

    def test_a_changed_note_is_read_again_and_a_deleted_one_dropped(self):
        self.note("Notes/a.md", "# A\n\nWorked the late bar shift at the Made-up Arms on weekends.\n")
        gone = self.note("Notes/b.md", "# B\n\nCaptained the university chess team for two seasons.\n")
        self.assertEqual(record.sync()["changed"], 2)
        self.note("Notes/a.md", "# A\n\nTrained three new bar staff at the Made-up Arms.\n")
        gone.unlink()
        out = record.sync()
        self.assertEqual((out["changed"], out["removed"], out["notes"]), (1, 1, 1))
        self.assertIn("Trained three", record.search("bar staff", min_score=0)[0]["text"])
        self.assertFalse(record.search("chess team"))

    def test_a_note_removed_while_the_record_is_read_is_left_out(self):
        """A tidy deleted a note between the walk and the read, and the sync failed (2026-11-05)."""
        self.note("Notes/a.md", "# A\n\nWorked the late bar shift at the Made-up Arms on weekends.\n")
        listed = [*record._walk((".md",)), self.vault / "Notes" / "gone.md"]
        with mock.patch.object(record, "_walk", return_value=listed):
            out = record.sync()
        self.assertEqual((out["notes"], out["changed"]), (1, 1))

    def test_word_forms_match_and_off_subject_passages_are_left_out(self):
        """"Archival cataloguing" finds a note on cataloguing archives; a passage sharing only "experience" does not."""
        self.note("Jobs/Museum.md", "# Museum volunteer\n\n## Archives\n\nCatalogued the parish archives, 400 items, "
                                    "for the county record office in 2024.\n\n## Visitors\n\nGave tours to school "
                                    "groups and gained experience of public speaking.\n")
        record.sync()
        hits = record.search("experience of archival cataloguing")
        self.assertEqual([h["heading"] for h in hits], ["Archives"])
        self.assertEqual(record.search("forklift licence"), [])

    def test_an_index_note_is_not_searched(self):
        self.note("Start Here.md", "---\ntype: index\n---\n# Start here\n\nDrop certificates into Certificates, transcripts "
                                   "into Education and anything else into the Inbox; certificates are read too.\n")
        self.note("Certificates/First Aid.md", "# First Aid\n\nEmergency first aid at work certificates, March 2025.\n")
        record.sync()
        self.assertEqual({h["path"] for h in record.search("certificates", k=5, min_score=0)},
                         {"Certificates/First Aid.md"})

    def test_context_names_each_passage_and_is_empty_when_the_record_cannot_be_read(self):
        self.note("Certificates/First Aid.md", "# First Aid\n\nEmergency first aid at work, completed 5 March 2025.\n")
        record.sync()
        found = record.context(["emergency first aid at work completed"])
        self.assertTrue(found.startswith("[Certificates/First Aid.md > First Aid]\nEmergency first aid"))
        Path(record.INDEX).write_text("not json", encoding="utf-8")
        record._CACHE.clear()
        self.assertEqual(record.context(["first aid"]), "")

    def test_a_missing_record_is_reported_not_created(self):
        record.VAULT = self.vault.parent / "nowhere"
        with self.assertRaisesRegex(record.RecordUnavailable, "no employment record"):
            record.sync()
        self.assertEqual((record.status()["exists"], record.status()["notes"]), (False, 0))
        self.assertFalse(record.VAULT.exists())

    def test_record_search_works_without_ollama(self):
        """Search by words needs no AI at all: with the AI switched off it still finds the passage."""
        self.note("Certificates/First Aid.md", "# First Aid\n\nEmergency first aid at work, completed 5 March 2025.\n")
        off = FakeBackend()
        off.ready = False
        with fake_ai() as be, ai.use(off):
            record.sync()
            hits = record.search("first aid at work")
        self.assertEqual(hits[0]["path"], "Certificates/First Aid.md")
        self.assertEqual(hits[0]["by"], "words")
        self.assertEqual(be.log, [])

    def test_search_by_meaning_is_used_when_ollama_answers(self):
        self.settings["record.search"] = "meaning"
        self.note("Jobs/Museum.md", "# Museum volunteer\n\n## Archives\n\nCatalogued the parish archives, 400 items, "
                                    "for the county record office in 2024.\n")
        with ai.use(WordVectors()):
            self.assertEqual(record.sync()["passages"], 1)
            hits = record.search("parish archives catalogued county record office")
        self.assertEqual((hits[0]["heading"], hits[0]["by"]), ("Archives", "meaning"))
        # When the AI stops answering, the same search falls back to words.
        off = WordVectors()
        off.embed = lambda texts, model: (_ for _ in ()).throw(ai.LLMUnavailable("stopped"))
        with ai.use(off):
            record._CACHE.clear()
            self.assertEqual(record.search("parish archives")[0]["by"], "words")

    def test_private_record_folders_never_reach_the_ai(self):
        self.settings["record.search"] = "meaning"
        self.note("Private/Health.md", f"# Health\n\nAdjustments at interview: {CANARY}, first aid at work.\n")
        self.note("Certificates/First Aid.md", "# First Aid\n\nEmergency first aid at work, completed 5 March 2025.\n")
        replies = {"record_ask": "You completed first aid at work on 5 March 2025 [Certificates/First Aid.md]."}
        with ai.use(WordVectors(replies)) as be:
            record.sync()
            answer = record.ask("When did I do first aid at work?")
            context = record.context(["first aid at work adjustments"])
        sent = "\n".join([r.system + r.user for r in be.log] + getattr(be, "embedded", []))
        self.assertNotIn(CANARY, sent)
        self.assertNotIn(CANARY, context)
        self.assertEqual(answer["sources"], ["Certificates/First Aid.md"])
        self.assertNotIn("Private/Health.md", [rel for rel, _ in record.notes_for_ai()])
        # Your own search on this computer still finds it, and the page marks it private.
        self.assertTrue(record.search(CANARY, include_private=True))
        self.assertEqual({f["path"]: f["private"] for f in record.files()},
                         {"Private/Health.md": True, "Certificates/First Aid.md": False})

    def test_a_file_outside_the_record_is_never_handed_out(self):
        self.note("Notes/a.md", "# A\n\nA note.\n")
        self.assertEqual(record.file_path("Notes/a.md"), (self.vault / "Notes/a.md").resolve())
        for bad in ("../record-index.json", "Notes/missing.md", "/etc/hostname"):
            with self.assertRaises(LookupError):
                record.file_path(bad)

    def test_the_index_says_when_the_record_was_last_read(self):
        self.note("Notes/a.md", "# A\n\nWorked the late bar shift at a made-up inn on weekends.\n")
        with mock.patch.dict("os.environ", {"HAL_EMPLOYMENT_FAKE_NOW": "2026-10-22T09:00:00Z"}):
            record.sync()
        self.assertEqual(record.status()["read"], "2026-10-22T09:00:00Z")
        self.assertEqual(json.loads(Path(record.INDEX).read_text())["read"], "2026-10-22T09:00:00Z")


class Health(unittest.TestCase):
    """Regression: the record check was a plain dict, so "Is Hal working?" showed it as a broken check."""

    def test_the_record_folder_check_runs_on_is_hal_working(self):
        from jobhunt.core import health

        missing = Path(tempfile.mkdtemp()) / "no record here"
        with mock.patch.object(record, "VAULT", missing, create=True):
            rows = {row["id"]: row for row in health.run_all()}
        self.assertEqual(rows["record"]["text"], "Your record folder is not there yet.")
        self.assertEqual(rows["record"]["fix"], {"label": "Make the folder", "action": None, "route": "record"})



class EvidenceWeight(unittest.TestCase):
    def test_a_cv_outranks_a_lecture_note_that_matches_the_words_better(self):
        # a trivia quiz in the study notes outranked the person's CVs as a letter's evidence (2026-11-05)
        hits = [[{"path": "Sources/Study notes/quiz.md", "heading": "Text", "text": "Quiz on customers.", "score": 1.0},
                 {"path": "Sources/CVs and letters/CV.md", "heading": "Experience", "text": "Served customers.",
                  "score": 0.8},
                 {"path": "My notes/Volunteering.md", "heading": "Museum", "text": "Ran tours.", "score": 0.7}]]
        with mock.patch.object(record, "search_many", return_value=hits):
            out = record.context(["customer service"], per_query=2)
        self.assertEqual(re.findall(r"^\[(.*?) >", out, re.M),
                         ["Sources/CVs and letters/CV.md", "My notes/Volunteering.md"])

if __name__ == "__main__":
    unittest.main()
