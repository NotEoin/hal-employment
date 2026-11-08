"""No repeated answers: a later answer saying what an earlier one said, and a gap listed twice.

The answers and gaps are made up.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import repeats

PIPELINE = "I built a made-up tide pipeline that cleans sensor logs, stores them in Postgres and charts each week."


class Repeats(unittest.TestCase):
    def test_a_later_answer_that_repeats_an_earlier_one_is_found(self):
        # two form answers repeated the same pipeline details nearly word for word (2026-11-07)
        later = "My made-up tide pipeline cleans sensor logs, stores them in Postgres and charts every week."
        found = repeats.repeated([f"I like tides. {PIPELINE}", f"I would bring care. {later}"])
        self.assertEqual(found, [(1, later, 0)])

    def test_sharing_a_project_name_or_a_short_sentence_is_no_repeat(self):
        self.assertEqual(repeats.repeated([PIPELINE, "The tide pipeline taught me to test each change.",
                                           "I like tides."]), [])
        self.assertEqual(repeats.repeated(["I like tides.", "I like tides."]), [], "too short to count")

    def test_the_earlier_sentence_a_sentence_says_again_is_found(self):
        later = "My made-up tide pipeline cleans sensor logs, stores them in Postgres and charts every week."
        self.assertEqual(repeats.first_said(later, ["I like tides.", PIPELINE]), PIPELINE)
        self.assertIsNone(repeats.first_said("The tide pipeline taught me to test each change.", [PIPELINE]))

    def test_a_project_told_again_with_evidence_a_far_paragraph_cites_is_found(self):
        # a letter told the same pipeline three times, each time in other words (2026-11-08)
        card_of = {"E1": "Tide pipeline", "E2": "Tide pipeline", "E3": ""}
        far = ["I built the pipeline that cleans sensor logs [E1].", "I scored 72 in Databases [E3]."]
        self.assertEqual(repeats.card_again(("E1",), far, card_of), "Tide pipeline")
        self.assertEqual(repeats.card_again(("E2",), far, card_of), "", "other evidence of the project is new")
        self.assertEqual(repeats.card_again(("E3",), far, card_of), "", "an item of no project is not a project")

    def test_a_gap_listed_twice_is_listed_once(self):
        # a gap was listed twice (2026-11-07)
        self.assertEqual(repeats.unique_gaps(["Experience with React", "Spreadsheets",
                                              "experience with React (no evidence found)"]),
                         ["Experience with React", "Spreadsheets"])


if __name__ == "__main__":
    unittest.main()
