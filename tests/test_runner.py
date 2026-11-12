"""The persisted action runner: steps, Stop, one action per subject, restarts, Carry on and the
job-keyed API that the form driver uses.
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import unittest.mock
from datetime import UTC, datetime
from pathlib import Path

import support  # noqa: F401 - puts the run in a temporary home before jobhunt is imported

from jobhunt import db
from jobhunt.core import paths, runner

WAIT_S = 10
SHORT_S = 0.2   # long enough to see that a held action keeps `idle` waiting
CHILD_TIMEOUT_S = 60
REPO = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def fresh_home():
    """A temporary home with a migrated database and two made-up jobs; yields an open connection."""
    with tempfile.TemporaryDirectory(prefix="hal-runner-") as tmp, paths.set_override(Path(tmp)):
        paths.ensure()
        con = db.connect()
        for n, (title, company) in enumerate((("Junior Archivist", "Riverside Records"),
                                              ("Research Assistant", "Northgate Institute")), start=1):
            db.upsert(con, {"id": f"demo:{n}", "source": "demo", "company": company, "title": title,
                            "location": "Leeds", "url": f"https://jobs.example.org/{n}", "description": "",
                            "salary": "", "posted_at": None, "board_size": None})
        con.commit()
        try:
            yield con
        finally:
            # every action's thread lets go of its connection and lock file first: Windows cannot remove them
            runner.idle(WAIT_S)
            con.close()


class Kinds:
    """Made-up kinds registered for one test and forgotten after it."""

    def __init__(self, case: unittest.TestCase) -> None:
        self.case = case

    def add(self, kind: str, fn, **meta) -> None:
        meta.setdefault("area", "find")
        meta.setdefault("label", "Checking {title} at {company}")
        meta.setdefault("why", "You asked Hal to check {title}")
        runner.action(kind, **meta)(fn)
        self.case.addCleanup(runner.forget, kind)


def gate(ctx, reached: threading.Event, release: threading.Event, steps: int = 3) -> str:
    """Takes one step, says it got there, waits to be released, then takes the rest."""
    ctx.step("reading the advert", total=steps)
    reached.set()
    release.wait(WAIT_S)
    for n in range(2, steps + 1):
        ctx.step(f"part {n}")
    return "all done"


class Runner(unittest.TestCase):
    def setUp(self):
        self.kinds = Kinds(self)
        self.con = self.enterContext(fresh_home())

    def gated(self, **start):
        """Starts a `test.gated` action and waits until it has taken its first step."""
        reached, release = threading.Event(), threading.Event()
        self.kinds.add("test.gated", lambda ctx: gate(ctx, reached, release))
        self.addCleanup(release.set)
        action_id = runner.start("test.gated", **start)
        self.assertTrue(reached.wait(WAIT_S))
        return action_id, reached, release

    def test_idle_waits_until_every_action_has_let_go_of_its_files(self):
        # regression: Windows could not remove a test's home while an action still held its lock file open
        action_id, _, release = self.gated()
        self.assertFalse(runner.idle(SHORT_S))
        self.assertTrue(runner._lock_path(action_id).exists())
        release.set()
        self.assertTrue(runner.idle(WAIT_S))
        self.assertFalse(runner._lock_path(action_id).exists())
        self.assertEqual(runner.get(action_id)["state"], "done")

    def test_an_action_writes_its_steps_its_result_and_one_activity_row_with_a_why(self):
        def check(ctx, n):
            for i in range(n):
                ctx.step(f"part {i + 1}", total=n)
            ctx.used("cv", "Your CV, version 2")
            ctx.changed("Nothing was sent.")
            return {"parts": n}
        self.kinds.add("test.parts", check)
        done = runner.wait(runner.start("test.parts", job_id="demo:1", params={"n": 2}))
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["result"], {"parts": 2})
        self.assertEqual([(s["n"], s["text"], s["state"]) for s in done["steps"]],
                         [(1, "Part 1", "done"), (2, "Part 2", "done")])
        row = self.con.execute("SELECT * FROM activity WHERE action_id = ?", (done["id"],)).fetchone()
        self.assertEqual(row["text"], "Checking Junior Archivist at Riverside Records")
        self.assertEqual(row["why"], "You asked Hal to check Junior Archivist")
        self.assertEqual(row["running"], 0)
        self.assertEqual(json.loads(row["used"]), [{"kind": "cv", "label": "Your CV, version 2", "ref": ""}])
        self.assertEqual(row["changed"], "Nothing was sent.")

    def test_stop_keeps_the_work_already_done(self):
        def save_then_wait(ctx, reached, release):
            ctx.step("saving a note")
            ctx.con.execute("INSERT INTO events (job_id, at, kind, detail) VALUES ('demo:1', ?, 'note', 'kept')",
                            (db.now(),))
            ctx.con.commit()
            reached.set()
            release.wait(WAIT_S)
            ctx.step("this step never starts")
            return "finished"
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.kinds.add("test.save", lambda ctx: save_then_wait(ctx, reached, release))
        action_id = runner.start("test.save", job_id="demo:1")
        self.assertTrue(reached.wait(WAIT_S))
        self.assertTrue(runner.stop(action_id))
        release.set()
        done = runner.wait(action_id)
        self.assertEqual(done["state"], "stopped")
        self.assertEqual([s["text"] for s in done["steps"]], ["Saving a note"])
        self.assertEqual(self.con.execute("SELECT count(*) FROM events WHERE detail = 'kept'").fetchone()[0], 1)
        row = self.con.execute("SELECT tone, running, changed FROM activity WHERE action_id = ?",
                               (action_id,)).fetchone()
        self.assertEqual((row["tone"], row["running"]), ("warn", 0))
        self.assertIn(runner.STOPPED_WORDS, row["changed"])
        self.assertFalse(runner.stop(action_id))

    def test_one_action_per_subject_unless_replaced(self):
        first, _, release = self.gated(job_id="demo:1")
        with self.assertRaisesRegex(RuntimeError, "still busy"):
            runner.start("test.gated", job_id="demo:1")
        # Another job is another subject, so it starts beside the first.
        self.kinds.add("test.quick", lambda ctx: ctx.step("one look") or "ok")
        self.assertEqual(runner.wait(runner.start("test.quick", job_id="demo:2"))["state"], "done")
        second = runner.start("test.quick", job_id="demo:1", replace=True)
        self.assertEqual(runner.wait(second)["state"], "done")
        release.set()
        self.assertEqual(runner.wait(first)["state"], "stopped")
        self.assertIsNone(runner.running_for("demo:1"))

    def test_an_action_that_just_ended_does_not_hold_its_subject(self):
        # two quick CV type changes; the first cv.suggest had ended but its last row write was still
        # queued, so the next start said "Hal is busy ... in another window"
        held, holding = [], threading.Event()
        put = runner._WRITER.put

        def hold(connect, op):
            (held.append((connect, op)) if holding.is_set() else put(connect, op))

        def quick(ctx):
            ctx.step("one look")
            runner.flush()
            holding.set()          # from here the ending's row write waits, as on a busy machine
            return "ok"
        self.kinds.add("test.quick", quick)
        self.addCleanup(runner.flush)
        with unittest.mock.patch.object(runner._WRITER, "put", hold):
            first = runner.start("test.quick", subject="cv")
            for _ in range(int(WAIT_S / 0.05)):
                if first not in runner._LIVE and holding.is_set():
                    break
                threading.Event().wait(0.05)
            second = runner.start("test.quick", subject="cv", replace=True)
            holding.clear()
            for connect, op in held:
                put(connect, op)
        self.assertEqual(runner.wait(second)["state"], "done")
        self.assertEqual(runner.wait(first)["state"], "done")

    def test_the_running_action_is_found_by_its_subject(self):
        action_id, _, release = self.gated(job_id="demo:2")
        self.assertEqual(runner.running_for("demo:2")["id"], action_id)
        release.set()
        runner.wait(action_id)
        self.assertIsNone(runner.running_for("demo:2"))

    def test_a_failure_is_kept_with_its_words_and_can_carry_on(self):
        tries = []

        def flaky(ctx):
            ctx.step("trying")
            tries.append(ctx.resumed_from)
            if len(tries) == 1:
                raise RuntimeError("The site did not answer")
            return "worked"
        self.kinds.add("test.flaky", flaky)
        failed = runner.wait(runner.start("test.flaky"))
        self.assertEqual((failed["state"], failed["error"]), ("error", "The site did not answer"))
        self.assertTrue(failed["can_carry_on"])
        again = runner.wait(runner.carry_on(failed["id"]))
        self.assertEqual(again["state"], "done")
        self.assertEqual(tries, [None, failed["id"]])

    def test_the_priority_is_known_inside_the_action(self):
        seen = []
        self.kinds.add("test.priority", lambda ctx: seen.append((runner.current_priority(), ctx.priority)) or "ok",
                       priority=2)
        runner.wait(runner.start("test.priority"))
        runner.wait(runner.start("test.priority", priority=0))
        self.assertEqual(seen, [(2, 2), (0, 0)])
        self.assertEqual(runner.current_priority(), 0)

    def test_what_the_person_just_asked_for_never_waits_behind_queued_jobs(self):
        """Regression (testers, 2026-11-04): "Fill it in again" waited behind a hundred queued jobs' documents."""
        release, done = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.kinds.add("test.queued_job", lambda ctx: release.wait(WAIT_S) and "done")
        self.kinds.add("test.pressed", lambda ctx: done.set() or "done", priority=0)
        for n in range(runner.USER_THREADS + 2):
            runner.start("test.queued_job", subject=f"job-{n}")
        runner.start("test.pressed", subject="pressed")
        self.assertTrue(done.wait(WAIT_S), "the pressed action waited for the queue")
        release.set()

    def test_ai_calls_inside_an_action_add_up_on_its_row(self):
        def ask_twice(ctx):
            runner.note_ai(provider="ollama", model="small-model", role="quick", ms=120)
            runner.note_ai(provider="ollama", model="small-model", role="quick", ms=80)
            ctx.step("asked")
        self.kinds.add("test.ai", ask_twice)
        done = runner.wait(runner.start("test.ai"))
        ai = json.loads(self.con.execute("SELECT ai FROM activity WHERE action_id = ?", (done["id"],)).fetchone()[0])
        self.assertEqual((ai["calls"], ai["ms"], ai["model"]), (2, 200, "small-model"))

    def test_an_action_reports_items_done_beside_its_steps_with_the_time_left(self):
        seen, release = threading.Event(), threading.Event()

        def read_files(ctx):
            ctx.step("Reading your files")
            ctx.progress(2, 8, "report.pdf")
            seen.set()
            release.wait(WAIT_S)
            ctx.progress(8, 8)
        self.kinds.add("test.items", read_files)
        aid = runner.start("test.items")
        self.assertTrue(seen.wait(WAIT_S))
        runner.flush()
        going = runner.get(aid)
        release.set()
        self.assertEqual((going["done_n"], going["done_total"], going["message"]), (2, 8, "Report.pdf"))
        self.assertEqual(len(going["steps"]), 1, "an item is not a step")
        row = {**going, "started": "2026-11-05T10:00:00Z"}
        self.assertEqual(runner.eta_s(row, now=datetime(2026, 11, 5, 10, 1, tzinfo=UTC)), 180,
                         "two items took a minute, so six more take three")
        done = runner.wait(aid)
        self.assertEqual((done["done_n"], done["done_total"], done["eta_s"]), (8, 8, None))

    def test_waiting_for_you_is_shown_until_the_next_step(self):
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def ask(ctx):
            ctx.step("filling the form")
            ctx.waiting_for_you("Sign in to Northgate's site", route="app-7")
            reached.set()
            release.wait(WAIT_S)
            ctx.step("carrying on")
        self.kinds.add("test.ask", ask)
        action_id = runner.start("test.ask", job_id="demo:2")
        self.assertTrue(reached.wait(WAIT_S))
        runner.flush()
        waiting = runner.get(action_id)
        self.assertEqual(waiting["state"], "waiting_for_you")
        self.assertEqual(waiting["waiting"], {"text": "Sign in to Northgate's site", "route": "app-7"})
        release.set()
        done = runner.wait(action_id)
        self.assertEqual((done["state"], done["waiting"]), ("done", None))

    def test_a_kind_needs_an_area_a_label_and_a_why(self):
        with self.assertRaisesRegex(ValueError, "needs a why"):
            runner.action("test.nowhy", area="find", label="Looking", why=" ")
        with self.assertRaisesRegex(ValueError, "area"):
            runner.action("test.badarea", area="kitchen", label="Looking", why="Because")
        with self.assertRaises(LookupError):
            runner.start("test.never_registered")

    def test_stop_from_another_process_is_seen_at_the_next_checkpoint(self):
        action_id, _, release = self.gated()
        runner.flush()
        # Another process can only change the row; this process notices at its next checkpoint.
        other = db.connect()
        other.execute("UPDATE actions SET state = 'stopped' WHERE id = ?", (action_id,))
        other.commit()
        other.close()
        with runner._lock:
            live = runner._LIVE[action_id]
        self.addCleanup(setattr, runner, "STOP_POLL_S", runner.STOP_POLL_S)
        runner.STOP_POLL_S = 0
        release.set()
        self.assertEqual(runner.wait(action_id)["state"], "stopped")
        self.assertTrue(live.stop.is_set())


