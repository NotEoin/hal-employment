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
from jobhunt.profile import store, tiers, writing

NOW = "2026-11-05T09:00:00Z"
SHORT = 400                # room for the heading, the name and some of the facts, not all of them
PASSAGE = "[Jobs/Fenwick.md > Collections]\nSorted 400 donated letters into series."


def check_everything(con) -> None:
    """The person says every fact Hal found is right, as on the Check your record page."""
    for kind in store.KINDS:
        for item in store.items(con, kind, hidden=False):
            if item["state"] == "hal":
                store.confirm(con, item["id"])


class Writing(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def test_the_degrees_a_draft_is_checked_against_are_the_education_cards_qualifications(self):
        # a README's "MSc dissertation" reached letters of a BSc holder; a draft is checked against these
        ids = humanities_profile(self.con)
        self.assertEqual(writing.degrees(self.con), [f"{HUMANITIES.you['education.degree']} "
                                                     f"{HUMANITIES.you['education.subject']}"])
        store.hide(self.con, ids["degree"])
        self.assertEqual(writing.degrees(self.con), [])

    def test_the_modules_an_opening_may_name_are_the_courses_own(self):
        # regression: an opening led with an Access diploma's "9 Distinction credits in Mathematics" beside the
        # degree, and ran CV marks into the employer's fact (final eval, 2026-11-12)
        for fields in ({"qualification": "BA (Hons)", "subject": "History", "institution": "Made-up University",
                        "modules": [{"name": "Oral History", "mark": "72"}]},
                       {"qualification": "Access to HE Diploma", "institution": "Made-up College",
                        "modules": [{"name": "Mathematics", "mark": "Distinction"}]}):
            store.confirm(self.con, store.add_found(self.con, "education", fields,
                                                    {"source_rel": "Sources/CVs/cv.md", "doc_type": "cv"}))
        self.assertEqual(len(writing.marked_modules(self.con)), 2)
        self.assertEqual(writing.marked_modules(self.con, institution="Made-up University"), [("Oral History", "72")])
        self.assertEqual(len(writing.marked_modules(self.con, institution="")), 2, "no course known: all of them")

    def test_hidden_items_never_reach_the_context_block(self):
        humanities_profile(self.con)
        block = profile.context_block(self.con)
        self.assertNotIn("Paper round", block)
        self.assertNotIn("Harrowgate Vale News", block)

    def test_a_fact_nobody_checked_never_reaches_the_block(self):
        # Hal used every fact it found, right or wrong, until the person checked them (2026-11-05)
        ids = humanities_profile(self.con)
        block = profile.context_block(self.con)
        self.assertIn("BA (Hons)", block, "the degree is confirmed")
        for unchecked in ("Collections volunteer", "Archival research", HUMANITIES.you["name"]):
            self.assertNotIn(unchecked, block)
        store.edit(self.con, ids["job"], {"title": "Collections assistant"})
        store.confirm(self.con, ids["name"])
        block = profile.context_block(self.con)
        self.assertIn("Job: Collections assistant", block)
        self.assertIn(f"Name: {HUMANITIES.you['name']}", block)

    def test_with_nothing_checked_there_is_no_block(self):
        store.add_found(self.con, "skill", {"name": "Archival research"},
                        {"source_rel": "Sources/CVs and letters/a.md", "quote": "archives", "doc_type": "cv"})
        self.assertEqual(profile.context_block(self.con), "")

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
        check_everything(self.con)
        block = profile.context_block(self.con)
        self.assertEqual(block.count("Location:"), 1)
        self.assertIn("Location: Harrowgate", block)
        self.assertEqual(block.count("donated letters"), 1)

    def test_a_job_with_every_duty_leaves_room_for_education_and_skills(self):
        # one job's duties from five CVs filled the block and education never reached the writer (2026-11-05)
        src = {"source_rel": "Sources/CVs and letters/a.md", "quote": "x", "field": "title", "doc_type": "cv"}
        store.add_found(self.con, "job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum",
                                          "start": "2023-09",
                                          "duties": [f"Catalogued box {n} of letters" for n in range(400)]}, src)
        store.add_found(self.con, "education", {"qualification": "BA History", "institution": "Wexley University",
                                                "start": "2021-09"}, src)
        check_everything(self.con)
        block = profile.context_block(self.con)
        self.assertIn("Education: BA History", block)
        self.assertLessEqual(max(len(x) for x in block.splitlines()), writing.LINE_MAX + len(" ..."))

    def test_the_projects_closest_to_the_advert_come_first(self):
        src = {"source_rel": "Sources/Projects/a.md", "quote": "x", "field": "name", "doc_type": "notes"}
        for name in ("Pottery stall accounts", "Oral history of the Wexley mills", "Choir rota"):
            store.add_found(self.con, "project", {"name": name}, src)
        check_everything(self.con)
        block = profile.context_block(self.con, queries=["Recording oral history interviews for a heritage trust"])
        projects = [x for x in block.splitlines() if x.startswith("- Project")]
        self.assertTrue(projects[0].startswith("- Project: Oral history"))

    def test_core_experiences_lead_and_archived_ones_never_reach_the_block(self):
        src = {"source_rel": "Sources/Projects/a.md", "quote": "x", "field": "name", "doc_type": "notes"}
        ids = {name: store.add_found(self.con, "project", {"name": name}, src)
               for name in ("Oral history of the Wexley mills", "Choir rota", "Fishing game bot")}
        check_everything(self.con)
        tiers.set_tier(self.con, ids["Oral history of the Wexley mills"], "supporting")
        tiers.set_tier(self.con, ids["Choir rota"], "core")
        tiers.set_tier(self.con, ids["Fishing game bot"], "archive")
        block = profile.context_block(self.con, queries=["Recording oral history interviews for a heritage trust"])
        projects = [x for x in block.splitlines() if x.startswith("- Project")]
        self.assertEqual(projects, ["- Project: Choir rota", "- Project: Oral history of the Wexley mills"])

    def test_jobs_lead_with_the_core_tier_then_the_closest_to_the_job(self):
        src = {"source_rel": "Sources/CVs and letters/a.md", "quote": "x", "field": "title", "doc_type": "cv"}
        newest = store.add_found(self.con, "job", {"title": "Bar staff", "employer": "Old Mill Inn",
                                                   "start": "2025-01", "current": True}, src)
        archive = store.add_found(self.con, "job", {"title": "Archive assistant", "employer": "Brackwater Museum",
                                                    "start": "2021-01", "end": "2022-01"}, src)
        heritage = store.add_found(self.con, "job", {"title": "Heritage guide", "employer": "Fenwick Lantern Museum",
                                                     "start": "2019-01", "end": "2020-01"}, src)
        check_everything(self.con)
        for item_id in (newest, archive):          # one tier: the closer to the job leads, not the newer
            tiers.set_tier(self.con, item_id, "supporting")
        tiers.set_tier(self.con, heritage, "core")
        block = profile.context_block(self.con, queries=["Heritage guide for a museum"])
        jobs = [x.split(" at ")[0] for x in block.splitlines() if x.startswith("- Job")]
        self.assertEqual(jobs, ["- Job: Heritage guide", "- Job: Archive assistant", "- Job: Bar staff"])

    def test_contact_details_are_left_out_even_when_checked(self):
        humanities_profile(self.con)
        check_everything(self.con)
        block = profile.context_block(self.con)
        self.assertTrue(block.startswith(writing.HEADING))
        self.assertNotIn(HUMANITIES.you["email"], block)
        self.assertIn(f"Name: {HUMANITIES.you['name']}", block)

    def test_a_humanities_profile_gives_modules_marks_and_research_skills(self):
        humanities_profile(self.con)
        check_everything(self.con)
        block = profile.context_block(self.con)
        # one line of every module let a sentence about one module cite another's words (2026-11-08)
        self.assertIn("- Education: BA (Hons) History and Imaginary Studies at University of Nowhere Fen "
                      "(September 2022 to June 2025), grade 2:1\n"
                      "- Module (BA (Hons) History and Imaginary Studies): Early Modern Letters (72)\n"
                      "- Module (BA (Hons) History and Imaginary Studies): Oral History in Practice (68)\n", block)
        self.assertIn("- Job duty (Collections volunteer at Fenwick Lantern Museum Trust): Catalogued a donated "
                      "collection of letters", block)
        self.assertIn('Skill: Archival research (research). Shown by: "read the parish records in the county '
                      'archive"', block)
        self.assertIn("Collections volunteer at Fenwick Lantern Museum Trust (September 2023 to now), part-time",
                      block)

    def test_the_block_is_cut_at_a_whole_line_and_keeps_the_first_facts(self):
        humanities_profile(self.con)
        check_everything(self.con)
        short = profile.context_block(self.con, limit=SHORT)
        self.assertLessEqual(len(short), SHORT)
        full = profile.context_block(self.con).splitlines()
        kept = short.splitlines()
        self.assertEqual([x for x in full if x in kept], kept, "whole lines, in the full block's order")
        self.assertEqual(kept[:2], full[:2], "the heading and the name come first")
        self.assertTrue(all(line.startswith(("Profile", "- ")) for line in short.splitlines()))
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


