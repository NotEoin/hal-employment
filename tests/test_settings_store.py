"""The minimal settings store: defaults are never stored, every write has history and a
revision, and other processes' writes are seen.
"""
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import stubs

from jobhunt import db
from jobhunt.core import events, paths, settings
from jobhunt.core.settings import store

stubs.install()


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
        settings.on_change("advanced.", lambda key, old, new: heard.append((key, old, new)))
        settings.set("advanced.port", 8800)
        self.assertIn(("advanced.port", 8737, 8800), heard)


if __name__ == "__main__":
    unittest.main()
