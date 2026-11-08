"""Plain first-person writing: wording that shows how a draft was made, and the tidy before a draft is saved.

The sentences are made up, in the shapes a local model wrote in 15 graded packs (2026-11-07).
"""
import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.core import settings
from jobhunt.writing import plain

NAMES = ["Avery Quillon", "Avery"]
LABELS = ["UK, no visa needed", "British or Irish citizen"]


class Problems(unittest.TestCase):
    def test_wording_that_shows_how_the_draft_was_made_is_found(self):
        cases = {
            "For the requirement to demonstrate strong coding skills, I built a tide logger.": plain.LEAD_IN_SAID,
            "Regarding the need for SQL, I wrote the queries of a made-up booking app.": plain.LEAD_IN_SAID,
            "As required, I have used Git on every project.": plain.LEAD_IN_SAID,
            "My CV lists C++ as key skills.": plain.CV_SAID,
            "As listed in my CV, I volunteered at a made-up heritage centre.": plain.CV_SAID,
            "The applicant developed this assistant in Python.": plain.THIRD_SAID,
            "Avery Quillon has built a sensor logger.": plain.THIRD_SAID,
            "Avery's logger records river levels.": plain.THIRD_SAID,
            "I can start now as a British or Irish citizen.": plain.LABEL_SAID.format(label="British or Irish citizen"),
        }
        for sentence, said in cases.items():
            self.assertEqual(plain.problems(sentence, NAMES, LABELS), [said], sentence)

    def test_lead_ins_mapping_the_advert_are_found(self):
        # every graded pack still opened sentences this way (2026-11-08)
        for sentence in ("The advert requires SQL, and I wrote the queries of a made-up booking app.",
                         "The role asks for teamwork, which I showed on a made-up ferry planner.",
                         "This position calls for Python, so I used it daily.",
                         "Regarding testing, I wrote 40 tests for a made-up tide logger.",
                         "In terms of communication, my weekly reports went to a made-up client.",
                         "You are looking for someone who can write reports."):
            self.assertEqual(plain.problems(sentence, NAMES, LABELS), [plain.LEAD_IN_SAID], sentence)

    def test_plain_first_person_sentences_are_fine(self):
        for sentence in ("I built a tide logger in Lua in my own time.", "My name is Avery Quillon.",
                         "I hope to be the candidate you need.", "The role needs SQL, which I used every week.",
                         "I am a UK and Irish citizen."):
            self.assertEqual(plain.problems(sentence, NAMES, LABELS), [], sentence)

    def test_the_settings_wording_is_the_choices_and_a_form_wording_that_is_not_a_sentence(self):
        values = {"you.work_status": "British or Irish citizen"}
        with mock.patch.object(settings, "get", lambda key: values.get(key, "")):
            labels = plain.settings_labels()
        self.assertIn("UK, no visa needed", labels)
        self.assertIn("British or Irish citizen", labels)
        values["you.work_status"] = "I have the right to work in the UK"
        with mock.patch.object(settings, "get", lambda key: values.get(key, "")):
            self.assertNotIn("I have the right to work in the UK", plain.settings_labels())


class Unlead(unittest.TestCase):
    CARDS = ({"name": "Tide gauge", "aliases": ["tide-logger"]},)

    def test_a_lead_in_before_a_subject_is_taken_out_with_no_ai(self):
        cases = {
            "As required, I have used SQL on every project [E1].": "I have used SQL on every project [E1].",
            "Regarding testing, I wrote 40 tests [E2].": "I wrote 40 tests [E2].",
            "In terms of communication, my weekly reports went to a client.": "My weekly reports went to a client.",
            "The advert requires Lua, and Tide gauge was written in it.": "The advert requires Lua, and Tide gauge "
                                                                          "was written in it.",
            "The role asks for teamwork, Tide gauge was a team's work.": "Tide gauge was a team's work.",
            "For the requirement to show SQL, we built a booking app.": "We built a booking app.",
        }
        for said, left in cases.items():
            self.assertEqual(plain.unlead(said, self.CARDS), left, said)

    def test_each_sentence_and_line_of_a_draft_is_read_on_its_own(self):
        draft = ("Hello,\n\nAs requested, I attach my CV. The role requires SQL, which I used.\n"
                 "Regarding the team, our standups were daily.")
        self.assertEqual(plain.unlead(draft), "Hello,\n\nI attach my CV. The role requires SQL, which I used.\n"
                                              "Our standups were daily.")

    def test_a_rest_that_is_not_a_sentence_is_left_for_its_rewrite(self):
        for said in ("Regarding SQL, Python and Lua, I used all three.", "The role requires SQL, which I used.",
                     "Regarding testing in my projects."):
            self.assertEqual(plain.unlead(said), said)

    def test_the_saved_draft_has_no_lead_in_that_can_be_taken_out(self):
        self.assertEqual(plain.tidy(" As required, \u201cI\u201d mean it.", self.CARDS),
                         "As required, \"I\" mean it.", "a quote is not a subject")
        self.assertEqual(plain.tidy("In terms of code, tide-logger ran for a year.", self.CARDS),
                         "Tide-logger ran for a year.")


class Tidy(unittest.TestCase):
    def test_quotes_are_straight_and_no_line_starts_with_a_space(self):
        curly = " I\u2019m glad to apply. \u201cTide gauge\u201d ran for a year.\n  - a bullet keeps its indent"
        self.assertEqual(plain.tidy(curly), "I'm glad to apply. \"Tide gauge\" ran for a year.\n"
                                            "  - a bullet keeps its indent")

    def test_a_project_is_named_as_its_card_spells_it(self):
        cards = [{"name": "Tide gauge"}, {"name": "FerryApp"}, {"name": "Website"}]
        self.assertEqual(plain.tidy("My tide Gauge and FERRYAPP, and my website.", cards),
                         "My Tide gauge and FerryApp, and my website.")


if __name__ == "__main__":
    unittest.main()
