"""Attribution: a freely written draft's sentences tied to the evidence plan after it is written, checked with
no AI and written in as markers."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import attribute, cites
from jobhunt.writing.plan import Item

ITEMS = [Item("E1", "built the statistics features of Ferry planner; team of 7", "CV", "Ferry planner", "team",
              "built the statistics features; team of 7"),
         Item("E2", "a sensor logger in Lua that records river levels", "CV", "Tide gauge", "personal")]
LETTER = ("I built the statistics features of Ferry planner with a team of seven classmates, and the timetable "
          "it made is still used. Your river data work is why I am applying.\n\nOn Tide gauge, my own project, "
          "I wrote a Lua logger for river levels. I would welcome a chance to talk.")


class FakeAttributor:
    """Answers the attribution prompt with `entries`, keeping each request."""

    def __init__(self, entries: list[dict] | dict):
        self.entries, self.asked = entries, []

    def chat_json(self, system: str, user: str, **kw) -> dict:
        self.asked.append((system, user))
        return self.entries if isinstance(self.entries, dict) else {"sentences": self.entries}


def attributing(entries) -> tuple[mock._patch, FakeAttributor]:
    fake = FakeAttributor(entries)
    return mock.patch.object(llm, "chat_json", fake.chat_json), fake


class Mark(unittest.TestCase):
    def test_a_free_draft_gets_each_sentences_markers_in_place_with_its_paragraphs_kept(self):
        patch, fake = attributing([{"n": 1, "ids": ["E1"], "kind": "fact"}, {"n": 2, "ids": [], "kind": "motivation"},
                                   {"n": 3, "ids": ["E2"], "kind": "fact"}, {"n": 4, "ids": [], "kind": "courtesy"}])
        with patch:
            done = attribute.mark([LETTER], ITEMS)
        self.assertEqual(len(fake.asked), 1, "one call for the draft")
        self.assertIn("Sentences:\n1. I built the statistics features", fake.asked[0][1])
        self.assertIn('E2: "a sensor logger in Lua', fake.asked[0][1])
        self.assertEqual([(c.ids, c.motive) for c in cites.cited(done[0])],
                         [(("E1",), False), ((), True), (("E2",), False), ((), True)])
        self.assertEqual(cites.strip(done[0]), LETTER, "the person's words and paragraphs are untouched")

    def test_the_answers_are_attributed_in_one_call_and_come_back_each_on_its_own(self):
        answers = ["I wrote a Lua logger for river levels.", "Hello,\n\nI would like to talk."]
        patch, fake = attributing([{"n": 1, "ids": ["E2"], "kind": "fact"}, {"n": 2, "ids": [], "kind": "courtesy"},
                                   {"n": 3, "ids": [], "kind": "courtesy"}])
        with patch:
            done = attribute.mark(answers, ITEMS)
        self.assertEqual(len(fake.asked), 1)
        self.assertIn("Draft 2:\n2. Hello,\n3. I would like to talk.", fake.asked[0][1])
        self.assertEqual(done, ["I wrote a Lua logger for river levels [E2].",
                                "Hello,\n\nI would like to talk [M]."], "a greeting gets no marker or stop")

    def test_a_reply_that_skips_a_sentence_or_is_not_json_fails_as_a_whole(self):
        for entries in ([{"n": 1, "ids": ["E1"], "kind": "fact"}], {}, {"sentences": "E1"}):
            patch, _ = attributing(entries)
            with patch, self.subTest(entries=json.dumps(entries)):
                self.assertIsNone(attribute.mark([LETTER], ITEMS))


class Validated(unittest.TestCase):
    def test_an_id_the_plan_does_not_hold_is_dropped(self):
        said = attribute.validated(["E2", "e9", "x"], "fact", "I wrote a logger.", {"E1", "E2"})
        self.assertEqual(said, attribute.Attribution(("E2",), attribute.FACT))

    def test_a_past_tense_or_numeric_sentence_about_you_is_never_a_courtesy(self):
        for sentence in ("I built the timetable app.", "My team had 7 people."):
            with self.subTest(sentence=sentence):
                said = attribute.validated([], "courtesy", sentence, {"E1"})
                self.assertEqual(said.kind, attribute.FACT)
                self.assertEqual(said.marker, "", "a fact citing nothing fails its check as citing no evidence")
        self.assertEqual(attribute.validated([], "courtesy", "Thank you for reading.", {"E1"}).marker, cites.MOTIVE)

    def test_an_unknown_kind_is_read_as_a_fact(self):
        self.assertEqual(attribute.validated(["E1"], "boast", "I led it.", {"E1"}).kind, attribute.FACT)


if __name__ == "__main__":
    unittest.main()
