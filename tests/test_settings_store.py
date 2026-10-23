"""The settings store: defaults are never stored, every change has history, Undo and a
revision, goal changes write goal versions, and other processes' writes are seen.
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import db
from jobhunt.core import events, paths, settings
from jobhunt.core.settings import store


@contextlib.contextmanager
def fresh_home():
    """A temporary home with an empty cache, so no value from another test's database is seen."""
    with tempfile.TemporaryDirectory(prefix="hal-settings-") as tmp, paths.set_override(Path(tmp)):
        store.drop_cache()
        try:
            yield
        finally:
            store.drop_cache()


class Store(unittest.TestCase):
    """Against a fresh database in a temporary home."""

    def setUp(self):
        home = fresh_home()
        home.__enter__()
        self.addCleanup(home.__exit__, None, None, None)

    def rows(self, sql: str) -> list:
        con = db.connect()
        try:
            return [tuple(r) for r in con.execute(sql)]
        finally:
            con.close()

    def test_a_default_is_read_and_never_written(self):
        self.assertEqual(settings.get("advanced.port"), 8737)
        self.assertEqual(self.rows("SELECT * FROM settings"), [])

    def test_a_change_is_stored_with_history_a_revision_and_an_event(self):
        sub = events.subscribe()
        self.addCleanup(sub.close)
        before = settings.revision()
        result = settings.set("advanced.port", 8800)
        self.assertEqual((result.key, result.value, result.restart), ("advanced.port", 8800, True))
        self.assertEqual(settings.get("advanced.port"), 8800)
        self.assertEqual(settings.revision(), before + 1)
        self.assertEqual(self.rows("SELECT key, old, new, by FROM settings_history"),
                         [("advanced.port", "8737", "8800", "you")])
        self.assertEqual(sub.get(timeout=1).data, {"keys": ["advanced.port"]})
        self.assertIsNotNone(result.activity_id)
        undo = self.rows("SELECT undo FROM activity WHERE kind = 'setting.changed'")[0][0]
        self.assertEqual(json.loads(undo), {"type": "setting", "key": "advanced.port", "value": 8737})

    def test_reset_removes_the_stored_value(self):
        settings.set("advanced.port", 8800)
        settings.reset("advanced.port")
        self.assertEqual(self.rows("SELECT * FROM settings"), [])
        self.assertEqual(settings.get("advanced.port"), 8737)

    def test_an_unregistered_key_is_a_key_error(self):
        with self.assertRaises(KeyError):
            settings.get("advanced.no_such_key")

    def test_a_value_out_of_range_is_refused_with_words(self):
        from jobhunt.core.settings import registry

        with self.assertRaises(registry.SettingError) as caught:
            settings.set("advanced.port", 80)
        self.assertEqual(caught.exception.key, "advanced.port")
        self.assertEqual(self.rows("SELECT * FROM settings"), [])

    def test_a_write_by_another_process_is_seen_after_the_revision_check(self):
        self.assertEqual(settings.get("advanced.port"), 8737)
        con = db.connect()
        try:
            store.write(con, "advanced.port", 9000, default=8737, by="you", at="2026-10-22T09:00:00Z")
        finally:
            con.close()
        with mock.patch.object(store, "REV_CHECK_S", 0):
            self.assertEqual(settings.get("advanced.port"), 9000)

    def test_cached_functions_run_again_after_a_change(self):
        calls = []

        @settings.cached("advanced.")
        def port_words():
            calls.append(1)
            return f"port {settings.get('advanced.port')}"

        self.assertEqual(port_words(), "port 8737")
        self.assertEqual(port_words(), "port 8737")
        settings.set("advanced.port", 8800)
        self.assertEqual(port_words(), "port 8800")
        self.assertEqual(len(calls), 2)

    def test_subscribers_hear_changes_under_their_prefix(self):
        heard = []
        self.addCleanup(settings.on_change("advanced.", lambda key, old, new: heard.append((key, old, new))))
        settings.set("advanced.port", 8800)
        self.assertIn(("advanced.port", 8737, 8800), heard)


    def test_a_bad_value_is_refused_in_words_and_nothing_is_written(self):
        from jobhunt.core.settings import registry

        before = settings.revision()
        with self.assertRaises(registry.SettingError) as caught:
            settings.set("goal.min_pay", "lots of money")
        self.assertEqual(caught.exception.key, "goal.min_pay")
        self.assertIn("Try 24000 or 24k", str(caught.exception))
        for table in ("settings", "settings_history", "goal_versions"):
            self.assertEqual(self.rows(f"SELECT * FROM {table}"), [], table)
        self.assertEqual(self.rows("SELECT * FROM activity WHERE area = 'settings'"), [])
        self.assertEqual(settings.revision(), before)

    def test_several_values_are_written_all_or_nothing(self):
        from jobhunt.core.settings import registry

        with self.assertRaises(registry.SettingError):
            settings.set_many({"you.city": "Harrowgate Vale", "advanced.port": 80})
        self.assertEqual(self.rows("SELECT * FROM settings"), [])
        results = settings.set_many({"you.city": "Harrowgate Vale", "advanced.port": 8800})
        self.assertEqual([r.key for r in results], ["you.city", "advanced.port"])
        self.assertEqual(settings.get("you.city"), "Harrowgate Vale")

    def test_a_value_written_by_another_process_is_seen_within_two_seconds(self):
        self.assertEqual(settings.get("advanced.port"), 8737)
        code = "from jobhunt.core import settings; settings.set('advanced.port', 8900)"
        env = {**os.environ, "HAL_EMPLOYMENT_HOME": str(paths.home_override())}
        subprocess.run([sys.executable, "-c", code], cwd=paths.code_dir(), env=env, check=True, timeout=60)
        written = time.monotonic()
        while settings.get("advanced.port") != 8900:
            self.assertLess(time.monotonic() - written, store.REV_CHECK_S + 0.5, "the change was not seen in time")
            time.sleep(0.05)
        self.assertLessEqual(time.monotonic() - written, store.REV_CHECK_S + 0.5)

    def test_every_change_is_in_history_with_undo(self):
        first = settings.set("you.city", "Harrowgate Vale")
        settings.set("you.city", "Lindenford")
        history = settings.history("you.city")
        self.assertEqual([(h["old"], h["new"]) for h in history], [("Harrowgate Vale", "Lindenford"),
                                                                    ("", "Harrowgate Vale")])
        self.assertEqual(history[1]["activity_id"], first.activity_id)
        undo, why = self.rows(f"SELECT undo, why FROM activity WHERE id = {history[0]['activity_id']}")[0]
        self.assertTrue(why)
        settings.undo(json.loads(undo))
        self.assertEqual(settings.get("you.city"), "Harrowgate Vale")
        self.assertEqual(len(settings.history("you.city")), 3)

    def test_a_goal_change_writes_a_goal_version(self):
        result = settings.set("goal.min_pay", "24k")
        self.assertEqual(result.value, 24000)
        self.assertIsNotNone(result.goal_version)
        summary, data = self.rows(f"SELECT summary, data FROM goal_versions WHERE id = {result.goal_version}")[0]
        # the summary once read "changed to Hal reads this as 24,000 pounds a year"
        self.assertEqual(summary, "Lowest pay you will consider changed to 24,000 pounds a year")
        data = json.loads(data)
        self.assertEqual(data["goal.min_pay"], 24000)
        self.assertIn("scoring.base", data)
        self.assertIsNone(settings.set("you.city", "Lindenford").goal_version)

    def test_a_list_of_choices_is_worded_with_its_labels_not_its_codes(self):
        # Reset and the goal history said "graduate_job, internship" (bare code words are banned on screen)
        settings.set("goal.kinds", ["graduate_job", "internship"])
        self.assertEqual(settings.display("goal.kinds"), "Graduate and entry-level jobs, Paid internships")

    def test_hal_never_changes_the_goal_scoring_or_cv_by_itself(self):
        for key, value in (("goal.min_pay", 30000), ("scoring.base", 30), ("cvdocs.tailor", "no")):
            with self.subTest(key=key), self.assertRaises(PermissionError):
                settings.set(key, value, by="hal")
        self.assertEqual(self.rows("SELECT * FROM settings"), [])

    def test_a_scoring_change_says_how_many_open_jobs_a_recheck_covers(self):
        con = db.connect()
        try:
            for n, status in enumerate(("new", "ready", "applied")):
                con.execute("INSERT INTO jobs (id, source, company, title, first_seen, last_seen, status) "
                            "VALUES (?, 'made-up', 'Fernhill Analytics', 'Graduate Engineer', 'x', 'x', ?)",
                            (f"job-{n}", status))
            con.commit()
        finally:
            con.close()
        self.assertEqual(settings.set("scoring.base", 30).rescore_count, 2)
        self.assertIsNone(settings.set("you.name", "Robin Testwell").rescore_count)

    def test_a_failing_subscriber_does_not_stop_the_others(self):
        heard = []

        def broken(key, old, new):
            raise RuntimeError("made-up failure")

        self.addCleanup(settings.on_change("you.", broken))
        self.addCleanup(settings.on_change("you.", lambda key, old, new: heard.append(key)))
        with self.assertLogs("jobhunt.core.settings", "ERROR"):
            settings.set("you.city", "Lindenford")
        self.assertEqual(heard, ["you.city"])


