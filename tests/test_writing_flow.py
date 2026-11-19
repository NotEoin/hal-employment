"""How a draft reads: sentences pointing back at nothing, paragraphs opening alike, and letters left short.

The sentences are made up, in the shapes graded packs showed (2026-11-07).
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import flow


class BackReferences(unittest.TestCase):
    def test_a_noun_pointed_back_at_needs_to_be_in_the_text_before(self):
        self.assertTrue(flow.refers_to_nothing("This project allowed me to test each change.", ""))
        self.assertTrue(flow.refers_to_nothing("That RAG experience taught me to cite sources.", "I like tides."))
        self.assertFalse(flow.refers_to_nothing("That RAG experience taught me to cite sources.",
                                                "I built a RAG search over my notes."))
        self.assertFalse(flow.refers_to_nothing("These projects ran for a year.", "I wrote one project in Lua."))

    def test_a_bare_pronoun_points_at_the_sentence_before(self):
        self.assertTrue(flow.refers_to_nothing("It runs offline.", ""))
        self.assertFalse(flow.refers_to_nothing("It runs offline.", "I built a made-up tide logger."))

    def test_the_job_and_a_sentence_not_pointing_back_are_never_dangling(self):
        for sentence in ("This role suits my evidence.", "It would be good to talk.", "I built it myself.",
                         "Thistle Labs builds sensors."):
            self.assertFalse(flow.refers_to_nothing(sentence, ""), sentence)

    def test_a_paragraph_opening_by_pointing_back_at_nothing_is_found(self):
        text = "I built a made-up tide logger.\n\nThis project allowed me to test changes.\n\nIt helps."
        self.assertEqual(flow.dangling(text), ["This project allowed me to test changes."])


class Openings(unittest.TestCase):
    """The letters read alike: three paragraphs each opening "I built <project>, a personal project that ..."
    (owner, 2026-11-19)."""

    def test_paragraphs_opening_with_the_same_words_or_a_second_i_are_found(self):
        text = ("I built a made-up tide logger.\n\nI built a ferry planner.\n\nDuring my degree I read maps.\n\n"
                "During my degree I wrote reports.\n\nI would welcome a talk.")
        self.assertEqual(flow.alike_openings(text), ["I built a ferry planner.", "During my degree I wrote reports.",
                                                     "I would welcome a talk."])

    def test_a_middle_paragraph_ending_on_a_claim_of_relevance_is_found(self):
        text = ("Your tide data drew me here.\n\nAt the museum I guided visitors. These experiences prepared me to "
                "work with the public.\n\nI logged river levels, which is relevant to your survey work.\n\n"
                "I logged river levels every ten minutes. It ran for a year.\n\nThis shows I would fit well.")
        self.assertEqual(flow.relevance_closers(text), ["These experiences prepared me to work with the public.",
                                                       "I logged river levels, which is relevant to your survey work."],
                         "the last paragraph is the closing, never a middle one")

    def test_paragraphs_that_open_differently_are_never_found(self):
        text = "Your tide data drew me here.\n\nAt the museum I guided visitors.\n\nI would welcome a talk."
        self.assertEqual(flow.alike_openings(text), [])


class Length(unittest.TestCase):
    def test_a_letter_under_the_floor_of_your_length_is_short(self):
        words = " ".join(["word"] * 199) + "."
        self.assertTrue(flow.short(words, 250))
        self.assertFalse(flow.short(words + " more", 250))
        self.assertFalse(flow.short(words, None), "no preferred length, no floor")


if __name__ == "__main__":
    unittest.main()
