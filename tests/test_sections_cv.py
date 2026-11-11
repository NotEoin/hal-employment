"""The CV reader: a CV added to CVs is read into jobs, education, skills, projects, basics and links, each
fact quoting its line, by the AI with a CV-only prompt or by the CV studio's rules when the AI is away, and the
CV's own date is kept on its source. The newest-dated CV leads, or the one the person chose; an older CV only
fills gaps, and folding, writing and the page agree on which one leads.

The two made-up graduates of `support.graduates` (Avery Quillon, software; Marnie Quillfeather, history) and
every name, place and file are invented.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import fake_ai
from support.graduates import AVERY, MARNIE
from test_ingest import home

from jobhunt import ai
from jobhunt.profile import conflicts, cvs
from jobhunt.profile import routes as profile_routes
from jobhunt.profile import store as profile_store
from jobhunt.profile import writing as profile_writing
from jobhunt.sections import cv, pipeline, readers, store
from jobhunt.sections.pipeline import Incoming
from jobhunt.writing import plan, rank

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

    def test_a_cv_is_read_into_jobs_education_skills_projects_and_basics(self):
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

    def test_an_ai_that_gives_no_facts_gives_the_cv_none(self):
        with fake_ai({cv.PURPOSE: "{}"}):
            got = self.read(AVERY.cv)
        self.assertEqual(got.facts, [])

    def test_a_cv_the_ai_cannot_read_now_is_never_read_by_rules(self):
        """With Hal's model stopped, the rules read every CV into junk education cards (2026-11-11)."""
        with fake_ai({cv.PURPOSE: ai.LLMUnavailable("backend qwen was stopped", kind="unavailable")}), \
                self.assertRaises(ai.LLMUnavailable):
            self.read(AVERY.cv)

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


def add_cv(con, name: str, text: str) -> int:
    """`text` added to CVs as the file `name` and read; its source's id."""
    source_id = pipeline.add_uploads(con, "cv", [Incoming(name, text.encode())])["added"][0]
    pipeline.read(con, source_id)
    return source_id


def card(con, kind: str, **match) -> dict:
    """The one shown card of `kind` whose fields hold `match`."""
    (found,) = [i for i in profile_store.items(con, kind, hidden=False)
                if all(i["fields"].get(k) == v for k, v in match.items())]
    return found


