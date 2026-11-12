"""The `learn.patterns` action and what starts it: feedback on a job, a finished search, a CV line kept.

The action is background work (priority 2) and needs no AI. A listener started with the server watches the
event bus and runs it `DELAY_S` after the last event that could change a pattern, so hiding five jobs in a row
makes one run, not five. Learning's own `suggestions` events carry `source="learn"` and are ignored, so a run
never wakes the listener again. The nightly run is `learn/schedule.py`, at 03:30.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import timedelta

from jobhunt.core import clock, events, plugins, runner
from jobhunt.learn import items, patterns

log = logging.getLogger(__name__)

KIND = "learn.patterns"
SUBJECT = "learn"
DELAY_S = 20.0              # quiet time after the last trigger before Hal looks (several clicks make one run)
POLL_S = 1.0                # how often the listener checks its deadline
BUSY_RETRY_S = 30.0         # a run already going: look again after this
SEARCH_KINDS = ("find.discover",)
WHY = {"search": "A search for jobs finished", "cv": "You left a CV suggestion as it is",
       "writing": "You changed something Hal wrote",
       "feedback": "You saved or hid a job", "nightly": "Nightly look at your choices"}


def _example(world) -> dict:
    return {"trigger": "feedback", "reason": '"Too far"', "n": 4}


@runner.action(KIND, area="learning", label="Looking for patterns in your choices",
               why="You chose {reason} for {n} jobs this week", priority=2, example=_example)
def look(ctx, trigger: str = "nightly", reason: str | None = None, n: int | None = None) -> dict:
    """Runs every pattern; suggestions wait for the person, nothing else changes."""
    ctx.step("Looking at the jobs you saved and hid, the CV lines you kept and your edits")
    found = patterns.run(ctx.con)
    if found["new"]:
        ctx.changed(f"{len(found['new'])} new suggestion{'s' if len(found['new']) != 1 else ''} waiting for you "
                    "in What Hal remembers. Nothing changes until you say yes.")
    ctx.step(f"{found['waiting']} suggestion{'s' if found['waiting'] != 1 else ''} waiting for you")
    return found


def why_for(con, trigger: str) -> tuple[str, dict]:
    """The Activity why and template values for a run; feedback names the reason chosen most this week."""
    if trigger == "feedback":
        since = clock.iso(clock.now_utc() - timedelta(days=patterns.WINDOW_DAYS))
        row = con.execute("""SELECT reason, count(DISTINCT job_id) AS n FROM job_feedback
                             WHERE verdict = 'no' AND reason IS NOT NULL AND reason != 'other' AND at >= ?
                             GROUP BY reason ORDER BY n DESC, reason LIMIT 1""", (since,)).fetchone()
        if row is not None and row["n"] > 1:
            from jobhunt.find import feedback

            reason = f'"{feedback.reason_words(row["reason"], None)}"'
            return f"You chose {reason} for {row['n']} jobs this week", {"reason": reason, "n": row["n"]}
    return WHY.get(trigger, WHY["nightly"]), {}


def start(trigger: str) -> str | None:
    """Starts a run in the background; None when one is already going."""
    from jobhunt import db

    con = db.connect()
    try:
        why, values = why_for(con, trigger)
    finally:
        con.close()
    try:
        return runner.start(KIND, subject=SUBJECT, params={"trigger": trigger, **values}, why=why)
    except RuntimeError:
        return None


def trigger_of(ev: events.Event) -> str | None:
    """Which kind of run an event calls for, or None."""
    if ev.type == "job":
        return "feedback"
    if ev.type == "writing":
        return "writing"
    if ev.type == "suggestions" and ev.data.get("source") != items.EVENT_SOURCE:
        return "cv"
    if ev.type == "action" and ev.data.get("kind") in SEARCH_KINDS and ev.data.get("state") == "done":
        return "search"
    return None


class Listener:
    """Collects triggers from the event bus and starts one run after `delay` quiet seconds."""

    def __init__(self, begin: Callable[[str], str | None] = start, *, delay: float = DELAY_S,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.begin, self.delay, self.now = begin, delay, now
        self.pending: str | None = None
        self.due = 0.0
        self.stop = threading.Event()

    def see(self, ev: events.Event | None) -> None:
        """Notes an event; a trigger pushes the run back to `delay` seconds from now."""
        trigger = trigger_of(ev) if ev is not None else None
        if trigger is not None:
            # a search outranks feedback for the why, as it may bring many new jobs at once
            self.pending = trigger if self.pending in (None, "feedback", "cv", "writing") else self.pending
            self.due = self.now() + self.delay

    def tick(self) -> str | None:
        """Starts the run when its time has come; returns the action id, if one started."""
        if self.pending is None or self.now() < self.due:
            return None
        trigger, self.pending = self.pending, None
        if not any(patterns.enabled(area) for area in set(patterns.SOURCES.values())):
            return None
        try:
            action_id = self.begin(trigger)
        except Exception:  # the listener must outlive one failed start; the log says why
            log.exception("could not start %s", KIND)
            action_id = None
        if action_id is None:
            self.pending, self.due = trigger, self.now() + BUSY_RETRY_S
        return action_id

    def loop(self) -> None:
        """Runs until `stop` is set."""
        sub = events.subscribe()
        try:
            while not self.stop.is_set():
                self.see(sub.get(timeout=POLL_S))
                self.tick()
        finally:
            sub.close()


_listener: Listener | None = None


@plugins.on_start
def start_listener() -> None:
    """Starts watching for choices to learn from when the server starts."""
    global _listener
    if _listener is not None:
        return
    _listener = Listener()
    threading.Thread(target=_listener.loop, name="learn-listener", daemon=True).start()
