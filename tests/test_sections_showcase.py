"""Showcase and authority for projects: only starred projects are told as stories once any is starred (all of them
while none is), unstarred ones still give skills, and a project's own source outranks a CV's line about it.

The person is Avery Quillon; every project and CV line is made up.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import repos
from test_ingest import home
from test_sections_repo import README, me

from jobhunt.profile import store as profile_store
from jobhunt.profile import writing
from jobhunt.sections import pipeline
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


if __name__ == "__main__":
    unittest.main()
