"""The profile in writing: the Profile block that document prompts get, and the letter that reads it.

Each test runs in its own temporary home. The person is the made-up humanities graduate of
support/people.py; her files, employers and modules are made up.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.people import HUMANITIES
from support.profiles import humanities_profile

from jobhunt import db, pack, profile, record
from jobhunt.core import paths
from jobhunt.cvstudio import context as cv_context
from jobhunt.profile import store, writing

NOW = "2026-11-05T09:00:00Z"
SHORT = 400                # room for the headings and the checked degree, not the found facts
PASSAGE = "[Jobs/Fenwick.md > Collections]\nSorted 400 donated letters into series."


class Writing(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def test_hidden_items_never_reach_the_context_block(self):
        humanities_profile(self.con)
        block = profile.context_block(self.con)
        self.assertNotIn("Paper round", block)
        self.assertNotIn("Harrowgate Vale News", block)

    def test_a_found_fact_whose_quote_was_not_in_its_file_waits_for_the_person(self):
        # a degree the model made up from a coursework report reached the profile block (2026-11-05)
        made_up = store.add_found(self.con, "education",
                                  {"qualification": "MA History", "institution": "Glenmoor College", "start": "2024"},
                                  {"source_rel": "Sources/Coursework/essay.md", "quote": "MA History, Glenmoor",
                                   "field": "qualification", "doc_type": "report", "verified": False})
        self.assertNotIn("Glenmoor", profile.context_block(self.con))
        store.confirm(self.con, made_up)
        self.assertIn("Glenmoor", profile.context_block(self.con))

    def test_the_block_names_one_place_and_one_wording_of_each_duty(self):
        # seven found places and twenty rewordings of one job's duties reached the block (2026-11-05)
        for n, (place, duty) in enumerate([("Harrowgate", "Catalogued donated letters"),
                                           ("Harrowgate", "Catalogued the donated letters"),
                                           ("Wexley University", "Ran Saturday tours for visitors")]):
            src = {"source_rel": f"Sources/CVs and letters/{n}.md", "quote": place, "field": "value", "doc_type": "cv"}
            store.add_found(self.con, "basic", {"key": "location", "value": place}, src)
            store.add_found(self.con, "job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum",
                                              "start": "2023-09", "duties": [duty]}, src)
        block = profile.context_block(self.con)
        self.assertEqual(block.count("Location:"), 1)
        self.assertIn("Location: Harrowgate", block)
        self.assertEqual(block.count("donated letters"), 1)

    def test_checked_facts_come_before_found_ones_and_contact_details_are_left_out(self):
        humanities_profile(self.con)
        block = profile.context_block(self.con)
        self.assertTrue(block.startswith(writing.HEADING))
        self.assertLess(block.index(writing.CHECKED), block.index("BA (Hons)"))
        self.assertLess(block.index("BA (Hons)"), block.index(writing.UNCHECKED))
        self.assertLess(block.index(writing.UNCHECKED), block.index("Collections volunteer"))
        self.assertNotIn(HUMANITIES.you["email"], block)
        self.assertIn(f"Name: {HUMANITIES.you['name']}", block)

    def test_a_humanities_profile_gives_modules_marks_and_research_skills(self):
        humanities_profile(self.con)
        block = profile.context_block(self.con)
        self.assertIn("Modules: Early Modern Letters (72); Oral History in Practice (68)", block)
        self.assertIn('Skill: Archival research (research). Shown by: "read the parish records in the county '
                      'archive"', block)
        self.assertIn("Collections volunteer at Fenwick Lantern Museum Trust (September 2023 to now), part-time",
                      block)

    def test_the_block_is_cut_at_a_whole_line_and_keeps_checked_facts_first(self):
        humanities_profile(self.con)
        short = profile.context_block(self.con, limit=SHORT)
        self.assertLessEqual(len(short), SHORT)
        self.assertTrue(profile.context_block(self.con).startswith(short))
        self.assertIn("BA (Hons)", short)
        self.assertTrue(all(line.startswith(("Profile", "Checked", "Found", "- ")) for line in short.splitlines()))
        self.assertEqual(profile.context_block(self.con, limit=10), "")

    def test_an_empty_profile_gives_no_block(self):
        self.assertEqual(profile.context_block(self.con), "")

    def test_the_letter_reads_the_profile_above_the_record_passages(self):
        humanities_profile(self.con)
        with mock.patch.object(record, "context", lambda queries: PASSAGE):
            kept = pack._kept({"must_have": ["cataloguing"]})
        self.assertLess(kept.index(writing.HEADING), kept.index(PASSAGE))
        self.assertEqual(pack.passages(kept), ["Jobs/Fenwick.md > Collections"],
                         "the profile block adds no record passage of its own")

    def test_with_nothing_in_the_profile_or_record_the_letter_still_reads_none(self):
        with mock.patch.object(record, "context", lambda queries: ""):
            self.assertEqual(pack._kept({"must_have": ["cataloguing"]}), "none")

    def test_document_prompts_get_the_profile_as_a_value(self):
        humanities_profile(self.con)
        self.assertEqual(cv_context.build()["profile"], profile.context_block(self.con))


if __name__ == "__main__":
    unittest.main()