CHILD = textwrap.dedent("""
    import os, sys, threading
    sys.path.insert(0, {tests!r})
    import support
    from jobhunt.core import runner

    @runner.action("test.long", area="find", label="Searching {{place}}", why="You pressed Search now",
                   resumable={resumable})
    def long(ctx, place):
        ctx.step("first site", total=3)
        ctx.step("second site")
        runner.flush()
        print("reached", flush=True)
        threading.Event().wait()

    threading.Thread(target=runner.run, args=("test.long",), kwargs={{"params": {{"place": "Leeds"}},
                     "subject": "_search"}}, daemon=True).start()
    sys.stdin.readline()
    os._exit(0)
""")


class Restart(unittest.TestCase):
    """A process that ends part way through, as a crash or a restart does."""

    def setUp(self):
        self.con = self.enterContext(fresh_home())
        self.home = paths.home_override()

    def crash_part_way(self, resumable: bool = False) -> str:
        env = {**os.environ, "HAL_EMPLOYMENT_HOME": str(self.home), "HAL_TEST_MODE": "1"}
        code = CHILD.format(tests=str(REPO / "tests"), resumable=resumable)
        child = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, env=env, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "reached")
            # The action is still running in the other process, so a start here leaves it alone.
            self.assertEqual(runner.recover(), 0)
            self.assertEqual(runner.running_for("_search")["kind"], "test.long")
            child.stdin.write("\n")
            child.stdin.flush()
            child.wait(CHILD_TIMEOUT_S)
        finally:
            child.kill()
            child.stdout.close()
            child.stdin.close()
        return self.con.execute("SELECT id FROM actions WHERE kind = 'test.long'").fetchone()[0]

    def test_an_action_and_its_steps_survive_a_restart(self):
        action_id = self.crash_part_way()
        self.assertEqual(runner.recover(), 1)
        after = runner.get(action_id)
        self.assertEqual(after["state"], "interrupted")
        self.assertEqual([(s["text"], s["state"]) for s in after["steps"]],
                         [("First site", "done"), ("Second site", "skipped")])
        row = self.con.execute("SELECT text, why, running, changed FROM activity WHERE action_id = ?",
                               (action_id,)).fetchone()
        self.assertEqual((row["text"], row["why"], row["running"]), ("Searching Leeds", "You pressed Search now", 0))
        self.assertIn(runner.INTERRUPTED_WORDS, row["changed"])
        self.assertEqual(runner.recover(), 0)

    def test_an_interrupted_action_can_carry_on(self):
        action_id = self.crash_part_way()
        runner.recover()
        places = []

        @runner.action("test.long", area="find", label="Searching {place}", why="You pressed Search now")
        def long(ctx, place):
            places.append((place, ctx.resumed_from))
            ctx.step("third site")
            return "searched"
        self.addCleanup(runner.forget, "test.long")
        self.assertTrue(runner.get(action_id)["can_carry_on"])
        again = runner.wait(runner.carry_on(action_id))
        self.assertEqual(again["state"], "done")
        self.assertEqual(places, [("Leeds", action_id)])
        why = self.con.execute("SELECT why FROM activity WHERE action_id = ?", (again["id"],)).fetchone()[0]
        self.assertEqual(why, "You pressed Search now")
        with self.assertRaisesRegex(RuntimeError, "stopped before it finished"):
            runner.carry_on(again["id"])

    def test_a_resumable_action_carries_on_by_itself_after_a_restart(self):
        action_id = self.crash_part_way(resumable=True)
        resumed = threading.Event()

        @runner.action("test.long", area="find", label="Searching {place}", why="You pressed Search now",
                       resumable=True)
        def long(ctx, place):
            ctx.step("watching again")
            resumed.set()
        self.addCleanup(runner.forget, "test.long")
        self.assertEqual(runner.recover(), 1)
        self.assertTrue(resumed.wait(WAIT_S))
        runner.flush()
        rows = self.con.execute("SELECT id, state FROM actions WHERE kind = 'test.long' ORDER BY created").fetchall()
        self.assertEqual(rows[0]["id"], action_id)
        self.assertEqual(rows[0]["state"], "interrupted")
        self.assertEqual(len(rows), 2)


