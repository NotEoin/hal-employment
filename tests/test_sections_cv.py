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

from jobhunt import ai, record
from jobhunt.core import settings
from jobhunt.cvstudio import parse as cv_parse
from jobhunt.cvstudio import store as cv_store
from jobhunt.profile import conflicts, cvs
from jobhunt.profile import routes as profile_routes
from jobhunt.profile import store as profile_store
from jobhunt.profile import writing as profile_writing
from jobhunt.sections import cv, pipeline, readers, store
from jobhunt.sections.pipeline import Incoming
from jobhunt.writing import plan

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


class SomeoneElsesCv(unittest.TestCase):
    def test_a_cv_headed_by_another_persons_name_is_theirs(self):
        cases = [("Quentin Arbuckle\nquentin@example.org\nSecond-year student", "Quentin Arbuckle"),
                 ("Document author: Quentin Arbuckle\n\nCurriculum Vitae\n# Quentin Arbuckle\nLeeds",
                  "Quentin Arbuckle"),
                 ("# Avery Quillon\nJunior developer", ""),
                 ("AVERY QUILLON\nJunior developer", ""),
                 ("Software Engineer\navery.quillon@example.org\nJunior developer", ""),
                 ("Junior developer at Fenwick Labs, 2024 to 2025", "")]
        for text, whose in cases:
            with self.subTest(text=text):
                self.assertEqual(cv.someone_else(text, "Avery Quillon"), whose)
        self.assertEqual(cv.someone_else("Quentin Arbuckle\nStudent", ""), "", "with no name set, every CV is theirs")
        irish = "Siobh\u00e1n N\u00ed Bhriain"
        self.assertEqual(cv.someone_else(f"{irish}\nArchivist", "Avery Quillon"), irish)
        self.assertEqual(cv.someone_else(f"{irish}\nArchivist", irish), "")

    def test_a_name_typed_without_its_accents_still_finds_your_own_cv(self):
        # matching was exact, so "Siobhan" in setup kept out a CV headed "Ren\u00e9e" as someone else's
        accented = "Ren\u00e9e Mu\u00f1oz"
        self.assertEqual(cv.someone_else(f"{accented}\nArchivist", "Renee Munoz"), "")
        self.assertEqual(cv.someone_else("Renee Munoz\nArchivist", accented), "")
        self.assertEqual(cv.someone_else("Quentin Arbuckle\nStudent", "Renee Munoz"), "Quentin Arbuckle")
        self.assertEqual(cv.someone_else("Quentin Arbuckle\nStudent", "Avery Quillon Arbuckle"), "",
                         "a CV in a name the person also goes by is theirs")

    def test_another_persons_cv_gives_no_cards_and_is_kept_out_saying_whose_it_is(self):
        """Regression: a classmate's CV sorted under CVs gave the person three of the classmate's projects
        (2026-11-12)."""
        with home() as (con, _vault, _):
            settings.set("you.name", AVERY.name)
            add_cv(con, "Avery CV.md", AVERY.cv)
            theirs = add_cv(con, "Comp sci CV.md", MARNIE.cv)
            got = store.get(con, theirs)
            jobs = {j["fields"]["title"] for j in profile_store.items(con, "job", hidden=False)}
        self.assertEqual((got["state"], got["state_words"], got["someone_else"], got["not_yours"]),
                         ("kept_out", "Kept out: someone else's", "Marnie Quillfeather", True))
        self.assertIn("Marnie Quillfeather", got["error"])
        self.assertEqual(jobs, {"Junior developer", "Lab helper"}, "only the person's own CV gives cards")

    def test_a_cv_the_person_says_is_theirs_is_read_whatever_name_heads_it(self):
        with home() as (con, _vault, _):
            settings.set("you.name", AVERY.name)
            theirs = add_cv(con, "Stage name CV.md", MARNIE.cv)
            store.change(con, theirs, {"theirs": True})
            out = pipeline.read(con, theirs)
            jobs = {j["fields"]["title"] for j in profile_store.items(con, "job", hidden=False)}
        self.assertEqual(out["state"], "read")
        self.assertIn("Archive volunteer", jobs)

    def test_reading_another_persons_cv_again_takes_back_the_cards_it_gave(self):
        with home() as (con, _vault, _):
            theirs = add_cv(con, "Comp sci CV.md", MARNIE.cv)
            self.assertIn("Archive volunteer", {j["fields"]["title"] for j in profile_store.items(con, "job")})
            settings.set("you.name", AVERY.name)
            pipeline.read(con, theirs)
            jobs = profile_store.items(con, "job", hidden=False)
        self.assertEqual(jobs, [])


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


