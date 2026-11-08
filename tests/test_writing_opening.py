"""The message's opening: who is writing and why, from the settings and the stage, with no AI.

All 15 graded messages lacked an introduction: a bare question, a dangling "I also ...", or "I am available to
start immediately" (2026-11-08). The people, courses and universities are made up.
"""
import unittest
from datetime import date

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import opening, stage

TODAY = date(2026, 11, 8)


def at(finished: bool | None, *, course: str = "BSc Imaginary Studies", end: str = "2026-06",
       subject: str = "Imaginary Studies", institution: str = "Made-up University", grade: str = "") -> stage.Stage:
    """A stage on `TODAY`."""
    return stage.Stage(TODAY, course, end, finished, "available now", subject, institution, grade)


class Described(unittest.TestCase):
    def test_a_graduate_is_named_by_class_subject_institution_and_year(self):
        cases = {"First Class Honours": "a first-class Imaginary Studies graduate from Made-up University (2026)",
                 "2:1": "a 2:1 Imaginary Studies graduate from Made-up University (2026)",
                 "Lower second (2:2)": "a 2:2 Imaginary Studies graduate from Made-up University (2026)",
                 "Distinction": "an Imaginary Studies graduate with Distinction from Made-up University (2026)",
                 "73%": "an Imaginary Studies graduate from Made-up University (2026)"}
        for grade, said in cases.items():
            self.assertEqual(opening.described(at(True, grade=grade)), said, grade)

    def test_a_student_is_final_year_only_in_the_last_year(self):
        self.assertEqual(opening.described(at(False, end="2027-06")),
                         "a final-year Imaginary Studies student at Made-up University")
        self.assertEqual(opening.described(at(False, end="2028-06", institution="")), "an Imaginary Studies student")

    def test_no_course_says_nothing(self):
        self.assertEqual(opening.described(at(None)), "")
        self.assertEqual(opening.described(at(True, course="", subject="")), "")

    def test_an_abbreviation_takes_the_article_of_its_letters_sound(self):
        self.assertEqual(opening.described(at(True, subject="", institution="", end="")), "a BSc Imaginary Studies "
                                                                                         "graduate")
        self.assertEqual(opening.described(at(True, course="MSc", subject="", institution="", end="")),
                         "an MSc graduate")


class Opening(unittest.TestCase):
    def test_the_greeting_names_the_hiring_contact_else_the_hiring_manager(self):
        said = opening.Opening.of("Avery Quillon", at(True, grade="First"), "Graduate Analyst", "Ada Penhallow")
        self.assertEqual(said.greeting, "Hello Ada Penhallow,")
        self.assertEqual(said.sentence, "I'm Avery Quillon, a first-class Imaginary Studies graduate from Made-up "
                                        "University (2026), applying for the Graduate Analyst role.")
        self.assertEqual(opening.Opening.of("", at(None), "Analyst role").greeting, "Hello Hiring Manager,")

    def test_what_is_unknown_is_left_out(self):
        self.assertEqual(opening.Opening.of("Avery", at(None), "Analyst").sentence,
                         "I'm Avery, applying for the Analyst role.")
        self.assertEqual(opening.Opening.of("", at(None), "Analyst position").sentence,
                         "I'm applying for the Analyst position.")

    def test_the_message_starts_with_the_opening_and_the_body_follows_it(self):
        said = opening.Opening.of("Avery", at(None), "Analyst")
        message = said.put("I built a made-up tide logger.\n\nWho leads the team?")
        self.assertEqual(message, "Hello Hiring Manager,\n\nI'm Avery, applying for the Analyst role. I built a "
                                  "made-up tide logger.\n\nWho leads the team?")
        self.assertTrue(said.starts(message))
        self.assertFalse(said.starts("Who leads the team?"))
        self.assertIn("Hello Hiring Manager, I'm Avery, applying for the Analyst role.", said.note)


class Body(unittest.TestCase):
    def test_the_writers_greeting_and_introduction_are_taken_out(self):
        cases = {"Hi Avery,\n\nI'm Avery Quillon, a recent graduate [M]. I built a logger [E1].": "I built a logger "
                                                                                                "[E1].",
                 "Dear Hiring Team,\n\nI am writing to apply for the role [M]. Who leads it [M]?": "Who leads it [M]?",
                 "Hello Ada, I built a logger [E1].\n\nWho leads it [M]?": "I built a logger [E1].\n\nWho leads it "
                                                                         "[M]?",
                 "My name is Avery. I built a logger [E1].": "I built a logger [E1].",
                 "I also built a logger [E1]. Who leads it [M]?": "I also built a logger [E1]. Who leads it [M]?"}
        for draft, body in cases.items():
            self.assertEqual(opening.body_of(draft), body, draft)


if __name__ == "__main__":
    unittest.main()
