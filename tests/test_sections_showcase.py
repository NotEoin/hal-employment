"""Showcase and authority for projects: only starred projects are told as stories once any is starred (all of them
while none is), unstarred ones still give skills, and a project's own source outranks a CV's line about it.

The person is Avery Quillon; every project and CV line is made up.
"""
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import graduates, repos
from test_ingest import home
from test_sections_coursework import add as coursework_added
from test_sections_manual import FORM
from test_sections_manual import added as manual_added
from test_sections_repo import README, me

from jobhunt import pack
from jobhunt.profile import store as profile_store
from jobhunt.profile import writing
from jobhunt.sections import pipeline
from jobhunt.sections import store as sources
from jobhunt.writing import plan, rank

CV = {"source_rel": "Sources/CVs/Avery CV.md", "doc_type": "cv"}


def project(con, name: str, star: bool = False) -> int:
    """A checked project card from the CV, starred or not; its id."""
    item_id = profile_store.add_found(con, "project", {"name": name, "summary": f"The made-up {name}."}, CV)
    profile_store.confirm(con, item_id)
    if star:
        profile_store.set_star(con, item_id, True)
    return item_id


def story(card: dict) -> plan.Story:
    """A plan's story about one card."""
    return plan.Story(key=card["name"], name=card["name"], where="", card=card, kind=plan.PROJECT,
                      facts=(card["summary"],))


class Showcase(unittest.TestCase):
    def test_once_a_project_is_starred_only_starred_projects_are_offered_as_stories(self):
        with home() as (con, _, _):
            project(con, "Tide gauge", star=True)
            project(con, "Harbour bot")
            cards = writing.cards(con)
        self.assertEqual({c["name"]: c["star"] for c in cards}, {"Tide gauge": True, "Harbour bot": False})
        self.assertEqual([c["name"] for c in rank.candidates([], cards)], ["Tide gauge"])
        told = rank.order([story(c) for c in cards], cards, [], "tides harbour", n=3)
        self.assertEqual([s.name for s in told.letter], ["Tide gauge"])
        self.assertNotIn("Harbour bot", [s.name for s in told.others])

    def test_with_nothing_starred_every_project_is_offered_as_before(self):
        with home() as (con, _, _):
            project(con, "Tide gauge")
            project(con, "Harbour bot")
            cards = writing.cards(con)
        self.assertEqual(sorted(c["name"] for c in rank.candidates([], cards)), ["Harbour bot", "Tide gauge"])
        self.assertEqual(plan.story_cards(cards), cards)

    def test_a_job_is_always_offered_whatever_is_starred(self):
        cards = [{"kind": "project", "name": "Tide gauge", "star": True},
                 {"kind": "job", "name": "Barista at Fen Cafe"},
                 {"kind": "project (personal)", "name": "Harbour bot", "star": False}]
        self.assertEqual([c["name"] for c in plan.story_cards(cards)], ["Tide gauge", "Barista at Fen Cafe"])


class Authority(unittest.TestCase):
    def test_a_projects_own_code_outranks_the_cv_line_about_it(self):
        with home() as (con, _, files):
            me()
            profile_store.add_found(con, "project", {"name": "tide-gauge", "role": "team member"}, CV)
            folder = repos.make(files / "tide-gauge", {"README.md": README, "app.py": "print(1)\n"})
            pipeline.read(con, pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0])
            card = profile_store.items(con, "project", hidden=False)[0]
        self.assertEqual(card["fields"]["role"], "sole author")

    def test_a_cv_still_wins_over_a_fact_read_before_the_sections(self):
        with home() as (con, _, _):
            profile_store.add_found(con, "project", {"name": "Tide gauge", "role": "team member"}, CV)
            profile_store.add_found(con, "project", {"name": "Tide gauge", "role": "lead"},
                                    {"source_rel": "Sources/Code/tide.md", "doc_type": "repository"})
            card = profile_store.items(con, "project", hidden=False)[0]
        self.assertEqual(card["fields"]["role"], "team member")