class SimilarNames(unittest.TestCase):
    """Drafts gave a local assistant the purpose and stack of a job-search helper of a similar name
    (2026-11-08)."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)
        src = {"source_rel": "Sources/Projects/list.md", "quote": "x", "field": "name", "doc_type": "notes"}
        for name, summary in (("Quill", "An offline assistant over personal notes, built with Lua."),
                              ("Quill Employment", "A job-search helper for graduates."),
                              ("quill-voice", "Speech for the assistant"), ("Tide gauge", "Logs tides.")):
            store.add_found(self.con, "project", {"name": name, "summary": summary}, src)
        check_everything(self.con)

    def test_cards_with_similar_names_each_lead_with_words_of_their_own_summary(self):
        block = profile.context_block(self.con)
        self.assertIn("- Project: Quill (offline assistant over personal notes). An offline assistant", block)
        self.assertIn("- Project: Quill Employment (job-search helper). A job-search helper", block)
        self.assertIn("- Project: quill-voice (Speech). Speech for the assistant", block)
        self.assertIn("- Project: Tide gauge. Logs tides.", block)

    def test_the_cards_say_their_tag_and_the_names_they_could_be_mixed_up_with(self):
        found = {c["name"]: (c["tag"], c["similar"]) for c in writing.cards(self.con)}
        self.assertEqual(found["Quill Employment"], ("job-search helper", ["Quill", "quill-voice"]))
        self.assertEqual(found["Tide gauge"], ("", []))

    def test_names_alike_by_their_first_word_or_one_inside_the_other(self):
        self.assertEqual(writing.similar(["Quill", "Quill Employment", "quill-tools", "The Ferry", "The Tide"]),
                         {"Quill": ["Quill Employment", "quill-tools"], "Quill Employment": ["Quill", "quill-tools"],
                          "quill-tools": ["Quill", "Quill Employment"]})


OLD_CV, NEW_CV = "Sources/CVs and letters/CV draft.md", "Sources/CVs and letters/CV.md"
REPO = "Sources/Code/ferry-planner/README.md"


class NewestCv(unittest.TestCase):
    """An older CV draft gave a team project tools the newest CV does not claim and its code never used, and every
    pack then said the person used them (2026-11-08)."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.vault = self.home / "made-up record"
        self.enterContext(mock.patch.object(record, "VAULT", self.vault, create=True))
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def note(self, rel: str, period: str) -> None:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: source\ndoc_type: cv\nperiod: {period}\nrole: own\n---\n# CV\n")

    def ferry(self, rel: str, tools: list[str], doc_type: str = "cv", role: str = "own", **more) -> int:
        return store.add_found(self.con, "project", {"name": "Ferry planner", "tools": tools, **more},
                               {"source_rel": rel, "quote": "Ferry planner", "field": "name", "doc_type": doc_type,
                                "role": role})

    def seed(self) -> int:
        self.note(OLD_CV, "2021..2024")
        self.note(NEW_CV, "2022..2026")
        self.ferry(OLD_CV, ["React", "Flask", "Azure", "Docker"], summary="A timetable app hosted on a cloud")
        self.ferry(NEW_CV, ["React", "Flask"])
        item_id = self.ferry(REPO, ["Docker"], doc_type="repository")
        check_everything(self.con)
        return item_id

    def test_a_tool_only_an_older_cv_gives_is_left_out_of_writing(self):
        self.seed()
        cards = writing.cards(self.con)
        self.assertEqual(cards[0]["tools"], ["React", "Flask", "Docker"], "Docker is in the repository")
        block = profile.context_block(self.con)
        self.assertIn("Tools: React, Flask, Docker", block)
        self.assertNotIn("Azure", block)
        self.assertNotIn("hosted on a cloud", block)

    def test_a_quote_only_an_older_cv_gives_is_never_one_of_the_cards_quotes(self):
        self.seed()
        self.con.execute("UPDATE profile_evidence SET quote = 'Ferry planner ran on Azure' WHERE source_rel = ?",
                         (OLD_CV,))
        self.con.execute("UPDATE profile_evidence SET quote = 'Ferry planner shows the next ferry' "
                         "WHERE source_rel != ?", (OLD_CV,))
        self.con.commit()
        quotes = writing.cards(self.con)[0]["quotes"]
        self.assertEqual(quotes, ["Ferry planner shows the next ferry"], "the newest CV's and the repository's, once")

    def test_the_profile_says_which_values_only_an_older_cv_gives(self):
        item = store.item(self.con, self.seed())
        self.assertEqual(writing.older_cv_only(self.con, item),
                         {"summary": ["A timetable app hosted on a cloud"], "tools": ["Azure"]})

    def test_the_newest_cv_is_the_one_whose_own_period_ends_latest(self):
        self.note(OLD_CV, "2021..2026")
        self.note(NEW_CV, "2022..2024")
        self.ferry(OLD_CV, ["React", "Azure"])
        self.ferry(NEW_CV, ["React", "Lua"])
        check_everything(self.con)
        self.assertEqual(writing.CvAuthority.of(self.con).newest, OLD_CV)
        self.assertEqual(writing.cards(self.con)[0]["tools"], ["React", "Azure"])

    def test_with_one_cv_or_a_card_you_added_nothing_is_left_out(self):
        self.note(OLD_CV, "2021..2024")
        self.ferry(OLD_CV, ["React", "Azure"])
        check_everything(self.con)
        self.assertEqual(writing.cards(self.con)[0]["tools"], ["React", "Azure"])
        self.note(NEW_CV, "2022..2026")
        store.add_found(self.con, "skill", {"name": "Lua"},
                        {"source_rel": NEW_CV, "quote": "Lua", "doc_type": "cv", "role": "own"})
        mine = store.add_by_hand(self.con, "project", {"name": "Tide gauge", "tools": ["Azure"]})
        self.assertEqual(writing.older_cv_only(self.con, mine), {}, "a card you added is all yours")
        self.assertEqual([c["tools"] for c in writing.cards(self.con)], [[], ["Azure"]],
                         "the newest CV no longer names Ferry planner's tools")
        tiers.set_tier(self.con, mine["id"], tiers.CORE)
        self.assertEqual({c["name"]: c["tier"] for c in writing.cards(self.con)}["Tide gauge"], tiers.CORE,
                         "the ranking of a pack's work reads the tier you gave")