class CurrentAndOlderCvs(unittest.TestCase):
    def test_the_newest_dated_cv_leads_and_an_older_cv_only_fills_gaps(self):
        with home() as (con, _, _):
            older = add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            current = add_cv(con, "Marnie CV.md", MARNIE.cv)
            lead = cvs.current_source(con)
            shop = card(con, "job", employer="Made-up Books Ltd")
            paper = card(con, "job", employer="Harrowgate Gazette")
            degree = card(con, "education", institution="University of Nowhere Fen")
            german = card(con, "skill", name="German")
            grade_conflicts = [c for c in conflicts.listing(con) if c.get("field") in ("grade", "end", "location")]
            shown = [store.get(con, i) for i in (current, older)]
        self.assertEqual(lead, current)
        self.assertEqual([(s["current"], s["dated"]) for s in shown], [(True, "2025-03"), (False, "2022-05")])
        self.assertEqual((shop["fields"]["title"], shop["fields"]["end"], shop["fields"]["location"]),
                         ("Shop assistant", "2023-08", "Harrowgate"), "the town only the older CV gives fills a gap")
        self.assertEqual(shop["fields"]["duties"], ["Advised customers on local history titles"],
                         "the older CV's duties stay out while the current CV lists some")
        self.assertNotIn("current", shop["fields"])
        self.assertEqual(paper["fields"]["title"], "Paper round", "the extra old job comes from the older CV")
        self.assertEqual(degree["fields"]["modules"], [{"name": "Medieval Europe", "mark": "68"},
                                                       {"name": "Archival Methods", "mark": "72"}])
        self.assertEqual({e["source_id"] for e in german["evidence"]}, {older})
        self.assertEqual(grade_conflicts, [], "an older CV's word is no conflict with the current CV's")

    def test_the_older_cvs_grade_never_beats_the_current_cvs(self):
        with home() as (con, _, _):
            add_cv(con, "Avery CV.md", AVERY.cv)
            add_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            degree = card(con, "education", institution="University of Nowhere Fen")
            lab = card(con, "job", title="Lab helper")
        self.assertEqual((degree["fields"]["grade"], degree["fields"]["end"]), ("First", "2024"))
        self.assertEqual(degree["fields"]["modules"], [{"name": "Databases", "mark": "70"},
                                                       {"name": "Algorithms", "mark": "65"}])
        self.assertEqual((lab["fields"]["location"], lab["fields"]["end"]), ("Fenmouth", "2024-06"))

    def test_choosing_another_cv_as_current_folds_the_cards_again_and_clearing_it_gives_back_the_newest(self):
        with home() as (con, _, _):
            current = add_cv(con, "Avery CV.md", AVERY.cv)
            older = add_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            store.change(con, older, {"current": True})
            chosen = (cvs.current_source(con), card(con, "education", institution="University of Nowhere Fen"))
            store.change(con, older, {"current": False})
            back = (cvs.current_source(con), card(con, "education", institution="University of Nowhere Fen"))
        self.assertEqual(chosen[0], older)
        self.assertEqual(chosen[1]["fields"]["grade"], "2:1")
        self.assertEqual(chosen[1]["fields"]["modules"], [{"name": "Databases", "mark": "60"}])
        self.assertEqual(back[0], current)
        self.assertEqual(back[1]["fields"]["grade"], "First")

    def test_removing_the_current_cv_makes_the_next_newest_lead(self):
        with home() as (con, _, _):
            current = add_cv(con, "Marnie CV.md", MARNIE.cv)
            older = add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            pipeline.remove(con, current)
            lead = cvs.current_source(con)
            shop = card(con, "job", employer="Made-up Books Ltd")
        self.assertEqual(lead, older)
        self.assertEqual(shop["fields"]["duties"], ["Ran the till on Saturdays"])
        self.assertTrue(shop["fields"]["current"])

    def test_writing_and_the_page_take_the_same_cv_as_current(self):
        with home() as (con, _, _):
            current = add_cv(con, "Marnie CV.md", MARNIE.cv)
            older = add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            authority = profile_writing.CvAuthority.of(con)
            notes = (store.get(con, current)["note_rel"], store.get(con, older)["note_rel"])
            paper = card(con, "job", employer="Harrowgate Gazette")
            left_out = profile_writing.older_cv_only(con, paper, authority)
            page = profile_routes.of_kind(SimpleNamespace(con=con, params={"kind": "job"}))
        self.assertEqual((authority.newest, authority.older), (notes[0], frozenset({notes[1]})))
        self.assertEqual(left_out, {"duties": ["Delivered papers before school"]})
        marks = {e["source_id"]: e["from_older_cv"] for i in page["items"] for e in i["evidence"]}
        self.assertEqual(marks, {current: False, older: True})

    def test_a_job_only_an_older_cv_gives_is_told_last_and_only_by_its_plain_facts(self):
        with home() as (con, _, _):
            add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            add_cv(con, "Marnie CV.md", MARNIE.cv)
            jobs = [card(con, "job", employer=e) for e in ("Harrowgate Gazette", "Made-up Books Ltd")]
            profile_store.confirm_many(con, [j["id"] for j in jobs])
            found = {c["name"]: c for c in profile_writing.cards(con) if c["kind"] == "job"}
        paper, shop = found["Paper round at Harrowgate Gazette"], found["Shop assistant at Made-up Books Ltd"]
        self.assertEqual((paper["older_cv_job"], shop["older_cv_job"]), (True, False))
        self.assertEqual(paper["quotes"], ["Paper round at Harrowgate Gazette, 2018 to 2020",
                                           "Delivered papers before school"], "its plain facts, nothing added")
        stories = [plan.card_story(paper), plan.card_story(shop)]
        ranked = [rank.Ranked(paper["name"]), rank.Ranked(shop["name"])]
        cards = [paper, shop]
        self.assertEqual([s.name for s in rank.order(stories, cards, ranked, "history", n=1).letter], [shop["name"]],
                         "ranked first, it is still told after the job the current CV gives")
        self.assertEqual([s.name for s in rank.order(stories, cards, ranked, "history", n=2).letter],
                         [shop["name"], paper["name"]], "and told when the letter has room")
        self.assertEqual(plan.letter_stories(stories, cards, "history", n=1)[0].name, shop["name"])
        self.assertIn(f"Where its facts come from: {plan.OLDER_CV_JOB_WORDS}", stories[0].block())
        self.assertNotIn(plan.OLDER_CV_JOB_WORDS, stories[1].block())

    def test_a_showcased_job_from_an_older_cv_still_leads_and_keeps_to_its_plain_facts(self):
        with home() as (con, _, _):
            add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            add_cv(con, "Marnie CV.md", MARNIE.cv)
            jobs = [card(con, "job", employer=e) for e in ("Harrowgate Gazette", "Made-up Books Ltd")]
            profile_store.confirm_many(con, [j["id"] for j in jobs])
            profile_store.set_star(con, jobs[0]["id"], True)
            found = {c["name"]: c for c in profile_writing.cards(con) if c["kind"] == "job"}
        paper, shop = found["Paper round at Harrowgate Gazette"], found["Shop assistant at Made-up Books Ltd"]
        stories = [plan.card_story(shop), plan.card_story(paper)]
        ranked = [rank.Ranked(shop["name"]), rank.Ranked(paper["name"])]
        cards = [shop, paper]
        self.assertEqual([s.name for s in rank.order(stories, cards, ranked, "history", n=1).letter], [paper["name"]],
                         "the person's star wins over the older CV")
        self.assertEqual(plan.letter_stories(stories, cards, "history", n=1)[0].name, paper["name"])
        self.assertEqual(stories[1].facts, ("Paper round at Harrowgate Gazette, 2018 to 2020",
                                            "Delivered papers before school"), "still only its plain facts")
        self.assertIn(plan.OLDER_CV_JOB_WORDS, stories[1].block())

    def test_a_project_only_cvs_give_is_marked_from_your_cv_only(self):
        with home() as (con, _, _):
            add_cv(con, "Avery CV.md", AVERY.cv)
            page = profile_routes.of_kind(SimpleNamespace(con=con, params={"kind": "project"}))
            jobs = profile_routes.of_kind(SimpleNamespace(con=con, params={"kind": "job"}))
        self.assertEqual([(i["title"], i["cv_only"]) for i in page["items"]], [("Tide gauge", True)])
        self.assertFalse(any(i["cv_only"] for i in jobs["items"]), "only a project is marked")


if __name__ == "__main__":
    unittest.main()
