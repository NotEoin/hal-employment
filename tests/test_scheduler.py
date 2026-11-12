"""The in-app schedule: missed slots catch up once, new times apply at once, children run hidden with
a time limit and Stop, pause spares email, and the owner's old timers and `hal off` are respected.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from test_runner import WAIT_S, fresh_home

from jobhunt.core import builtin_schedule, clock, health, runner, scheduler, settings

MORNING = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)     # a Thursday; the computer is on UTC in these tests


def setUpModule():
    os.environ[clock.ENV_TZ] = "UTC"


def tearDownModule():
    os.environ.pop(clock.ENV_TZ, None)


class Launches:
    """Stands in for starting a job's action and remembers which jobs were started."""

    def __init__(self):
        self.started: list[str] = []

    def __call__(self, job, *, why):
        self.started.append(job.id)
        return f"action-{len(self.started)}"


class Schedule(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(fresh_home())
        self.launches = Launches()
        self.enterContext(mock.patch.object(scheduler, "_launch", self.launches))
        self.enterContext(mock.patch.object(scheduler, "_running", lambda con, action_id: False))
        self.enterContext(mock.patch.object(scheduler, "_STATE", scheduler._State()))
        self.timers: list[str] = []
        platform = mock.Mock(legacy_timers_enabled=lambda: list(self.timers))
        self.enterContext(mock.patch("jobhunt.core.platform.current", return_value=platform))

    def start(self, now):
        scheduler.start(now=now, thread=False)

    def test_a_missed_morning_search_runs_once_when_hal_starts(self):
        yesterday = MORNING - timedelta(days=1)
        self.start(yesterday)                                  # planned yesterday at 10:00
        self.con.execute("UPDATE schedule_state SET last_run = ? WHERE job = 'find.discover'",
                         (clock.iso(yesterday.replace(hour=13, minute=30)),))
        self.con.commit()
        # the computer was off from yesterday afternoon until 10:00 today, past this morning's 07:30 search
        self.start(MORNING)
        self.assertEqual(self.launches.started.count("find.discover"), 1)
        scheduler.tick(MORNING + timedelta(minutes=1))
        scheduler.tick(MORNING + timedelta(minutes=2))
        self.assertEqual(self.launches.started.count("find.discover"), 1)
        nxt = {r["id"]: r["at"] for r in scheduler.next_runs()}
        self.assertEqual(nxt["find.discover"], "2026-10-22T13:30:00Z")

    def test_a_missed_search_waits_for_its_next_time_when_catching_up_is_off(self):
        settings.set("schedule.catch_up", False)
        self.start(MORNING - timedelta(days=1))
        self.start(MORNING)
        self.assertNotIn("find.discover", self.launches.started)

    def test_the_first_start_runs_nothing_that_was_never_planned(self):
        self.start(MORNING)
        self.assertEqual(self.launches.started, [])

    def test_new_search_times_apply_without_a_restart(self):
        self.start(MORNING)
        self.assertEqual({r["id"]: r["at"] for r in scheduler.next_runs()}["find.discover"], "2026-10-22T13:30:00Z")
        settings.set("schedule.search_times", ["11:00", "16:00"])
        scheduler.tick(MORNING + timedelta(minutes=1))
        self.assertEqual({r["id"]: r["at"] for r in scheduler.next_runs()}["find.discover"], "2026-10-22T11:00:00Z")
        scheduler.tick(datetime(2026, 10, 22, 11, 0, 30, tzinfo=UTC))
        self.assertEqual(self.launches.started.count("find.discover"), 1)

    def test_just_watching_searches_on_mondays_only(self):
        settings.set("goal.mode", "watching")
        self.start(MORNING)
        at = {r["id"]: r["at"] for r in scheduler.next_runs()}["find.discover"]
        self.assertEqual(at, "2026-10-26T07:30:00Z")

    def test_pause_stops_searching_but_not_email(self):
        self.start(MORNING)
        scheduler.set_paused(True)
        later = MORNING + timedelta(hours=4)              # past the 13:30 search and several email checks
        scheduler.tick(later)
        self.assertIn("mail.check", self.launches.started)
        self.assertNotIn("find.discover", self.launches.started)
        self.assertNotIn("find.discover", [r["id"] for r in scheduler.next_runs()])
        row = self.con.execute("SELECT state, detail FROM schedule_state WHERE job = 'find.discover'").fetchone()
        self.assertEqual((row["state"], row["detail"]), ("skipped", scheduler.PAUSED_WORDS))
        scheduler.set_paused(False)
        self.assertFalse(scheduler.paused())

    def test_a_job_whose_old_timer_is_enabled_does_not_run(self):
        self.timers[:] = ["jobhunt-mail.timer"]
        self.start(MORNING)
        scheduler.tick(MORNING + timedelta(hours=4))
        self.assertNotIn("mail.check", self.launches.started)
        self.assertIn("find.discover", self.launches.started)
        self.assertEqual(scheduler.timers_in_charge(), ["jobhunt-mail.timer"])
        self.assertNotIn("mail.check", [r["id"] for r in scheduler.next_runs()])

    def test_hal_off_pauses_searches_and_mail_with_the_addon(self):
        with tempfile.TemporaryDirectory() as tmp:
            off = Path(tmp) / "off"
            off.write_text("")
            self.enterContext(mock.patch.object(scheduler, "HAL_OFF", off))
            self.start(MORNING)
            scheduler.tick(MORNING + timedelta(hours=4))
            self.assertIn("mail.check", self.launches.started, "without the add-on, hal off changes nothing")
            self.launches.started.clear()
            settings.set("addon.enabled", True)
            settings.set("addon.respect_off", True)
            scheduler.tick(MORNING + timedelta(hours=8))
            self.assertEqual(self.launches.started, [])
            off.unlink()
            scheduler.tick(MORNING + timedelta(hours=9))
            self.assertIn("mail.check", self.launches.started)

    def test_email_checks_keep_to_their_hours(self):
        late = datetime(2026, 10, 22, 22, 50, tzinfo=UTC)
        self.assertEqual(builtin_schedule.mail_after(late), datetime(2026, 10, 23, 7, 0, tzinfo=UTC))
        self.assertEqual(builtin_schedule.mail_after(MORNING), MORNING + timedelta(minutes=30))

    def test_the_summary_is_never_planned_when_it_is_off(self):
        settings.set("notify.daily_summary", "off")
        self.assertIsNone(builtin_schedule.summary_after(MORNING))


CHILD = """
import sys, time
print("Searching the first job site", flush=True)
print("Found 3 new jobs", flush=True)
if sys.argv[1:] == ["forever"]:
    import subprocess
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    time.sleep(600)
"""


class Children(unittest.TestCase):
    def setUp(self):
        self.con = self.enterContext(fresh_home())
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        script = Path(tmp) / "child.py"
        script.write_text(CHILD)
        self.argv: list[list[str]] = []
        real_argv = scheduler.child_argv

        def argv(args):
            self.argv.append(real_argv(args))
            return [sys.executable, str(script), *args]
        self.enterContext(mock.patch.object(scheduler, "child_argv", argv))
        self.jobs = {}
        self.enterContext(mock.patch.object(scheduler, "jobs", lambda: dict(self.jobs)))
        self.addCleanup(runner.forget, "test.search")
        from jobhunt.core import platform

        real_popen = platform.current().popen_hidden
        self.popen_kw: dict = {}

        def hidden(args, **kw):
            proc = real_popen(args, **kw)
            self.popen_kw.update(kw, proc=proc)
            return proc
        self.enterContext(mock.patch.object(platform.current(), "popen_hidden", hidden))

    def job(self, *args):
        self.jobs["test.search"] = scheduler.Job("test.search", "find", "Search for jobs", lambda after: None, args)

    def test_jobs_run_hidden_with_a_one_hour_limit_and_can_be_stopped(self):
        self.assertEqual(scheduler.CHILD_TIMEOUT_S, 3600)
        self.job("discover")
        done = runner.wait(scheduler.run_now("test.search"), WAIT_S)
        self.assertEqual(done["state"], "done")
        self.assertEqual([s["text"] for s in done["steps"]],
                         ["Starting: Search for jobs", "Searching the first job site", "Found 3 new jobs"])
        self.assertEqual(self.argv[0][1:], ["-X", "utf8", "-m", "jobhunt", "discover"])
        self.assertEqual(self.popen_kw["stdin"], scheduler.subprocess.DEVNULL)

        self.job("forever")
        action_id = scheduler.run_now("test.search")
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline and len(runner.get(action_id)["steps"]) < 3:
            time.sleep(0.05)
            runner.flush()
        child = self.popen_kw["proc"]
        self.assertIsNone(child.poll(), runner.get(action_id))
        runner.stop(action_id)
        self.assertEqual(runner.wait(action_id, WAIT_S)["state"], "stopped")
        child.wait(WAIT_S)
        self.assertIsNotNone(child.poll(), "Stop ends the child")

    def test_a_child_that_runs_past_its_limit_is_stopped_with_words(self):
        def short(ctx):
            ctx.step("Starting")
            return scheduler.run_child(ctx, ("forever",), timeout=1)
        runner.action("test.short", area="find", label="Search", why="Because")(short)
        self.addCleanup(runner.forget, "test.short")
        with self.assertLogs("jobhunt.core.runner", "ERROR"):
            done = runner.wait(runner.start("test.short"), WAIT_S)
        self.assertEqual(done["state"], "error")
        self.assertIn("took over", done["error"])
        self.assertIsNotNone(self.popen_kw["proc"].wait(WAIT_S))


class Routes(unittest.TestCase):
    def test_pause_and_carry_on_through_the_api(self):
        with HalServer() as hal:
            client = hal.signed_in()
            self.assertEqual(client.post("/api/v2/status/pause", {"paused": True}).json(), {"paused": True})
            self.assertTrue(client.get("/api/v2/status").json()["paused"])
            self.assertEqual(client.post("/api/v2/status/pause", {}).json(), {"paused": False})
            self.assertEqual(client.post("/api/v2/status/pause", {"paused": "yes"}).status, 400)
            self.assertEqual(hal.client().post("/api/v2/status/pause", {"paused": True}).status, 401)

    def test_restart_answers_first_then_restarts(self):
        restarted = threading.Event()
        plat = mock.Mock(restart_server=restarted.set)
        with HalServer() as hal, mock.patch("jobhunt.core.platform.current", return_value=plat):
            self.assertEqual(hal.signed_in().post("/api/v2/system/restart").json(), {"ok": True})
            self.assertTrue(restarted.wait(WAIT_S))

    def test_home_answers_with_what_needs_you(self):
        with HalServer() as hal:
            client = hal.signed_in()
            client.post("/api/v2/status/pause", {"paused": True})
            home = client.get("/api/v2/home").json()
            self.assertTrue({"date_words", "greeting", "needs", "new_matches", "counts", "today"} <= set(home))
            self.assertEqual([n["title"] for n in home["needs"]], ["Hal is paused"])
            self.assertEqual(home["needs"][0]["act"], {"label": "Carry on", "action": "/status/pause"})

    def test_the_schedule_lists_every_job(self):
        with HalServer() as hal:
            reply = hal.signed_in().get("/api/v2/schedule").json()
            self.assertEqual({j["id"] for j in reply["jobs"]},
                             {"find.discover", "learn.patterns", "mail.check", "notify.digest", "record.sync",
                              "record.projects_refresh",
                              "system.backup", "system.tidy", "updates.check"})


class IsHalWorking(unittest.TestCase):
    def setUp(self):
        self.enterContext(fresh_home())
        self.enterContext(mock.patch.object(scheduler, "_STATE", scheduler._State()))

    def schedule_row(self):
        return next(r for r in health.run_all() if r["id"] == "schedule")

    def test_the_owners_timers_are_fine_and_anyone_elses_get_a_fix(self):
        scheduler._STATE.timers = frozenset({"jobhunt-discover.timer"})
        row = self.schedule_row()
        self.assertEqual((row["level"], row["text"]), ("warn", "Your old timers still run searches."))
        self.assertEqual(row["fix"]["label"], "Let Hal run them instead")
        settings.set("addon.enabled", True)
        row = self.schedule_row()
        self.assertEqual((row["level"], row["text"]),
                         ("ok", "Searches, email checks and the summary run from your own timers."))

    def test_the_schedule_row_names_the_next_search(self):
        with mock.patch("jobhunt.core.platform.current", return_value=mock.Mock(legacy_timers_enabled=list)), \
                mock.patch.object(scheduler, "_launch", Launches()):
            scheduler.start(now=clock.now_utc(), thread=False)
        self.assertRegex(self.schedule_row()["text"], r"^Next job search at .+\. Missed runs catch up\.$")

    def test_start_at_login_is_checked_against_the_setting(self):
        def row(service, state):
            plat = mock.Mock(**{"service.return_value": service, "start_at_login_state.return_value": state})
            with mock.patch.object(health, "in_own_home", return_value=False), \
                    mock.patch("jobhunt.core.platform.current", return_value=plat):
                return health.login_check()
        self.assertEqual(health.login_check().level, "ok", "a test home never asks the computer")
        self.assertEqual(row("hal-unit", None).text, "Managed by your Hal setup.")
        self.assertEqual(row("app-unit", True).text, "Hal starts when the computer starts.")
        missing = row("none", False)
        self.assertEqual((missing.level, missing.fix.route), ("warn", "settings-schedule"))
        settings.set("schedule.start_at_login", False)
        self.assertEqual(row("none", False).text, "Hal starts only when you open it.")

    def test_a_broken_check_is_a_finding_and_the_count_is_kept_a_minute(self):
        broken = health.Check("made-up", "Made-up check", lambda: 1 / 0)
        with mock.patch.object(health, "CHECKS", [*health.CHECKS, broken]), \
                mock.patch.object(health, "_cache", {"at": 0.0, "count": 0}), \
                self.assertLogs("jobhunt.core.health", "ERROR"):
            row = next(r for r in health.run_all() if r["id"] == "made-up")
            self.assertEqual(row["level"], "bad")
            first = health.problem_count()
            self.assertGreaterEqual(first, 1)
            with mock.patch.object(health, "run_all", side_effect=AssertionError("not asked again")):
                self.assertEqual(health.problem_count(), first)


class Concurrent(unittest.TestCase):
    def test_tick_and_pause_from_two_threads_do_not_clash(self):
        with fresh_home(), mock.patch.object(scheduler, "_launch", Launches()), \
                mock.patch("jobhunt.core.platform.current", return_value=mock.Mock(legacy_timers_enabled=list)):
            scheduler.start(now=MORNING, thread=False)
            t = threading.Thread(target=scheduler.set_paused, args=(True,))
            t.start()
            scheduler.tick(MORNING + timedelta(minutes=5))
            t.join(WAIT_S)
            self.assertTrue(scheduler.paused())


if __name__ == "__main__":
    unittest.main()
