"""The scoring adapter: today's scores carry over, reasons are stored, and a full re-check is quick.

The compatibility test scores the world's made-up jobs with the technical-graduate preset and a goal like the
owner's imported one (support.jobs.TODAYS_GOAL, with an invented first place), and compares them with what
the code at ef53088 gave. The one intended difference: a paid internship the goal includes keeps its
"graduate internship" note but no longer loses 10 points.
"""

import json
import time
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support import jobs as fixtures
from support.stubs import runner as runner_stub
from support.world import World, jobs

runner_stub.install()

from jobhunt import score  # noqa: E402 - the runner stand-in goes in first
from jobhunt.core.db import tasks  # noqa: E402
from jobhunt.find import actions, rules  # noqa: E402 - actions registers the find kinds

RESCORE_LIMIT_S = 60
MANY_JOBS = 10_000


class Compatibility(unittest.TestCase):
    """The technical preset with the owner's kind of goal gives today's scores and notes."""

    def test_the_technical_preset_reproduces_todays_scores_and_notes_on_synthetic_jobs(self):
        with World(seeded=False), fixtures.goal(fixtures.TODAYS_GOAL):
            for job in jobs():
                with self.subTest(job["id"]):
                    want_score, want_notes = fixtures.TODAYS_SCORES[job["id"]]
                    if "graduate internship" in want_notes:
                        want_score += fixtures.INTERNSHIP_PENALTY
                    self.assertEqual(list(score.score(job)), [want_score, want_notes])

    def test_the_quick_check_keeps_todays_places_and_roles(self):
        with World(seeded=False), fixtures.goal(fixtures.TODAYS_GOAL):
            self.assertTrue(score.prefilter({"title": "Graduate Software Engineer", "location": "Northby Quay"}))
            self.assertFalse(score.prefilter({"title": "Graduate Software Engineer", "location": "Ravensmoor"}))
            self.assertFalse(score.prefilter({"title": "Senior Software Engineer", "location": "London"}))
            self.assertFalse(score.prefilter({"title": "Graduate Sales Executive", "location": "London"}))


class Stored(unittest.TestCase):
    """Scores are written with their reasons, kind and pay, and score_notes as before."""

    def test_every_job_keeps_its_reasons_and_score_notes_is_still_written(self):
        with World() as w, fixtures.goal(fixtures.TODAYS_GOAL):
            self.assertEqual(rules.rescore_all(w.con), len(jobs()))
            for job in jobs():
                row = w.con.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
                reasons = w.con.execute("SELECT * FROM job_reasons WHERE job_id = ?", (job["id"],)).fetchall()
                with self.subTest(job["id"]):
                    self.assertTrue(reasons)
                    self.assertEqual((row["score"], row["score_notes"]), tuple(score.score(dict(row))))
                    self.assertEqual(row["hidden_rule"] is None, row["score"] > 0)
                    self.assertIsNotNone(row["kind"])
                    self.assertEqual(row["preset"], "technical_grad")
            intern = w.con.execute("SELECT * FROM jobs WHERE id = 'greenhouse:quillon-intern'").fetchone()
            self.assertEqual((intern["kind"], intern["paid"]), ("internship", None))
            elig = json.loads(intern["eligibility"])
            self.assertEqual((elig["requires"], elig["eligible"]), ("graduates_welcome", True))

    def test_rescoring_ten_thousand_jobs_takes_under_a_minute(self):
        with World(seeded=False) as w, fixtures.goal(fixtures.TODAYS_GOAL):
            base = jobs()
            rows = [{**base[n % len(base)], "id": f"perf:{n}"} for n in range(MANY_JOBS)]
            w.con.executemany(
                """INSERT INTO jobs (id, source, company, title, location, url, description, salary, first_seen,
                                     last_seen, active, board_size, status)
                   VALUES (:id, :source, :company, :title, :location, :url, :description, :salary,
                           '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 1, :board_size, 'new')""", rows)
            w.con.commit()
            started = time.monotonic()
            self.assertEqual(rules.rescore_all(w.con), MANY_JOBS)
            self.assertLess(time.monotonic() - started, RESCORE_LIMIT_S)


class Actions(unittest.TestCase):
    """The find action kinds and the one-off rescore task."""

    def test_the_rescore_task_runs_after_the_find_migration(self):
        task = tasks.TASKS["find.rescore_all"]
        self.assertEqual(task.after, actions.FIND_MIGRATION)
        with World() as w:
            ctx = runner_stub.Ctx(w.con)
            task.fn(ctx)
            unscored = w.con.execute("SELECT COUNT(*) FROM jobs WHERE scored_at IS NULL AND source != 'email'")
            self.assertEqual(unscored.fetchone()[0], 0)
            self.assertTrue(ctx.changes[0].startswith("Re-checked 17 jobs"))

    def test_every_find_kind_has_a_why_and_an_example(self):
        from jobhunt.core import runner

        for kind in ("find.rescore", "find.screen", "find.prepare"):
            with self.subTest(kind):
                meta = runner.KINDS[kind]
                self.assertTrue(meta["why"].strip())
                self.assertTrue(callable(meta["example"]))


if __name__ == "__main__":
    unittest.main()
