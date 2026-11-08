"""The CV reader: a CV added to CVs is read into jobs, education, skills, projects, basics and links, each
fact quoting its line, by the AI with a CV-only prompt or by the CV studio's rules when the AI is away, and the
CV's own date is kept on its source.

The two made-up graduates of `support.graduates` (Avery Quillon, software; Marnie Quillfeather, history) and
every name, place and file are invented.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import fake_ai
from support.graduates import AVERY, MARNIE
from test_ingest import home

from jobhunt.profile import store as profile_store
from jobhunt.sections import cv, pipeline, readers, store
from jobhunt.sections.pipeline import Incoming

AI_REPLY = {"facts": [
    {"kind": "job", "fields": {"title": "Archive volunteer", "employer": "Fenwick Lantern Museum Trust",
                               "start": "Sept 2023", "end": "present"},
     "field": "title", "quote": "Archive volunteer, Fenwick Lantern Museum Trust"},
    {"kind": "education", "fields": {"qualification": "BA (Hons)", "subject": "History",
                                     "institution": "University of Made-up Elsewhere", "grade": "2:1"},
     "field": "grade", "quote": "BA (Hons) History, University of Nowhere Fen, 2021 - 2024, 2:1"},
    {"kind": "skill", "fields": {"name": "Palaeography"}, "field": "name", "quote": "palaeography"}]}


def cv_source(text: str, name: str = "CV.md") -> tuple[readers.Source, tempfile.TemporaryDirectory]:
    """A CV source holding `text` as the file `name`, kept in a temporary folder."""
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / name
    path.write_text(text, encoding="utf-8")
    return readers.Source(1, "cv", "cv", "", "upload", files=(readers.SourceFile(name, path),)), tmp


def facts_of(reading: readers.Reading, kind: str) -> list[readers.Fact]:
    """The reading's facts of one kind."""
    return [f for f in reading.facts if f.kind == kind]


class CvReader(unittest.TestCase):
    def read(self, text: str, name: str = "CV.md") -> readers.Reading:
        src, tmp = cv_source(text, name)
        self.addCleanup(tmp.cleanup)
        return readers.get("cv").read(src)

    def test_a_cv_is_read_by_rules_into_jobs_education_skills_projects_and_basics_when_the_ai_is_away(self):
        got = self.read(MARNIE.cv, "Marnie CV 2025.md")
        jobs = {f.fields["title"]: f for f in facts_of(got, "job")}
        (degree,) = facts_of(got, "education")
        basics = {f.fields["key"]: f.fields["value"] for f in facts_of(got, "basic")}
        self.assertEqual(set(jobs), {"Archive volunteer", "Shop assistant"})
        volunteer = jobs["Archive volunteer"]
        self.assertEqual((volunteer.fields["employer"], volunteer.fields["start"], volunteer.fields["end"]),
                         ("Fenwick Lantern Museum Trust", "Sept 2023", "present"))
        self.assertIn("Catalogued 400 letters from the 1840s", volunteer.fields["duties"])
        self.assertEqual((volunteer.quote.place, volunteer.quote.line), ("Marnie CV 2025.md", 9))
        self.assertEqual((degree.fields["qualification"], degree.fields["subject"], degree.fields["grade"]),
                         ("BA (Hons)", "History", "2:1"))
        self.assertEqual(degree.fields["modules"], [{"name": "Medieval Europe", "mark": "68"},
                                                    {"name": "Archival Methods", "mark": "72"}])
        self.assertEqual({f.fields["name"] for f in facts_of(got, "skill")},
                         {"archival research", "palaeography", "French"})
        self.assertEqual(basics["name"], "Marnie Quillfeather")
        self.assertEqual(basics["email"], "marnie.quillfeather@example.org")
        self.assertEqual(got.title, "Marnie CV 2025")

    def test_a_software_graduates_projects_are_read_as_the_cv_lists_them(self):
        got = self.read(AVERY.cv)
        (project,) = facts_of(got, "project")
        self.assertEqual(project.fields, {"name": "Tide gauge",
                                          "summary": "A tide gauge that reads a made-up bay's tides"})

    def test_the_ai_reads_a_cv_with_a_prompt_that_never_asks_its_kind_or_whose_it_is(self):
        with fake_ai({cv.PURPOSE: json.dumps(AI_REPLY)}) as backend:
            got = self.read(MARNIE.cv, "Marnie CV 2025.md")
        (request,) = backend.log
        self.assertNotIn("doc_type", request.system)
        self.assertNotIn('"role"', request.system)
        (degree,) = facts_of(got, "education")
        self.assertNotIn("institution", degree.fields, "an institution the CV never names is left out")
        (job,) = facts_of(got, "job")
        self.assertEqual(job.quote.line, 9)
        self.assertEqual([f.fields["name"] for f in facts_of(got, "skill")], ["Palaeography"])

    def test_an_ai_that_gives_no_facts_leaves_the_cv_to_the_rules(self):
        with fake_ai({cv.PURPOSE: "{}"}):
            got = self.read(AVERY.cv)
        self.assertEqual({f.fields["title"] for f in facts_of(got, "job")}, {"Junior developer", "Lab helper"})

    def test_a_cvs_date_is_its_headings_else_its_latest_entry_else_its_files(self):
        self.assertEqual(self.read(AVERY.cv).date, "2025-06")
        no_heading = AVERY.older_cv.replace("CV, January 2023\n", "")
        self.assertEqual(self.read(no_heading).date, "2022-10", "the lab job from Oct 2022 is its latest entry")
        src, tmp = cv_source("# Avery Quillon\n\nI write software.\n")
        self.addCleanup(tmp.cleanup)
        stamp = time.mktime((2021, 4, 15, 12, 0, 0, 0, 0, -1))
        os.utime(src.files[0].path, (stamp, stamp))
        self.assertEqual(readers.get("cv").read(src).date, "2021-04")

    def test_a_cv_with_no_words_cannot_be_read(self):
        with self.assertRaisesRegex(ValueError, "no words"):
            self.read("   \n")


class CvSection(unittest.TestCase):
    def test_a_cv_added_to_cvs_fills_the_profile_and_keeps_its_date(self):
        with home() as (con, vault, _):
            source_id = pipeline.add_uploads(con, "cv", [Incoming("Avery CV.md", AVERY.cv.encode())])["added"][0]
            out = pipeline.read(con, source_id)
            got = store.get(con, source_id)
            jobs = profile_store.items(con, "job")
            note = (vault / got["note_rel"]).is_file()
        self.assertEqual((out["state"], got["dated"], got["title"]), ("read", "2025-06", "Avery CV"))
        self.assertTrue(note)
        self.assertEqual({j["fields"]["title"] for j in jobs}, {"Junior developer", "Lab helper"})
        ev = jobs[0]["evidence"][0]
        self.assertEqual((ev["doc_type"], ev["role"], ev["source_id"], ev["verified"]), ("cv", "own", source_id, True))


if __name__ == "__main__":
    unittest.main()
