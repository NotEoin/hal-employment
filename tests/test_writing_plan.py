"""The evidence plan: numbered sources, picks kept only when they quote a source, contexts from the cards."""
from __future__ import annotations

import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import plan

TIDE = {"kind": "project (personal project)", "name": "Tide gauge", "tools": ["Lua"], "summary": "Logs river levels.",
        "context": "personal", "aliases": [], "role": "", "files": ["Sources/Code/Tide gauge.md"]}
FERRY = {"kind": "project (university team project)", "name": "Ferry planner", "tools": ["Python", "Flask"],
         "summary": "A timetable app built by a team of 7.", "context": "team", "aliases": [],
         "role": "built the statistics features; team of 7", "files": []}
CARDS = [TIDE, FERRY]
FACTS = ("Avery Quillon. BSc Imaginary Studies.\n"
         "Project, Ferry planner (Python, Flask): wrote the statistics features of a timetable app in a team of 7\n"
         "Volunteer guide at a made-up heritage centre, 2024 to 2026")
KEPT = ("Profile (facts about the applicant from their files, each checked by them):\n"
        "- Project (personal project): Tide gauge. Logs river levels. Tools: Lua\n"
        "- Skill: Lua. Shown by: \"the Tide gauge logger\"\n\n"
        "[Sources/Code/Tide gauge.md > Readme]\n(Kind: personal project)\nA logger in Lua that records river levels "
        "every ten minutes, written in my own time.\n\n"
        "[Notes/river.md > Field trip]\nWe measured the river at three sites.")


class Sources(unittest.TestCase):
    def setUp(self):
        self.found = plan.sources(FACTS, KEPT, CARDS)

    def by_text(self, start: str) -> plan.Source:
        return next(s for s in self.found if s.text.startswith(start))

    def test_every_cv_line_profile_line_and_passage_is_a_numbered_source(self):
        self.assertEqual([s.id for s in self.found], [f"S{n}" for n in range(1, 8)])
        self.assertEqual([s.label for s in self.found],
                         ["CV", "CV", "CV", "Profile", "Profile", "Sources/Code/Tide gauge.md > Readme",
                          "Notes/river.md > Field trip"])
        self.assertNotIn("(Kind:", self.by_text("A logger").text, "the Kind line is Hal's, not the passage's")

    def test_a_source_is_tied_to_the_card_it_is_about(self):
        self.assertIs(self.by_text("Project, Ferry planner").card, FERRY)
        self.assertIs(self.by_text("Project (personal project): Tide gauge").card, TIDE)
        self.assertIs(self.by_text("A logger").card, TIDE, "a passage of a card's own file")
        self.assertIsNone(self.by_text("Skill: Lua").card, "a skill line quoting a project is not the project")
        self.assertIsNone(self.by_text("We measured").card)


class Picks(unittest.TestCase):
    def setUp(self):
        self.found = plan.sources(FACTS, KEPT, CARDS)

    def test_a_pick_is_kept_only_when_it_quotes_its_source_word_for_word(self):
        items = plan.checked({"evidence": [
            {"source": "S2", "quote": "wrote the statistics  features of a timetable app."},
            {"source": "S2", "quote": "built the React frontend and Flask backend"},   # in no source
            {"source": "S99", "quote": "Volunteer guide at a made-up heritage centre"},  # no such source
            {"source": "S3", "quote": "volunteer guide at a made-up heritage centre"},
            {"source": "S3", "quote": "Volunteer guide at a made-up heritage centre"},  # the same fact twice
            {"source": "S1", "quote": "Avery"},                                         # one word is no evidence
            "S4"]}, self.found)
        self.assertEqual([(i.id, i.text) for i in items],
                         [("E1", "wrote the statistics features of a timetable app"),
                          ("E2", "Volunteer guide at a made-up heritage centre")])

    def test_the_context_and_role_come_from_the_card_never_from_the_reply(self):
        # letters framed projects in the wrong context and wrote team work as solo (2026-11-07)
        items = plan.checked({"evidence": [
            {"source": "S2", "quote": "wrote the statistics features", "context": "personal", "card": "Tide gauge"},
            {"source": "S6", "quote": "A logger in Lua that records river levels", "context": "degree"}]}, self.found)
        self.assertEqual([(i.card, i.context, i.role) for i in items],
                         [("Ferry planner", "team", "built the statistics features; team of 7"),
                          ("Tide gauge", "personal", "")])
        self.assertEqual(items[0].tools, ("Python", "Flask"))

    def test_a_plan_holds_at_most_its_named_number_of_items(self):
        reply = {"evidence": [{"source": "S2", "quote": " ".join(FACTS.splitlines()[1].split()[:n])}
                              for n in range(2, 20)]}
        self.assertEqual(len(plan.checked(reply, self.found)), plan.MAX_ITEMS)

    def test_a_reply_that_is_not_a_plan_gives_none(self):
        for reply in ({}, {"evidence": "S2"}, [], "no"):
            self.assertEqual(plan.checked(reply, self.found), [])

    def test_the_plan_reads_as_its_ids_quotes_contexts_and_roles(self):
        items = plan.checked({"evidence": [{"source": "S2", "quote": "wrote the statistics features"}]}, self.found)
        self.assertEqual(plan.block(items),
                         f"{plan.HEADING}\nE1: \"wrote the statistics features\" (from the CV; about Ferry planner, "
                         "university team project; the applicant's part: built the statistics features; team of 7; "
                         "tools: Python, Flask)")
        self.assertEqual(plan.block([]), "")

    def test_one_call_asks_for_the_plan_with_the_requirements_and_every_source(self):
        asked = []

        def reply(system, user, **kw):
            asked.append(user)
            return {"evidence": [{"source": "S7", "quote": "We measured the river at three sites"}]}

        with mock.patch.object(llm, "chat_json", reply):
            items = plan.make(["Field work"], ["Why this role?"], FACTS, KEPT, CARDS)
        self.assertEqual(len(asked), 1)
        self.assertIn("Requirements:\n- Field work\n", asked[0])
        self.assertIn("Questions:\n- Why this role?\n", asked[0])
        self.assertIn("S7 (Notes/river.md > Field trip): We measured the river at three sites.", asked[0])
        self.assertIn(f"Pick at most {plan.MAX_ITEMS}.", asked[0])
        self.assertEqual([i.text for i in items], ["We measured the river at three sites"])


if __name__ == "__main__":
    unittest.main()