def my_cv(con, name: str, text: str) -> str:
    """A CV of My CVs read from the same file as the record's CV `name` (an upload there): its id. Made in use,
    first in the person's order."""
    data = text.encode()
    file_id = cv_store.add_file(data, name, role="baseline", con=con)
    doc = cv_parse.read_rules(cv_parse.lines_of(data, "md")).doc
    return cv_store.create(name, doc, origin="uploaded", what="Your file", by="baseline", file_id=file_id, con=con)


def marked(con) -> list[int]:
    """The CVs `record_sources.current` names as the lead."""
    return [r[0] for r in con.execute("SELECT id FROM record_sources WHERE current = 1 ORDER BY id")]


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

    def test_the_cv_in_use_for_applications_leads_the_facts_and_taking_it_out_of_use_gives_back_the_newest(self):
        with home() as (con, _, _):
            current = add_cv(con, "Avery CV.md", AVERY.cv)
            older = add_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            first = (cvs.current_source(con), marked(con))
            mine = my_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            cvs.settle(con)
            chosen = (cvs.current_source(con), marked(con),
                      card(con, "education", institution="University of Nowhere Fen"))
            my_cv(con, "Someone else's starter.md", "# Avery Quillon\n\n## Skills\n\nKnitting\n")
            cv_store.star(mine, False, con=con)
            cvs.settle(con)
            back = (cvs.current_source(con), marked(con),
                    card(con, "education", institution="University of Nowhere Fen"))
        self.assertEqual(first, (current, [current]))
        self.assertEqual(chosen[:2], (older, [older]), "the record follows the CV in use, and marks it")
        self.assertEqual(chosen[2]["fields"]["grade"], "2:1")
        self.assertEqual(chosen[2]["fields"]["modules"], [{"name": "Databases", "mark": "60"}])
        self.assertEqual(back[:2], (current, [current]), "no record CV in use: the newest leads again")
        self.assertEqual(back[2]["fields"]["grade"], "First")

    def test_the_first_cv_in_use_leads_in_the_order_of_my_cvs_and_one_not_read_yet_waits(self):
        with home() as (con, _, _):
            newer = add_cv(con, "Avery CV.md", AVERY.cv)
            older = add_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            a, b = my_cv(con, "Avery CV.md", AVERY.cv), my_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            cv_store.reorder([b, a], con=con)
            first = cvs.current_source(con)
            cv_store.reorder([a, b], con=con)
            second = cvs.current_source(con)
            waiting = pipeline.add_uploads(con, "cv", [Incoming("Avery CV 2019.md", b"# Avery Quillon\n\n2019\n")])
            cv_store.reorder([my_cv(con, "Avery CV 2019.md", "# Avery Quillon\n\n2019\n"), a, b], con=con)
            third = cvs.current_source(con)
        self.assertEqual((first, second), (older, newer))
        self.assertTrue(waiting["added"])
        self.assertEqual(third, newer, "a CV in use that is not read yet does not lead")

    def test_the_cvs_list_puts_those_in_use_first_in_my_order_then_the_rest_newest_first(self):
        with home() as (con, _, _):
            old = add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            new = add_cv(con, "Marnie CV.md", MARNIE.cv)
            oldest = add_cv(con, "Marnie CV 2019.md", "# Marnie Quillfeather\n\nCV, May 2019\n")
            waiting = pipeline.add_uploads(con, "cv", [Incoming("Marnie CV draft.md", b"# Marnie\n\nDraft\n")])
            before = [s["id"] for s in store.listing(con, "cv")]
            a, b = my_cv(con, "Marnie CV 2019.md", "# Marnie Quillfeather\n\nCV, May 2019\n"), \
                my_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            cv_store.reorder([b, a], con=con)
            after = [s["id"] for s in store.listing(con, "cv")]
            cv_store.reorder([a, b], con=con)
            swapped = [s["id"] for s in store.listing(con, "cv")][:2]
        draft = waiting["added"][0]
        self.assertEqual(before, [draft, new, old, oldest], "newest first; one not read yet by when it was added")
        self.assertEqual(after, [old, oldest, draft, new], "the CVs in use first, in the order on My CVs")
        self.assertEqual(swapped, [oldest, old])

    def test_an_old_current_choice_is_replaced_by_the_rule_and_the_record_no_longer_takes_one(self):
        with home() as (con, _, _):
            current = add_cv(con, "Marnie CV.md", MARNIE.cv)
            older = add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            with con:
                con.execute("UPDATE record_sources SET current = (id = ?)", (older,))   # as 2.2.0 kept a choice
            refolded = cvs.settle(con)
            with self.assertRaisesRegex(ValueError, "Use this for applications"):
                store.change(con, older, {"current": True})
            again = cvs.settle(con)
            lead = (marked(con), store.get(con, current)["current"], store.get(con, older)["current"])
        self.assertTrue(refolded, "the cards are worked out again for the rule's lead")
        self.assertEqual(again, [], "nothing to do while the lead stays")
        self.assertEqual(lead, ([current], True, False))

    def test_the_first_start_after_the_update_leaves_the_record_as_it_was(self):
        # review, 2026-11-18: the column held only an explicit choice, so it was 0 in most installs; the first
        # settle took that for a change of lead and called every application out of date
        with home() as (con, _, _):
            add_cv(con, "Marnie CV old.md", MARNIE.older_cv)
            newest = add_cv(con, "Marnie CV.md", MARNIE.cv)
            with con:
                con.execute("UPDATE record_sources SET current = 0")   # as an install before the update kept it
            before = record.revision(con)
            refolded = cvs.settle(con)
            after = (record.revision(con), marked(con))
        self.assertEqual(refolded, [], "the newest CV led before and leads now")
        self.assertEqual(after, (before, [newest]))

    def test_using_a_cv_that_does_not_lead_for_applications_or_not_leaves_the_record_as_it_was(self):
        with home() as (con, _, _):
            newest = add_cv(con, "Avery CV.md", AVERY.cv)
            add_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            lead = my_cv(con, "Avery CV.md", AVERY.cv)
            other = my_cv(con, "Avery CV 2023.md", AVERY.older_cv)
            cv_store.reorder([lead, other], con=con)
            cvs.settle(con)
            before = record.revision(con)
            cv_store.star(other, False, con=con)
            off = (cvs.settle(con), record.revision(con))
            cv_store.star(other, True, con=con)
            on = (cvs.settle(con), record.revision(con), marked(con))
        self.assertEqual(off, ([], before))
        self.assertEqual(on, ([], before, [newest]))

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
        cards = [paper, shop]
        self.assertEqual([s.name for s in plan.chosen(cards, "papers", {paper["name"]: 2}, n=1)], [shop["name"]],
                         "cited first, it is still given after the job the current CV gives")
        self.assertEqual([s.name for s in plan.chosen(cards, "papers", {paper["name"]: 2}, n=2)],
                         [shop["name"], paper["name"]], "and given when there is room")
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
        cards = [shop, paper]
        self.assertEqual([s.name for s in plan.chosen(cards, "books", {shop["name"]: 2}, n=1)], [paper["name"]],
                         "the person's star wins over the older CV")
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
