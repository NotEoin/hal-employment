"""Tests of the migration series and its runner (gate G10).

Every database here is made up: the old shapes come from `support.old_db`, in a temporary folder.
"""

import contextlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import old_db

from jobhunt.core.db import migrate, tasks

ROOT = Path(__file__).resolve().parents[1]
CHILD_TIMEOUT = 90   # seconds; a child waits up to 60 s for the lock, then migrates in well under one
DESTRUCTIVE = re.compile(r"\b(DROP|RENAME|TRUNCATE|DELETE\s+FROM|executescript)\b", re.I)
CHILD = """
import sqlite3, sys
from pathlib import Path
from jobhunt.core.db import migrate
path = Path(sys.argv[1])
con = sqlite3.connect(path, timeout=30)
print(migrate.migrate(con, db_path=path, backup_dir=path.parent / "backups"))
"""


class Folder(unittest.TestCase):
    """A temporary data folder per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.db = self.data / "jobs.db"
        self.backups = self.data / "backups"

    def open(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db, timeout=30)
        self.addCleanup(con.close)
        return con

    def run_migrate(self, con) -> list[int]:
        return migrate.migrate(con, db_path=self.db, backup_dir=self.backups)


class OldDatabases(Folder):
    """Gate G10: today's databases reach LATEST with nothing lost."""

    def check_layout(self, layout):
        old_db.build(self.db, layout)
        before = old_db.counts(self.db)
        con = self.open()
        self.assertEqual(self.run_migrate(con), list(range(1, migrate.LATEST + 1)))
        self.assertEqual(migrate.user_version(con), migrate.LATEST)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        after = old_db.counts(con)
        for table in old_db.TABLES:
            if table == "meta":   # migrations add their own meta rows; the old ones stay
                self.assertGreaterEqual(after[table], before[table])
            else:
                self.assertEqual(after[table], before[table], table)
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k = 'last_discover'").fetchone()[0][:4],
                         str(old_db.datetime.now(old_db.UTC).year))
        self.assertIn("contacts", migrate.columns(con, "applications"))
        copies = list(self.backups.glob("jobs-v0-to-v17-*.db"))
        self.assertEqual(len(copies), 1, "one backup before the first migration")
        with contextlib.closing(sqlite3.connect(copies[0])) as copy:
            self.assertEqual(copy.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(old_db.counts(copies[0]), before)
        return con

    def test_a_linux_database_migrates_to_latest_with_counts_kept_and_a_backup(self):
        self.check_layout("L")

    def test_a_stick_database_without_the_contacts_column_migrates_to_latest(self):
        self.check_layout("U")

    def test_a_second_run_does_nothing(self):
        old_db.build(self.db, "L")
        con = self.open()
        self.run_migrate(con)
        self.assertEqual(self.run_migrate(con), [])
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1)

    def test_the_run_is_recorded_in_activity_with_a_why(self):
        old_db.build(self.db, "L")
        con = self.open()
        self.run_migrate(con)
        rows = con.execute("SELECT text, why, area, detail FROM activity WHERE kind = 'db.migrate'").fetchall()
        self.assertEqual(len(rows), 1)
        text, why, area, detail = rows[0]
        self.assertEqual(text, f"Hal updated its records to version {migrate.LATEST}")
        self.assertTrue(why)
        self.assertEqual(area, "system")
        self.assertTrue(json.loads(detail)["backup"].startswith("jobs-v0-to-v17-"))

    def test_the_activity_table_refuses_a_row_without_a_why(self):
        con = self.open()
        self.run_migrate(con)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO activity (at, area, kind, text, why) VALUES ('x', 'system', 'k', 't', '')")

    def test_existing_applications_are_numbered_in_order(self):
        old_db.build(self.db, "L")
        con = self.open()
        self.run_migrate(con)
        numbers = dict(con.execute("SELECT job_id, number FROM applications").fetchall())
        self.assertEqual(numbers, old_db.EXPECTED_NUMBERS)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("UPDATE applications SET number = 1 WHERE job_id = 'greenhouse:aldermoor-1'")

    def test_saved_answers_wait_for_a_choice_after_migration_and_private_ones_are_not_copied(self):
        old_db.build(self.db, "L")
        con = self.open()
        self.assertEqual(con.execute("SELECT count(*) FROM answers").fetchone()[0], 22)
        self.run_migrate(con)
        rows = con.execute("SELECT label, answer, scope, scope_key, kind, source FROM answers2").fetchall()
        self.assertEqual(sorted(r[0] for r in rows), sorted(label for label, _ in old_db.ORDINARY_ANSWERS))
        self.assertEqual({r[2] for r in rows}, {"review"})
        self.assertEqual({(r[3], r[4], r[5]) for r in rows}, {("", "fixed", "import")})
        copied = json.dumps([r[1] for r in rows])
        for label, answer in old_db.PRIVATE_ANSWERS:
            self.assertNotIn(label, [r[0] for r in rows])
            if answer.startswith("SYNTH"):
                self.assertNotIn(answer, copied)
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k = 'answers2:private_not_copied'").fetchone()[0],
                         str(len(old_db.PRIVATE_ANSWERS)))
        self.assertEqual(con.execute("SELECT count(*) FROM answers").fetchone()[0], 22, "the old table stays")

    def test_old_messages_are_marked_read_and_recent_ones_needing_attention_are_not(self):
        old_db.build(self.db, "L")
        con = self.open()
        self.run_migrate(con)
        unread = {r[0] for r in con.execute("SELECT msgid FROM emails WHERE read = 0")}
        self.assertEqual(unread, {"m1", "m5"}, "interview and offer emails from the last 3 days stay unread")
        unread_alerts = {r[0] for r in con.execute("SELECT title FROM alerts WHERE read = 0")}
        self.assertEqual(unread_alerts, {"Alert 0", "Alert 1"})

    def test_settings_revision_starts_at_zero(self):
        con = self.open()
        self.run_migrate(con)
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k = 'settings_rev'").fetchone()[0], "0")


