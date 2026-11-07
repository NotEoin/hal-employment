"""The tiers of the profile's experiences: the rules, the AI's judgement, the person's choice and when Hal
judges a card again.

Each test runs in its own temporary home. The person, her files, employers and courses are made up.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.fake_ai import fake_ai

from jobhunt import db
from jobhunt.core import paths, runner, settings
from jobhunt.core.settings import store as settings_store
from jobhunt.profile import actions, store, tiers

NOW = "2026-11-07T09:00:00Z"
CV = "Sources/CVs and letters/Avery Quillon CV.md"
LECTURE = "Sources/Study notes/Week 4 stream processing.md"
REPORT = "Sources/Projects/Lantern counter report.md"


def own(source: str = CV, doc_type: str = "cv", quote: str = "") -> dict:
    """Evidence from one of the person's own documents."""
    return {"source_rel": source, "quote": quote, "field": "", "doc_type": doc_type, "role": "own"}


def taught(source: str = LECTURE) -> dict:
    """Evidence from course material the person was given."""
    return {"source_rel": source, "quote": "", "field": "", "doc_type": "notes", "role": "taught"}


def judge_all(tier: str):
    """A made-up AI reply that puts every card it is sent in `tier`."""
    def reply(req) -> str:
        ids = [json.loads(line)["id"] for line in req.user.splitlines() if line.startswith("{")]
        said = "leave_out" if tier == "archive" else tier       # the prompt's word for archive
        return json.dumps({"cards": [{"id": i, "tier": said, "why": "Made-up reason"} for i in ids]})
    return reply


