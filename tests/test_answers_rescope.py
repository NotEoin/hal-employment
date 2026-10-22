"""Tests of the data task `answers.rescope`: where each imported saved answer may be reused.

The saved answers are the made-up ones of `support.old_db`; the profile is a made-up owner imported from
`support.old_repo`, or the registry's defaults.
"""
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import old_db, old_repo
from test_importer import FAKE_NOW, load_export, writable

from jobhunt.core import answers_rescope, importer
from jobhunt.core.db import migrate, tasks
from jobhunt.core.settings import store

DEFAULTS = {"you.start": "now", "you.notice": "None, available immediately", "you.office": True, "you.relocate": False}
RELOCATE = "Are you willing to relocate?"


def scopes(con: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """label -> (scope, kind) of every row in `answers2`."""
    return {label: (scope, kind) for label, scope, kind in con.execute("SELECT label, scope, kind FROM answers2")}


class Classify(unittest.TestCase):
    def check(self, label, answer, expected, profile=None):
        self.assertEqual(answers_rescope.classify(label, answer, profile or DEFAULTS), expected, label)

    def test_salary_and_how_you_heard_are_worked_out_for_each_job(self):
        self.check("What are your salary expectations?", "28,000 GBP", ("review", "derived"))
        self.check("How did you hear about this role?", "Company careers page", ("review", "derived"))

    def test_start_notice_and_office_answers_that_match_the_profile_are_used_everywhere(self):
        self.check("When can you start?", "Straight away", ("everywhere", "fixed"))
        self.check("What is your notice period?", "None, available immediately", ("everywhere", "fixed"))
        self.check("Are you happy to work in the office three days a week?", "Yes", ("everywhere", "fixed"))

    def test_answers_that_disagree_with_the_profile_wait_for_you(self):
        self.check("Which office would you prefer?", "Leeds", ("review", "fixed"))
        self.check(RELOCATE, "Yes", ("review", "fixed"))
        self.check("When can you start?", "In three months", ("review", "fixed"))

    def test_why_us_answers_and_essays_are_kept_only_as_examples(self):
        self.check("Why do you want to work for the employer?", "Because.", ("never", "fixed"))
        self.check("What interests you in this field?", "Archives.", ("never", "fixed"))
        self.check("Tell us about a project", "x" * (answers_rescope.ESSAY_CHARS + 1), ("never", "fixed"))

    def test_answers_naming_the_employer_wait_for_you(self):
        self.check("Have you worked for the employer before?", "No", ("review", "fixed"))

    def test_anything_else_is_used_everywhere(self):
        self.check("Preferred pronouns", "they/them", ("everywhere", "fixed"))
        self.check("Do you hold a full UK driving licence?", "No", ("everywhere", "fixed"))


class Task(unittest.TestCase):
    """A made-up old database migrated to the latest version, in a temporary home."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(writable, self.root)
        self.home = self.root / "home"
        env = mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_HOME": str(self.home), "HAL_EMPLOYMENT_FAKE_NOW": FAKE_NOW})
        env.start()
        self.addCleanup(env.stop)
        store.drop_cache()
        self.addCleanup(store.drop_cache)

    def migrated(self) -> sqlite3.Connection:
        path = self.root / "jobs.db"
        old_db.build(path, "L")
        con = sqlite3.connect(path)
        self.addCleanup(con.close)
        migrate.migrate(con, db_path=path, backup_dir=self.root / "backups")
        return con

    def test_with_the_default_profile_a_saved_yes_to_relocating_stays_for_you_to_choose(self):
        con = self.migrated()
        answers_rescope.run_now(con)
        self.assertEqual(scopes(con)[RELOCATE], ("review", "fixed"))
        self.assertEqual(tasks.state(con, answers_rescope.NAME), tasks.DONE)

    def test_answers_are_rescoped_against_the_imported_profile(self):
        old = old_repo.build(self.root / "old")
        prefs = self.home / "config" / "import" / "owner-prefs.toml"
        with contextlib.redirect_stdout(io.StringIO()):
            load_export().main(["--repo", str(old.root), "--ref", "HEAD", "--out", str(prefs)])
        system = importer.System(port_answers=lambda port: False, timer_active=lambda name: False,
                                 free_bytes=lambda path: 10 ** 12, platform_call=lambda name: None)
        importer.run(importer.Options(repo=old.root, legacy_secrets=old.secrets), system)
        con = sqlite3.connect(self.home / "data" / "jobs.db")
        self.addCleanup(con.close)
        found = scopes(con)
        self.assertEqual(found[RELOCATE], ("everywhere", "fixed"))
        self.assertEqual(found["What are your salary expectations?"], ("review", "derived"))
        self.assertEqual(tasks.state(con, answers_rescope.NAME), tasks.DONE)
        for label, _ in old_db.PRIVATE_ANSWERS:
            self.assertNotIn(label, found)

    def test_a_second_run_changes_nothing_and_keeps_your_own_choices(self):
        con = self.migrated()
        with con:
            con.execute("UPDATE answers2 SET scope = 'job', scope_key = 'lever:birchwell-2', source = 'you' "
                        "WHERE label = ?", ("Preferred pronouns",))
        answers_rescope.run_now(con)
        first = scopes(con)
        moved = answers_rescope.rescope(con)
        self.assertEqual(scopes(con), first)
        self.assertEqual(first["Preferred pronouns"], ("job", "fixed"))
        self.assertEqual(sum(moved.values()), sum(1 for scope, _ in first.values() if scope == "review"))

    def test_the_summary_gives_counts_and_never_an_answer(self):
        con = self.migrated()
        text = answers_rescope.summary(answers_rescope.rescope(con))
        self.assertRegex(text, r"^Sorted \d+ saved answers: \d+ used everywhere")
        for _, answer in old_db.ORDINARY_ANSWERS:
            self.assertNotIn(answer, text)


if __name__ == "__main__":
    unittest.main()