class ByJob(unittest.TestCase):
    """The form driver's job-keyed API on top of the runner."""

    def setUp(self):
        self.con = self.enterContext(fresh_home())
        self.addCleanup(runner.ACTIONS.clear)

    def test_the_actions_mirror_shows_each_jobs_action_and_its_steps(self):
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def fill(con, job_id):
            runner._say(job_id, "reading the page")
            reached.set()
            release.wait(WAIT_S)
            runner._say(job_id, "filling in the form")
            return "form filled"
        entry = runner.run_action("demo:1", "applying", fill, "demo:1")
        self.assertTrue(reached.wait(WAIT_S))
        self.assertIs(runner.ACTIONS["demo:1"], entry)
        self.assertEqual(set(entry), {"name", "state", "message", "at"})
        self.assertEqual((entry["name"], entry["state"], entry["message"]), ("applying", "running", "reading the page"))
        with self.assertRaisesRegex(RuntimeError, "still busy with 'applying'"):
            runner.run_action("demo:1", "applying", fill, "demo:1")
        release.set()
        found = None
        for _ in range(100):
            runner.flush()
            found = self.con.execute("SELECT * FROM actions WHERE subject = 'demo:1'").fetchone()
            if found["state"] == "done":
                break
            threading.Event().wait(0.05)
        self.assertEqual((found["state"], found["kind"], found["message"]), ("done", "form.applying", "form filled"))
        self.assertEqual(entry["state"], "done")
        steps = [s["text"] for s in runner.get(found["id"])["steps"]]
        self.assertEqual(steps, ["Reading the page", "Filling in the form"])
        text = self.con.execute("SELECT text FROM activity WHERE action_id = ?", (found["id"],)).fetchone()[0]
        self.assertEqual(text, "Applying for Junior Archivist at Riverside Records")

    def test_a_new_action_with_a_subject_shows_in_the_mirror_and_stops_from_it(self):
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        runner.action("test.job_view", area="applying", label="Filling in the form for {title}",
                      why="You approved the documents")(lambda ctx: gate(ctx, reached, release))
        self.addCleanup(runner.forget, "test.job_view")
        action_id = runner.start("test.job_view", job_id="demo:2")
        self.assertTrue(reached.wait(WAIT_S))
        entry = runner.ACTIONS["demo:2"]
        self.assertEqual((entry["name"], entry["state"]), ("Filling in the form for Research Assistant", "running"))
        self.assertTrue(runner.stop_action("demo:2"))
        self.assertNotIn("demo:2", runner.ACTIONS)
        release.set()
        self.assertEqual(runner.wait(action_id)["state"], "stopped")

    def test_a_watched_form_page_is_recorded_as_an_action(self):
        runner.ACTIONS["demo:1"] = {"name": "filling the next page", "state": "running", "message": "", "at": db.now()}
        runner._carry_out(self.con, "demo:1", lambda con, job_id: runner._say(job_id, "answering 2 questions") or "ok",
                          "demo:1")
        runner.flush()
        row = self.con.execute("SELECT * FROM actions WHERE subject = 'demo:1'").fetchone()
        self.assertEqual((row["state"], row["step_n"]), ("done", 1))
        why = self.con.execute("SELECT why FROM activity WHERE action_id = ?", (row["id"],)).fetchone()[0]
        self.assertEqual(why, runner.WATCH_WHY)
        self.assertEqual(runner.ACTIONS["demo:1"]["state"], "done")


if __name__ == "__main__":
    unittest.main()
