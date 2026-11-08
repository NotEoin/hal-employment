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
