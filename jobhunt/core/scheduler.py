"""The in-app schedule: searches, email checks, the daily summary, record reads, backups.

Jobs come from modules named `schedule.py` or `*_schedule.py` that define `JOBS`. A thread named
"schedule" wakes every `TICK_S`, and `tick()` starts each job that is due as a persisted action of the
job's kind, so every run has an Activity row, steps and Stop. A command-line job runs as a hidden child
(`python -X utf8 -m jobhunt <args>`) with a one-hour limit; each line it prints becomes a step, and Stop
ends its whole process tree.

Design decisions, each with its reason:

- The next run of each job is stored in `schedule_state`, so a daily slot missed while the computer was off
  runs once at start (with `schedule.catch_up`), however many slots were missed (U schedule.py:62-72).
- New times apply without a restart: each tick compares the settings the plan depends on with the ones it
  was made from, and plans again when they differ. That also sees a change made by another process
  (`jobhunt settings set`), which an in-process `on_change` subscription would not.
- No double running: at start, a job whose old `jobhunt-*` systemd timer is still enabled
  never runs in-process; the timer keeps running it, as before the rebuild.
- A kind is registered here only when no area registered it, so a later package that adds its own
  `find.discover` or `mail.check` takes over without touching this module.
- Pause is a `meta` row (`schedule:paused`), so the status line and a second process read the same answer.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import db
from . import clock, events, paths, plugins, runner

log = logging.getLogger(__name__)

TICK_S = 30                  # how often the schedule looks for due jobs
HOUR_S = 3600
CHILD_TIMEOUT_S = HOUR_S     # one hour, then the child is stopped
LINE_WAIT_S = 1.0            # how long the reader waits for a line before checking for Stop
STEP_CHARS = 300             # one printed line as a step; the rest is cut
PAUSED_META = "schedule:paused"
HAL_OFF = Path("~/.local/state/hal/off")    # `hal off` writes this file (the owner's Hal setup)
PAUSED_AREAS = ("find.", "notify.")         # pause stops searching and the summary, not email
STARTED_WHY = "Scheduled at {time}"
RUN_NOW_WHY = "You pressed Run now"
TIMERS_WORDS = "Your own timer runs this"
PAUSED_WORDS = "Paused"
OFF_WORDS = "Hal is switched off"
STILL_RUNNING = "The last run is still going"
CHILD_EXAMPLE = ("--help",)  # what gate G9 runs: a quick child that prints and ends well
# Settings the plan depends on; a change to any of them plans again.
WATCHED = ("schedule.search_times", "schedule.mail_every", "schedule.mail_hours", "schedule.record_every",
           "schedule.catch_up", "goal.mode", "notify.daily_summary", "updates.auto_check")


@dataclass(frozen=True)
class Job:
    """One scheduled job."""

    id: str                                    # "find.discover", also its action kind
    area: str                                  # Activity area
    label: str                                 # "Search for jobs"
    next_after: Callable[[datetime], datetime | None]   # the first run after this time, from live settings
    run: Callable[[], Any] | tuple[str, ...]   # in-process callable, or CLI args run as a hidden child
    catch_up: bool = True
    while_paused: bool = False
    legacy_timer: str | None = None            # "jobhunt-discover.timer": skipped while it is still enabled
    why: str = STARTED_WHY                     # the Activity why of a scheduled run


@dataclass
class _State:
    started: bool = False
    timers: frozenset[str] = frozenset()
    fingerprint: tuple | None = None


_STATE = _State()
_lock = threading.RLock()
_stop = threading.Event()
_thread: threading.Thread | None = None


# ---------------------------------------------------------------------------------------------- jobs

def jobs() -> dict[str, Job]:
    """Every scheduled job, by id, from the `schedule` modules."""
    out: dict[str, Job] = {}
    for module in plugins.modules("schedule"):
        for job in getattr(module, "JOBS", ()):
            if job.id in out:
                raise ValueError(f"scheduled job {job.id!r} is defined twice")
            out[job.id] = job
    return out


def _settings_get(key: str) -> Any:
    from . import settings

    return settings.get(key)


def _fingerprint() -> tuple:
    return tuple(repr(_settings_get(key)) for key in WATCHED)


# ---------------------------------------------------------------------------------------------- pause and off

def paused(con=None) -> bool:
    """True while the person has paused Hal's searches and summaries."""
    own = con is None
    con = con or db.connect()
    try:
        row = con.execute("SELECT v FROM meta WHERE k = ?", (PAUSED_META,)).fetchone()
        return bool(row and row[0] == "1")
    finally:
        if own:
            con.close()


def set_paused(on: bool, *, why: str = "You pressed Pause") -> bool:
    """Pauses or carries on; records it in Activity and tells every window."""
    from . import activity, status

    text = ("Paused searches and the daily summary. Email checks carry on." if on
            else "Carried on with searches and the daily summary.")
    con = db.connect()
    try:
        con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (PAUSED_META, "1" if on else "0"))
        con.commit()
        activity.record("system", "schedule.pause", text, why if on else "You pressed Carry on",
                        icon="pause" if on else "play")
        events.publish("status", **status.status(con))
    finally:
        con.close()
    return on