class Stories(unittest.TestCase):
    """Stories come from the sections: a project's own sources, starred coursework and jobs, a starred job first."""

    def test_a_projects_story_tells_its_own_sources_and_marks_what_only_a_cv_fills(self):
        with home() as (con, _, _):
            profile_store.add_found(con, "project", {"name": FORM["name"], "tools": ["Excel"]},
                                    {**CV, "quote": "Lantern letters exhibition: built the catalogue in Excel"})
            pipeline.read(con, manual_added(con, {k: v for k, v in FORM.items() if k != "tools"}))
            profile_store.confirm(con, profile_store.items(con, "project")[0]["id"])
            (card,) = writing.cards(con)
        self.assertIn("- What it was: A made-up exhibition of 40 letters from the Fenwick archive.", card["quotes"])
        self.assertFalse([q for q in card["quotes"] if "catalogue in Excel" in q], "a CV's quote is not the project's")
        self.assertEqual(card["cv_filled"], {"tools": ["Excel"]})
        block = plan.card_story(card).block()
        self.assertNotIn("Tools: Excel", block)
        self.assertIn(f"{plan.CV_FILLED_WORDS}:\n- tools: Excel", block)

    def test_a_card_from_the_older_bulk_read_still_gets_its_notes_sentences(self):
        with home() as (con, _, _):
            item = profile_store.add_found(con, "project", {"name": "Tide gauge"},
                                           {"source_rel": "Sources/Code/tide.md", "doc_type": "repository",
                                            "quote": "Tide gauge reads tide heights every ten minutes for 40 sailors"})
            profile_store.confirm(con, item)
            (card,) = writing.cards(con)
        self.assertEqual(card["quotes"], ["Tide gauge reads tide heights every ten minutes for 40 sailors"])
        self.assertEqual((card["cv_only"], card["cv_filled"], card["dissertation"]), (False, {}, False))

    def test_a_project_only_a_cv_names_is_told_only_while_nothing_is_starred_and_says_so(self):
        cv_only = {"kind": "project", "name": "Harbour bot", "summary": "A made-up bot.", "cv_only": True,
                   "quotes": ["Harbour bot answers sailors"]}
        job = {"kind": "job", "name": "Lab helper at University of Nowhere Fen", "summary": "Helped in labs",
               "quotes": ["Helped first-year students in programming labs"], "star": False}
        self.assertEqual(plan.story_cards([cv_only, job]), [cv_only, job])
        self.assertIn(f"Where its facts come from: {plan.CV_ONLY_WORDS}", plan.card_story(cv_only).block())
        self.assertEqual(plan.story_cards([cv_only, {**job, "star": True}]), [{**job, "star": True}])

    def test_a_starred_job_leads_the_letter_whatever_the_ranking(self):
        with home() as (con, _, _):
            project(con, "Tide gauge")
            job = profile_store.add_found(con, "job", {"title": "Lab helper", "employer": "University of Nowhere Fen",
                                                       "duties": ["Helped first-year students in programming labs"]},
                                          {**CV, "quote": "Helped first-year students in programming labs"})
            profile_store.confirm(con, job)
            profile_store.set_star(con, job, True)
            cards = writing.cards(con)
        stories = [story(c) for c in cards]
        ranked = [rank.Ranked("Tide gauge"), rank.Ranked("Lab helper at University of Nowhere Fen")]
        self.assertEqual(rank.order(stories, cards, ranked, "tides").letter[0].name,
                         "Lab helper at University of Nowhere Fen")
        self.assertEqual(plan.letter_stories(stories[:1], cards, "tides")[0].name,
                         "Lab helper at University of Nowhere Fen", "a starred job is told though the plan left it")

    def test_a_showcase_dissertation_is_told_under_its_title_as_the_dissertation(self):
        with home() as (con, _, _):
            thesis = coursework_added(con, graduates.education(con, "ba"), "final_v3.md",
                                      graduates.HISTORY_DISSERTATION)
            sources.change(con, thesis, {"dissertation": True})
            sources.change(con, thesis, {"starred": True})
            pipeline.read(con, thesis)
            profile_store.confirm(con, profile_store.items(con, "project")[0]["id"])
            (card,) = writing.cards(con)
        told = plan.card_story(card)
        self.assertEqual(told.name, "Bread, rivers and riot: grain prices on the Fen, 1790 to 1801")
        self.assertTrue(card["star"] and card["dissertation"])
        self.assertIn(f"What kind of work: {plan.DISSERTATION_WORDS}", told.block())
        self.assertIn("Where it was done: degree work", told.block())
        self.assertTrue(told.facts, "a showcase piece is told from its own summary")


class Sources(unittest.TestCase):
    """Each card and fit row says where its facts come from; an older CV's tool is kept apart."""

    def test_each_card_says_where_its_facts_come_from_and_an_older_cvs_tool_is_kept_apart(self):
        with home() as (con, _, _):
            pipeline.read(con, manual_added(con))
            old = profile_store.add_found(con, "project", {"name": "Harbour bot", "tools": ["Java"]},
                                          {"source_rel": "Sources/CVs/old.md", "doc_type": "cv", "role": "own"})
            profile_store.add_found(con, "project", {"name": "Harbour bot", "summary": "A made-up bot."},
                                    {"source_rel": "Sources/CVs/new.md", "doc_type": "cv", "role": "own"})
            with mock.patch.object(writing, "_cv_key", lambda con, rel: (2025 if "new" in rel else 2020, "")):
                profile_store.confirm_many(con, [c["id"] for c in profile_store.items(con, "project")])
                cards = {c["name"]: c for c in writing.cards(con)}
        self.assertEqual(cards[FORM["name"]]["source"], "from what you typed")
        self.assertEqual(cards["Harbour bot"]["source"], "from your current CV")
        self.assertEqual((cards["Harbour bot"]["tools"], cards["Harbour bot"]["older_cv"]), ([], ["Java"]))
        self.assertTrue(old)

    def test_a_fit_row_cites_the_card_it_names_or_the_cv(self):
        cards = [{"kind": "project (personal project)", "name": "Tide gauge", "tools": ["Python"], "summary": "",
                  "source": "from the code project"}]
        fit = pack.with_sources({"matches": [{"requirement": "Python", "evidence": "Tide gauge reads tides in Python"},
                                             {"requirement": "Teamwork", "evidence": "Helped first-year students"}]},
                                cards, "Lab helper\n- Helped first-year students in programming labs")
        self.assertEqual([m["source"] for m in fit["matches"]], ["from the code project", "from your current CV"])


if __name__ == "__main__":
    unittest.main()
