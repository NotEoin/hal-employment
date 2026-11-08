"""Skills only studied in a course are told as knowledge, never as done: the check with no AI, for a software
graduate and a history graduate, in one paragraph and in a whole draft."""
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from test_writing_paragraphs import QUILL_STORY, SCOPE, FakeAI

from jobhunt import llm
from jobhunt.writing import paragraphs, strengths

SOFTWARE = ("Haskell", "linear regression")         # from the made-up software graduate's lecture slides
HISTORY = ("palaeography", "thematic analysis")      # from the made-up history graduate's seminar notes


class StudiedClaims(unittest.TestCase):
    def test_a_sentence_saying_a_studied_skill_was_used_fails_in_any_field(self):
        for sentence, skill in (("I used Haskell to write the parser.", "Haskell"),
                                ("I built the tide model using linear regression.", "linear regression"),
                                ("I used palaeography to date the letters.", "palaeography"),
                                ("We carried out a thematic analysis of the interviews.", "thematic analysis")):
            with self.subTest(sentence):
                self.assertEqual(strengths.problems(sentence, (*SOFTWARE, *HISTORY)),
                                 [strengths.STUDIED_SAID.format(skill=skill)])

    def test_a_studied_skill_told_as_knowledge_passes(self):
        for sentence in ("I studied Haskell in my second year.",
                         "My degree covered palaeography and thematic analysis.",
                         "I learned about linear regression in a module on data analysis.",
                         "Palaeography fascinates me."):
            with self.subTest(sentence):
                self.assertEqual(strengths.problems(sentence, (*SOFTWARE, *HISTORY)), [])

    def test_a_studied_skill_its_own_storys_facts_name_is_never_flagged(self):
        own = "Rainfall and ferry delays: fitted a linear regression in R"
        self.assertEqual(strengths.problems("I used linear regression on two years of data.", SOFTWARE, own), [])

    def test_a_whole_draft_lists_each_sentence_with_its_reason(self):
        draft = "I catalogued 400 letters. I used palaeography to date them. I studied thematic analysis."
        self.assertEqual(strengths.studied_claims(draft, HISTORY),
                         ["I used palaeography to date them. (palaeography was only studied in your course; say you "
                          "studied it, never that you used it)"])


class InAParagraph(unittest.TestCase):
    def test_a_paragraph_claiming_a_studied_skill_is_drafted_again_once_without_it(self):
        good = "Quill is my offline assistant over personal notes, in Lua. It answers questions over my notes offline."
        fake = FakeAI([f"{good} I used Haskell to write its parser.", good])
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            done = paragraphs.settle(QUILL_STORY, paragraphs.LETTER, replace(SCOPE, studied=("Haskell",)))
        again = fake.calls("Write")[1]
        self.assertIn('- "I used Haskell to write its parser.": Haskell was only studied in your course', again)
        self.assertEqual((done.text, done.redrafted, done.listed), (good, True, ""))


if __name__ == "__main__":
    unittest.main()
