"""Checks on finished drafts with no AI: a reply cut off, a greeting to the applicant, a tool on the wrong project,
a project placed where it was not done, a sentence that restates the advert.

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

    def test_a_tool_is_taken_out_of_the_list_it_stands_in(self):
        self.assertEqual(drafts.without_tools("Tide gauge uses Lua, Pandas and SQL.", ["Pandas"]),
                         "Tide gauge uses Lua and SQL.")
        self.assertEqual(drafts.without_tools("Pandas, Lua, and SQL", ["Pandas", "SQL"]), "Lua")
        self.assertEqual(drafts.without_tools("Tide gauge (tools: Pandas)", ["Pandas"]), "Tide gauge")

    def test_a_tool_is_backed_by_the_cards_own_facts_or_a_cv_line_naming_both(self):
        self.assertTrue(drafts.backed("Lua", CARDS[:1]))
        self.assertFalse(drafts.backed("C++", CARDS[:1]))
        self.assertTrue(drafts.backed("C++", CARDS[:1], "Project, Tide gauge (Lua, C++): logs tides."))
        self.assertFalse(drafts.backed("C++", CARDS[:1], "Skills: C++\nProject, Tide gauge (Lua): logs tides."))

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


QUILL = {"kind": "project (personal project)", "name": "Quill", "tools": ["Lua", "SQLite"],
         "summary": "An offline assistant over personal notes, built with Lua.",
         "tag": "offline assistant over personal notes", "similar": ["Quill Employment"]}
QUILL_JOBS = {"kind": "project (personal project)", "name": "Quill Employment", "tools": ["Python", "Flask"],
              "summary": "A job-search helper for graduates.", "tag": "job-search helper", "similar": ["Quill"]}
ALIKE = [QUILL, QUILL_JOBS, *CARDS]


class SimilarNames(unittest.TestCase):
    """Drafts gave a local assistant the purpose and stack of a job-search helper of a similar name
    (2026-11-08)."""

    def test_a_longer_name_names_its_own_card_and_not_the_shorter_one_inside_it(self):
        self.assertEqual([c["name"] for c in drafts.named_cards("Quill Employment drafts letters.", ALIKE)],
                         ["Quill Employment"])
        self.assertEqual([c["name"] for c in drafts.named_cards("Quill reads notes; Quill Employment drafts "
                                                                "letters.", ALIKE)], ["Quill", "Quill Employment"])

    def test_one_cards_purpose_or_tool_given_to_its_namesake_is_found(self):
        for said, hit, whose, card in (
                ("I built Quill, a local job-search helper.", "job-search", "Quill Employment", "Quill"),
                ("Quill runs on Flask.", "Flask", "Quill Employment", "Quill"),
                ("Quill Employment answers questions over my personal notes.", "notes", "Quill",
                 "Quill Employment")):
            with self.subTest(said=said):
                self.assertEqual(drafts.crossed(said, ALIKE), [f"{said} ({hit} is {whose}'s, not {card}'s)"])

    def test_each_card_with_its_own_purpose_and_tools_is_left_alone(self):
        for said in ("Quill is an offline assistant over my notes, written in Lua.",
                     "Quill Employment is a job-search helper built with Flask.",
                     "Tide gauge logs tides in Lua.", "Quill Employment is a personal project of mine."):
            with self.subTest(said=said):
                self.assertEqual(drafts.crossed(said, ALIKE), [])

    def test_a_cv_line_of_the_longer_name_never_backs_a_tool_for_the_shorter(self):
        cv_line = "Quill Employment: Python, Flask, PostgreSQL"
        self.assertFalse(drafts.backed("PostgreSQL", [QUILL], cv_line, ALIKE))
        self.assertTrue(drafts.backed("PostgreSQL", [QUILL_JOBS], cv_line, ALIKE))

    def test_evidence_giving_one_card_its_namesakes_tool_loses_the_tool_and_names_each_with_its_tag(self):
        # the fit table and the letter's evidence notes said the assistant used the job-search helper's stack
        # (2026-11-08)
        known = ["Lua", "SQLite", "Python", "Flask", "PostgreSQL"]
        cv_line = "Quill Employment: Python, Flask, PostgreSQL"
        said, dropped = drafts.evidence_checked("Quill using Flask and Lua; Quill Employment uses PostgreSQL.",
                                                ALIKE, known, cv_line)
        self.assertTrue(dropped)
        self.assertEqual(said, "Quill (offline assistant over personal notes) using Lua; Quill Employment "
                               "(job-search helper) uses PostgreSQL.")
        said, dropped = drafts.evidence_checked("Quill uses PostgreSQL.", ALIKE, known, cv_line)
        self.assertEqual((said, dropped), ("", True), "a part left with no tool is dropped")
        said, dropped = drafts.evidence_checked("Tide gauge logs tides in Lua.", ALIKE, known)
        self.assertEqual((said, dropped), ("Tide gauge logs tides in Lua.", False))

    def test_a_tag_already_given_is_not_given_twice(self):
        said = "Quill (offline assistant over personal notes) runs on Lua."
        self.assertEqual(drafts.tagged(said, ALIKE), said)


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

    def test_a_project_is_caught_under_its_other_names(self):
        # "During my degree, I built a real-time autopilot for the LIDAR Autonavigation project" of a personal
        # project whose card is named "LIDAR Autonomous Navigation" (2026-11-07)
        cards = [*PLACED, {"kind": "project (personal project)", "name": "LIDAR Autonomous Navigation",
                           "tools": [], "summary": "", "context": "personal", "aliases": ["lidar-autonav"]},
                 {"kind": "project (personal project)", "name": "Kelp counter", "tools": [], "summary": "",
                  "context": "personal", "aliases": ["Seaweed tally"]}]
        for said in ("During my degree, I built a real-time autopilot for the LIDAR Autonavigation project.",
                     "My degree included the lidar autonav repository.",
                     "Seaweed tally was coursework for my degree."):
            with self.subTest(said):
                self.assertEqual(len(drafts.wrong_context(said, cards)), 1)
        self.assertEqual(drafts.wrong_context("My degree taught me about LIDAR.", cards), [],
                         "a word of the name used as a plain word names no project")

    def test_words_that_agree_or_say_nothing_of_where_it_was_done_are_left_alone(self):
        for said in ("Alongside my degree, I built Tide gauge in my own time.",
                     "Tide gauge loads a Lua module and keeps personal data out of its logs.",
                     "Ferry planner was a group project for my degree.",
                     "Lamp list was a personal project.",
                     "Tide gauge and Harbour survey were both for my degree."):
            with self.subTest(said):
                self.assertEqual(drafts.wrong_context(said, PLACED), [])


ADVERT = ("Graduate Credit Analyst\nBrightloan | Made-up Town\n\nJoin our credit team and take on the challenge of "
          "maintaining low default rates while we grow. The role offers full in-house training and a clear path "
          "to senior analyst. You will work on data quality across our lending platform.")
BRIEF = "Brightloan is a made-up consumer lender that uses open banking data to price personal loans."
NAMES = ("Graduate Credit Analyst", "Brightloan")


class Echoes(unittest.TestCase):
    def test_a_sentence_that_restates_the_advert_or_brief_echoes(self):
        # "drawn to the challenge of maintaining low default rates", "the role offers full in-house training"
        # (2026-11-07)
        for said in ("I am drawn to the challenge of maintaining low default rates.",
                     "The role offers full in-house training, which appeals to me.",
                     "I like that Brightloan uses open banking data to price personal loans."):
            with self.subTest(said):
                self.assertTrue(drafts.echoes(said, ADVERT, BRIEF, NAMES))

    def test_naming_the_role_or_tying_your_own_work_to_the_employer_is_no_echo(self):
        for said in ("I am applying for the Graduate Credit Analyst role at Brightloan.",
                     "My Tide gauge project taught me to clean messy sensor data, which matches your work on data "
                     "quality.",
                     "Brightloan's use of open banking data connects with my dissertation on how small lenders "
                     "score credit."):
            with self.subTest(said):
                self.assertFalse(drafts.echoes(said, ADVERT, BRIEF, NAMES))

    def test_only_the_letters_first_paragraph_and_why_questions_are_read(self):
        first = "I am drawn to the challenge of maintaining low default rates."
        self.assertEqual(drafts.first_paragraph(f"{first}\n\nI built Tide gauge."), first)
        self.assertTrue(drafts.why_question("Why do you want to work here?"))
        self.assertTrue(drafts.why_question("What motivates you to apply?"))
        self.assertFalse(drafts.why_question("Why are you a good fit for this role?"))


if __name__ == "__main__":
    unittest.main()