class Routes(unittest.TestCase):
    """The Settings page's API against a real server in its own home."""

    def setUp(self):
        from support.api import HalServer

        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.client = self.hal.signed_in()

    def test_a_change_through_the_page_answers_with_its_words(self):
        reply = self.client.request("PUT", "/api/v2/settings/goal.min_pay", {"value": "\u00a324,000"})
        self.assertEqual(reply.status, 200, reply.text)
        body = reply.json()
        self.assertEqual((body["value"], body["display"]), (24000, settings.display("goal.min_pay", 24000)))
        self.assertIsNotNone(body["goal_version"])
        values = self.client.get("/api/v2/settings").json()
        self.assertEqual(values["values"]["goal.min_pay"], 24000)
        self.assertIn("goal.min_pay", values["changed"])
        self.assertEqual(values["secrets"]["email.app_password"], "not saved")
        self.assertNotIn("you.private", values["values"])
        reset = self.client.request("DELETE", "/api/v2/settings/goal.min_pay").json()
        self.assertEqual(reset["value"], 0)

    def test_a_bad_value_through_the_page_names_its_field(self):
        reply = self.client.request("PUT", "/api/v2/settings/goal.min_pay", {"value": "lots"})
        self.assertEqual(reply.status, 400)
        self.assertEqual(reply.json()["error"]["field"], "goal.min_pay")

    def test_a_port_change_says_a_restart_is_needed(self):
        self.assertFalse(self.client.get("/api/v2/settings").json()["restart_needed"])
        body = self.client.request("PUT", "/api/v2/settings/advanced.port", {"value": 8801}).json()
        self.assertTrue(body["restart"])
        self.assertTrue(self.client.get("/api/v2/settings").json()["restart_needed"])

    def test_the_schema_hides_developer_rows_and_disables_local_rows_remotely(self):
        settings.set("advanced.developer_mode", False)
        groups = self.client.get("/api/v2/settings/schema").json()["groups"]
        keys = {i["key"] for g in groups for i in g["items"]}
        self.assertNotIn("addon", {g["id"] for g in groups})
        self.assertIn("you.name", keys)
        remote = self.hal.signed_in(remote=True, remote_host="desktop")
        rows = {i["key"]: i for g in remote.get("/api/v2/settings/schema").json()["groups"] for i in g["items"]}
        self.assertTrue(rows["record.folder"]["disabled_why"])
        reply = remote.request("PUT", "/api/v2/settings/record.folder", {"value": "/made-up/record"})
        self.assertEqual(reply.status, 409)

    def test_an_unknown_setting_is_not_found(self):
        self.assertEqual(self.client.request("PUT", "/api/v2/settings/goal.nothing", {"value": 1}).status, 404)


