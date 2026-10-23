"""Area code is found by module name, failures are reported, and start tasks run once."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.core import plugins


class Discovery(unittest.TestCase):
    """`modules(kind)` imports every `<kind>.py` and `*_<kind>.py` under jobhunt."""

    def test_routes_modules_are_found_by_name_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("find/routes.py", "find/goal_routes.py", "core/settings/api_routes.py", "web/router.py",
                        "web/static/app/fake_routes.py", "find/routes_helper.py"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("", encoding="utf-8")
            with mock.patch.object(plugins, "_root", return_value=root):
                names = plugins.names("routes")
        self.assertEqual(names, ["jobhunt.core.settings.api_routes", "jobhunt.find.goal_routes", "jobhunt.find.routes"])

    def test_migrations_and_commands_are_never_loaded_as_area_modules(self):
        # migration 0003_settings.py matched the settings kind and would have been imported
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("core/db/migrations/0003_settings.py", "cli/settings.py", "find/settings.py",
                        "find/goal_settings.py"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("", encoding="utf-8")
            with mock.patch.object(plugins, "_root", return_value=root):
                names = plugins.names("settings")
        self.assertEqual(names, ["jobhunt.find.goal_settings", "jobhunt.find.settings"])

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            plugins.names("widgets")

    def test_a_module_that_fails_to_import_is_reported_not_skipped(self):
        with mock.patch.object(plugins, "names", return_value=["jobhunt.core.no_such_routes"]), \
                self.assertLogs("jobhunt.core.plugins", "ERROR"):
            self.assertEqual(plugins.modules("routes"), [])
        self.assertIn("jobhunt.core.no_such_routes", plugins.FAILED)
        plugins.FAILED.pop("jobhunt.core.no_such_routes")


class StartTasks(unittest.TestCase):
    """Work the server does once at start."""

    def test_start_tasks_run_in_order_and_a_failure_is_named(self):
        ran = []
        with mock.patch.object(plugins, "_start", []):
            plugins.on_start(lambda: ran.append("first"))

            @plugins.on_start
            def broken():
                raise RuntimeError("a made-up failure")

            plugins.on_start(lambda: ran.append("last"))
            with self.assertLogs("jobhunt.core.plugins", "ERROR"):
                failed = plugins.run_start_tasks()
        self.assertEqual(ran, ["first", "last"])
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].endswith("broken"))


if __name__ == "__main__":
    unittest.main()