class Tiers(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        settings_store.drop_cache()
        self.addCleanup(settings_store.drop_cache)
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def tier(self, item_id: int) -> tuple[str, str]:
        found = store.item(self.con, item_id)
        return found["tier"], found["tier_by"]

    def test_rules_archive_hidden_cards_and_cards_from_no_own_document_and_school_courses_support(self):
        job = store.add_found(self.con, "job", {"title": "Lantern keeper", "employer": "Glimmerholt Trust"}, own())
        hidden = store.add_found(self.con, "project", {"name": "Tide clock"}, own(REPORT, "report"))
        store.hide(self.con, hidden)
        lecture = store.add_found(self.con, "project", {"name": "Stream joins demo"}, taught())
        school = store.add_found(self.con, "education", {"qualification": "GCSEs", "institution": "Brackwater School",
                                                         "end": "2019"}, own())
        skill = store.add_found(self.con, "skill", {"name": "Lamp trimming"}, own())
        counts = tiers.refresh(self.con, judge=False)
        self.assertEqual(counts, {"rules": 3, "judged": 0, "unjudged": 1})
        self.assertEqual(self.tier(hidden), ("archive", "hal"))
        self.assertEqual(self.tier(lecture), ("archive", "hal"))
        self.assertEqual(self.tier(school), ("supporting", "hal"))
        self.assertEqual(self.tier(job), ("", ""), "no rule decides a job from the CV; the AI does")
        self.assertEqual(store.item(self.con, skill)["tier_words"], "", "a skill has no tier")

    def test_confirming_a_card_from_no_own_document_takes_it_out_of_the_archive(self):
        lecture = store.add_found(self.con, "project", {"name": "Stream joins demo"}, taught())
        tiers.refresh(self.con, judge=False)
        self.assertEqual(self.tier(lecture), ("archive", "hal"))
        store.confirm(self.con, lecture)
        self.assertEqual(self.tier(lecture), ("", ""), "the person's check is the evidence")

    def test_the_ai_sorts_cards_in_batches_given_the_roles_the_person_wants(self):
        settings.set("goal.roles", ["Museum assistant"], by="you")
        ids = [store.add_found(self.con, "project", {"name": f"Lantern survey {n}"}, own(REPORT, "report"))
               for n in range(tiers.BATCH + 1)]
        with fake_ai({tiers.PURPOSE: judge_all("core")}) as backend:
            counts = tiers.refresh(self.con)
        asked = backend.requests(tiers.PURPOSE)
        self.assertEqual(len(asked), 2, "one call per batch of cards")
        self.assertIn("jobs as Museum assistant", asked[0].user)
        self.assertEqual(asked[0].role, "checker")
        self.assertEqual(counts, {"rules": 0, "judged": len(ids), "unjudged": 0})
        shown = store.item(self.con, ids[0])
        self.assertEqual((shown["tier"], shown["tier_by"], shown["tier_why"]), ("core", "hal", "Made-up reason"))

    def test_a_card_is_judged_again_only_when_its_evidence_changes(self):
        survey = store.add_found(self.con, "project", {"name": "Lantern survey"}, own(REPORT, "report"))
        bot = store.add_found(self.con, "project", {"name": "Fishing game bot"}, own(REPORT, "report"))
        with fake_ai({tiers.PURPOSE: judge_all("archive")}) as backend:
            tiers.refresh(self.con)
            tiers.refresh(self.con)
            self.assertEqual(len(backend.requests(tiers.PURPOSE)), 1, "nothing new, nothing asked")
        store.add_found(self.con, "project", {"name": "Lantern survey", "summary": "Counted 400 lanterns."},
                        own(CV, "cv", "Counted 400 lanterns"))
        with fake_ai({tiers.PURPOSE: judge_all("core")}) as backend:
            tiers.refresh(self.con)
        (asked,) = backend.requests(tiers.PURPOSE)
        self.assertIn("Counted 400 lanterns", asked.user)
        self.assertNotIn("Fishing game bot", asked.user)
        self.assertEqual(self.tier(survey), ("core", "hal"))
        self.assertEqual(self.tier(bot), ("archive", "hal"))

    def test_a_tier_the_person_set_is_never_changed_by_hal(self):
        bot = store.add_found(self.con, "project", {"name": "Fishing game bot"}, own(REPORT, "report"))
        tiers.set_tier(self.con, bot, "core")
        store.add_found(self.con, "project", {"name": "Fishing game bot", "summary": "Plays a game."},
                        own(CV, "cv", "game bot"))
        with fake_ai({tiers.PURPOSE: judge_all("archive")}) as backend:
            tiers.refresh(self.con)
        self.assertEqual(backend.requests(tiers.PURPOSE), [])
        self.assertEqual(self.tier(bot), ("core", "person"))
        with self.assertRaises(ValueError):
            tiers.set_tier(self.con, bot, "best")
        skill = store.add_found(self.con, "skill", {"name": "Lamp trimming"}, own())
        with self.assertRaises(ValueError):
            tiers.set_tier(self.con, skill, "core")

    def test_without_an_ai_the_cards_no_rule_decides_stay_unsorted(self):
        job = store.add_found(self.con, "job", {"title": "Lantern keeper", "employer": "Glimmerholt Trust"}, own())
        counts = tiers.refresh(self.con)
        self.assertEqual(counts["unjudged"], 1)
        self.assertEqual(store.item(self.con, job)["tier_words"], "Not sorted yet")

    def test_the_sort_action_says_what_it_sorted_and_what_waits_for_the_ai(self):
        store.add_found(self.con, "project", {"name": "Stream joins demo"}, taught())
        store.add_found(self.con, "job", {"title": "Lantern keeper", "employer": "Glimmerholt Trust"}, own())
        done = runner.run(actions.KIND)
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["result"], {"rules": 1, "judged": 0, "unjudged": 1})

    def test_a_reply_naming_unknown_cards_or_tiers_changes_nothing_else(self):
        job = store.add_found(self.con, "job", {"title": "Lantern keeper", "employer": "Glimmerholt Trust"}, own())
        reply = json.dumps({"cards": [{"id": job + 100, "tier": "core"}, {"id": job, "tier": "best"}, "core"]})
        with fake_ai({tiers.PURPOSE: reply}):
            counts = tiers.refresh(self.con)
        self.assertEqual(counts, {"rules": 0, "judged": 0, "unjudged": 1})
        self.assertEqual(self.tier(job), ("", ""))


if __name__ == "__main__":
    unittest.main()
