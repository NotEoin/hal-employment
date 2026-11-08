"""No repeated answers: a later answer saying what an earlier one said, and a gap listed twice.

The answers and gaps are made up.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import repeats

PIPELINE = "I built a made-up tide pipeline that cleans sensor logs, stores them in Postgres and charts each week."


class Verbatim(unittest.TestCase):
    CV = "Tide gauge: a sensor logger in Lua that records river levels every ten minutes"

    def test_a_cv_line_copied_word_for_word_by_two_drafts_is_found(self):
        # one CV line turned up in the letter, the message and both answers (2026-11-07)
        letter = "Tide gauge is a sensor logger in Lua that records river levels [E2]."
        said = "Since my first year I keep improving a sensor logger in Lua that records river levels for a club."
        self.assertFalse(repeats.same(said, letter), "different sentences around the line")
        self.assertEqual(repeats.verbatim(said, (self.CV,), (letter,)), self.CV)

    def test_a_short_shared_phrase_or_a_line_no_earlier_draft_copies_is_not_found(self):
        self.assertIsNone(repeats.verbatim("My sensor logger in Lua records levels.", (self.CV,),
                                           ("A sensor logger in Lua.",)))
        self.assertIsNone(repeats.verbatim("I wrote a sensor logger in Lua that records river levels.", (self.CV,),
                                           ("I like rivers.",)))


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

    def test_a_gap_listed_twice_is_listed_once(self):
        # a gap was listed twice (2026-11-07)
        self.assertEqual(repeats.unique_gaps(["Experience with React", "Spreadsheets",
                                              "experience with React (no evidence found)"]),
                         ["Experience with React", "Spreadsheets"])


if __name__ == "__main__":
    unittest.main()
