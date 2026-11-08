"""Coursework on a course's card: its title, date, mark, summary and suggested module, and the skills and methods
it shows as done in coursework, each with its quote. The software and history graduates' work is made up
(`support.graduates`).
"""
import json
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import graduates
from support.fake_ai import fake_ai
from test_ingest import home

from jobhunt.profile import store as profile_store
from jobhunt.sections import coursework, pipeline, store
from jobhunt.sections.pipeline import Incoming

REPORT_READ = {"summary": "Models how rainfall delays the Fen ferries with a regression in R.",
               "methods": [{"name": "regression in R", "quote": "fitted a linear regression in R"},
                           {"name": "data cleaning", "quote": "I cleaned two years of timetable data"},
                           {"name": "machine learning", "quote": "a deep neural network"}]}
ESSAY_READ = {"summary": "Reads parish registers against tithe maps.",
              "methods": [{"name": "close reading", "quote": "Close reading of the baptism registers"},
                          {"name": "archival research", "quote": "archival research at the county record office"}]}


def add(con, target: int, name: str, text: str, kind: str = "essay") -> int:
    """`text` added as coursework of the card `target`; its source's id."""
    return pipeline.add_uploads(con, "coursework", [Incoming(name, text.encode())], doc_kind=kind,
                                target_id=target)["added"][0]


class Coursework(unittest.TestCase):
    def test_a_report_gives_its_title_date_mark_summary_and_module_with_no_ai(self):
        with home() as (con, _, _):
            bsc = graduates.education(con, "bsc")
            source_id = add(con, bsc, "rainfall.md", graduates.REPORT, "report")
            out = pipeline.read(con, source_id)
            got = store.get(con, source_id)
        self.assertEqual((out["state"], out["facts"]), ("read", 0))
        self.assertEqual((got["title"], got["dated"], got["mark"]), ("Rainfall and ferry delays on the Fen",
                                                                     "2024-03", "68"))
        self.assertTrue(got["summary"].startswith("This report models how rainfall delays"))
        self.assertEqual((got["can_have_module"], got["module"], got["module_suggested"]),
                         (True, None, "Data Analysis (COMP2040)"))
        self.assertEqual(got["module_choices"], [m["name"] for m in graduates.COURSES["bsc"]["modules"]])

    def test_the_methods_it_shows_are_skills_done_in_coursework_each_with_its_quote_and_line(self):
        with home() as (con, _, _), fake_ai({coursework.PURPOSE: json.dumps(REPORT_READ)}):
            bsc = graduates.education(con, "bsc")
            pipeline.read(con, add(con, bsc, "rainfall.md", graduates.REPORT, "report"))
            skills = {s["fields"]["name"]: s for s in profile_store.items(con, "skill")}
            projects = profile_store.items(con, "project")
            got = store.listing(con, "coursework")[0]
        self.assertEqual(set(skills), {"regression in R", "Data cleaning"})
        evidence = skills["regression in R"]["evidence"][0]
        self.assertEqual((evidence["role"], evidence["doc_type"], evidence["verified"], evidence["line"]),
                         ("own", "report", True, 8))
        self.assertEqual(got["summary"], REPORT_READ["summary"])
        self.assertEqual(projects, [])

    def test_code_coursework_gives_its_language_with_no_ai(self):
        with home() as (con, _, _):
            bsc = graduates.education(con, "bsc")
            uploads = [Incoming(rel.rsplit("/", 1)[1], text.encode(), rel) for rel, text in graduates.CODE.items()]
            source_id = pipeline.add_uploads(con, "coursework", uploads, doc_kind="other", target_id=bsc)["added"][0]
            pipeline.read(con, source_id)
            skill = profile_store.items(con, "skill")[0]
            got = store.get(con, source_id)
        self.assertEqual((skill["fields"]["name"], skill["evidence"][0]["place"]), ("Python", "tide.py"))
        self.assertTrue(skill["evidence"][0]["verified"])
        self.assertEqual((got["title"], got["module_suggested"]), ("Tide model", "Programming Foundations (COMP1010)"))

    def test_reading_the_same_work_again_asks_the_ai_nothing_new(self):
        with home() as (con, _, _), fake_ai({coursework.PURPOSE: json.dumps(ESSAY_READ)}) as backend:
            ba = graduates.education(con, "ba")
            source_id = add(con, ba, "registers.md", graduates.ESSAY)
            pipeline.read(con, source_id)
            pipeline.read(con, source_id)
            names = sorted(s["fields"]["name"] for s in profile_store.items(con, "skill"))
        self.assertEqual(len(backend.requests(coursework.PURPOSE)), 1)
        self.assertEqual(names, ["Archival research", "Close reading"])

    def test_the_person_confirms_or_changes_the_suggested_module_and_only_from_the_cards_list(self):
        with home() as (con, _, _):
            ba = graduates.education(con, "ba")
            source_id = add(con, ba, "registers.md", graduates.ESSAY)
            pipeline.read(con, source_id)
            self.assertEqual(store.get(con, source_id)["module_suggested"], "Early Modern Records (HIST2040)")
            self.assertEqual(store.change(con, source_id, {"module": "Rivers and Trade (HIST2110)"})["module"],
                             "Rivers and Trade (HIST2110)")
            pipeline.read(con, source_id)
            self.assertEqual(store.get(con, source_id)["module"], "Rivers and Trade (HIST2110)")
            self.assertEqual(store.change(con, source_id, {"module": ""})["module"], "")
            with self.assertRaises(ValueError):
                store.change(con, source_id, {"module": "Underwater Basketry"})
            other = graduates.education(con, "access")
            self.assertIsNone(store.change(con, source_id, {"target_id": other})["module"])

    def test_a_module_is_suggested_by_its_words_when_no_code_is_given(self):
        choices = [m["name"] for m in graduates.COURSES["ba"]["modules"]]
        self.assertEqual(coursework.suggest_module("rivers-essay.md", "How rivers shaped trade", "", choices),
                         "Rivers and Trade (HIST2110)")
        self.assertEqual(coursework.suggest_module("notes.md", "Untitled", "Nothing to go on.", choices), "")

    def test_a_mark_is_taken_only_where_the_work_states_one(self):
        self.assertEqual(coursework.mark_in("Some words.\n\nFinal grade: 72%\n"), "72%")
        self.assertEqual(coursework.mark_in("Grade: 2:1"), "2:1")
        self.assertEqual(coursework.mark_in("We marked the samples and graded them by size."), "")


if __name__ == "__main__":
    unittest.main()
