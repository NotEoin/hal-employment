"""Preparing ahead picks the best shown jobs among the kinds chosen, internships included."""

import unittest
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support import jobs as fixtures
from support.world import World, job_id

from jobhunt import gpu, pack, replenish, screen
from jobhunt.find import rules

INTERN = job_id("quillon-intern")


def ready(con, jid):
    con.execute("UPDATE jobs SET status = 'ready' WHERE id = ?", (jid,))


class PreparingAhead(unittest.TestCase):
    """One step at a time: screen, then draft the pack."""

    def picks(self, values: dict) -> list[str]:
        built = []
        with World() as w, fixtures.goal(values), \
                mock.patch.object(gpu, "busy", lambda: None), \
                mock.patch.object(screen, "screen_one", lambda con, job: (True, "fits")), \
                mock.patch.object(pack, "has_facts", lambda con: True), \
                mock.patch.object(pack, "build", lambda con, jid: (built.append(jid), ready(con, jid))):
            rules.rescore_all(w.con)
            for _ in range(3):
                replenish.step(w.con)
        return built

    def test_nothing_is_drafted_while_there_is_nothing_of_yours_to_write_from(self):
        # regression: with nothing checked, each job's pack failed in turn and left an error (2026-11-12)
        built = []
        with World() as w, fixtures.goal(fixtures.TODAYS_GOAL), mock.patch.object(gpu, "busy", lambda: None), \
                mock.patch.object(pack, "has_facts", lambda con: False), \
                mock.patch.object(pack, "build", lambda con, jid: built.append(jid)):
            rules.rescore_all(w.con)
            worked, said = replenish.step(w.con)
        self.assertEqual((worked, built), (False, []))
        self.assertIn("check your record", said)

    def test_the_queue_prepares_internships_when_wanted(self):
        self.assertEqual(self.picks(fixtures.TODAYS_GOAL)[0], INTERN)

    def test_the_queue_leaves_internships_alone_when_not_wanted(self):
        values = {**fixtures.TODAYS_GOAL, "goal.kinds": ["graduate_job", "graduate_scheme"]}
        picked = self.picks(values)
        self.assertTrue(picked)
        self.assertNotIn(INTERN, picked)

    def test_the_old_constants_read_the_live_settings(self):
        with World(seeded=False):
            self.assertEqual(replenish.MIN_SCORE, 45.0)
            self.assertIn(replenish.TARGET, (0, 3, 5, 10))


if __name__ == "__main__":
    unittest.main()
