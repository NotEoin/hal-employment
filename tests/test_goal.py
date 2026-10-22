"""The goal: built from settings, summarised for Home, suggested by stage, restored from history."""

import json
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support import jobs as fixtures
from support.world import World

from jobhunt.core.settings.catalog import scoring as scoring_catalog
from jobhunt.find import goal as goal_mod
from jobhunt.find import rules


class Goal(unittest.TestCase):
    """What the person wants, read live."""

    def test_the_goal_follows_the_settings(self):
        with World(seeded=False), fixtures.goal(fixtures.TODAYS_GOAL):
            g = goal_mod.current()
            self.assertEqual([p.name for p in g.places], ["Northby", "London"])
            self.assertIn("Eastholm", g.places[0].aliases)
            self.assertIn("internship", g.kinds)
            self.assertEqual(g.min_pay, 30000)
            self.assertIn(goal_mod.company_key("Brightwater Bets"), g.avoid_employers)
        with World(seeded=False):
            self.assertEqual(goal_mod.current().places, ())

    def test_the_home_card_says_the_goal_in_words(self):
        rows = dict(goal_mod.summary_rows(goal_mod.from_values(fixtures.TODAYS_GOAL)))
        self.assertEqual(rows["Places"], "Northby, London")
        self.assertEqual(rows["Lowest pay"], "30,000 pounds a year")
        self.assertIn("Paid internships", rows["Looking for"])

    def test_each_stage_suggests_its_kinds_and_preset(self):
        student = goal_mod.defaults_for("student")
        self.assertEqual(student["scoring.preset"], "internship_seeker")
        self.assertIn("insight", student["goal.kinds"])
        self.assertEqual(goal_mod.defaults_for("experienced")["goal.experience_years"], 5)
        with self.assertRaises(ValueError):
            goal_mod.defaults_for("astronaut")

    def test_restore_writes_an_old_version_back(self):
        with World(seeded=False) as w, fixtures.goal({"goal.min_pay": 21000}):
            data = json.dumps({"goal.min_pay": 26000, "goal.places": ["Northby"]})
            w.con.execute("INSERT INTO goal_versions (at, by, summary, data) VALUES ('2026-10-01T00:00:00Z', "
                          "'you', 'Lowest pay changed', ?)", (data,))
            w.con.commit()
            version = w.con.execute("SELECT MAX(id) FROM goal_versions").fetchone()[0]
            goal_mod.restore(version)
            self.assertEqual(goal_mod.current().min_pay, 26000)
            with self.assertRaises(LookupError):
                goal_mod.restore(version + 100)
            fixtures.use({"goal.places": []})

    def test_every_preset_and_starter_loads(self):
        technical = rules.preset("technical_grad")["weights"]
        for preset_id, _label in scoring_catalog.PRESETS:
            with self.subTest(preset_id):
                self.assertEqual(set(rules.preset(preset_id)["weights"]), set(technical))
        starters = goal_mod.starters()
        self.assertEqual(len(starters), 8)
        for area in starters.values():
            self.assertTrue(area["roles"] and area["interests"])


if __name__ == "__main__":
    unittest.main()