DESIGN = "Sources/Projects/Ferry planner/Design Document.md"


class PlannedNotDone(unittest.TestCase):
    """A team project card listed Docker and PostgreSQL because the team's design document planned them; the
    built app used SQLite, and every pack said the person used them (2026-11-08)."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def ferry(self, rel: str, tools: list[str], quote: str, doc_type: str) -> int:
        return store.add_found(self.con, "project", {"name": "Ferry planner", "tools": tools},
                               {"source_rel": rel, "quote": quote, "field": "tools", "doc_type": doc_type,
                                "role": "own"})

    def seed(self) -> int:
        self.ferry(DESIGN, ["Docker", "PostgreSQL"], "The app will use Docker and PostgreSQL", "report")
        item_id = self.ferry(REPO, ["SQLite"], "Stores timetables in SQLite", "repository")
        store.add_found(self.con, "skill", {"name": "Kubernetes"},
                        {"source_rel": DESIGN, "quote": "we plan to run it on Kubernetes", "doc_type": "report",
                         "role": "own"})
        store.add_found(self.con, "skill", {"name": "SQLite"},
                        {"source_rel": REPO, "quote": "Stores timetables in SQLite", "doc_type": "repository",
                         "role": "own"})
        check_everything(self.con)
        return item_id

    def test_a_tool_only_a_plan_gives_is_left_out_of_writing(self):
        self.seed()
        card = writing.cards(self.con)[0]
        self.assertEqual((card["tools"], card["planned"]), (["SQLite"], ["Docker", "PostgreSQL"]))
        block = profile.context_block(self.con)
        self.assertIn("Tools: SQLite", block)
        for planned_tool in ("Docker", "PostgreSQL", "Kubernetes"):
            self.assertNotIn(planned_tool, block)
        self.assertEqual(writing.skill_names(self.con), ["SQLite"], "a skill only a plan names is left out")
        self.assertIn("Docker", pack.known_tools(writing.cards(self.con)), "still a tool a draft may misplace")

    def test_the_profile_says_which_document_plans_each_value(self):
        item = store.item(self.con, self.seed())
        self.assertEqual(writing.planned_only(self.con, item),
                         {"tools": {"Docker": "Design Document", "PostgreSQL": "Design Document"}})
        mine = store.add_by_hand(self.con, "project", {"name": "Tide gauge", "tools": ["Docker"]})
        self.assertEqual(writing.planned_only(self.con, mine), {}, "a card you added is all yours")

    def test_a_cards_own_quotes_for_its_paragraph_never_hold_a_plan(self):
        # a paragraph is drafted from one card's quotes (2026-11-08); a plan's quote would bring Docker back
        self.seed()
        self.assertEqual(writing.cards(self.con)[0]["quotes"], ["Stores timetables in SQLite"])

    def test_a_tool_a_plan_and_a_record_of_the_work_both_give_is_kept(self):
        self.ferry(DESIGN, ["Docker"], "The app will use Docker", "report")
        self.ferry(REPO, ["Docker"], "Runs in Docker", "repository")
        check_everything(self.con)
        self.assertEqual(writing.cards(self.con)[0]["tools"], ["Docker"])


DOSSIER = "Sources/Projects/Portfolio overview.md"


def own(rel: str, doc_type: str, quote: str = "") -> dict:
    """Evidence from one of the person's own made-up files."""
    return {"source_rel": rel, "quote": quote, "field": "", "doc_type": doc_type, "role": "own"}


