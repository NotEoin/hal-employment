"""How strongly each skill is shown: done in coursework, studied in a module, on a CV or in the person's own work,
with the words the skill card shows and the strengths the writer reads. The graduates' files are made up
(`support.graduates`).
"""
import json
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import graduates
from support.fake_ai import fake_ai
from test_ingest import home

from jobhunt.profile import store, strength
from jobhunt.profile import writing as profile_writing
from jobhunt.sections import coursework, lecture, pipeline
from jobhunt.sections import store as sources
from jobhunt.sections.pipeline import Incoming

ESSAY_READ = {"summary": "Reads parish registers.",
              "methods": [{"name": "Archival research", "quote": "archival research at the county record office"}]}
SEMINAR_TOPICS = {"topics": [{"name": "Port books as sources", "quote": "Port books record cargoes"},
                             {"name": "Archival research", "quote": "Port books record cargoes, masters"}]}
REPLIES = {coursework.PURPOSE: json.dumps(ESSAY_READ), lecture.PURPOSE: json.dumps(SEMINAR_TOPICS)}


def add(con, section: str, target: int, name: str, text: str, kind: str) -> int:
    """`text` added to a section of the card `target` and read; its source's id."""
    source_id = pipeline.add_uploads(con, section, [Incoming(name, text.encode())], doc_kind=kind,
                                     target_id=target)["added"][0]
    pipeline.read(con, source_id)
    return source_id


def skills(con) -> dict[str, dict]:
    """The skill cards by name."""
    return {s["fields"]["name"]: s for s in store.items(con, "skill")}


class Strength(unittest.TestCase):
    def test_each_source_of_a_skill_says_done_in_coursework_or_studied_in_its_module(self):
        with home() as (con, _, _), fake_ai(REPLIES):
            ba = graduates.education(con, "ba")
            add(con, "coursework", ba, "registers.md", graduates.ESSAY, "essay")
            seminar = add(con, "lecture", ba, "seminar4.md", graduates.SEMINAR_NOTES, "notes")
            before = skills(con)["Port books as sources"]["evidence"][0]["strength_words"]
            sources.change(con, seminar, {"module": "Rivers and Trade (HIST2110)"})
            found = skills(con)
        self.assertEqual(before, "Studied in BA History")
        self.assertEqual([(e["strength"], e["strength_words"]) for e in found["Port books as sources"]["evidence"]],
                         [("studied", "Studied in Rivers and Trade (HIST2110)")])
        self.assertEqual({e["strength_words"] for e in found["Archival research"]["evidence"]},
                         {"Done in your coursework: The parish registers of Harrowgate Vale",
                          "Studied in Rivers and Trade (HIST2110)"})

    def test_a_skill_done_in_coursework_and_studied_is_coursework_and_not_studied(self):
        with home() as (con, _, _), fake_ai(REPLIES):
            ba = graduates.education(con, "ba")
            add(con, "coursework", ba, "registers.md", graduates.ESSAY, "essay")
            add(con, "lecture", ba, "seminar4.md", graduates.SEMINAR_NOTES, "notes")
            found = skills(con)
        self.assertEqual(strength.of_skill(found["Archival research"]), strength.COURSEWORK)
        self.assertEqual(strength.of_skill(found["Port books as sources"]), strength.STUDIED)
        for item in found.values():
            self.assertEqual(store.is_studied(item), strength.of_skill(item) == strength.STUDIED,
                             item["fields"]["name"])

    def test_evidence_from_the_bulk_read_has_a_strength_too(self):
        self.assertEqual(strength.of_evidence({"role": "taught", "doc_type": "slides"}), strength.STUDIED)
        self.assertEqual(strength.of_evidence({"role": "own", "doc_type": "repository"}), strength.SHOWN)
        self.assertEqual(strength.of_evidence({"role": "own", "doc_type": "resume"}), strength.ON_CV)
        self.assertEqual(strength.of_evidence({"role": "own", "doc_type": "dissertation"}), strength.COURSEWORK)
        self.assertEqual(strength.of_evidence({"role": "", "doc_type": "notes"}), "")
        self.assertEqual(strength.words(strength.ON_CV), "On your CV")

    def test_the_writer_gets_the_strength_of_each_skill_it_may_use(self):
        with home() as (con, _, _), fake_ai(REPLIES):
            ba = graduates.education(con, "ba")
            add(con, "coursework", ba, "registers.md", graduates.ESSAY, "essay")
            add(con, "lecture", ba, "seminar4.md", graduates.SEMINAR_NOTES, "notes")
            cv_skill = store.add_found(con, "skill", {"name": "Cataloguing"},
                                       {"source_rel": graduates.CV_REL, "doc_type": "cv", "role": "own"})
            unchecked = profile_writing.skill_strengths(con)
            store.confirm_many(con, [s["id"] for s in store.items(con, "skill")])
            got = profile_writing.skill_strengths(con)
        self.assertEqual(unchecked, {})
        self.assertEqual(got, {"Archival research": "coursework", "Port books as sources": "studied",
                               "Cataloguing": "cv"})
        self.assertTrue(cv_skill)

    def test_the_profile_block_says_how_each_skill_may_be_told_and_names_the_studied_ones(self):
        with home() as (con, _, _), fake_ai(REPLIES):
            ba = graduates.education(con, "ba")
            add(con, "coursework", ba, "registers.md", graduates.ESSAY, "essay")
            add(con, "lecture", ba, "seminar4.md", graduates.SEMINAR_NOTES, "notes")
            store.add_by_hand(con, "skill", {"name": "Bookbinding"})
            store.confirm_many(con, [s["id"] for s in store.items(con, "skill")])
            block = profile_writing.context_block(con)
            studied = profile_writing.studied_skills(con)
        self.assertIn("- Skill: Archival research. Done in coursework: may be told as experience", block)
        self.assertIn('- Skill: Port books as sources. Studied only: tell it as knowledge ("I studied", "covered in '
                      'my degree"), never as done', block)
        self.assertEqual(studied, ["Port books as sources"], "a skill you added yourself is never only studied")


if __name__ == "__main__":
    unittest.main()
