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

    def test_the_adverts_requirements_said_as_the_employers_are_found(self):
        # "Your requirement to write and improve SQL queries against Postgres ..." and "Your work on ...
        # Reinforcement Learning Agents builds on ..." opened graded letters (2026-11-08)
        for sentence in ("Your requirement to write and improve SQL queries draws me to the role.",
                         "Your need for reliable tide data is why I am applying.",
                         "Your work on made-up learning agents builds on my studies."):
            self.assertEqual(plain.problems(sentence, NAMES, LABELS), [plain.LEAD_IN_SAID], sentence)
        stock = plain.problems("I would like to meet your requirement for SQL skills.", NAMES, LABELS)
        self.assertEqual(len(stock), 1)
        self.assertTrue(stock[0].startswith("ties the work to the job with a stock phrase"))
        self.assertEqual(plain.problems("Your flood sensors help river trusts plan ahead.", NAMES, LABELS), [])

    def test_saying_what_roles_you_are_looking_for_is_found(self):
        # "I am seeking a role in backend, systems or applied AI" reached a letter (2026-11-08)
        for said in ("I am seeking a role in backend, systems or applied AI.",
                     "I am looking for graduate positions in data.", "My career goal is research."):
            self.assertIn(plain.GOAL_SAID, plain.problems(said), said)
        self.assertEqual(plain.problems("Quill is looking for patterns in my notes."), [])

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


class Saved(unittest.TestCase):
    """What the saved draft keeps: a lead-in is never cut with no AI, as cutting one left garbled text
    (2026-11-08), and a stray evidence id never reaches the person."""

    def test_a_lead_in_is_left_for_its_paragraph_to_be_drafted_again(self):
        said = "Regarding testing, I wrote 40 tests."
        self.assertEqual(plain.tidy(said), said)
        self.assertEqual(plain.problems(said), [plain.LEAD_IN_SAID])

    def test_a_stray_evidence_id_is_taken_out(self):
        # a leftover "[E4,M]" reached a form answer (2026-11-08)
        self.assertEqual(plain.tidy("I wrote the tests [E4,M]. I ran them weekly [E2]."),
                         "I wrote the tests. I ran them weekly.")
        self.assertEqual(plain.tidy("Lists [like this] stay."), "Lists [like this] stay.")


    def test_a_module_code_is_taken_out_of_a_draft_and_its_name_kept(self):
        # a letter opened "having achieved 85 in my CSC1034 Programming Portfolio and 81 in my CSC1035
        # Programming Portfolio": an employer does not know a university's codes (2026-11-12)
        self.assertEqual(plain.tidy("I scored 85 in my CSC1034 Programming Portfolio."),
                         "I scored 85 in my Programming Portfolio.")
        self.assertEqual(plain.tidy("My Distributed Systems module (CSC3121) taught me consensus."),
                         "My Distributed Systems module taught me consensus.")
        self.assertEqual(plain.tidy("In HIS 2040 Parish Records I read vestry minutes."),
                         "In Parish Records I read vestry minutes.")
        for kept in ("I wrote it in C++ for Python 3.11.", "The A320 crew used it.", "I passed CSC1034."):
            self.assertEqual(plain.tidy(kept), kept, "a code with no name after it stays")


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
