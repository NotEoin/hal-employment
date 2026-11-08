"""How a draft reads once sentences are taken out: sentences pointing back at nothing, and letters left short.

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


class ReadsWithout(unittest.TestCase):
    PARA = "I like tides. I built Hal. It runs offline. My logger works."

    def test_a_sentence_the_next_one_points_back_at_cannot_go(self):
        self.assertFalse(flow.reads_without(self.PARA, "I built Hal."))

    def test_any_other_sentence_can_go_but_never_the_last_one_left(self):
        for sentence in ("I like tides.", "It runs offline.", "My logger works."):
            self.assertTrue(flow.reads_without(self.PARA, sentence), sentence)
        self.assertFalse(flow.reads_without("I built Hal.", "I built Hal."))


class Length(unittest.TestCase):
    def test_a_letter_under_the_floor_of_your_length_is_short(self):
        words = " ".join(["word"] * 199) + "."
        self.assertTrue(flow.short(words, 250))
        self.assertFalse(flow.short(words + " more", 250))
        self.assertFalse(flow.short(words, None), "no preferred length, no floor")


if __name__ == "__main__":
    unittest.main()
