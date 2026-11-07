"""Where each experience was done: Hal's reading of each card, the person's choice, and the words writing gets.

Each test runs in its own temporary home. The person, her projects, files and employers are made up.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import db
from jobhunt.core import paths
from jobhunt.profile import contexts, store, writing
from jobhunt.writing import stage

NOW = "2026-11-07T09:00:00Z"


def ev(source: str, doc_type: str, quote: str = "") -> dict:
    """Evidence from one of the person's own made-up files."""
    return {"source_rel": source, "quote": quote, "field": "", "doc_type": doc_type, "role": "own"}


class Contexts(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def project(self, name: str, source: str, doc_type: str, quote: str = "") -> int:
        item = store.add_found(self.con, "project", {"name": name, "tools": ["Lua"]}, ev(source, doc_type, quote))
        store.confirm(self.con, item)
        return item

    def test_hal_gives_each_card_the_context_its_evidence_shows(self):
        own = self.project("Tide gauge", "Sources/Code/Tide gauge.md", "repository")
        degree = self.project("Harbour survey", "Sources/Coursework/Harbour survey.md", "coursework")
        team = self.project("Ferry planner", "Sources/Code/Ferry planner.md", "repository",
                            "A group project with four classmates")
        unknown = self.project("Lamp list", "Sources/CVs and letters/CV.md", "cv")
        job = store.add_found(self.con, "job", {"title": "Lantern keeper", "employer": "Glimmerholt Trust"},
                              ev("Sources/CVs and letters/CV.md", "cv"))
        self.assertEqual(contexts.refresh(self.con), 4, "nothing shows where the lamp list was done")
        shown = {i: store.item(self.con, i) for i in (own, degree, team, unknown, job)}
        self.assertEqual([shown[i]["context"] for i in (own, degree, team, unknown, job)],
                         ["personal", "degree", "team", "", "work"])
        self.assertEqual(shown[team]["context_words"], "University team project")
        self.assertEqual(shown[own]["context_by"], "hal")
        self.assertEqual(contexts.refresh(self.con), 0, "a second pass with nothing new changes nothing")

    def test_a_context_the_person_chose_is_never_changed_by_hal(self):
        own = self.project("Tide gauge", "Sources/Code/Tide gauge.md", "repository")
        chosen = contexts.set_context(self.con, own, "degree")
        self.assertEqual((chosen["context"], chosen["context_by"], chosen["context_words"]),
                         ("degree", "person", "Part of my degree"))
        contexts.refresh(self.con)
        self.assertEqual(store.item(self.con, own)["context"], "degree")
        self.assertIn("- Project (degree work): Tide gauge. Tools: Lua", writing.context_block(self.con))
        self.assertEqual(writing.cards(self.con)[0]["context"], "degree")

    def test_an_unknown_context_or_a_skill_is_refused(self):
        own = self.project("Tide gauge", "Sources/Code/Tide gauge.md", "repository")
        skill = store.add_found(self.con, "skill", {"name": "Lua"}, ev("Sources/CVs and letters/CV.md", "cv"))
        with self.assertRaises(ValueError):
            contexts.set_context(self.con, own, "hobby")
        with self.assertRaises(ValueError):
            contexts.set_context(self.con, skill, "personal")
        with self.assertRaises(LookupError):
            contexts.set_context(self.con, 999, "personal")
        self.assertEqual(store.item(self.con, skill)["context_words"], "", "a skill has no context")

    def test_the_page_words_cover_every_context(self):
        self.assertEqual(sorted(contexts.CHOICE_WORDS), sorted(stage.CONTEXTS))
        self.assertTrue(set(contexts.BY_KIND.values()) <= set(stage.CONTEXTS))

    def test_a_job_done_as_part_of_a_degree_says_so_in_the_block(self):
        job = store.add_found(self.con, "job", {"title": "Placement analyst", "employer": "Brackwater Mills"},
                              ev("Sources/CVs and letters/CV.md", "cv"))
        store.confirm(self.con, job)
        self.assertIn("- Job: Placement analyst at Brackwater Mills", writing.context_block(self.con))
        contexts.set_context(self.con, job, "degree")
        self.assertIn("- Job (degree work): Placement analyst at Brackwater Mills", writing.context_block(self.con))

    def test_a_passage_from_a_project_cards_file_says_the_cards_context(self):
        own = self.project("Tide gauge", "Sources/Code/Tide gauge.md", "repository")
        contexts.set_context(self.con, own, "team")
        passages = ("[Sources/Code/Tide gauge.md > Tide gauge]\n(Kind: personal project)\nLogs tides.\n\n"
                    "[Sources/Notes/Boats.md > Boats]\nA made-up note.")
        said = writing.with_contexts(passages, writing.passage_contexts(self.con))
        self.assertIn("[Sources/Code/Tide gauge.md > Tide gauge]\n(Kind: university team project)\nLogs tides.", said)
        self.assertIn("[Sources/Notes/Boats.md > Boats]\nA made-up note.", said)


if __name__ == "__main__":
    unittest.main()
