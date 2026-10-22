"""The AI check on a new match: filled from the goal, and its verdict kept as a reason."""

import re
import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support import jobs as fixtures
from support.world import World, job_id

from jobhunt import llm, screen
from jobhunt.find import rules

FERNHILL = job_id("fernhill-grad")


class Screen(unittest.TestCase):
    """The prompt describes the person by their goal, never by a fixed person."""

    def run_screen(self, verdict: dict):
        asked = []

        def fake(system, user, **kw):
            asked.append(system)
            return verdict

        with World() as w, fixtures.goal(fixtures.TODAYS_GOAL), mock.patch.object(llm, "chat_json", fake):
            rules.rescore_all(w.con)
            row = w.con.execute("SELECT * FROM jobs WHERE id = ?", (FERNHILL,)).fetchone()
            result = screen.screen_one(w.con, row)
            after = dict(w.con.execute("SELECT * FROM jobs WHERE id = ?", (FERNHILL,)).fetchone())
            reasons = [r["rule"] for r in w.con.execute("SELECT rule FROM job_reasons WHERE job_id = ?", (FERNHILL,))]
            rescored = rules.score(after)
        return asked[0], result, after, reasons, rescored

    def test_the_prompt_is_filled_from_the_goal(self):
        prompt, *_ = self.run_screen({"eligible": True, "reason": "A graduate role."})
        self.assertIn("a new graduate", prompt)
        self.assertIn("Paid internships", prompt)
        self.assertIn("They finished their course in 2026", prompt)
        self.assertIsNone(re.search(r"\{[a-z_]+\}", prompt))
        self.assertIsNone(re.search(r"\b(he|him|his|she|her)\b", prompt, re.I))

    def test_a_job_that_does_not_fit_is_hidden_with_the_ai_reason_and_stays_hidden(self):
        _prompt, result, after, reasons, rescored = self.run_screen(
            {"eligible": False, "reason": "Needs two years in a similar team."})
        self.assertEqual(result, (False, "Needs two years in a similar team."))
        self.assertEqual((after["screen_state"], after["hidden_rule"], after["score"]), ("out", "screen_out", 0))
        self.assertIn("screen_out", reasons)
        self.assertEqual(rescored.hidden.label, "Hal's AI check: Needs two years in a similar team.")

    def test_a_job_that_fits_is_marked_and_kept(self):
        _prompt, _result, after, _reasons, rescored = self.run_screen({"eligible": True, "reason": "Fits."})
        self.assertEqual(after["screen_state"], "eligible")
        self.assertIsNone(rescored.hidden)


if __name__ == "__main__":
    unittest.main()


class StillOpen(unittest.TestCase):
    """Stage 3: a closed advert is hidden before it is ever shown, with no network in tests."""

    def test_a_closed_advert_is_hidden_with_its_reason(self):
        from jobhunt.find import closed_check

        pages = {"https://jobs.example.org/fernhill-grad": (200, "https://jobs.example.org/fernhill-grad",
                                                            "This position is no longer accepting applications."),
                 "https://jobs.example.org/sorrel-ready": (404, "https://jobs.example.org/sorrel-ready", ""),
                 "https://jobs.example.org/cobalt-interview": (200, "https://jobs.example.org/cobalt-interview",
                                                               "Apply now for our graduate role.")}
        with World() as w:
            ids = [FERNHILL, job_id("sorrel-ready"), job_id("cobalt-interview")]
            states = closed_check.check_new(w.con, ids, fetch=lambda url, timeout: pages[url])
            self.assertEqual(list(states.values()).count("closed"), 2)
            self.assertEqual(states[job_id("cobalt-interview")], "open")
            row = w.con.execute("SELECT * FROM jobs WHERE id = ?", (FERNHILL,)).fetchone()
            self.assertEqual((row["open_state"], row["hidden_rule"], row["score"]), ("closed", "closed", 0))
            self.assertEqual(rules.score(dict(row)).hidden.rule, "closed")

    def test_a_site_that_will_not_answer_leaves_the_job_unknown(self):
        from jobhunt.find import closed_check

        def broken(url, timeout):
            raise TimeoutError("no answer")

        self.assertEqual(closed_check.check({"id": "x", "url": "https://jobs.example.org/x"}, broken), "unknown")