class Listings(unittest.TestCase):
    """A note describing every project backed a hidden copy of a sniffer and two other cards, so the copy's name
    became an alias of both and a letter put the sniffer in a navigation project (2026-11-08)."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)
        for name, repo in (("Quill", "quill"), ("Tide gauge", "tide-gauge"), ("Packet listener", "ferry-sniffer")):
            store.add_found(self.con, "project", {"name": name}, own(f"Sources/Code/{repo}/README.md", "repository"))
        store.add_found(self.con, "project", {"name": "Quill"},
                        own(DOSSIER, "project", "Quill answers questions over my notes offline"))
        store.add_found(self.con, "project", {"name": "Quill"},
                        own(DOSSIER, "project", "Packet listener fingerprints phones by their probe requests"))
        store.add_found(self.con, "project", {"name": "Tide gauge"}, own(DOSSIER, "project", "Tide gauge logs"))
        copy = store.add_found(self.con, "project", {"name": "Ferry sniffer"}, own(DOSSIER, "project"))
        store.add_found(self.con, "project", {"name": "Ferry sniffer"},
                        own("Sources/Code/ferry-sniffer/README.md", "repository"))
        check_everything(self.con)
        store.hide(self.con, copy)
        self.cards = {c["name"]: c for c in writing.cards(self.con)}

    def test_a_file_listing_projects_gives_no_card_an_alias(self):
        self.assertEqual(self.cards["Quill"]["aliases"], [])
        self.assertEqual(self.cards["Tide gauge"]["aliases"], ["tide-gauge"])
        self.assertEqual(self.cards["Packet listener"]["aliases"], ["Ferry sniffer", "ferry-sniffer"],
                         "a hidden copy still names the card whose own repository it rests on")

    def test_a_file_listing_projects_is_no_cards_own_file(self):
        self.assertEqual(self.cards["Quill"]["files"], ["Sources/Code/quill/README.md"])
        self.assertIn(DOSSIER, writing.listings(self.con))
        self.assertNotIn(DOSSIER, writing.passage_contexts(self.con))

    def test_a_listings_quote_naming_another_card_is_never_this_cards(self):
        self.assertEqual(self.cards["Quill"]["quotes"], ["Quill answers questions over my notes offline"])

    def test_a_listings_passage_counts_only_for_the_one_card_it_names(self):
        from jobhunt.writing import plan

        kept = (f"[{DOSSIER} > Tide gauge]\n(Kind: personal project)\nThe Tide gauge logs river levels.\n\n"
                f"[{DOSSIER} > Sniffer]\n(Kind: personal project)\nIt listens for probe requests.")
        found = plan.sources("", kept, list(self.cards.values()), writing.listings(self.con))
        self.assertEqual([(s.text, s.card["name"]) for s in found], [("The Tide gauge logs river levels.",
                                                                       "Tide gauge")])


class OwnFiles(unittest.TestCase):
    """An earlier project's card carried evidence from the repository of a later project with a longer name,
    and letters said the earlier one used the later one's database (2026-11-08)."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)
        later = "Sources/Code/quill-employment/README.md"
        self.quill = store.add_found(self.con, "project", {"name": "Quill", "tools": ["Lua"]},
                                     own("Sources/Code/quill/README.md", "repository", "Quill answers offline"))
        store.add_found(self.con, "project", {"name": "Quill", "tools": ["SQLite"]},
                        own(later, "repository", "Stores each application in SQLite"))
        store.add_found(self.con, "project", {"name": "Quill Employment", "tools": ["Preact", "SQLite"]},
                        own(later, "repository", "Quill Employment drafts letters"))
        check_everything(self.con)
        self.cards = {c["name"]: c for c in writing.cards(self.con)}

    def test_another_projects_own_file_gives_this_card_no_tool_quote_file_or_alias(self):
        quill = self.cards["Quill"]
        self.assertEqual((quill["tools"], quill["quotes"]), (["Lua"], ["Quill answers offline"]))
        self.assertEqual((quill["files"], quill["aliases"]), (["Sources/Code/quill/README.md"], []))
        line = next(x for x in profile.context_block(self.con).splitlines() if ": Quill" in x and "Employment" not in x)
        self.assertTrue(line.endswith("Tools: Lua"), line)

    def test_the_project_whose_file_it_is_keeps_it(self):
        employment = self.cards["Quill Employment"]
        self.assertEqual(employment["tools"], ["Preact", "SQLite"])
        self.assertEqual(employment["files"], ["Sources/Code/quill-employment/README.md"])

    def test_a_cards_quotes_are_sentences_about_the_work_with_its_readmes_first_by_result(self):
        # a card's quotes held a table row and a credit line while its README's test suite was missing
        # (2026-11-08)
        vault = self.home / "made-up record"
        self.enterContext(mock.patch.object(record, "VAULT", vault, create=True))
        note = vault / "Sources/Code/quill/README.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\ntype: source\ndoc_type: repository\n---\n# Quill\n\n| Quill | Yes |\n\n"
                        "Quill answers questions over a folder of notes with no network. A 40-test suite checks "
                        "every answer against its note.\n")
        for junk in ("quill | Yes | https://example.org/avery/quill", "Built by [@avery](https://example.org)"):
            store.add_found(self.con, "project", {"name": "Quill"},
                            own("Sources/Code/quill/README.md", "repository", junk))
        quotes = {c["name"]: c for c in writing.cards(self.con)}["Quill"]["quotes"]
        self.assertEqual(quotes, ["A 40-test suite checks every answer against its note.",
                                  "Quill answers questions over a folder of notes with no network.",
                                  "Quill answers offline"])

    def test_a_repository_note_named_with_an_imports_suffix_gives_this_card_nothing(self):
        # ingest names a repository's note "<repo> repository"; that note's tools, name and quotes stayed on
        # an earlier card with a shorter name, and letters said it had the later one's front end (2026-11-08)
        store.add_found(self.con, "project", {"name": "Quill", "tools": ["Preact"]},
                        own("Sources/Code/quill-employment repository.md", "repository", "Drafts letters in Preact"))
        store.add_found(self.con, "project", {"name": "Quill Employment",
                                              "link": "https://example.org/avery/quill-employment"},
                        own("Sources/Code/quill-employment/README.md", "repository", ""))
        check_everything(self.con)
        quill = {c["name"]: c for c in writing.cards(self.con)}["Quill"]
        self.assertEqual((quill["tools"], quill["quotes"]), (["Lua"], ["Quill answers offline"]))
        self.assertEqual((quill["files"], quill["aliases"]), (["Sources/Code/quill/README.md"], []))

    def test_the_record_keeps_the_evidence_as_it_was_stored(self):
        self.assertIn("SQLite", store.item(self.con, self.quill)["fields"]["tools"])
        self.assertEqual(writing.elsewhere_only(self.con, store.item(self.con, self.quill)), {"tools": ["SQLite"]})


if __name__ == "__main__":
    unittest.main()
