"""Each sentence of a marked draft against only the evidence it cites: the checks with no AI, one small AI call
per sentence, and the rewrite, cut or listing of a sentence that fails."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import llm
from jobhunt.writing import cites, grounded, plain
from jobhunt.writing.plan import Item

FLAGS = json.loads((Path(__file__).parent / "fixtures" / "grounded_flags.json").read_text(encoding="utf-8"))
ITEMS = {k: Item(k, v["text"], "CV", v["card"], v["context"], v["role"], tuple(v["tools"]))
         for k, v in FLAGS["evidence"].items()}
FACTS = grounded.Facts(ITEMS, [], FLAGS["known"], FLAGS["allowed"], None, ["Databases"],
                       ("\n".join(i.text for i in ITEMS.values()),), {"spelling": "UK English"})
FINE = {"supported": True, "unsupported": [], "context_ok": True, "role_ok": True}


def one(text: str) -> cites.Cited:
    """The one sentence of `text`."""
    return cites.cited(text)[0]


class FakeChecker:
    """Answers the sentence check with `replies` by sentence, else as fine, the fix with `fixes` in turn, and the
    paragraph smoothing and adding with `paragraphs` in turn."""

    def __init__(self, replies: dict | None = None, fixes: list[str] = (), paragraphs: list[str] = ()):
        self.replies, self.fixes, self.paragraphs, self.asked = replies or {}, list(fixes), list(paragraphs), []

    def chat_json(self, system: str, user: str, **kw) -> dict:
        self.asked.append((system.split()[0:3], user))
        if system.startswith("Rewrite one sentence"):
            return {"sentence": self.fixes.pop(0) if self.fixes else ""}
        if system.startswith(("Rewrite one paragraph", "Write one more paragraph")):
            return {"paragraph": self.paragraphs.pop(0) if self.paragraphs else ""}
        said = user.split("\n", 1)[0].removeprefix("Sentence: ")
        return self.replies.get(said, FINE)

    def checks(self) -> list[str]:
        return [u for s, u in self.asked if s == ["Check", "one", "sentence"]]

    def paragraph_calls(self) -> list[str]:
        return [u for s, u in self.asked if s in (["Rewrite", "one", "paragraph"], ["Write", "one", "more"])]


class NoAI(unittest.TestCase):
    """Five graded packs listed about 20 false alarms for 10 real problems under Check before sending, from one
    verify call over every draft (2026-11-07); each kind is in `fixtures/grounded_flags.json`."""

    def test_every_sentence_of_the_fixture_is_judged_right_with_no_ai(self):
        wrong = [s["text"] for s in FLAGS["sentences"]
                 if bool(grounded.problems(one(s["text"]), FACTS)) != s["flagged"]]
        real = sum(s["flagged"] for s in FLAGS["sentences"])
        self.assertEqual(wrong, [], f"{len(wrong)} judged wrong of {len(FLAGS['sentences'])} ({real} real)")

    def test_what_only_the_ai_can_see_is_left_to_it_and_found_there(self):
        replies = {cites.strip(s["text"]): s["reply"] for s in FLAGS["ai_only"]}
        fake = FakeChecker(replies)
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            for s in FLAGS["ai_only"]:
                self.assertEqual(grounded.problems(one(s["text"]), FACTS), [], s["text"])
                self.assertTrue(grounded.check(one(s["text"]), FACTS), s["text"])
        self.assertEqual(len(fake.checks()), len(FLAGS["ai_only"]), "one call per sentence, never a batch")

    def test_a_team_project_written_as_yours_alone_names_your_part(self):
        # "I built the React frontend and Flask backend" of a seven-person project; the person built the
        # statistics features (2026-11-07)
        found = grounded.problems(one("I built the React frontend and Flask backend of Ferry planner [E1]."), FACTS)
        self.assertEqual(found, ["says you did Ferry planner alone; it was a team's work; your part: built the "
                                 "statistics features"])

    def test_a_project_described_rather_than_named_is_held_to_its_cards_context(self):
        found = grounded.problems(one("My degree included building a river logger in Lua [E2]."), FACTS)
        self.assertEqual(found, ["calls Tide gauge part of a degree; its evidence says a personal project"])


class WithAI(unittest.TestCase):
    def check(self, text: str, reply: dict) -> tuple[list[str], FakeChecker]:
        fake = FakeChecker({cites.strip(text): reply})
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            return grounded.check(one(text), FACTS), fake

    def test_the_call_holds_only_the_sentence_and_the_evidence_it_cites(self):
        _, fake = self.check("My logger helped the volunteer guides plan tours [E2].", FINE)
        asked = fake.checks()[0]
        self.assertIn('E2: "a sensor logger in Lua', asked)
        self.assertNotIn("E1", asked)
        self.assertNotIn("Databases", asked)

    def test_words_the_evidence_or_employer_holds_are_never_a_problem(self):
        found, _ = self.check("My logger records levels for a river data team [E2].",
                              {**FINE, "supported": False, "unsupported": ["records river levels", "river data team"]})
        self.assertEqual(found, [])

    def test_words_no_evidence_holds_are_a_problem(self):
        found, _ = self.check("My logger records levels for a river data team [E2].",
                              {**FINE, "supported": False, "unsupported": ["for a hospital"]})
        self.assertEqual(found, ['its evidence does not say: "for a hospital"'])

    def test_a_wrong_place_or_too_large_a_part_is_a_problem(self):
        found, _ = self.check("My logger helped the volunteer guides plan tours [E2].",
                              {**FINE, "context_ok": False, "role_ok": False})
        self.assertEqual(found, [grounded.WRONG_PLACE, grounded.TOO_MUCH])

    def test_a_reply_that_is_not_json_fails_nothing(self):
        found, _ = self.check("My logger helped the volunteer guides plan tours [E2].", {})
        self.assertEqual(found, [])

    def test_a_sentence_that_restates_its_evidence_or_fails_with_no_ai_is_never_sent(self):
        for text in ("I was a volunteer guide at a made-up heritage centre from 2024 to 2026 [E4].",
                     "I scored 75 in the Databases module [E3].", "I would welcome a call [M]."):
            _, fake = self.check(text, {**FINE, "supported": False})
            self.assertEqual(fake.checks(), [], text)


class PlainWords(unittest.TestCase):
    """Advert lead-ins, "My CV lists" and "The applicant developed" reached graded letters (2026-11-07)."""

    def test_wording_that_shows_how_the_draft_was_made_needs_a_rewrite(self):
        facts = grounded.Facts(**{**FACTS.__dict__, "names": ("Avery Quillon",), "labels": ("UK, no visa needed",)})
        cases = {"For the requirement to know SQL, I scored 72 in the Databases module [E3].": plain.LEAD_IN_SAID,
                 "Avery Quillon wrote the statistics features of Ferry planner [E1].": plain.THIRD_SAID,
                 "My CV lists a sensor logger in Lua that records river levels every ten minutes [E2].":
                     plain.CV_SAID}
        for sentence, said in cases.items():
            self.assertEqual(grounded.problems(one(sentence), facts), [said], sentence)


class SkillsList(unittest.TestCase):
    """A letter said "I used NumPy and pandas to build" a project whose card never names pandas, citing the CV's
    skills line (2026-11-07)."""

    def setUp(self):
        tide = {"kind": "project", "name": "Tide gauge", "tools": ["Lua", "NumPy"], "summary": "Logs river levels.",
                "aliases": [], "context": "personal"}
        skills = Item("E5", "AI and ML: NumPy, pandas, scikit-learn", "CV", skills=True)
        self.facts = grounded.Facts({**ITEMS, "E5": skills}, [tide], [*FLAGS["known"], "NumPy", "pandas"],
                                    FLAGS["allowed"])

    def test_a_tool_claimed_for_a_project_needs_the_projects_own_evidence(self):
        found = grounded.problems(one("I used NumPy and pandas to build Tide gauge [E2,E5]."), self.facts)
        self.assertIn(grounded.NOT_PROJECTS.format(tool="pandas", card="Tide gauge"), found)
        found = grounded.problems(one("My river logger in Lua uses pandas [E2,E5]."), self.facts)
        self.assertEqual(found, [grounded.NOT_PROJECTS.format(tool="pandas", card="Tide gauge")],
                         "a project described rather than named, through its item")

    def test_a_skills_list_alone_never_supports_a_sentence_about_a_project(self):
        found = grounded.problems(one("I built Tide gauge with NumPy [E5]."), self.facts)
        self.assertEqual(found, [grounded.ONLY_SKILLS.format(card="Tide gauge")])

    def test_a_skills_list_supports_what_you_know(self):
        self.assertEqual(grounded.problems(one("I have used NumPy and pandas [E5]."), self.facts), [])


class Reason(unittest.TestCase):
    def setUp(self):
        self.facts = grounded.Facts(**{**FACTS.__dict__, "motive": ITEMS["E2"]})

    def test_a_reason_stating_a_fact_about_you_is_checked_against_the_pairings_item(self):
        found = grounded.problems(one("I spent three years building a river logger, which is why I applied [M]."),
                                  self.facts)
        self.assertEqual(found, ["the number 3 is not in its evidence"])
        fake = FakeChecker()
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            grounded.check(one("Your flood work is why I built a logger in my own time [M]."), self.facts)
        self.assertIn('E2: "a sensor logger in Lua', fake.checks()[0])

    def test_with_no_pairing_a_reason_stating_a_fact_about_you_cites_nothing(self):
        found = grounded.problems(one("Your flood work is why I built a logger in my own time [M]."), FACTS)
        self.assertEqual(found, [grounded.NO_EVIDENCE])
        self.assertEqual(grounded.problems(one("I am interested in your flood work [M]."), FACTS), [])


LETTER = ("I wrote the statistics features of Ferry planner [E1]. I scored 75 in the Databases module [E3]. My "
          "logger records river levels every ten minutes [E2].\n\nI would welcome a call [M].")


class Grounding(unittest.TestCase):
    """Runs `grounded.ground` with a fake checker kept as `self.fake`."""

    def ground(self, text: str, fixes: list[str], essential=grounded.only_in_paragraph,
               paragraphs: list[str] = ()) -> grounded.Grounded:
        fake = FakeChecker(fixes=fixes, paragraphs=paragraphs)
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            self.fake = fake
            return grounded.ground(text, FACTS, essential)


class Ground(Grounding):
    def test_a_failing_sentence_rewritten_to_fit_its_evidence_is_kept(self):
        done = self.ground(LETTER, ["I scored 72 in the Databases module [E3]."])
        self.assertIn("I scored 72 in the Databases module [E3].", done.text)
        self.assertEqual((done.failed, done.rewrites, done.removed), ([], 1, 0))
        fix = next(u for s, u in self.fake.asked if s == ["Rewrite", "one", "sentence"])
        self.assertIn("What is wrong: the number 75 is not in its evidence", fix)
        self.assertIn('E3: "Databases module: SQL, normalisation and query plans, mark 72"', fix)
        self.assertNotIn("E1:", fix, "the rewrite sees the sentence's own evidence")

    def test_a_sentence_still_failing_is_taken_out_and_the_paragraphs_kept(self):
        done = self.ground(LETTER, ["I scored 80 in the Databases module [E3]."])
        self.assertEqual(done.text, "I wrote the statistics features of Ferry planner [E1]. My logger records river "
                                    "levels every ten minutes [E2].\n\nI would welcome a call [M].")
        self.assertEqual((done.failed, done.removed), ([], 1))

    def test_a_sentence_the_draft_needs_is_kept_and_listed(self):
        done = self.ground("I scored 75 in the Databases module [E3].\n\nI would welcome a call [M].", [])
        self.assertEqual(done.failed, ["I scored 75 in the Databases module. (the number 75 is not in its evidence)"])
        self.assertIn("I scored 75", done.text)

    def test_an_answers_first_sentence_is_never_cut(self):
        answer = "I scored 75 in the Databases module [E3]. My logger records river levels every ten minutes [E2]."
        done = self.ground(answer, [], grounded.first_of_answer)
        self.assertEqual(len(done.failed), 1)
        self.assertEqual(done.text, answer)


class Smooth(Grounding):
    """Sentences taken out left "This project allowed me ...", one-sentence paragraphs and fragments in 15
    graded packs (2026-11-07): a paragraph that changed is rewritten once to read well, and checked again."""

    def test_a_sentence_the_next_one_points_back_at_is_kept_and_listed_not_cut(self):
        text = ("I scored 75 in the Databases module [E3]. This module taught me to plan queries [E3]. My logger "
                "records river levels every ten minutes [E2].\n\nI would welcome a call [M].")
        done = self.ground(text, [])
        self.assertIn("I scored 75 in the Databases module [E3].", done.text)
        self.assertEqual((len(done.failed), done.removed), (1, 0))
        self.assertEqual(self.fake.paragraph_calls(), [], "a paragraph keeping a listed sentence is not smoothed")

    def test_a_paragraph_that_lost_a_sentence_is_smoothed_once_keeping_its_evidence(self):
        smoothed = ("I wrote the statistics features of Ferry planner [E1], and in my own time my logger records "
                    "river levels every ten minutes [E2].")
        done = self.ground(LETTER, ["I scored 80 in the Databases module [E3]."], paragraphs=[smoothed])
        self.assertEqual(done.text, f"{smoothed}\n\nI would welcome a call [M].")
        self.assertEqual((done.removed, done.smoothed), (1, 1))
        asked = self.fake.paragraph_calls()
        self.assertEqual(len(asked), 1, "one call for the one paragraph that changed")
        self.assertIn("Paragraph:\nI wrote the statistics features of Ferry planner [E1]. My logger", asked[0])
        self.assertIn('E2: "a sensor logger in Lua', asked[0])

    def test_a_smoothing_that_changes_the_evidence_or_adds_a_fact_is_refused(self):
        for smoothed in ("I wrote the statistics features of Ferry planner and a logger [E1].",
                         "I wrote the statistics features of Ferry planner [E1], and my logger records river "
                         "levels every five minutes [E2]."):
            done = self.ground(LETTER, ["I scored 80 in the Databases module [E3]."], paragraphs=[smoothed])
            self.assertEqual(done.text, "I wrote the statistics features of Ferry planner [E1]. My logger records "
                                        "river levels every ten minutes [E2].\n\nI would welcome a call [M].")
            self.assertEqual(done.smoothed, 0)


TWICE = ("I wrote the statistics features of Ferry planner for a team of 7 [E1]. I wrote the statistics features of "
         "Ferry planner for a team of 7 [E1].\n\nI would welcome a call [M].")


class Unrepeat(unittest.TestCase):
    """Packs said the same sentence twice back to back, and reused one CV line in the letter, the message and both
    answers (2026-11-07)."""

    def unrepeat(self, text: str, fixes: list[str], earlier: tuple[str, ...] = (),
                 essential=grounded.only_in_paragraph, paragraphs: list[str] = ()) -> grounded.Grounded:
        self.fake = FakeChecker(fixes=fixes, paragraphs=paragraphs)
        with mock.patch.object(llm, "chat_json", self.fake.chat_json):
            return grounded.unrepeat(text, FACTS, essential, earlier)

    def fixes(self) -> list[str]:
        return [u for s, u in self.fake.asked if s == ["Rewrite", "one", "sentence"]]

    def test_a_sentence_said_twice_is_rewritten_once_from_evidence_not_yet_used(self):
        new = "I scored 72 in the Databases module [E3]."
        done = self.unrepeat(TWICE, [new])
        self.assertEqual(done.text, "I wrote the statistics features of Ferry planner for a team of 7 [E1]. "
                                    f"{new}\n\nI would welcome a call [M].")
        asked = self.fixes()[0]
        self.assertIn('What is wrong: already said as "I wrote the statistics features', asked)
        self.assertIn("E3:", asked)
        self.assertNotIn("E1:", asked, "the rewrite rests on evidence the draft has not used")

    def test_a_rewrite_still_repeating_is_taken_out_and_the_paragraph_smoothed(self):
        done = self.unrepeat(TWICE, ["I wrote the statistics features of Ferry planner for a team of seven [E1]."])
        self.assertEqual(done.text, "I wrote the statistics features of Ferry planner for a team of 7 [E1].\n\n"
                                    "I would welcome a call [M].")
        self.assertEqual((done.removed, done.failed), (1, []))

    def test_a_sentence_saying_what_an_earlier_draft_said_is_rewritten(self):
        letter = "I built a sensor logger in Lua that records river levels every ten minutes [E2]."
        message = "Hello,\n\nMy sensor logger in Lua records river levels every ten minutes [E2]. Can we talk [M]?"
        done = self.unrepeat(message, ["I scored 72 in the Databases module [E3]."], (letter,))
        self.assertIn("I scored 72 in the Databases module [E3]. Can we talk [M]?", done.text)
        self.assertNotIn("E2:", self.fixes()[0], "evidence an earlier draft cites is not offered again")

    def test_an_answers_first_sentence_still_repeating_is_kept_and_listed(self):
        earlier = ("I built a sensor logger in Lua that records river levels every ten minutes [E2].",)
        answer = "My sensor logger in Lua records river levels every ten minutes [E2]."
        done = self.unrepeat(answer, [], earlier, grounded.first_of_answer)
        self.assertEqual(done.text, answer)
        self.assertEqual(done.failed, ['My sensor logger in Lua records river levels every ten minutes. (already said '
                                       'as "I built a sensor logger in Lua that records river levels every ten '
                                       'minutes.")'])


SHORT = "I wrote the statistics features of Ferry planner [E1].\n\nI would welcome a call [M]."


class Lengthen(unittest.TestCase):
    """Letters were 105 to 216 words against a preferred 250 once sentences were taken out (2026-11-07)."""

    def lengthen(self, text: str, words: int, paragraphs: list[str]) -> tuple[grounded.Grounded, FakeChecker]:
        fake = FakeChecker(paragraphs=paragraphs)
        with mock.patch.object(llm, "chat_json", fake.chat_json):
            return grounded.lengthen(grounded.Grounded(text), FACTS, words, grounded.only_in_paragraph), fake

    def test_a_short_letter_gets_one_paragraph_from_unused_evidence_before_its_close(self):
        added = "I built a sensor logger in Lua in my own time [E2]. I scored 72 in the Databases module [E3]."
        done, fake = self.lengthen(SHORT, 250, [added])
        self.assertEqual(done.text, f"I wrote the statistics features of Ferry planner [E1].\n\n{added}\n\n"
                                    "I would welcome a call [M].")
        self.assertTrue(done.added)
        asked = fake.paragraph_calls()[0]
        self.assertIn("Unused evidence:\nE2:", asked)
        self.assertNotIn("E1:", asked, "only the evidence the letter does not cite")
        self.assertIn("the applicant wants about 250", asked)

    def test_the_added_paragraph_is_checked_like_any_other(self):
        added = "I built a sensor logger in Lua in my own time [E2]. I scored 99 in the Databases module [E3]."
        done, _ = self.lengthen(SHORT, 250, [added])
        self.assertIn("I built a sensor logger in Lua in my own time [E2].\n\nI would welcome", done.text)
        self.assertNotIn("99", done.text)
        self.assertEqual(done.removed, 1)

    def test_a_letter_near_its_length_is_left_alone(self):
        done, fake = self.lengthen(SHORT, 10, ["I built a sensor logger in Lua in my own time [E2]."])
        self.assertEqual((done.text, done.added, fake.asked), (SHORT, False, []))


if __name__ == "__main__":
    unittest.main()
