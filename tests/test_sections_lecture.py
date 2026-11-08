"""Lecture material on a course's card: slides and notes give the topics they teach as skills studied, never as
work done or a project, with contacts scrubbed and large decks summarised. Every course, lecturer and line is
made up (`support.graduates`).
"""
import json
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import graduates
from support.fake_ai import fake_ai
from test_ingest import home

from jobhunt.ingest import lectures
from jobhunt.profile import store as profile_store
from jobhunt.sections import lecture, pipeline, store
from jobhunt.sections.pipeline import Incoming

SLIDE_TOPICS = {"topics": [{"name": "Least squares regression",
                            "quote": "Least squares finds the line that makes the squared residuals smallest"},
                           {"name": "Residual plots", "quote": "Plot the residuals against the fitted values"},
                           {"name": "Neural networks", "quote": "a deep neural network"}]}
SEMINAR_TOPICS = {"topics": [{"name": "Port books as sources", "quote": "Port books record cargoes"}]}
STUDY_NOTES = "# Made-up study notes\n\n" + "Least squares fits a line to the points by keeping residuals small. " * 6


def add(con, target: int, name: str, text: str, kind: str = "slides") -> int:
    """`text` added as lecture material of the card `target`; its source's id."""
    return pipeline.add_uploads(con, "lecture", [Incoming(name, text.encode())], doc_kind=kind,
                                target_id=target)["added"][0]


class Lecture(unittest.TestCase):
    def test_slides_give_studied_skills_and_never_a_done_skill_or_a_project(self):
        with home() as (con, vault, _), fake_ai({lecture.PURPOSE: json.dumps(SLIDE_TOPICS)}):
            bsc = graduates.education(con, "bsc")
            source_id = add(con, bsc, "comp2040-week3.md", graduates.SLIDES)
            out = pipeline.read(con, source_id)
            skills = profile_store.items(con, "skill")
            got = store.get(con, source_id)
            note = (vault / got["note_rel"]).read_text(encoding="utf-8")
            others = [profile_store.items(con, k) for k in ("project", "job", "achievement")]
            with self.assertRaises(ValueError):
                store.change(con, source_id, {"starred": True})
        self.assertEqual((out["state"], out["facts"]), ("read", 2))
        self.assertEqual(sorted(s["fields"]["name"] for s in skills), ["Least squares regression", "Residual plots"])
        self.assertTrue(all(profile_store.is_studied(s) for s in skills))
        self.assertTrue(all(e["role"] == "taught" for s in skills for e in s["evidence"]))
        self.assertEqual(others, [[], [], []])
        self.assertNotIn("ivo.pellingrove@example.org", note)
        self.assertEqual((got["title"], got["module_suggested"]), ("COMP2040 Week 3: Linear regression",
                                                                   "Data Analysis (COMP2040)"))

    def test_a_large_deck_is_summarised_in_parts_and_read_again_with_no_new_ai_call(self):
        deck = "# Week 9: Everything\n\n" + "A slide about one more idea of the course.\n" * 40
        with home() as (con, vault, _), mock.patch.object(lecture, "DECK_CHARS", 1000), \
                fake_ai({lectures.PURPOSE: STUDY_NOTES, lecture.PURPOSE: json.dumps({"topics": []})}) as backend:
            source_id = add(con, graduates.education(con, "bsc"), "week9.md", deck)
            pipeline.read(con, source_id)
            store.change(con, source_id, {"module": "Data Analysis (COMP2040)"})
            pipeline.read(con, source_id)
            note = (vault / store.get(con, source_id)["note_rel"]).read_text(encoding="utf-8")
        self.assertEqual(len(backend.requests(lectures.PURPOSE)), 2)
        self.assertEqual(len(backend.requests(lecture.PURPOSE)), 1)
        self.assertIn("Made-up study notes", note)
        self.assertNotIn("one more idea of the course", note)

    def test_without_the_ai_a_large_deck_keeps_its_first_part_and_says_the_rest_was_left_out(self):
        deck = "# Week 9: Everything\n\n" + "A slide about one more idea of the course.\n" * 40
        with home() as (con, vault, _), mock.patch.object(lecture, "DECK_CHARS", 1000):
            source_id = add(con, graduates.education(con, "bsc"), "week9.md", deck)
            out = pipeline.read(con, source_id)
            note = (vault / store.get(con, source_id)["note_rel"]).read_text(encoding="utf-8")
        self.assertEqual((out["state"], out["facts"]), ("read", 0))
        self.assertIn("was left out: it is long", note)

    def test_seminar_notes_suggest_their_module_and_give_the_topics_studied(self):
        with home() as (con, _, _), fake_ai({lecture.PURPOSE: json.dumps(SEMINAR_TOPICS)}):
            ba = graduates.education(con, "ba")
            source_id = add(con, ba, "seminar4.md", graduates.SEMINAR_NOTES, kind="notes")
            pipeline.read(con, source_id)
            got = store.get(con, source_id)
            (skill,) = profile_store.items(con, "skill")
        self.assertEqual(got["module_suggested"], "Rivers and Trade (HIST2110)")
        self.assertEqual((skill["fields"]["name"], skill["evidence"][0]["doc_type"]), ("Port books as sources",
                                                                                     "notes"))


if __name__ == "__main__":
    unittest.main()