def hal_switched_off() -> bool:
    """True when the owner's Hal is switched off and the Hal add-on says to respect that."""
    if not (_settings_get("addon.enabled") and _settings_get("addon.respect_off")):
        return False
    return HAL_OFF.expanduser().exists()


def paused_for(job_id: str, con=None) -> bool:
    """True when pause covers this job (searches and the summary, never email)."""
    return job_id.startswith(PAUSED_AREAS) and paused(con)


# ---------------------------------------------------------------------------------------------- state

def _rows(con) -> dict[str, Any]:
    return {r["job"]: r for r in con.execute("SELECT * FROM schedule_state")}


def _save(con, job_id: str, **fields: Any) -> None:
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    con.execute(f"INSERT INTO schedule_state (job, {cols}) VALUES (?, {marks}) "
                f"ON CONFLICT(job) DO UPDATE SET {updates}", (job_id, *fields.values()))


def _iso(at: datetime | None) -> str | None:
    return clock.iso(at) if at else None


def _parse(text: str | None) -> datetime | None:
    return clock.parse(text) if text else None


def _catch_up_on() -> bool:
    return bool(_settings_get("schedule.catch_up"))


def plan(con, now: datetime, *, starting: bool = False) -> None:
    """Works out each job's next run. At start a missed run stays due once, when catching up is on."""
    rows = _rows(con)
    for job in jobs().values():
        row = rows.get(job.id)
        nxt = job.next_after(now)
        if starting and row is not None:
            last = _parse(row["last_run"])
            missed = job.next_after(last) if last is not None else _parse(row["next_run"])
            if missed is not None and missed <= now and job.catch_up and _catch_up_on():
                nxt = missed
        _save(con, job.id, next_run=_iso(nxt))
    con.commit()
    _STATE.fingerprint = _fingerprint()


def _running(con, action_id: str | None) -> bool:
    if not action_id:
        return False
    row = con.execute("SELECT state FROM actions WHERE id = ?", (action_id,)).fetchone()
    return row is not None and row["state"] in runner.ACTIVE


def _skip_reason(job: Job, con) -> str | None:
    if hal_switched_off():
        return OFF_WORDS
    if job.legacy_timer and job.legacy_timer in _STATE.timers:
        return TIMERS_WORDS
    if not job.while_paused and paused_for(job.id, con):
        return PAUSED_WORDS
    return None


def tick(now: datetime | None = None) -> list[str]:
    """Starts every job that is due; returns the ids of the actions it started."""
    now = now or clock.now_utc()
    started = []
    with _lock:
        runner.flush()
        con = db.connect()
        try:
            if _STATE.fingerprint != _fingerprint():
                plan(con, now)
            rows = _rows(con)
            for job in jobs().values():
                row = rows.get(job.id)
                due = _parse(row["next_run"]) if row is not None else None
                if due is None or due > now:
                    continue
                reason = _skip_reason(job, con)
                if reason is None and row is not None and _running(con, row["action_id"]):
                    reason = STILL_RUNNING
                if reason is not None:
                    _save(con, job.id, next_run=_iso(job.next_after(now)), state="skipped", detail=reason)
                    con.commit()
                    continue
                why = job.why.format(time=due.astimezone().strftime("%H:%M"), n=_settings_get("schedule.mail_every"))
                try:
                    action_id = _launch(job, why=why)
                except RuntimeError as e:   # one of this kind is already running
                    _save(con, job.id, next_run=_iso(job.next_after(now)), state="skipped", detail=str(e)[:200])
                    con.commit()
                    continue
                _save(con, job.id, last_run=clock.iso(now), next_run=_iso(job.next_after(now)), state="started",
                      detail=None, action_id=action_id)
                con.commit()
                started.append(action_id)
        finally:
            con.close()
    return started


def next_runs() -> list[dict]:
    """[{id, label, at}] of the jobs that will run, soonest first, for the status line."""
    con = db.connect()
    try:
        rows = _rows(con)
        stopped = paused(con)
    finally:
        con.close()
    out = []
    for job in jobs().values():
        row = rows.get(job.id)
        if row is None or not row["next_run"]:
            continue
        if job.legacy_timer and job.legacy_timer in _STATE.timers:
            continue
        if stopped and not job.while_paused and job.id.startswith(PAUSED_AREAS):
            continue
        out.append({"id": job.id, "label": job.label, "at": row["next_run"]})
    return sorted(out, key=lambda r: r["at"])


def overview() -> list[dict]:
    """Every job with its last and next run and whether a timer runs it instead, for "Is Hal working?"."""
    con = db.connect()
    try:
        rows = _rows(con)
    finally:
        con.close()
    out = []
    for job in jobs().values():
        row = rows.get(job.id)
        out.append({"id": job.id, "label": job.label, "next": row["next_run"] if row is not None else None,
                    "last": row["last_run"] if row is not None else None,
                    "state": row["state"] if row is not None else None,
                    "detail": row["detail"] if row is not None else None,
                    "timer": bool(job.legacy_timer and job.legacy_timer in _STATE.timers)})
    return out


