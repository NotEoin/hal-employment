"""Checks on finished drafts with no AI: a reply cut off, a greeting to the applicant, a tool on the wrong project,
a project placed where it was not done.

The drafts, names and projects are made up.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import drafts

CARDS = [{"kind": "project (personal project)", "name": "Tide gauge", "tools": ["Lua"], "summary": "Logs tides."},
         {"kind": "project (degree work)", "name": "Harbour survey", "tools": ["C++"], "summary": ""}]


class CutOff(unittest.TestCase):
    def test_a_reply_that_stops_mid_word_is_cut_off(self):
        self.assertTrue(drafts.cut_off("I catalogued letters. I would bring the distingu"))

    def test_a_sign_off_or_a_closing_question_is_not_a_cut(self):
        self.assertFalse(drafts.cut_off("Who leads the team?\n\nBest wishes,\nAvery Quillon"))
        self.assertFalse(drafts.cut_off("I catalogued letters at a made-up museum."))

    def test_a_cut_draft_keeps_its_last_full_sentence(self):
        self.assertEqual(drafts.last_full_sentence("I catalogued letters. Who leads it? I would bring the dist"),
                         "I catalogued letters. Who leads it?")


class Greeting(unittest.TestCase):
    def test_a_greeting_naming_the_applicant_is_found_and_replaced(self):
        message = "Hi Avery,\n\nI applied for the role."
        self.assertTrue(drafts.greets_applicant(message, ["Avery Quillon", "Avery"]))
        self.assertFalse(drafts.greets_applicant("Dear Ada Penhallow,\n\nAvery here.", ["Avery"]))
        self.assertEqual(drafts.regreet(message, "Hello,"), "Hello,\n\nI applied for the role.")


class Tools(unittest.TestCase):
    def test_a_tool_another_card_lists_is_flagged_on_a_project_that_never_used_it(self):
        found = drafts.misplaced_tools("My Tide gauge needed C++ and threads. My Harbour survey used C++.", CARDS,
                                       ["Lua", "C++"])
        self.assertEqual(found, ["My Tide gauge needed C++ and threads. (C++ is not among Tide gauge's tools)"])

    def test_a_sentence_naming_two_projects_is_left_alone(self):
        self.assertEqual(drafts.misplaced_tools("Tide gauge and Harbour survey used Lua and C++.", CARDS,
                                                ["Lua", "C++"]), [])

    def test_short_tools_match_only_in_their_capitals(self):
        self.assertTrue(drafts.names_tool("Written in R and C#", "R"))
        self.assertFalse(drafts.names_tool("a r value", "R"))
        self.assertTrue(drafts.names_tool("used c++ daily", "C++"))


# Made-up project cards with the context each one's card keeps (`profile.contexts`).
PLACED = [{"kind": "project (personal project)", "name": "Tide gauge", "tools": [], "summary": "",
           "context": "personal"},
          {"kind": "project (university team project)", "name": "Ferry planner", "tools": [], "summary": "",
           "context": "team"},
          {"kind": "project (degree work)", "name": "Harbour survey", "tools": [], "summary": "", "context": "degree"},
          {"kind": "project", "name": "Lamp list", "tools": [], "summary": "", "context": ""}]


class Contexts(unittest.TestCase):
    def test_a_personal_project_placed_in_a_degree_is_found(self):
        # "My first-class Computer Science degree includes building a local RAG assistant", a personal project
        # (2026-11-07)
        said = "My first-class Imaginary Studies degree includes building Tide gauge."
        self.assertEqual(drafts.wrong_context(said, PLACED),
                         [f"{said} (Tide gauge is a personal project, not part of a degree)"])
        self.assertEqual(len(drafts.wrong_context("Tide gauge became my research into tides.", PLACED)), 1)

    def test_a_team_or_degree_project_called_personal_is_found(self):
        # a university team project called a personal project, and coursework called "personal work" (2026-11-07)
        found = drafts.wrong_context("Ferry planner was a personal project. Harbour survey was personal work.", PLACED)
        self.assertEqual(found, ["Ferry planner was a personal project. (Ferry planner is a university team "
                                 "project, not a personal project)",
                                 "Harbour survey was personal work. (Harbour survey is degree work, not a "
                                 "personal project)"])

    def test_words_that_agree_or_say_nothing_of_where_it_was_done_are_left_alone(self):
        for said in ("Alongside my degree, I built Tide gauge in my own time.",
                     "Tide gauge loads a Lua module and keeps personal data out of its logs.",
                     "Ferry planner was a group project for my degree.",
                     "Lamp list was a personal project.",
                     "Tide gauge and Harbour survey were both for my degree."):
            with self.subTest(said):
                self.assertEqual(drafts.wrong_context(said, PLACED), [])


if __name__ == "__main__":
    unittest.main()