class Runner(Folder):
    """The runner's guarantees: order, locking, failures, newer data."""

    def test_migrations_are_numbered_one_to_latest_with_no_gaps(self):
        numbers = [m.number for m in migrate.discover()]
        self.assertEqual(numbers, list(range(1, 18)))
        self.assertEqual(migrate.LATEST, 17)
        self.assertTrue(all(m.title and m.title[0].isupper() for m in migrate.discover()))

    def test_no_migration_holds_destructive_sql(self):
        for path in sorted(migrate.FOLDER.glob("[0-9]*.py")):
            with self.subTest(path.name):
                self.assertIsNone(DESTRUCTIVE.search(path.read_text(encoding="utf-8")))

    def test_a_brand_new_database_is_made_without_a_backup(self):
        con = self.open()
        self.assertEqual(self.run_migrate(con)[-1], migrate.LATEST)
        self.assertFalse(self.backups.exists() and any(self.backups.iterdir()))
        text = con.execute("SELECT text FROM activity").fetchone()[0]
        self.assertEqual(text, "Hal set up its records")

    def test_data_saved_by_a_newer_hal_raises_data_too_new(self):
        con = self.open()
        con.execute(f"PRAGMA user_version = {migrate.LATEST + 1}")
        with self.assertRaises(migrate.DataTooNew) as caught:
            self.run_migrate(con)
        self.assertIn("Update Hal", str(caught.exception))
        with self.assertRaises(migrate.DataTooNew):
            migrate.pending(con)

    def test_a_failing_migration_keeps_the_earlier_ones_and_names_the_backup(self):
        old_db.build(self.db, "L")
        real = migrate.discover()
        broken = [m if m.number != 5 else migrate.Migration(5, m.name, m.title, self._fail) for m in real]
        con = self.open()
        with mock.patch.object(migrate, "discover", return_value=broken), self.assertLogs(migrate.log, "ERROR"), \
                self.assertRaises(migrate.MigrationError) as caught:
            self.run_migrate(con)
        self.assertEqual(migrate.user_version(con), 4)
        self.assertNotIn("ai_usage", {r[0] for r in con.execute("SELECT name FROM sqlite_master")})
        copy = next(self.backups.glob("jobs-v0-to-v17-*.db"))
        self.assertIn(str(copy), str(caught.exception))
        self.assertEqual(self.run_migrate(con)[0], 5, "the next start carries on from where it stopped")

    @staticmethod
    def _fail(con):
        con.execute("CREATE TABLE ai_usage (id INTEGER PRIMARY KEY)")
        raise sqlite3.OperationalError("made-up failure halfway through")

    def test_a_migration_that_commits_by_itself_is_refused(self):
        def commits(con):
            con.execute("COMMIT")

        broken = [migrate.Migration(m.number, m.name, m.title, commits if m.number == 2 else m.up)
                  for m in migrate.discover()]
        con = self.open()
        with mock.patch.object(migrate, "discover", return_value=broken), self.assertLogs(migrate.log, "ERROR"), \
                self.assertRaises(migrate.MigrationError):
            self.run_migrate(con)
        self.assertEqual(migrate.user_version(con), 1)

    def test_two_processes_migrating_at_once_run_it_once(self):
        old_db.build(self.db, "U")
        env = {"PYTHONPATH": str(ROOT), "HAL_TEST_MODE": "1", "HAL_EMPLOYMENT_HOME": str(self.data / "home")}
        # Hold the lock while both start, so they really do meet at it.
        with migrate.file_lock(migrate.lock_path(self.db)):
            children = [subprocess.Popen([sys.executable, "-c", CHILD, str(self.db)], cwd=ROOT, env=env,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        for _ in range(2)]
        outputs = []
        for child in children:
            out, err = child.communicate(timeout=CHILD_TIMEOUT)
            self.assertEqual(child.returncode, 0, err)
            outputs.append(out.strip())
        self.assertEqual(sorted(outputs), sorted(["[]", str(list(range(1, migrate.LATEST + 1)))]))
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1)
        con = self.open()
        self.assertEqual(con.execute("SELECT count(*) FROM activity WHERE kind = 'db.migrate'").fetchone()[0], 1)

    def test_a_held_lock_gives_up_with_plain_words(self):
        con = self.open()
        with migrate.file_lock(migrate.lock_path(self.db)), mock.patch.object(migrate, "LOCK_TIMEOUT", 0.2), \
                self.assertRaises(migrate.MigrationError) as caught:
            self.run_migrate(con)
        self.assertEqual(str(caught.exception), migrate.BUSY)
        self.assertEqual(migrate.user_version(con), 0)

    def test_statements_are_split_whole(self):
        script = "CREATE TABLE a (x TEXT); -- a comment; with a semicolon\nINSERT INTO a VALUES ('1;2');\n"
        self.assertEqual(len(list(migrate.statements(script))), 2)


class DataTasks(Folder):
    """The data-task registry: what is due, and its state."""

    def setUp(self):
        super().setUp()
        saved = dict(tasks.TASKS)
        self.addCleanup(lambda: (tasks.TASKS.clear(), tasks.TASKS.update(saved)))

    def test_a_task_is_due_after_its_migration_until_it_is_done(self):
        @tasks.data_task("tests.example", after=7, title="Did a made-up thing")
        def example(ctx):
            pass

        con = self.open()
        self.run_migrate(con)
        task = tasks.TASKS["tests.example"]
        self.assertIn(task, tasks.due(con))
        tasks.mark(con, task, "running")
        tasks.mark(con, task, "error", "made-up failure")
        self.assertIn(task, tasks.due(con), "a failed task is retried at the next start")
        tasks.mark(con, task, "done")
        self.assertNotIn(task, tasks.due(con))
        self.assertEqual(tasks.state(con, "tests.example"), "done")

    def test_a_task_after_a_migration_that_does_not_exist_is_refused(self):
        with self.assertRaises(ValueError):
            tasks.data_task("tests.bad", after=migrate.LATEST + 1, title="Never")


if __name__ == "__main__":
    unittest.main()
