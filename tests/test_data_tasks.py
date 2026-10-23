"""Data tasks: each runs once as a background action after its migration, a failed one is tried
again next time, and their state is in the `data_tasks` table.
"""
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - puts the run in a temporary home before jobhunt is imported
from test_runner import fresh_home

from jobhunt.core import plugins, runner
from jobhunt.core.db import tasks


class DataTasks(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(fresh_home())
        self.runs = []
        # Only this test's made-up tasks are due: the real ones (find.rescore_all) are loaded first, so a later
        # `load()` finds their modules imported already, then set aside until the test ends.
        tasks.load()
        self.enterContext(mock.patch.dict(tasks.TASKS, clear=True))

    def task(self, name: str, *, after: int = 4, fail_first: bool = False):
        def fn(ctx):
            self.runs.append(name)
            ctx.changed("Sorted 3 saved answers.")
            if fail_first and self.runs.count(name) == 1:
                raise RuntimeError("The record folder was not there")
        tasks.data_task(name, after=after, title=f"Tidied {name}")(fn)
        self.addCleanup(tasks.TASKS.pop, name, None)
        self.addCleanup(runner.forget, name)

    def test_a_data_task_runs_once_and_retries_after_a_failure(self):
        self.task("test.flaky_task", fail_first=True)
        first = tasks.run_due(self.con)
        self.assertEqual([(r["kind"], r["state"]) for r in first], [("test.flaky_task", "error")])
        self.assertEqual(tasks.state(self.con, "test.flaky_task"), "error")
        detail = self.con.execute("SELECT detail FROM data_tasks WHERE name = 'test.flaky_task'").fetchone()[0]
        self.assertIn("The record folder was not there", detail)
        second = tasks.run_due(self.con)
        self.assertEqual([r["state"] for r in second], ["done"])
        self.assertEqual(tasks.state(self.con, "test.flaky_task"), "done")
        self.assertEqual(tasks.run_due(self.con), [])
        self.assertEqual(self.runs, ["test.flaky_task", "test.flaky_task"])

    def test_tasks_run_in_migration_order_as_background_actions_with_a_why(self):
        self.task("test.later", after=9)
        self.task("test.earlier", after=6)
        done = tasks.run_due(self.con)
        self.assertEqual(self.runs, ["test.earlier", "test.later"])
        for d in done:
            self.assertEqual((d["priority"], d["state"], d["steps"][0]["text"]), (2, "done", f"Tidied {d['kind']}"))
            row = self.con.execute("SELECT area, why, changed FROM activity WHERE action_id = ?", (d["id"],)).fetchone()
            self.assertEqual((row["area"], row["why"], row["changed"]),
                             ("system", tasks.WHY, "Sorted 3 saved answers."))

    def test_a_task_waits_for_its_migration(self):
        self.task("test.waiting")
        with mock.patch.object(tasks, "user_version", lambda con: 3):
            self.assertEqual(tasks.run_due(self.con), [])
        self.assertEqual(self.runs, [])

    def test_an_importer_can_run_a_task_now_even_after_it_ran(self):
        self.task("test.again")
        tasks.run_due(self.con)
        self.assertEqual(tasks.run_now("test.again")["state"], "done")
        self.assertEqual(self.runs, ["test.again", "test.again"])
        with self.assertRaises(LookupError):
            tasks.run_now("test.not_a_task")

    def test_a_task_registered_in_an_area_actions_module_is_found(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "made_up_area_actions.py").write_text(textwrap.dedent("""
            from jobhunt.core.db import tasks

            @tasks.data_task("test.from_module", after=4, title="Tidied the made-up area")
            def tidy(ctx):
                ctx.changed("Nothing needed tidying.")
        """), encoding="utf-8")
        self.enterContext(mock.patch.object(sys, "path", [str(folder), *sys.path]))
        self.addCleanup(sys.modules.pop, "made_up_area_actions", None)
        self.addCleanup(tasks.TASKS.pop, "test.from_module", None)
        self.addCleanup(runner.forget, "test.from_module")
        names = {"tasks": [], "actions": ["made_up_area_actions"]}
        real = plugins.names   # other kinds keep theirs: the status line lists the schedule on another thread
        with mock.patch.object(plugins, "names", lambda kind: names[kind] if kind in names else real(kind)):
            done = tasks.run_due(self.con)
        self.assertEqual([(d["kind"], d["state"]) for d in done], [("test.from_module", "done")])

    def test_the_old_to_do_list_is_not_loaded_as_data_tasks(self):
        self.assertIn("jobhunt.tasks", tasks.NOT_TASK_MODULES)
        tasks.load()
        self.assertNotIn("jobhunt.tasks", plugins.FAILED)


if __name__ == "__main__":
    unittest.main()
