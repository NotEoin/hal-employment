"""The status line (`/status`): the most urgent action wins, with its step, its Why? and Stop; idle,
it says when Hal works next and whether it is paused.
"""
import sys
import threading
import types
import unittest
from unittest import mock

from support.api import HalServer
from test_runner import WAIT_S, Kinds, fresh_home

from jobhunt.core import events, runner, status


def from_modules(module: str, name: str):
    """`status._optional` for the stand-in area modules a test put in `sys.modules`."""
    return getattr(sys.modules.get(module), name, None)


class StatusLine(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(fresh_home())
        self.kinds = Kinds(self)

    def hold(self, kind: str, *, label: str, steps: int = 0, total: int | None = None,
             **start) -> tuple[str, threading.Event]:
        """Starts an action that takes `steps` steps and then waits; returns its id and its release."""
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def held(ctx):
            for n in range(steps):
                ctx.step(f"part {n + 1}", total=total)
            reached.set()
            release.wait(WAIT_S)
        self.kinds.add(kind, held, label=label, why="Because the test said so")
        action_id = runner.start(kind, **start)
        self.assertTrue(reached.wait(WAIT_S))
        runner.flush()
        return action_id, release

    def test_the_status_line_shows_the_most_urgent_running_action(self):
        background, _ = self.hold("test.screen", label="Checking 12 new matches", priority=2, subject="_screen")
        line = status.status(self.con)
        self.assertEqual((line["busy"], line["text"], line["action_id"]), (True, "Checking 12 new matches", background))
        asked, release = self.hold("test.letter", label="Writing the cover letter for {title} at {company}",
                                   steps=3, total=5, job_id="demo:1", priority=0)
        line = status.status(self.con)
        self.assertEqual(line["text"], "Writing the cover letter for Junior Archivist at Riverside Records")
        self.assertEqual((line["step_n"], line["step_total"], line["action_id"]), (3, 5, asked))
        why = self.con.execute("SELECT activity_id FROM actions WHERE id = ?", (asked,)).fetchone()[0]
        self.assertEqual(line["why_activity_id"], why)
        release.set()
        runner.wait(asked)
        self.assertEqual(status.status(self.con)["action_id"], background)

    def test_an_action_waiting_for_you_comes_before_any_other(self):
        self.hold("test.urgent", label="Answering a question", priority=0, subject="_ask")
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def sign_in(ctx):
            ctx.waiting_for_you("Sign in to the site")
            reached.set()
            release.wait(WAIT_S)
        self.kinds.add("test.signin", sign_in, label="Opening the form", priority=1)
        waiting = runner.start("test.signin", job_id="demo:2")
        self.assertTrue(reached.wait(WAIT_S))
        runner.flush()
        line = status.status(self.con)
        self.assertEqual((line["action_id"], line["text"]), (waiting, "Opening the form. Sign in to the site"))

    def test_idle_it_says_when_hal_works_next_and_whether_it_is_paused(self):
        scheduler = types.ModuleType("jobhunt.core.scheduler")
        # the schedule's labels are verbs, and the line once read "Next search for jobs at 12:30"
        scheduler.next_runs = lambda: [
            {"id": "find.discover", "label": "Search for jobs", "at": "2026-10-22T12:30:00Z"},
            {"id": "mail.check", "label": "Check email", "at": "2026-10-22T11:45:00Z"},
            {"id": "notify.digest", "label": "Send the daily summary", "at": "2026-10-22T17:00:00Z"}]
        scheduler.paused = lambda: True
        with mock.patch.dict(sys.modules, {"jobhunt.core.scheduler": scheduler}), \
             mock.patch.object(status, "_optional", from_modules):
            line = status.status(self.con)
        self.assertFalse(line["busy"])
        self.assertTrue(line["paused"])
        self.assertEqual(line["next"], [{"what": "job search", "at": "2026-10-22T12:30:00Z"},
                                        {"what": "email check", "at": "2026-10-22T11:45:00Z"}])

    def test_a_broken_area_never_blanks_the_line(self):
        def broken(con):
            raise OSError("the disk went away")
        health = types.ModuleType("jobhunt.core.health")
        health.problem_count = broken
        with mock.patch.dict(sys.modules, {"jobhunt.core.health": health}), \
             mock.patch.object(status, "_optional", from_modules), \
             self.assertLogs("jobhunt.core.status", "ERROR"):
            line = status.status(self.con)
        self.assertEqual((line["busy"], line["problems"]), (False, 0))

    def test_each_change_publishes_the_whole_status_line(self):
        sub = events.subscribe()
        self.addCleanup(sub.close)
        self.kinds.add("test.one", lambda ctx: ctx.step("looking", total=1), label="One look")
        runner.wait(runner.start("test.one"))
        seen = []
        while (ev := sub.get(timeout=0.2)) is not None:
            seen.append(ev)
        states = [e.data["state"] for e in seen if e.type == "action"]
        self.assertEqual(states, ["queued", "running", "done"])
        lines = [e.data for e in seen if e.type == "status"]
        self.assertTrue(any(line["busy"] and line["text"] == "One look" for line in lines))
        self.assertFalse(lines[-1]["busy"])


class Route(unittest.TestCase):
    def test_the_status_route_needs_a_session_and_answers_the_shape(self):
        with HalServer() as hal:
            line = hal.signed_in().get("/api/v2/status").json()
            self.assertEqual(set(line) >= {"busy", "text", "step_n", "step_total", "action_id", "why_activity_id",
                                           "next", "paused", "problems", "ai_limited_until", "reconnecting"}, True)
            self.assertEqual(hal.client().get("/api/v2/status").status, 401)


if __name__ == "__main__":
    unittest.main()
