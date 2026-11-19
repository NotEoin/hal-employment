"""The background worker's rounds: one that fails must not spoil the rounds after it.

Two real connections share one temporary database in WAL mode, as the worker and a document write do in Hal.
Every email, sender and employer here is made up.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt import db, worker

SHORT_WAIT_MS = 100  # the worker's connection gives up quickly, so a locked round fails fast in a test
ROUNDS = 3


class StopWorker(BaseException):
    """Ends the worker's endless loop; a BaseException, because the loop swallows every Exception."""


class Rounds(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "hal.db"
        self.mine = db.connect(path)    # the worker's connection, kept for ever
        self.other = db.connect(path)   # a document write
        for con in (self.mine, self.other):
            self.addCleanup(con.close)
        self.mine.execute(f"PRAGMA busy_timeout = {SHORT_WAIT_MS}")
        self.mine.execute("""INSERT INTO emails (msgid, thrid, date, sender, rcpt, subject, kind, company, role,
                                                 job_id, snippet)
                             VALUES ('m1', '1', ?, 'talent@acme.example', '', 'Please call us', 'human', 'Acme',
                                     'Engineer', NULL, '')""", (db.now(),))
        self.mine.commit()

    def run_rounds(self, *, between) -> None:
        """Runs the worker for ROUNDS rounds on its own connection, calling `between(n)` after each one."""
        rounds = []

        def pause(_seconds):
            rounds.append(len(rounds))
            between(len(rounds))
            if len(rounds) == ROUNDS:
                raise StopWorker

        real_connect = db.connect
        handed = []

        def connect(*args):
            """The worker's own connection first; any other caller (the inbox's address lookup) gets a real one."""
            return real_connect(*args) if handed else handed.append(1) or self.mine

        quiet = mock.patch.object(worker.replenish, "step", return_value=(False, "nothing"))
        with (mock.patch.object(worker.db, "connect", side_effect=connect),
              mock.patch.object(worker.time, "sleep", pause),
              mock.patch.object(worker.contacts, "step", return_value=False),
              mock.patch.object(worker, "_held", return_value="held"), quiet,
              self.assertLogs(worker.log, "ERROR")):
            with self.assertRaises(StopWorker):
                worker.worker()

    def test_a_round_that_failed_with_a_locked_database_does_not_fail_every_round_after_it(self):
        """Bug: a worker round that failed with a locked database failed every round after it.

        Hal had to be restarted to recover (2026-11-19).
        """
        self.other.execute("BEGIN IMMEDIATE")   # document writes are busy: round 1's INSERT must wait, and fails
        reads = []

        def thread_url(_thrid):
            """Another write lands between round 2's read of the emails and its INSERT."""
            reads.append(1)
            if len(reads) == 2:
                db.add_todo(self.other, "message", "An unrelated to-do")
                self.other.commit()

        def between(n: int) -> None:
            if n == 1:
                self.other.commit()             # the busy write ends: nothing else holds the database now

        with mock.patch.object(worker.tasks, "thread_url", thread_url):
            self.run_rounds(between=between)
        todos = self.mine.execute("SELECT COUNT(*) FROM todos WHERE source = 'email:m1'").fetchone()[0]
        self.assertEqual(todos, 1)


    def test_a_connection_that_cannot_roll_back_is_replaced_by_a_new_one(self):
        """Bug: a worker round that failed with a locked database failed every round after it.

        Hal had to be restarted to recover (2026-11-19).
        """
        broken = mock.Mock()
        broken.rollback.side_effect = sqlite3.OperationalError("disk I/O error")
        with mock.patch.object(worker.db, "connect", return_value=self.other), self.assertLogs(worker.log, "ERROR"):
            fresh = worker._clean(broken)
        self.assertIs(fresh, self.other)
        broken.close.assert_called_once_with()

    def test_a_clean_connection_is_kept(self):
        self.assertIs(worker._clean(self.mine), self.mine)


if __name__ == "__main__":
    unittest.main()
