"""Tests of database snapshots and how long they are kept. Every database here is made up."""

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from support import old_db

from jobhunt.core import backup

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


class Snapshots(unittest.TestCase):
    """A snapshot is a checked copy beside the database."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.db = old_db.build(self.data / "jobs.db", "L")

    def test_a_snapshot_is_a_checked_copy_named_for_its_reason(self):
        before = old_db.counts(self.db)
        con = sqlite3.connect(self.db)
        self.addCleanup(con.close)
        path = backup.snapshot(con, "Before update")
        self.assertEqual(path.parent, self.data / "backups")
        self.assertRegex(path.name, r"^jobs-\d{8}T\d{6}Z-before-update\.db$")
        self.assertEqual(old_db.counts(path), before)
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k = 'backup:last'").fetchone()[0], path.name)
        self.assertEqual(list(self.data.glob("backups/*.partial")), [])

    def test_two_snapshots_in_the_same_second_do_not_overwrite_each_other(self):
        con = sqlite3.connect(self.db)
        self.addCleanup(con.close)
        first, second = backup.snapshot(con, "daily"), backup.snapshot(con, "daily")
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())

    def test_an_in_memory_database_cannot_be_backed_up(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        with self.assertRaises(backup.BackupError):
            backup.snapshot(con, "daily")


class Retention(unittest.TestCase):
    """The newest 10 migration and update copies, 7 daily ones, and anything from the last 30 days stay."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def make(self, days_ago: int, suffix: str, migration: bool = False) -> Path:
        stamp = backup.utc_stamp(NOW - timedelta(days=days_ago))
        name = f"jobs-v{days_ago}-to-v17-{stamp}.db" if migration else f"jobs-{stamp}-{suffix}.db"
        path = self.folder / name
        path.write_bytes(b"")
        return path

    def test_old_copies_beyond_the_limits_are_deleted_and_recent_ones_kept(self):
        daily = [self.make(40 + n, "daily") for n in range(10)]
        updates = [self.make(40 + n, "before-update") for n in range(8)]
        migrations = [self.make(60 + n, "", migration=True) for n in range(5)]
        recent = [self.make(n, "manual") for n in range(3)]
        old_other = self.make(90, "manual")
        stranger = self.folder / "notes.txt"
        stranger.write_text("not a backup")
        deleted = set(backup.prune(self.folder, now=NOW))
        self.assertEqual(deleted, set(daily[7:]) | set(migrations[2:]) | {old_other})
        for path in daily[:7] + updates + migrations[:2] + recent:
            self.assertTrue(path.exists(), path.name)
        self.assertTrue(stranger.exists(), "files Hal did not name are left alone")


if __name__ == "__main__":
    unittest.main()