class CommandLine(unittest.TestCase):
    """`python -m jobhunt settings` in a temporary home."""

    def setUp(self):
        home = fresh_home()
        home.__enter__()
        self.addCleanup(home.__exit__, None, None, None)

    def run_cli(self, *argv: str) -> tuple[str, str | None]:
        from jobhunt.cli import settings as cli

        parser = argparse.ArgumentParser(prog="jobhunt")
        cli.register(parser.add_subparsers())
        args = parser.parse_args(["settings", *argv])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                args.fn(args)
            except SystemExit as e:
                return out.getvalue(), str(e.code)
        return out.getvalue(), None

    def test_a_value_is_set_read_and_listed_with_its_words(self):
        out, error = self.run_cli("set", "goal.min_pay", "24k")
        self.assertIsNone(error)
        self.assertIn("goal.min_pay = 24000", out)
        self.assertEqual(self.run_cli("get", "goal.min_pay"), ("24000\n", None))
        out, _ = self.run_cli("list", "goal.min")
        self.assertIn("goal.min_pay = 24000 *", out)

    def test_a_bad_value_is_refused_in_words(self):
        _, error = self.run_cli("set", "goal.min_pay", "lots")
        self.assertIn("Try 24000 or 24k", error)
        self.assertEqual(settings.get("goal.min_pay"), 0)

    def test_secrets_and_private_answers_print_only_whether_they_are_saved(self):
        from jobhunt.core.settings import secrets

        with secrets.use_keyring(None):
            secrets.put("sites.reed_key", "made-up-reed-4410")
            out, _ = self.run_cli("list", "sites.")
            self.assertIn("sites.reed_key = (saved)", out)
            self.assertNotIn("made-up-reed-4410", out)
            _, error = self.run_cli("set", "sites.reed_key", "made-up-other")
            self.assertIn("Settings page", error)


if __name__ == "__main__":
    unittest.main()
