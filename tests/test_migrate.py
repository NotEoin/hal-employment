"""Tests of the migration series and its runner.

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
# The one destructive statement allowed, by file: 0018 retires the table 0007 copied into `answers2`.
ALLOWED_DESTRUCTIVE = {"0018_old_answers.py": "DROP TABLE answers"}
CHILD = """
import sqlite3, sys
from pathlib import Path
from jobhunt.core.db import migrate
path = Path(sys.argv[1])
con = sqlite3.connect(path, timeout=30)
print(migrate.migrate(con, db_path=path, backup_dir=path.parent / "backups"))
"""


def tables(con: sqlite3.Connection) -> set[str]:
    """The names of the database's tables."""
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


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
        self.assertNotIn("answers", tables(con), "0018 drops the old saved answers once 0007 copied them")
        after = old_db.counts(con, [t for t in old_db.TABLES if t != "answers"])
        for table in after:
            if table == "meta":   # migrations add their own meta rows; the old ones stay
                self.assertGreaterEqual(after[table], before[table])
            else:
                self.assertEqual(after[table], before[table], table)
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k = 'last_discover'").fetchone()[0][:4],
                         str(old_db.datetime.now(old_db.UTC).year))
        self.assertIn("contacts", migrate.columns(con, "applications"))
        copies = list(self.backups.glob(f"jobs-v0-to-v{migrate.LATEST}-*.db"))
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
        self.assertTrue(json.loads(detail)["backup"].startswith(f"jobs-v0-to-v{migrate.LATEST}-"))

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

    def test_saved_answers_carry_over_waiting_for_a_choice_and_private_ones_are_not_copied(self):
        old_db.build(self.db, "L")
        con = self.open()
        self.assertEqual(con.execute("SELECT count(*) FROM answers").fetchone()[0], 22)
        self.run_migrate(con)
        rows = con.execute("SELECT label, answer, scope, scope_key, kind, source FROM answers2").fetchall()
        self.assertEqual(sorted((r[0], json.loads(r[1])) for r in rows), sorted(old_db.ORDINARY_ANSWERS))
        self.assertEqual({r[2] for r in rows}, {"review"})
        self.assertEqual({(r[3], r[4], r[5]) for r in rows}, {("", "fixed", "import")})
        copied = json.dumps([r[1] for r in rows])
        for label, answer in old_db.PRIVATE_ANSWERS:
            self.assertNotIn(label, [r[0] for r in rows])
            if answer.startswith("SYNTH"):
                self.assertNotIn(answer, copied)
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k = 'answers2:private_not_copied'").fetchone()[0],
                         str(len(old_db.PRIVATE_ANSWERS)))
        self.assertNotIn("answers", tables(con))

    def test_the_old_answers_go_at_version_18_and_a_deleted_answer_stays_deleted(self):
        """A database already at 17: what the person kept in `answers2` is kept as it is, and an old row they
        deleted from it is not brought back."""
        old_db.build(self.db, "L")
        con = self.open()
        up_to_17 = migrate.discover()[:17]
        with mock.patch.object(migrate, "LATEST", 17), mock.patch.object(migrate, "discover", return_value=up_to_17):
            self.run_migrate(con)
        con.execute("DELETE FROM answers2 WHERE label = 'When can you start?'")
        con.execute("UPDATE answers2 SET scope = 'everywhere' WHERE label = 'Are you willing to relocate?'")
        con.commit()
        kept = con.execute("SELECT * FROM answers2 ORDER BY id").fetchall()
        self.assertEqual(self.run_migrate(con), list(range(18, migrate.LATEST + 1)))
        self.assertEqual(con.execute("SELECT * FROM answers2 ORDER BY id").fetchall(), kept)
        self.assertNotIn("answers", tables(con))
        self.assertEqual(migrate.user_version(con), migrate.LATEST)

    def test_old_messages_are_marked_read_and_recent_ones_needing_attention_are_not(self):
        old_db.build(self.db, "L")
        con = self.open()
        self.run_migrate(con)
        unread = {r[0] for r in con.execute("SELECT msgid FROM emails WHERE read = 0")}
        self.assertEqual(unread, {"m1", "m5"}, "interview and offer emails from the last 3 days stay unread")
        unread_alerts = {r[0] for r in con.execute("SELECT title FROM alerts WHERE read = 0")}
        self.assertEqual(unread_alerts, {"Alert 0", "Alert 1"})

    def test_record_sources_and_stars_arrive_at_version_28_with_the_profile_kept(self):
        """A profile from version 27 keeps its cards and evidence; each card gets no star and each piece of
        evidence no source, and removing a source's card clears the source's link to it."""
        con = self.open()
        up_to_27 = migrate.discover()[:27]
        with mock.patch.object(migrate, "LATEST", 27), mock.patch.object(migrate, "discover", return_value=up_to_27):
            self.run_migrate(con)
        con.execute("INSERT INTO profile_items (id, kind, fields, created, updated) "
                    "VALUES (1, 'project', '{\"name\": \"Tide gauge\"}', 'x', 'x')")
        con.execute("INSERT INTO profile_evidence (item_id, source_rel, found_at) VALUES (1, 'Sources/CV.md', 'x')")
        con.commit()
        self.assertEqual(self.run_migrate(con), [28, 29, 30, 31])
        self.assertEqual(con.execute("SELECT star, star_by, orphaned_from FROM profile_items").fetchone(), (0, "", ""))
        self.assertEqual(con.execute("SELECT source_id, place, line FROM profile_evidence").fetchone(),
                         (None, "", None))
        self.assertTrue({"record_sources", "record_source_files"} <= tables(con))
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT INTO record_sources (section, target_id, kind, doc_kind, added) "
                    "VALUES ('coursework', 1, 'upload', 'essay', 'x')")
        con.execute("DELETE FROM profile_items WHERE id = 1")
        self.assertEqual(con.execute("SELECT target_id, state, revision FROM record_sources").fetchone(),
                         (None, "new", 0))

    def test_a_record_source_from_version_28_has_no_module_chosen_and_no_details(self):
        con = self.open()
        up_to_28 = migrate.discover()[:28]
        with mock.patch.object(migrate, "LATEST", 28), mock.patch.object(migrate, "discover", return_value=up_to_28):
            self.run_migrate(con)
        con.execute("INSERT INTO record_sources (section, kind, doc_kind, added) VALUES ('coursework', 'upload', "
                    "'essay', 'x')")
        con.commit()
        self.assertEqual(self.run_migrate(con), [29, 30, 31])
        self.assertEqual(con.execute("SELECT module, details FROM record_sources").fetchone(), (None, "{}"))

    def test_project_sources_get_a_parent_and_revisions_at_version_30(self):
        """A source from version 29 keeps its row with nothing said of it; removing a parent removes the sources
        found inside it and their kept reads."""
        con = self.open()
        up_to_29 = migrate.discover()[:29]
        with mock.patch.object(migrate, "LATEST", 29), mock.patch.object(migrate, "discover", return_value=up_to_29):
            self.run_migrate(con)
        con.execute("INSERT INTO record_sources (id, section, kind, doc_kind, locator, added) "
                    "VALUES (1, 'project', 'account', 'github_account', 'averyq', 'x')")
        con.commit()
        self.assertEqual(self.run_migrate(con), [30, 31])
        self.assertEqual(con.execute("SELECT parent_id, meta, what_changed FROM record_sources").fetchone(),
                         (None, "{}", ""))
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT INTO record_sources (id, section, kind, doc_kind, locator, added, parent_id) "
                    "VALUES (2, 'project', 'link', 'repo_url', 'https://github.com/averyq/tide', 'x', 1)")
        con.execute("INSERT INTO record_source_revisions (source_id, revision, read_at) VALUES (2, 1, 'x')")
        con.execute("DELETE FROM record_sources WHERE id = 1")
        self.assertEqual(con.execute("SELECT count(*) FROM record_sources").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT count(*) FROM record_source_revisions").fetchone()[0], 0)

    def test_sorts_and_their_proposals_arrive_at_version_31_and_go_together(self):
        """A record from version 30 keeps its sources; a sort's proposals and what a move set aside go with it."""
        con = self.open()
        up_to_30 = migrate.discover()[:30]
        with mock.patch.object(migrate, "LATEST", 30), mock.patch.object(migrate, "discover", return_value=up_to_30):
            self.run_migrate(con)
        con.execute("INSERT INTO record_sources (id, section, kind, doc_kind, added) "
                    "VALUES (1, 'cv', 'upload', 'cv', 'x')")
        con.commit()
        self.assertEqual(self.run_migrate(con), [31])
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT INTO record_sorts (id, kind, created, updated) VALUES (1, 'folder', 'x', 'x')")
        con.execute("INSERT INTO record_sort_rows (sort_id, path, rel, held, source_id) "
                    "VALUES (1, '/a', 'a', 'upload', 1)")
        con.execute("INSERT INTO record_sort_kept_out (sort_id, what, row) VALUES (1, 'evidence', '{}')")
        self.assertEqual(con.execute("SELECT state, ticked, by FROM record_sort_rows").fetchone(),
                         ("proposed", 1, "rules"))
        con.execute("DELETE FROM record_sorts WHERE id = 1")
        self.assertEqual([con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                          for t in ("record_sort_rows", "record_sort_kept_out", "record_sources")], [0, 0, 1])

    def test_settings_revision_starts_at_zero(self):
        con = self.open()
        self.run_migrate(con)
        self.assertEqual(con.execute("SELECT v FROM meta WHERE k = 'settings_rev'").fetchone()[0], "0")


class Runner(Folder):
    """The runner's guarantees: order, locking, failures, newer data."""

    def test_migrations_are_numbered_one_to_latest_with_no_gaps(self):
        numbers = [m.number for m in migrate.discover()]
        self.assertEqual(numbers, list(range(1, 32)))
        self.assertEqual(migrate.LATEST, 31)
        self.assertTrue(all(m.title and m.title[0].isupper() for m in migrate.discover()))

    def test_no_migration_holds_destructive_sql_but_the_one_allowed(self):
        for path in sorted(migrate.FOLDER.glob("[0-9]*.py")):
            with self.subTest(path.name):
                found = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                         if DESTRUCTIVE.search(line)]
                allowed = ALLOWED_DESTRUCTIVE.get(path.name)
                self.assertEqual(found, [f'con.execute("{allowed}")'] if allowed else [])

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
        copy = next(self.backups.glob(f"jobs-v0-to-v{migrate.LATEST}-*.db"))
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