def timers_in_charge() -> list[str]:
    """The old timers found enabled at start; their jobs do not run in-process."""
    return sorted(_STATE.timers)


# ---------------------------------------------------------------------------------------------- running

def child_argv(args: tuple[str, ...] | list[str]) -> list[str]:
    """The command line of a scheduled child: this Python, UTF-8 mode, `-m jobhunt` and the job's args."""
    return [sys.executable, "-X", "utf8", "-m", "jobhunt", *args]


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    home = paths.home_override()
    if home is not None:
        env["HAL_EMPLOYMENT_HOME"] = str(home)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_child(ctx: runner.Ctx, args: tuple[str, ...] | list[str], *, timeout: float = CHILD_TIMEOUT_S) -> int:
    """Runs `jobhunt <args>` hidden; each printed line is a step; Stop or the time limit end the whole tree."""
    from . import platform

    plat = platform.current()
    proc = plat.popen_hidden(child_argv(args), cwd=str(paths.code_dir()), env=_child_env(),
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, name="schedule-child", daemon=True).start()
    deadline = clock.now_utc().timestamp() + timeout
    try:
        while True:
            ctx.checkpoint()
            if clock.now_utc().timestamp() > deadline:
                minutes = max(1, round(timeout / 60))
                limit = "an hour" if timeout >= HOUR_S else f"{minutes} minute{'s' if minutes > 1 else ''}"
                raise TimeoutError(f"This took over {limit}, so Hal stopped it.")
            try:
                line = lines.get(timeout=LINE_WAIT_S)
            except queue.Empty:
                continue
            if line is None:
                break
            if line.strip():
                ctx.step(line.strip()[:STEP_CHARS])
        code = proc.wait(timeout=LINE_WAIT_S * 5)
    finally:
        plat.kill_tree(proc)
        if proc.stdout is not None:
            proc.stdout.close()
    if code != 0:
        raise RuntimeError(f"It ended with an error (code {code}). The details are in Hal's log.")
    return code


def _body(job_id: str, fallback: Job) -> Callable[..., Any]:
    def body(ctx: runner.Ctx, args: list[str] | None = None) -> Any:
        job = jobs().get(job_id, fallback)      # the job as it is defined now, not when the kind was made
        ctx.step(f"Starting: {job.label}")
        if args is not None or isinstance(job.run, tuple):
            return {"code": run_child(ctx, args if args is not None else job.run)}
        return job.run()
    return body


def _kind_for(job: Job) -> str:
    """The job's action kind; registered here only when its area has not registered one."""
    with _lock:
        if job.id not in runner.KINDS:
            runner.load_kinds()
        if job.id not in runner.KINDS:
            example = (lambda w: {"args": list(CHILD_EXAMPLE)}) if isinstance(job.run, tuple) else (lambda w: {})
            runner.action(job.id, area=job.area, label=job.label, why=STARTED_WHY.format(time="the set time"),
                          priority=2, exclusive="kind", example=example)(_body(job.id, job))
    return job.id


def _launch(job: Job, *, why: str) -> str:
    kind = _kind_for(job)
    return runner.start(kind, why=why, priority=2)


def run_now(job_id: str) -> str:
    """Starts a job now, whatever its schedule or pause, and returns the action id."""
    found = jobs().get(job_id)
    if found is None:
        raise LookupError(f"Hal has no scheduled job called {job_id}.")
    action_id = _launch(found, why=RUN_NOW_WHY)
    con = db.connect()
    try:
        _save(con, found.id, last_run=clock.iso(), state="started", detail=None, action_id=action_id)
        con.commit()
    finally:
        con.close()
    return action_id


# ---------------------------------------------------------------------------------------------- the thread

def check_timers() -> frozenset[str]:
    """Reads which old timers are enabled (Linux; 5 s each) and remembers them until the next start."""
    from . import platform

    try:
        _STATE.timers = frozenset(platform.current().legacy_timers_enabled())
    except OSError as e:
        log.warning("could not read the old timers: %s", e)
        _STATE.timers = frozenset()
    return _STATE.timers


def _loop() -> None:
    while not _stop.wait(TICK_S):
        try:
            tick()
        except Exception:  # a failing job or source must never stop the schedule
            log.exception("the schedule's tick failed")


def start(*, now: datetime | None = None, thread: bool = True) -> None:
    """Checks the old timers, plans (running missed slots once) and starts the "schedule" thread."""
    global _thread
    with _lock:
        check_timers()
        con = db.connect()
        try:
            plan(con, now or clock.now_utc(), starting=True)
        finally:
            con.close()
        _STATE.started = True
        _stop.clear()
        if thread and (_thread is None or not _thread.is_alive()):
            _thread = threading.Thread(target=_loop, name="schedule", daemon=True)
            _thread.start()
    tick(now)


def stop() -> None:
    """Stops the schedule thread (tests and shutdown); running jobs carry on to their end."""
    _stop.set()
