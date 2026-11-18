"""Document writes and the record: a write waits for a record update in hand, starts again when the record
moves under it, stops when asked while it waits, and gives up starting again after `MAX_RESTARTS`."""
import threading
import time
import unittest

import support  # noqa: F401 - puts the run in a temporary home before jobhunt is imported
from test_runner import WAIT_S, Kinds, fresh_home

from jobhunt import documents, record
from jobhunt.core import runner, work
from jobhunt.pack import freshness

STEP_POLL_S = 0.01     # how often a test looks for an action's step to land


class Freshness(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(fresh_home())
        self.kinds = Kinds(self)

    def record_update(self) -> tuple[str, threading.Event]:
        """Starts a record update that raises the revision once released; returns its id and its release."""
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def update(ctx):
            reached.set()
            release.wait(WAIT_S)
            record.bump_revision(ctx.con)
            return "read"
        self.kinds.add("record.test_update", update, label="Reading what you added", exclusive="none")
        action_id = runner.start("record.test_update")
        self.assertTrue(reached.wait(WAIT_S))
        return action_id, release

    def writer(self, write) -> str:
        """Starts a document write of `write(fresh)`; its result is the revision it was written from."""
        self.kinds.add("test.write", lambda ctx: freshness.written(write, ctx, then="Writing")[1],
                       label="Writing your documents", work_class=work.DOCUMENTS)
        return runner.start("test.write", job_id="demo:1")

    def steps_of(self, action_id: str) -> list[str]:
        return [s["text"] for s in runner.get(action_id)["steps"]]

    def wait_for_step(self, action_id: str, text: str) -> None:
        deadline = time.monotonic() + WAIT_S
        while text not in self.steps_of(action_id) and time.monotonic() < deadline:
            time.sleep(STEP_POLL_S)
        self.assertIn(text, self.steps_of(action_id))

    def test_a_write_waits_for_a_running_record_update_and_starts_again_when_the_record_moves(self):
        start = record.revision(self.con)
        seen = []

        def write(fresh):
            seen.append(fresh.revision)
            if len(seen) == 1:
                record.bump_revision()     # another change lands while the first write is under way
            fresh.check()
            return "written"
        _, release = self.record_update()
        writing = self.writer(write)
        self.wait_for_step(writing, work.RECORD_WAIT_WORDS)
        self.assertEqual(seen, [], "nothing is written while the record update runs")
        release.set()
        done = runner.wait(writing)
        self.assertEqual(done["state"], "done")
        self.assertEqual(seen, [start + 1, start + 2])
        self.assertEqual(done["result"], start + 2)
        self.assertIn(freshness.AGAIN_WORDS, self.steps_of(writing))
        self.assertIn("Writing", self.steps_of(writing))

    def test_a_write_waiting_for_the_record_stops_when_asked(self):
        seen = []
        self.record_update()
        writing = self.writer(lambda fresh: seen.append(fresh.revision))
        self.wait_for_step(writing, work.RECORD_WAIT_WORDS)
        runner.stop(writing)
        self.assertEqual(runner.wait(writing)["state"], "stopped")
        self.assertEqual(seen, [])

    def test_a_write_stopped_part_way_saves_nothing_at_its_next_check(self):
        # review, 2026-11-18: an approval stopped a rewrite, which carried on and wrote its CV and pack.md over the
        # approved files, as nothing between the record wait and the end looked at Stop
        reached, release, saved = threading.Event(), threading.Event(), []
        self.addCleanup(release.set)

        def write(fresh):
            reached.set()
            release.wait(WAIT_S)
            fresh.check()
            saved.append(fresh.revision)
        writing = self.writer(write)
        self.assertTrue(reached.wait(WAIT_S))
        runner.stop(writing)
        release.set()
        self.assertEqual(runner.wait(writing)["state"], "stopped")
        self.assertEqual(saved, [])

    def test_a_record_that_keeps_changing_is_written_from_once_the_restarts_run_out(self):
        seen = []

        def write(fresh):
            seen.append(fresh.revision)
            record.bump_revision()
            fresh.check()
            return "written"
        result, revision = freshness.written(write)
        self.assertEqual((result, len(seen)), ("written", freshness.MAX_RESTARTS + 1))
        self.assertEqual(revision, seen[-1])
        self.assertLess(revision, record.revision(), "its documents show as out of date")

    def test_the_revision_documents_were_written_from_is_kept_per_job(self):
        self.assertIsNone(documents.written_from(self.con, "demo:1"))
        documents.mark_written_from(self.con, "demo:1", 7)
        self.con.commit()
        self.assertEqual((documents.written_from(self.con, "demo:1"), documents.written_from(self.con, "demo:2")),
                         (7, None))


if __name__ == "__main__":
    unittest.main()
