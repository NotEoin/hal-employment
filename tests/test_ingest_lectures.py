"""Course material as lecture notes: slides written again as study notes, contact details taken out, slides
the person's own notes cover left unfiled, and one course note per module code.

Every file, module and lecturer here is made up.
"""
import json
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import fake_ai
from test_ingest import NOTE_REPLY, OWNER, home, put, rows, run

from jobhunt.core import settings
from jobhunt.ingest import engine, frontmatter, lectures

SLIDES = ("GEO101 Lecture 3: Rivers\n\nDr Ama Pell, ama.pell@example.org, 0191 208 6000, Room 4.005\n\n"
          "Meanders form where the outer bank erodes and the inner bank gains sediment.")
LECTURE_NOTE = ("# GEO101 Rivers\n\n## Meanders\n\nA meander forms where the outer bank erodes faster than "
                "the inner bank, which gains the sediment the river drops. Ask ama.pell@example.org.\n\n"
                "## Oxbow lakes\n\nWhen the neck of a meander is cut through, the old loop is left as a lake "
                "beside the new, straighter channel.")
TAUGHT = {**NOTE_REPLY, "doc_type": "slides", "category": "Slides", "role": "taught", "title": "Rivers"}


class Scrub(unittest.TestCase):
    def test_emails_and_phone_numbers_go_and_figures_stay(self):
        text = ("Contact a.pell@example.org or +44 191 208 6000 or 0191 208 6000. "
                "Pi is 3.14159; the 2024-25 module; samples 1 2 3 4 5; ISBN 978-0-00-000000-0.")
        out = lectures.scrub(text)
        self.assertNotIn("@", out)
        self.assertNotIn("208 6000", out)
        for kept in ("3.14159", "2024-25", "1 2 3 4 5"):
            self.assertIn(kept, out)

    def test_titles_of_one_lecture_match_and_others_do_not(self):
        self.assertTrue(lectures.same_lecture("Lecture 3 - Rivers", "Rivers notes"))
        self.assertTrue(lectures.same_lecture("4 Hash-2024", "Hash functions and MACs week 4"))
        self.assertFalse(lectures.same_lecture("Lecture 3 - Rivers", "Lecture 4 - Coasts"))
        self.assertFalse(lectures.same_lecture("Lecture 3", "Lecture 3 notes"), "no subject words, no match")


class LectureNotes(unittest.TestCase):
    def test_slides_become_a_lecture_note_with_no_contact_details(self):
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(TAUGHT),
                                                     "lecture_note": LECTURE_NOTE}) as backend, \
                mock.patch.object(engine.convert, "text_of", return_value=SLIDES):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            put(files, "GEO101/Lecture 3 slides.pdf", b"%PDF-1.4 made up")
            run(con, [files])
            row = rows(con)["Files/GEO101/Lecture 3 slides.pdf"]
            fm, body = frontmatter.parse((vault / row["note"]).read_text(encoding="utf-8"))
            copies = list((vault / "Attachments").rglob("*.pdf")) if (vault / "Attachments").exists() else []
        self.assertEqual(len(backend.requests("lecture_note")), 1)
        self.assertEqual(fm["role"], "taught")
        self.assertIn("## Oxbow lakes", body)
        self.assertNotIn("Room 4.005", body, "the raw slide words are replaced by the note")
        self.assertNotIn("@example.org", body)
        self.assertEqual(copies, [], "no copy of the deck, which still holds the contact details")

    def test_without_a_lecture_note_the_slide_words_are_kept_without_contact_details(self):
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(TAUGHT)}), \
                mock.patch.object(engine.convert, "text_of", return_value=SLIDES):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            put(files, "GEO101/Lecture 3 slides.pdf", b"%PDF-1.4 made up")
            run(con, [files])
            body = (vault / rows(con)["Files/GEO101/Lecture 3 slides.pdf"]["note"]).read_text(encoding="utf-8")
        self.assertIn("outer bank erodes", body)
        self.assertNotIn("ama.pell@example.org", body)
        self.assertNotIn("208 6000", body)

    def test_slides_the_persons_notes_cover_get_no_note_of_their_own(self):
        with home() as (con, vault, files), fake_ai({}) as backend, \
                mock.patch.object(engine.convert, "text_of", side_effect=lambda p, r: p.read_text()):
            settings.set("you.name", OWNER)
            put(files, "Study/GEO101/Rivers notes.md", "# Rivers\n\nMeanders and oxbow lakes, in my words.")
            put(files, "Study/GEO101/Coasts notes.md", "# Coasts\n\nLongshore drift.")
            put(files, "Study/GEO101/Glaciers notes.md", "# Glaciers\n\nCirques.")
            put(files, "Canvas/GEO101 Rivers and Coasts/Lecture 3 - Rivers slides.pdf", SLIDES)
            run(con, [files])
            got = rows(con)
            slides = got["Files/Canvas/GEO101 Rivers and Coasts/Lecture 3 - Rivers slides.pdf"]
            mine = (vault / got["Files/Study/GEO101/Rivers notes.md"]["note"]).read_text(encoding="utf-8")
        self.assertEqual(slides["state"], "covered")
        self.assertEqual(slides["note"], got["Files/Study/GEO101/Rivers notes.md"]["note"])
        self.assertIn("## Also at", mine)
        self.assertIn("Lecture 3 - Rivers slides.pdf", mine)
        self.assertEqual(backend.requests("ingest_doc"), [])

    def test_one_module_in_two_folders_has_one_course_note(self):
        with home() as (con, vault, files), fake_ai({}):
            for folder in ("2023/GEO101 Rivers", "Old laptop/GEO101"):
                for name in ("week 1 notes.md", "week 2 notes.md", "week 3 notes.md"):
                    put(files, f"{folder}/{name}", f"# {folder} {name}\n\nMeanders, {name}.")
            run(con, [files])
            units = [r for r in rows(con).values() if r["kind"] == "course"]
            text = (vault / units[0]["note"]).read_text(encoding="utf-8")
        self.assertEqual(len(units), 2)
        self.assertEqual(len({u["note"] for u in units}), 1)
        files = text.split("## Files", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(files.count("- [["), 6, "both folders' files are listed in the one note")


if __name__ == "__main__":
    unittest.main()
