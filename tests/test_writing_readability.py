"""What makes a checked draft read badly, found with no AI, and the one guarded polish pass that fixes it."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import cites, grounded, plain, polish, readability
from jobhunt.writing.plan import Item

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "readability.json").read_text(encoding="utf-8"))
ITEMS = {k: Item(k, v["text"], "CV", v["card"], v["context"], v["role"], tuple(v["tools"]), v["skills"])
         for k, v in FIXTURE["evidence"].items()}
CARDS, KNOWN = FIXTURE["cards"], FIXTURE["known"]
FACTS = grounded.Facts(ITEMS, CARDS, KNOWN, "Join our ferry team in 2027.", values={"spelling": "UK English"})


def found(text: str, kind: str, earlier: tuple[str, ...] = ()) -> list[readability.Finding]:
    return readability.findings(text, kind, ITEMS, CARDS, KNOWN, earlier)


class Findings(unittest.TestCase):
    """Free and grounded drafts both scored about 2.1 of 5, 14 of 15 packs of each held there by readability
    (30 packs, 2026-11-08); each kind is in `fixtures/readability.json` with a version that reads well."""

    def test_every_before_of_the_fixture_has_its_finding_and_every_after_has_none(self):
        for case in FIXTURE["cases"]:
            with self.subTest(code=case["code"], before=case["before"][:60]):
                self.assertIn(case["code"], [f.code for f in found(case["before"], case["kind"])])
                self.assertEqual(found(case["after"], case["kind"]), [])

    def test_every_kind_of_finding_has_cases_in_the_fixture(self):
        self.assertEqual({c["code"] for c in FIXTURE["cases"]}, set(readability.ASKS))

    def test_a_sentence_said_in_an_earlier_draft_is_a_repeat(self):
        letter = "On Ferry planner I wrote the statistics features in a team of seven [E1]."
        answer = "On Ferry planner I wrote the statistics features in a team of seven people [E1]."
        self.assertEqual([f.code for f in found(answer, readability.ANSWER, (letter,))], [readability.REPEAT])

    def test_a_skills_list_never_makes_a_sentence_about_an_unnamed_project(self):
        self.assertEqual(found("I know Python and SQL well [E6].", readability.ANSWER), [])

    def test_only_a_message_needs_an_introduction_and_only_a_letter_its_ending(self):
        text = "On Ferry planner I wrote the statistics features [E1]. I used Python, Flask and SQL there [E6]."
        self.assertEqual([f.code for f in found(text, readability.MESSAGE)], [readability.NO_INTRO])
        self.assertEqual([f.code for f in found(text, readability.LETTER)], [readability.TOOL_LIST])
        self.assertEqual(found(text, readability.ANSWER), [])

    def test_a_stray_fact_is_moved_to_the_closing_paragraph(self):
        # "I have the right to work in the UK." in a paragraph on teamwork (2026-11-08)
        case = next(c for c in FIXTURE["cases"] if c["code"] == readability.STRAY)
        text, moved = readability.placed(case["before"], ITEMS, CARDS)
        self.assertEqual(text, case["after"])
        self.assertEqual(moved, ["I have the right to work in the UK."])

    def test_a_stray_in_the_closing_paragraph_or_a_one_paragraph_draft_stays_for_the_polish(self):
        one_paragraph = [c for c in FIXTURE["cases"] if c["code"] == readability.STRAY][1]["before"]
        self.assertEqual(readability.placed(one_paragraph, ITEMS, CARDS), (one_paragraph, []))

    def test_requirement_framing_anywhere_in_a_sentence_is_a_sentence_problem(self):
        for sentence in ("I scored 72 in Databases, which meets the requirement for SQL.",
                         "The position requires teamwork, which I showed on Ferry planner.",
                         "This is proof of the requirement to work in a team."):
            with self.subTest(sentence=sentence):
                self.assertEqual(plain.problems(sentence), [plain.REQUIREMENT_SAID])
        self.assertEqual(plain.problems("I met the team that built it."), [])


BAD = ("I wrote a 113-test suite for the timetable rules [E4]. We met each week [E1].\n\n"
       "I would bring Python, Flask, SQL and Docker to your team [E6].")
GOOD = ("For Ferry planner I wrote a suite of 113 tests for the timetable rules, meeting the team each week.\n\n"
        "I would like to keep building tools people rely on, and would welcome a chance to talk.")


class FakePolisher:
    """Answers the polish prompt with `drafts`, and the attribution by `cites_by` (a sentence's first key held)."""

    def __init__(self, drafts: list[str], cites_by: dict[str, list[str]]):
        self.drafts, self.cites_by, self.asked = drafts, cites_by, []

    def chat_json(self, system: str, user: str, **kw) -> dict:
        self.asked.append((system.split()[0], user))
        if system.startswith("Polish"):
            return {"drafts": self.drafts}
        said = [line.split(". ", 1) for line in user.split("\nSentences:\n", 1)[1].splitlines() if ". " in line]
        return {"sentences": [{"n": int(n), "ids": next((v for k, v in self.cites_by.items() if k in s), []),
                               "kind": "fact" if any(k in s for k in self.cites_by) else "courtesy"}
                              for n, s in said if n.isdigit()]}


def polishing(drafts: list[str], cites_by: dict[str, list[str]], *batch: polish.Draft) -> tuple[polish.Polished,
                                                                                                  FakePolisher]:
    fake = FakePolisher(drafts, cites_by)
    with mock.patch.object(llm, "chat_json", fake.chat_json):
        return polish.polish(list(batch), FACTS), fake


class Polish(unittest.TestCase):
    def test_a_draft_that_reads_well_is_never_sent_to_polish(self):
        done, fake = polishing([], {}, polish.Draft(readability.LETTER, FIXTURE["cases"][0]["after"]))
        self.assertEqual((fake.asked, done.polished), ([], 0))

    def test_a_draft_that_reads_badly_is_polished_once_attributed_again_and_kept_when_it_helps(self):
        done, fake = polishing([GOOD], {"Ferry planner": ["E4", "E1"]}, polish.Draft(readability.LETTER, BAD))
        self.assertEqual([who for who, _ in fake.asked], ["Polish", "Say"], "one polish, one attribution")
        asked = fake.asked[0][1]
        self.assertIn("Draft 1 (Cover letter):\nI wrote a 113-test suite for the timetable rules.", asked)
        self.assertIn(readability.ASKS[readability.UNNAMED], asked)
        self.assertIn(readability.ASKS[readability.TOOL_LIST], asked)
        self.assertEqual((cites.strip(done.texts[0]), done.polished, done.left), (GOOD, 1, []))

    def test_a_polish_adding_a_number_a_tool_or_a_project_is_refused(self):
        for added in (GOOD.replace("113", "120"), GOOD.replace("tools people", "Lua tools people"),
                      GOOD + " Tide gauge taught me patience."):
            with self.subTest(added=added[-40:]):
                done, _ = polishing([added], {"Ferry planner": ["E4", "E1"], "Tide gauge": ["E2"]},
                                    polish.Draft(readability.LETTER, BAD))
                self.assertEqual((done.texts[0], done.polished), (BAD, 0))

    def test_a_polish_failing_a_check_or_leaving_as_many_findings_is_refused(self):
        solo = GOOD.replace("For Ferry planner I wrote", "In my own time, for Ferry planner, I wrote")
        for reply, cites_by in ((solo, {"Ferry planner": ["E4", "E1"]}), (cites.strip(BAD), {"113": ["E4"],
                                                                                              "met": ["E1"]})):
            with self.subTest(reply=reply[:40]):
                done, _ = polishing([reply], cites_by, polish.Draft(readability.LETTER, BAD))
                self.assertEqual(done.texts[0], BAD)

    def test_only_the_drafts_with_findings_are_polished(self):
        clean = "I scored 72 in my Databases module, where I learned normalisation and query plans [E3]."
        message = "Hello,\n\nOn Ferry planner I wrote the statistics features [E1]. What do you build [M]?"
        better = ("Hello,\n\nI am a software graduate applying for the role. On Ferry planner I wrote the statistics "
                  "features. What do you build?")
        done, fake = polishing([better, "I liked databases."], {"Ferry planner": ["E1"]},
                               polish.Draft(readability.MESSAGE, message),
                               polish.Draft(readability.ANSWER, clean, "Why this role?"))
        self.assertIn("Draft 2 (Answer: Why this role?):", fake.asked[0][1])
        self.assertNotIn("I liked databases", fake.asked[1][1], "a clean draft's reply is never attributed")
        self.assertEqual(done.texts[1], clean)
        self.assertEqual((cites.strip(done.texts[0]), done.polished), (better, 1))

    def test_a_reply_with_the_wrong_number_of_drafts_changes_nothing(self):
        done, _ = polishing([], {}, polish.Draft(readability.LETTER, BAD))
        self.assertEqual((done.texts, done.polished), ([BAD], 0))
        self.assertTrue(done.left)


if __name__ == "__main__":
    unittest.main()
