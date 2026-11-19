"""The work a job's drafts are written from, chosen by code: which cards, and what each one says."""
from __future__ import annotations

import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import plan

TIDE = {"kind": "project (personal project)", "name": "Tide gauge", "tools": ["Lua"], "summary": "Logs river levels.",
        "context": "personal", "aliases": [], "role": "", "files": ["Sources/Code/Tide gauge.md"],
        "quotes": ["Tide gauge has logged river levels every ten minutes since 2024"]}
FERRY = {"kind": "project (university team project)", "name": "Ferry planner", "tools": ["Python", "Flask"],
         "summary": "A timetable app built by a team of 7.", "context": "team", "aliases": [],
         "role": "built the statistics features; team of 7", "files": [],
         "quotes": ["The Ferry planner's statistics page shows late sailings by route"]}
CAFE = {"kind": "job", "name": "Barista at Fen Cafe", "tools": [], "summary": "Served customers and ran the till.",
        "context": "work", "aliases": [], "role": "", "files": [], "quotes": ["Ran the till on busy weekend shifts"]}
QUILL = {"kind": "project (personal project)", "name": "Quill", "tools": ["Lua"], "context": "personal",
         "summary": "An offline assistant that answers questions over notes.", "aliases": [], "role": "",
         "files": [], "quotes": ["Quill answers questions over a folder of notes with a 40-test suite."]}


class Chosen(unittest.TestCase):
    """Code chooses the work: an AI plan, ranking and pairing took most of an application's 24 to 27 calls
    (owner, 2026-11-19)."""

    def test_the_work_the_fit_map_cites_and_the_job_names_comes_first(self):
        wanted = "Python dashboards of late sailings and timetables"
        told = plan.chosen([TIDE, CAFE, FERRY], wanted)
        self.assertEqual(told[0].name, "Ferry planner")
        cited = plan.fit_names({"matches": [{"requirement": "Customer service", "evidence": "Barista at Fen Cafe",
                                             "strength": "strong"}]}, [TIDE, CAFE, FERRY])
        self.assertEqual(cited, {"Barista at Fen Cafe": 2})
        self.assertEqual(plan.chosen([TIDE, CAFE, FERRY], "customer service", cited)[0].name, "Barista at Fen Cafe")

    def test_at_most_the_given_number_of_pieces_and_never_a_card_with_nothing_to_say(self):
        empty = {**QUILL, "name": "Empty", "quotes": [], "summary": ""}
        self.assertEqual(len(plan.chosen([TIDE, FERRY, CAFE, QUILL, empty], "", n=plan.WORK_GIVEN)), plan.WORK_GIVEN)
        self.assertNotIn("Empty", [s.name for s in plan.chosen([TIDE, empty], "")])

    def test_a_starred_job_leads_and_unstarred_projects_wait_while_any_project_is_starred(self):
        told = [s.name for s in plan.chosen([TIDE, {**FERRY, "star": True}, {**CAFE, "star": True}], "tides")]
        self.assertEqual(told, ["Barista at Fen Cafe", "Ferry planner"], "Tide gauge is not starred")

    def test_a_job_only_older_cvs_give_comes_after_every_other(self):
        older = {**CAFE, "older_cv_job": True}
        self.assertEqual([s.name for s in plan.chosen([older, TIDE], "till customers")], ["Tide gauge",
                                                                                         "Barista at Fen Cafe"])
        self.assertIn(plan.OLDER_CV_JOB_WORDS, plan.card_story(older).block())


class Story(unittest.TestCase):
    def test_a_story_reads_as_plain_lines_under_its_name_with_where_and_the_part(self):
        self.assertEqual(plan.card_story(FERRY).block(),
                         "Project: Ferry planner\nWhere it was done: university team project\nYour part: built the "
                         "statistics features; team of 7\nWhat it is: A timetable app built by a team of 7.\n"
                         "Tools: Python, Flask\nFacts:\n- The Ferry planner's statistics page shows late sailings by "
                         "route")
        self.assertTrue(plan.card_story(CAFE).block().startswith("Job: Barista at Fen Cafe\nWhere it was done: a job"))

    def test_a_story_keeps_only_the_tools_its_facts_name_unless_they_name_none(self):
        # a LIDAR project was drafted "using Lua and Python" when no fact of it named Python (2026-11-08)
        lidar = {**TIDE, "name": "LIDAR mapper", "tools": ["Lua", "Python"], "summary": "Maps rooms with a LIDAR."}
        story = plan.card_story({**lidar, "quotes": ["LIDAR mapper reads each sweep in Lua"]})
        self.assertEqual(story.tools, ("Lua",))
        self.assertNotIn("Python", story.words())
        self.assertEqual(plan.card_story({**lidar, "quotes": ["LIDAR mapper reads each sweep"]}).tools,
                         ("Lua", "Python"), "with no tool named, the card's list is all there is")

    def test_a_tool_only_a_plan_gives_is_listed_as_planned_never_used(self):
        # a design document's planned tools became "I used Docker" in every pack (2026-11-08)
        block = plan.card_story({**FERRY, "planned": ["Docker"]}).block()
        self.assertIn(f"{plan.PLANNED_WORDS}: Docker", block)
        self.assertNotIn("Docker", plan.card_story({**FERRY, "planned": ["Docker"]}).words())

    def test_school_work_says_school_work(self):
        self.assertIn("Where it was done: school work", plan.card_story({**TIDE, "context": "school"}).block())

    def test_a_card_with_a_namesake_says_its_tag(self):
        self.assertTrue(plan.card_story({**QUILL, "tag": "offline assistant"}).block().startswith(
            "Project: Quill (offline assistant)\n"))


class Modules(unittest.TestCase):
    def test_the_strongest_modules_the_job_names_that_no_work_shows_are_given(self):
        # letters to an employer stressing security missed high marks in security modules (2026-11-08)
        modules = [("Network Security", "91"), ("Applied Cryptography", "88"), ("Security Law", "70"),
                   ("Oral History", "95")]
        self.assertEqual(plan.relevant_modules(modules, "Secure networks and cryptography; security reviews",
                                               "Quill answers questions"),
                         ("Network Security (91)", "Applied Cryptography (88)"))


class Listings(unittest.TestCase):
    def test_a_passage_of_a_file_listing_projects_is_kept_only_when_it_names_one_card(self):
        # a dossier of every project put a network sniffer's facts beside a navigation project (2026-11-08)
        kept = ("Profile (facts about the applicant from their files, each checked by them):\n- Skill: Lua\n\n"
                "[Notes/projects.md > Tide gauge]\n(Kind: personal project)\nThe Tide gauge logs river levels.\n\n"
                "[Notes/projects.md > Sniffer]\n(Kind: personal project)\nIt listens for probe requests.\n\n"
                "[Sources/Code/Tide gauge.md > Readme]\nA logger in Lua.")
        out = plan.without_listings(kept, [TIDE, FERRY], frozenset({"Notes/projects.md"}))
        self.assertIn("[Notes/projects.md > Tide gauge]\n(Kind: personal project)\nThe Tide gauge logs", out)
        self.assertNotIn("probe requests", out)
        self.assertIn("[Sources/Code/Tide gauge.md > Readme]\nA logger in Lua.", out)
        self.assertTrue(out.startswith("Profile (facts"))
        self.assertEqual(plan.without_listings(kept, [TIDE], frozenset()), kept)


if __name__ == "__main__":
    unittest.main()
