"""Persisted actions: anything slow Hal does runs here, with its steps, its Activity row and Stop.

An action kind is registered with `@action(kind, area=, label=, why=)`; `start(kind, ...)` writes an
`actions` row and its Activity row (`running = 1`) and runs `fn(ctx, **params)` on one of two pools: the
user pool for priorities 0 and 1, the background pool for 2. `run(kind, ...)` does the same in the calling
thread, for the command line and scheduled children. Steps go to `action_steps`; the server's `DbPoller`
turns those rows into `step` events, so the runner itself publishes only `action` and `status`.

Design decisions, each with its reason:

- Every bookkeeping write goes through one writer thread, in order, on its own connection. The caller of
  `start`, `stop` or `step` may hold an open write transaction on its own connection (the classic handlers
  do), and a second connection in the same thread would wait the whole busy timeout on that lock. The
  writer never commits the action's own work either: that stays the action's to commit. Reads in this
  process call `flush()` first, so they see their own writes.
- Each queued or running action holds a lock file `state/actions/<id>.lock` until its last row is written.
  `recover()` and the one-per-subject check treat a row whose lock is free as left behind by a process
  that ended, so a command-line run beside the server is never marked interrupted.
- A stop is seen in two ways: in this process at once, and from another process through the row's state,
  read at a checkpoint at most every `STOP_POLL_S`.
- The classic API (`run_action`, `stop_action`, `_checkpoint`, `_say`, `_note`, `ACTIONS`) stays for the
  driver and Classic. `ACTIONS` mirrors every action with a subject in today's shape {name, state, message,
  at}; a classic action is persisted too (kind `classic.<name>`), and `_say` adds a step to it.
"""
from __future__ import annotations

import atexit
import contextvars
import json
import logging
import os
import queue
import sqlite3
import string
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from . import clock, events, paths, plugins

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running", "waiting_for_you")
FINAL = ("done", "error", "stopped", "interrupted")
CARRY_ON_FROM = ("interrupted", "stopped", "error")
USER_THREADS = 4             # priorities 0 and 1
BACKGROUND_THREADS = 2       # priority 2
CLASSIC_THREADS = 6          # as before the rebuild: classic actions mostly wait on the browser or the model
STOP_POLL_S = 1.0            # how often a checkpoint asks the database whether another process said stop
FLUSH_S = 2.0                # how long a read waits for this process's own writes to land
EXIT_FLUSH_S = 10.0          # what a command-line run waits at exit for its last rows
WRITE_TRIES = 3              # a write still failing after the busy timeout this often is logged and dropped
LIST_LIMIT = 200
LOCK_DIR = "actions"
MESSAGE_CHARS = 500          # the classic queue shows one line; the whole error is kept in `error`
AREAS = ("find", "documents", "applying", "email", "learning", "settings", "cv", "system")
EXCLUSIVE = ("subject", "kind", "none")
STOPPED_WORDS = "You stopped Hal. The work so far is kept."
INTERRUPTED_WORDS = "Hal closed before this finished. The work so far is kept."
CLASSIC_WHY = "You started this from the classic screens."
WATCH_WHY = "Hal is watching the form while you fill it in."
CLASSIC_AREAS = {"_mail": "email", "_cv": "cv", "_cvbuild": "cv", "_cvreview": "cv"}


@dataclass(frozen=True)
class ActionKind:
    """One kind of action and its defaults."""

    kind: str                     # "apply.fill"
    area: str                     # activity area
    label: str                    # "Filling in the form for {title} at {company}"
    why: str                      # default reason: "You approved the documents for {title}."
    priority: int = 1             # 0 you just asked, 1 a job you chose, 2 background
    resumable: bool = False       # restarted by itself after a server restart (form watch)
    exclusive: str = "subject"    # "subject": one running per subject unless replace=True; "kind"; "none"
    example: Callable[[Any], dict] | None = None   # params for the synthetic world (gate G9)
    icon: str = "hal"


KINDS: dict[str, ActionKind] = {}
_FNS: dict[str, Callable[..., Any]] = {}
ACTIONS: dict[str, dict] = {}
# Classic actions and Classic's own background jobs (closing a tab quietly) run here, as before.
POOL = ThreadPoolExecutor(max_workers=CLASSIC_THREADS, thread_name_prefix="action")
_USER = ThreadPoolExecutor(max_workers=USER_THREADS, thread_name_prefix="action-user")
_BACKGROUND = ThreadPoolExecutor(max_workers=BACKGROUND_THREADS, thread_name_prefix="action-bg")
_PRIORITY: contextvars.ContextVar[int] = contextvars.ContextVar("hal_action_priority", default=0)
_CURRENT: contextvars.ContextVar[Ctx | None] = contextvars.ContextVar("hal_action", default=None)
_lock = threading.RLock()


class Stopped(BaseException):
    """The action was stopped, replaced, or its application discarded or marked applied while it ran.

    A BaseException, so the many `except Exception` blocks on the way (a failed
    search, a tab that moved on) let it through to the runner.
    """


# The classic entry and the Ctx, by subject, that this thread is carrying out. An action whose entry in
# ACTIONS has been replaced or removed is stopped at its next checkpoint.
_RUN = threading.local()


def action(kind: str, **meta: Any) -> Callable:
    """Decorator: registers `fn(ctx, **params)` as action kind `kind`; its return value is stored as the result."""
    for need in ("area", "label", "why"):
        if not str(meta.get(need) or "").strip():
            raise ValueError(f"action kind {kind!r} needs a {need}")
    if meta["area"] not in AREAS:
        raise ValueError(f"action kind {kind!r}: area {meta['area']!r} is not one of {', '.join(AREAS)}")
    if meta.get("exclusive", "subject") not in EXCLUSIVE:
        raise ValueError(f"action kind {kind!r}: exclusive must be one of {', '.join(EXCLUSIVE)}")

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        with _lock:
            if kind in KINDS and _FNS.get(kind) is not fn:
                raise ValueError(f"action kind {kind!r} is registered twice")
            KINDS[kind] = ActionKind(kind, **meta)
            _FNS[kind] = fn
        return fn
    return register


def forget(kind: str) -> None:
    """Removes a registered kind (tests that register made-up kinds)."""
    with _lock:
        KINDS.pop(kind, None)
        _FNS.pop(kind, None)


def load_kinds() -> dict[str, ActionKind]:
    """Imports every area's `actions` modules and the data tasks, so every kind is registered."""
    plugins.modules("actions")
    from .db import tasks

    tasks.load()
    return dict(KINDS)


def current() -> Ctx | None:
    """The action this code runs inside, or None."""
    return _CURRENT.get()


def current_priority() -> int:
    """The priority of the action this code runs inside (0 outside any action); the AI layer reads it."""
    return _PRIORITY.get()


# ---------------------------------------------------------------------------------------------- writer

Connect = Callable[[], sqlite3.Connection]
Op = Callable[[sqlite3.Connection], None]


class _Writer:
    """One thread that applies bookkeeping writes in order, each batch on a fresh connection."""

    def __init__(self) -> None:
        self.q: queue.Queue[tuple[Connect, Op]] = queue.Queue()
        self.idle = threading.Condition()
        self.pending = 0
        self.thread: threading.Thread | None = None

    def put(self, connect: Connect, op: Op) -> None:
        with self.idle:
            self.pending += 1
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="action-writer", daemon=True)
                self.thread.start()
        self.q.put((connect, op))

    def flush(self, timeout: float = FLUSH_S) -> bool:
        """Waits until every write queued so far has landed; False when `timeout` passed first."""
        if threading.current_thread() is self.thread:
            return True
        deadline = time.monotonic() + timeout
        with self.idle:
            while self.pending:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.idle.wait(left)
        return True

    def _loop(self) -> None:
        while True:
            batch = [self.q.get()]
            while True:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            i = 0
            while i < len(batch):
                connect, j = batch[i][0], i
                while j < len(batch) and batch[j][0] is connect:
                    j += 1
                self._apply(connect, [op for _, op in batch[i:j]])
                i = j
            with self.idle:
                self.pending -= len(batch)
                self.idle.notify_all()

    def _apply(self, connect: Connect, ops: list[Op]) -> None:
        try:
            con = connect()
        except Exception:  # logged; the writer must outlive one bad database
            log.exception("the action writer could not open the database; %d writes dropped", len(ops))
            return
        try:
            for op in ops:
                for attempt in range(WRITE_TRIES):
                    try:
                        op(con)
                        con.commit()
                        break
                    except sqlite3.OperationalError:
                        con.rollback()
                        if attempt == WRITE_TRIES - 1:
                            log.exception("an action write failed %d times and was dropped", WRITE_TRIES)
                    except Exception:  # logged; one bad write must not stop the others
                        con.rollback()
                        log.exception("an action write failed and was dropped")
                        break
        finally:
            con.close()


_WRITER = _Writer()


def flush(timeout: float = FLUSH_S) -> bool:
    """Waits until this process's action rows are written; True when they all landed."""
    return _WRITER.flush(timeout)


atexit.register(flush, EXIT_FLUSH_S)


# ---------------------------------------------------------------------------------------- owner locks

def _lock_path(action_id: str) -> Path:
    return paths.state_dir() / LOCK_DIR / f"{action_id}.lock"


def _take(path: Path) -> Any | None:
    """Opens and locks `path` without waiting; None when another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    return file


def _release(file: Any, path: Path) -> None:
    try:
        file.close()
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("could not remove %s", path)


def _owner_alive(action_id: str) -> bool:
    """True when some process still holds this action's lock, so it is really going."""
    with _lock:
        if action_id in _LIVE:
            return True
    path = _lock_path(action_id)
    if not path.exists():
        return False
    probe = _take(path)
    if probe is None:
        return True
    _release(probe, path)
    return False


# ------------------------------------------------------------------------------------------- live state

class _Live:
    """What this process knows about an action it runs."""

    def __init__(self, action_id: str, kind: ActionKind, subject: str | None, job_id: str | None, entry: dict,
                 connect: Connect) -> None:
        self.id, self.kind, self.subject, self.job_id, self.entry = action_id, kind, subject, job_id, entry
        self.connect = connect
        self.lock_file: Any = None
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.state = "queued"
        self.started = time.monotonic()
        self.priority, self.text, self.why = kind.priority, "", ""
        self.session_id: str | None = None
        self.helper_id: str | None = None
        self.remote = False
        self.closed = False


_LIVE: dict[str, _Live] = {}


class _Values(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(template: str, values: dict[str, Any]) -> str:
    """`template` with each `{word}` it has a value for filled in; unknown words are left as they are."""
    words = _Values({k: v for k, v in values.items() if isinstance(v, (str, int, float))})
    try:
        return string.Formatter().vformat(template, (), words)
    except (ValueError, IndexError):
        return template


def _job_words(connect: Connect, job_id: str | None) -> dict[str, str]:
    if not job_id:
        return {}
    try:
        con = connect()
        try:
            row = con.execute("SELECT title, company FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {"title": row["title"], "company": row["company"]} if row else {}


def _publish_action(live: _Live, state: str, message: str) -> None:
    events.publish("action", id=live.id, kind=live.kind.kind, state=state, subject=live.subject,
                   job_id=live.job_id, message=message)


def _publish_status(con: sqlite3.Connection) -> None:
    from . import status

    try:
        events.publish("status", **status.status(con))
    except Exception:  # logged; the status line catches up at its next reload
        log.exception("could not work out the status line")


def _guarded(text: str | None) -> str | None:
    from . import activity

    return activity.guard_text(text)


def _held_elsewhere(con: sqlite3.Connection, column: str, value: str, connect: Connect) -> dict | None:
    """An active row for `value` that another live process holds; rows left by an ended process are interrupted."""
    with _lock:
        mine = set(_LIVE)
    rows = con.execute(f"SELECT * FROM actions WHERE {column} = ? AND state IN ('queued','running','waiting_for_you')",
                       (value,)).fetchall()
    for r in rows:
        if r["id"] in mine:
            continue
        if _owner_alive(r["id"]):
            return dict(r)
        left = r["id"]
        _WRITER.put(connect, lambda c, left=left: _interrupt(c, left))
    return None


def _holder(kind: ActionKind, subject: str | None, connect: Connect) -> tuple[_Live | None, dict | None]:
    """The action already holding this subject (or kind): one in this process, or a row from another."""
    if kind.exclusive == "none" or (kind.exclusive == "subject" and not subject):
        return None, None
    by_subject = kind.exclusive == "subject"
    with _lock:
        for live in _LIVE.values():
            if live.state in ACTIVE and (live.subject == subject if by_subject else live.kind.kind == kind.kind):
                return live, None
    try:
        con = connect()
        try:
            return None, _held_elsewhere(con, "subject" if by_subject else "kind",
                                         subject if by_subject else kind.kind, connect)
        finally:
            con.close()
    except sqlite3.Error:
        log.exception("could not check what else is running for %s", subject or kind.kind)
        return None, None


def _label(kind: ActionKind, name: str | None, values: dict[str, Any]) -> str:
    if name is None:
        return fill(kind.label, values)
    text = name[:1].upper() + name[1:]
    if values.get("title"):
        text += f" for {values['title']}" + (f" at {values['company']}" if values.get("company") else "")
    return text


def _begin(kind: ActionKind, *, subject: str | None, job_id: str | None, params: dict, why: str | None,
           priority: int | None, replace: bool, session_id: str | None, helper_id: str | None,
           remote: bool = False, name: str | None = None) -> _Live:
    """Checks the subject, queues the action's row and its Activity row, and returns the live action."""
    connect = db.connect
    priority = kind.priority if priority is None else priority
    if priority not in (0, 1, 2):
        raise ValueError(f"priority must be 0, 1 or 2, not {priority!r}")
    try:
        params_json = json.dumps(params)
    except (TypeError, ValueError) as e:
        raise ValueError(f"the params of a {kind.kind} action must be plain JSON values: {e}") from e
    values = {**_job_words(connect, job_id), **params}
    with _lock:
        live, other = _holder(kind, subject, connect)
        where = "for this job" if job_id else "here"
        if other is not None:
            raise RuntimeError(f"Hal is busy with '{other['message'] or other['kind']}' {where} in another window. "
                               "Stop it there first.")
        if live is not None:
            if not replace:
                raise RuntimeError(f"Hal is still busy with '{live.entry['name']}' {where}")
            _stop_live(live)
        action_id = uuid.uuid4().hex
        text = _label(kind, name, values)
        entry = {"name": name if name is not None else text, "state": "running", "message": "", "at": db.now()}
        live = _Live(action_id, kind, subject, job_id, entry, connect)
        live.priority, live.text = priority, text
        live.why = (why or "").strip() or fill(kind.why, values)
        live.session_id, live.helper_id, live.remote = session_id, helper_id, remote
        live.lock_file = _take(_lock_path(action_id))
        _LIVE[action_id] = live
        if subject:
            ACTIONS[subject] = entry
    at, words, reason = clock.iso(), _guarded(text), _guarded(live.why)

    def insert(con: sqlite3.Connection) -> None:
        cur = con.execute(
            """INSERT INTO activity (at, area, kind, icon, tone, text, why, job_id, action_id, session_id,
                                     remote, running)
               VALUES (?, ?, ?, ?, 'hal', ?, ?, (SELECT id FROM jobs WHERE id = ?), ?, ?, ?, 1)""",
            (at, kind.area, kind.kind, kind.icon, words, reason, job_id, action_id, session_id, int(remote)))
        con.execute(
            """INSERT INTO actions (id, kind, subject, job_id, state, priority, params, message, resumable,
                                    session_id, helper_id, activity_id, created)
               VALUES (?, ?, ?, (SELECT id FROM jobs WHERE id = ?), 'queued', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (action_id, kind.kind, subject, job_id, priority, params_json, words, int(kind.resumable),
             session_id, helper_id, cur.lastrowid, at))

    _WRITER.put(connect, insert)
    _publish_action(live, "queued", text)
    return live


# ------------------------------------------------------------------------------------------------- Ctx

class Ctx:
    """What an action's function gets: its connection, its steps, and what it used and changed."""

    def __init__(self, live: _Live, con: sqlite3.Connection, resumed_from: str | None = None) -> None:
        self._live = live
        self.action_id, self.kind, self.job_id = live.id, live.kind.kind, live.job_id
        self.priority, self.con, self.remote = live.priority, con, live.remote
        self.session_id, self.helper_id = live.session_id, live.helper_id
        self.resumed_from = resumed_from
        self.step_n = 0
        self._total: int | None = None
        self._used: list[dict] = []
        self._changed: list[str] = []
        self._ai: dict[str, Any] = {}
        self._polled = time.monotonic()

    def _write(self, op: Op, *, status: bool = False) -> None:
        def run(con: sqlite3.Connection) -> None:
            op(con)
            if status:
                con.commit()
                _publish_status(con)
        _WRITER.put(self._live.connect, run)

    def checkpoint(self) -> None:
        """Raises `Stopped` when this action was stopped here or from another process."""
        live = self._live
        if live.stop.is_set() or (live.subject and ACTIONS.get(live.subject) is not live.entry):
            raise Stopped()
        if time.monotonic() - self._polled >= STOP_POLL_S:
            self._polled = time.monotonic()
            row = self.con.execute("SELECT state FROM actions WHERE id = ?", (live.id,)).fetchone()
            if row is not None and row["state"] == "stopped":
                live.stop.set()
                raise Stopped()

    def step(self, text: str, *, why: str | None = None, used: str | None = None, total: int | None = None) -> None:
        """Starts the next step ("step N of M" on the status line); the one before it is done."""
        self.checkpoint()
        self.step_n += 1
        if total is not None:
            self._total = total
        live = self._live
        n, at, aid, step_total = self.step_n, clock.iso(), live.id, self._total
        words = _guarded(text[:1].upper() + text[1:])
        live.entry["message"] = text
        live.state = "running"
        reason = _guarded(why)

        def op(con: sqlite3.Connection) -> None:
            con.execute("UPDATE action_steps SET state = 'done' WHERE action_id = ? AND state = 'running'", (aid,))
            con.execute("""INSERT INTO action_steps (action_id, n, at, text, why, used, state)
                           VALUES (?, ?, ?, ?, ?, ?, 'running')""", (aid, n, at, words, reason, used))
            con.execute("""UPDATE actions SET step_n = ?, step_total = ?, message = ?,
                               state = CASE WHEN state = 'waiting_for_you' THEN 'running' ELSE state END
                           WHERE id = ?""", (n, step_total, words, aid))
        self._write(op, status=True)

    def used(self, kind: str, label: str, ref: str = "") -> None:
        """Notes something Hal used to decide ("Your CV, version 5"), shown under Why."""
        self._used.append({"kind": kind, "label": _guarded(label), "ref": ref})
        raw, aid = json.dumps(self._used), self._live.id
        self._write(lambda con: con.execute("UPDATE activity SET used = ? WHERE action_id = ?", (raw, aid)))

    def changed(self, words: str) -> None:
        """Adds a sentence to what this action changed."""
        self._changed.append(_guarded(words.strip()) or "")
        text, aid = " ".join(self._changed), self._live.id
        self._write(lambda con: con.execute("UPDATE activity SET changed = ? WHERE action_id = ?", (text, aid)))

    def undo_with(self, spec: dict) -> None:
        """Offers Undo on this action's Activity row; `spec["type"]` names the undo handler."""
        if not spec.get("type"):
            raise ValueError("an undo needs a type")
        raw, aid = json.dumps(spec), self._live.id
        self._write(lambda con: con.execute("UPDATE activity SET undo = ? WHERE action_id = ?", (raw, aid)))

    def waiting_for_you(self, text: str, *, route: str | None = None) -> None:
        """Says the action waits for the person (Home "Needs you"); its next step carries on."""
        self.checkpoint()
        live, words = self._live, _guarded(text)
        live.state = "waiting_for_you"
        live.entry["message"] = text
        raw, aid = json.dumps({"waiting_for_you": {"text": words, "route": route}}), live.id
        self._write(lambda con: con.execute(
            "UPDATE actions SET state = 'waiting_for_you', message = ?, result = ? WHERE id = ?",
            (words, raw, aid)), status=True)
        _publish_action(live, "waiting_for_you", text)

    def note_ai(self, *, provider: str, model: str | None, role: str, ms: int) -> None:
        """Adds one AI call to this action's Activity row (the AI layer calls it)."""
        ai = self._ai
        if ai and (ai.get("provider") != provider or ai.get("model") != model):
            ai["mixed"] = True
        ai.setdefault("provider", provider)
        ai.setdefault("model", model)
        ai.setdefault("role", role)
        ai["calls"] = ai.get("calls", 0) + 1
        ai["ms"] = ai.get("ms", 0) + int(ms)
        raw, aid = json.dumps(ai), self._live.id
        self._write(lambda con: con.execute("UPDATE activity SET ai = ? WHERE action_id = ?", (raw, aid)))


def note_ai(*, provider: str, model: str | None, role: str, ms: int) -> None:
    """Adds an AI call to the current action's Activity row; a no-op outside any action."""
    ctx = current()
    if ctx is not None:
        ctx.note_ai(provider=provider, model=model, role=role, ms=ms)


# ------------------------------------------------------------------------------------------- execution

def _finish(live: _Live, state: str, *, message: str = "", result: Any = None, error: str | None = None,
            tone: str = "good") -> None:
    """Writes how an action ended, then lets go of its lock; a second call does nothing."""
    with _lock:
        if live.closed:
            return
        live.closed = True
        _LIVE.pop(live.id, None)
    if live.state == "stopped":
        state = "stopped"
    live.state = state
    if state == "stopped":
        tone = "warn"
    at, took, aid = clock.iso(), int((time.monotonic() - live.started) * 1000), live.id
    words = _guarded(message[:MESSAGE_CHARS]) if message else ""
    raw = json.dumps(result, default=str) if result is not None else None
    extra = STOPPED_WORDS if state == "stopped" else None
    step_state = {"done": "done", "error": "error"}.get(state, "skipped")
    lock_file = live.lock_file
    fault = _guarded(error)

    def op(con: sqlite3.Connection) -> None:
        con.execute("""UPDATE actions SET state = ?, message = ?, result = ?, error = ?, ended = ?
                       WHERE id = ?""", (state, words, raw, fault, at, aid))
        con.execute("UPDATE action_steps SET state = ? WHERE action_id = ? AND state = 'running'", (step_state, aid))
        con.execute("""UPDATE activity SET running = 0, took_ms = ?, tone = ?, detail = coalesce(?, detail),
                           changed = CASE WHEN ? IS NULL THEN changed
                                          WHEN changed IS NULL OR changed = '' THEN ?
                                          ELSE changed || ' ' || ? END
                       WHERE action_id = ?""", (took, tone, fault, extra, extra, extra, aid))
        con.commit()
        if lock_file is not None:
            _release(lock_file, _lock_path(aid))
        _publish_status(con)

    _WRITER.put(live.connect, op)
    _publish_action(live, state, message)
    live.finished.set()


def _execute(live: _Live, fn: Callable[[Ctx], Any], resumed_from: str | None, *, classic: bool = False) -> None:
    """Runs one action in this thread from start to end and records how it ended; never raises."""
    if live.state in FINAL:
        return
    live.state, live.started = "running", time.monotonic()
    at, aid = clock.iso(), live.id
    _WRITER.put(live.connect, lambda con: con.execute(
        "UPDATE actions SET state = 'running', started = ? WHERE id = ? AND state = 'queued'", (at, aid)))
    _publish_action(live, "running", live.text)
    try:
        con = live.connect()
    except Exception as e:  # recorded on the action; the pool thread must not die
        log.exception("action %s could not open the database", aid)
        _finish(live, "error", message=f"{e}", error=f"{e}", tone="bad")
        return
    ctx = Ctx(live, con, resumed_from)
    tokens = (_CURRENT.set(ctx), _PRIORITY.set(live.priority))
    if not hasattr(_RUN, "actions"):
        _RUN.actions, _RUN.ctx = {}, {}
    if live.subject:
        _RUN.actions[live.subject] = live.entry
        _RUN.ctx[live.subject] = ctx
    try:
        result = fn(ctx)
        ctx.checkpoint()
        message = result if isinstance(result, str) else "done"
        live.entry.update(state="done", message=message, at=db.now())
        _commit_quietly(con)
        _finish(live, "done", message=message, result=result)
    except Stopped:
        _stopped(live, con)
    except Exception as e:  # shown to the person, who can try again; the traceback is logged
        if live.stop.is_set() or (live.subject and ACTIONS.get(live.subject) is not live.entry):
            _stopped(live, con)
        else:
            if not isinstance(e, RuntimeError):
                log.error("action %s (%s) failed\n%s", aid, live.kind.kind, traceback.format_exc())
            live.entry.update(state="error", message=f"{e}", at=db.now())
            _rollback_quietly(con)
            _finish(live, "error", message=f"{e}", error=f"{e}", tone="bad")
            if classic and live.subject:
                _note(con, live.subject, "stuck", f"{e}")
    finally:
        _CURRENT.reset(tokens[0])
        _PRIORITY.reset(tokens[1])
        if live.subject:
            _RUN.actions.pop(live.subject, None)
            _RUN.ctx.pop(live.subject, None)
        con.close()


def _stopped(live: _Live, con: sqlite3.Connection) -> None:
    live.entry.update(state="stopped", message="stopped", at=db.now())
    _commit_quietly(con)
    live.state = "stopped"
    _finish(live, "stopped", message="stopped")


def _commit_quietly(con: sqlite3.Connection) -> None:
    try:
        con.commit()
    except sqlite3.Error:
        log.exception("could not keep an action's last work")


def _rollback_quietly(con: sqlite3.Connection) -> None:
    try:
        con.rollback()
    except sqlite3.Error:
        log.exception("could not undo a failed action's half-done work")


def kind_of(kind: str) -> ActionKind:
    """The registered kind; LookupError when there is none."""
    found = KINDS.get(kind)
    if found is None:
        raise LookupError(f"Hal does not know how to do {kind!r}")
    return found


def _bound(kind: ActionKind, params: dict) -> Callable[[Ctx], Any]:
    fn = _FNS[kind.kind]
    clean = {k: v for k, v in params.items() if not k.startswith("_")}
    return lambda ctx: fn(ctx, **clean)


def start(kind: str, *, subject: str | None = None, job_id: str | None = None, params: dict | None = None,
          why: str | None = None, priority: int | None = None, replace: bool = False,
          session_id: str | None = None, helper_id: str | None = None, remote: bool = False) -> str:
    """Starts an action in the background and returns its id at once.

    `subject` defaults to `job_id`. With the subject already busy, raises RuntimeError unless `replace`,
    which stops the one running in this process first.
    """
    k = kind_of(kind)
    params = dict(params or {})
    subject = subject if subject is not None else job_id
    live = _begin(k, subject=subject, job_id=job_id, params=params, why=why, priority=priority, replace=replace,
                  session_id=session_id, helper_id=helper_id, remote=remote)
    pool = _BACKGROUND if live.priority == 2 else _USER
    pool.submit(_execute, live, _bound(k, params), params.get("_carry_on_from"))
    return live.id


def run(kind: str, *, subject: str | None = None, job_id: str | None = None, params: dict | None = None,
        why: str | None = None, priority: int | None = None, replace: bool = False,
        session_id: str | None = None, helper_id: str | None = None) -> dict:
    """Runs an action in the calling thread (command line, scheduled children) and returns it as `get` does."""
    k = kind_of(kind)
    params = dict(params or {})
    subject = subject if subject is not None else job_id
    live = _begin(k, subject=subject, job_id=job_id, params=params, why=why, priority=priority, replace=replace,
                  session_id=session_id, helper_id=helper_id)
    _execute(live, _bound(k, params), params.get("_carry_on_from"))
    return get(live.id)


def wait(action_id: str, timeout: float = 10.0) -> dict:
    """Waits for an action of this process to end, then returns it as `get` does."""
    with _lock:
        live = _LIVE.get(action_id)
    if live is not None:
        live.finished.wait(timeout)
    flush(timeout)
    return get(action_id)


def _stop_live(live: _Live) -> None:
    """Lets go of a live action at once; it stops itself at its next checkpoint."""
    live.stop.set()
    if live.subject and ACTIONS.get(live.subject) is live.entry:
        ACTIONS.pop(live.subject, None)
    if live.state not in ACTIVE:
        return
    was_queued = live.state == "queued"
    live.state = "stopped"
    at, aid = clock.iso(), live.id
    _WRITER.put(live.connect, lambda con: con.execute(
        """UPDATE actions SET state = 'stopped', ended = ?
           WHERE id = ? AND state IN ('queued','running','waiting_for_you')""", (at, aid)))
    if was_queued:
        _finish(live, "stopped", message="stopped")


def stop(action_id: str) -> bool:
    """Stops an action, here or in another process; True when it was still going. The work done is kept."""
    with _lock:
        live = _LIVE.get(action_id)
        if live is not None:
            going = live.state in ACTIVE
            _stop_live(live)
            return going
    flush()
    con = db.connect()
    try:
        cur = con.execute("""UPDATE actions SET state = 'stopped', ended = ?
                             WHERE id = ? AND state IN ('queued','running','waiting_for_you')""",
                          (clock.iso(), action_id))
        if cur.rowcount and not _owner_alive(action_id):
            con.execute("UPDATE activity SET running = 0, tone = 'warn', changed = ? WHERE action_id = ?",
                        (STOPPED_WORDS, action_id))
        con.commit()
        return bool(cur.rowcount)
    finally:
        con.close()


def _steps(con: sqlite3.Connection, action_id: str) -> list[dict]:
    return [{"n": r["n"], "text": r["text"], "state": r["state"], "why": r["why"]}
            for r in con.execute("SELECT n, text, state, why FROM action_steps WHERE action_id = ? ORDER BY n, id",
                                 (action_id,))]


def shape(con: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """An `actions` row as the API's Action, plus `waiting`, `activity_id` and `can_carry_on`."""
    result = json.loads(row["result"]) if row["result"] else None
    waiting = None
    if isinstance(result, dict) and "waiting_for_you" in result:
        waiting = result["waiting_for_you"] if row["state"] == "waiting_for_you" else None
        result = None
    return {"id": row["id"], "kind": row["kind"], "state": row["state"], "subject": row["subject"],
            "job_id": row["job_id"], "message": row["message"] or "", "step_n": row["step_n"],
            "step_total": row["step_total"], "steps": _steps(con, row["id"]), "result": result,
            "error": row["error"], "waiting": waiting, "activity_id": row["activity_id"],
            "priority": row["priority"], "created": row["created"], "started": row["started"],
            "ended": row["ended"], "can_carry_on": row["state"] in CARRY_ON_FROM and row["kind"] in KINDS}


def get(action_id: str, con: sqlite3.Connection | None = None) -> dict:
    """One action and its steps; LookupError when there is none."""
    flush()
    own = con is None
    con = con or db.connect()
    try:
        row = con.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            raise LookupError("Hal has no record of that action.")
        return shape(con, row)
    finally:
        if own:
            con.close()


def running_for(subject: str) -> dict | None:
    """The action holding `subject` now, here or in another live process, or None."""
    with _lock:
        mine = next((live.id for live in _LIVE.values() if live.subject == subject and live.state in ACTIVE), None)
    if mine is not None:
        return get(mine)
    flush()
    con = db.connect()
    try:
        row = _held_elsewhere(con, "subject", subject, db.connect)
        return get(row["id"], con) if row else None
    finally:
        con.close()


def listing(con: sqlite3.Connection, state: str | None = None) -> list[dict]:
    """Running and interrupted actions (or those in the comma-separated `state`), most urgent and newest first."""
    flush()
    if state:
        wanted = tuple(s for s in state.split(",") if s in ACTIVE + FINAL)
        if not wanted:
            raise ValueError(f"There is no action state called {state!r}.")
    else:
        wanted = (*ACTIVE, "interrupted")
    rows = con.execute(f"""SELECT * FROM actions WHERE state IN ({','.join('?' * len(wanted))})
                           ORDER BY priority, created DESC LIMIT ?""", (*wanted, LIST_LIMIT)).fetchall()
    return [shape(con, r) for r in rows]


def _interrupt(con: sqlite3.Connection, action_id: str) -> None:
    cur = con.execute("""UPDATE actions SET state = 'interrupted', ended = ?
                         WHERE id = ? AND state IN ('queued','running','waiting_for_you')""",
                      (clock.iso(), action_id))
    if not cur.rowcount:
        return
    con.execute("UPDATE action_steps SET state = 'skipped' WHERE action_id = ? AND state = 'running'", (action_id,))
    con.execute("""UPDATE activity SET running = 0, tone = 'warn',
                       changed = CASE WHEN changed IS NULL OR changed = '' THEN ? ELSE changed || ' ' || ? END
                   WHERE action_id = ?""", (INTERRUPTED_WORDS, INTERRUPTED_WORDS, action_id))
    _lock_path(action_id).unlink(missing_ok=True)


def recover() -> int:
    """At start: marks actions left by an ended process as interrupted; resumable kinds carry on by themselves.

    Returns how many were interrupted. Rows another live process still holds are left alone.
    """
    flush()
    con = db.connect()
    resume = []
    n = 0
    try:
        rows = con.execute("SELECT id, kind FROM actions WHERE state IN ('queued','running','waiting_for_you')")
        for r in rows.fetchall():
            if _owner_alive(r["id"]):
                continue
            _interrupt(con, r["id"])
            n += 1
            kind = KINDS.get(r["kind"])
            if kind is not None and kind.resumable:
                resume.append(r["id"])
        con.commit()
    finally:
        con.close()
    for action_id in resume:
        try:
            carry_on(action_id)
        except (RuntimeError, LookupError, ValueError):
            log.exception("could not carry on %s", action_id)
    return n


def carry_on(action_id: str, *, why: str | None = None, session_id: str | None = None) -> str:
    """Starts an interrupted, stopped or failed action again with the same params; returns the new id.

    The new action's `ctx.resumed_from` names the old one, so its function can skip work already done.
    """
    old = get(action_id)
    if old["state"] not in CARRY_ON_FROM:
        raise RuntimeError("Only an action that stopped before it finished can carry on.")
    if old["kind"] not in KINDS:
        raise RuntimeError("This one cannot carry on by itself. Start it again from its page.")
    con = db.connect()
    try:
        row = con.execute("""SELECT a.params, a.priority, v.why FROM actions a
                             LEFT JOIN activity v ON v.id = a.activity_id WHERE a.id = ?""", (action_id,)).fetchone()
    finally:
        con.close()
    params = json.loads(row["params"] or "{}")
    params["_carry_on_from"] = action_id
    return start(old["kind"], subject=old["subject"], job_id=old["job_id"], params=params,
                 why=why or row["why"], priority=row["priority"], session_id=session_id)


@plugins.on_start
def at_start() -> None:
    """Server start: interrupted actions first, then the data tasks still due."""
    load_kinds()
    n = recover()
    if n:
        log.info("%d actions were interrupted by the last stop", n)
    from .db import tasks

    tasks.start_due()


# ------------------------------------------------------------------------------------------ classic API

def _classic_kind(subject: str, name: str) -> ActionKind:
    slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_") or "action"
    return ActionKind(f"classic.{slug}", CLASSIC_AREAS.get(subject, "applying"), "{name}", CLASSIC_WHY)


def _checkpoint(job_id: str) -> None:
    """Raises `Stopped` in an action whose application no longer wants it; a no-op in any other thread."""
    mine = getattr(_RUN, "actions", {}).get(job_id)
    if mine is not None and ACTIONS.get(job_id) is not mine:
        raise Stopped()
    ctx = getattr(_RUN, "ctx", {}).get(job_id)
    if ctx is not None:
        ctx.checkpoint()


def stop_action(job_id: str) -> bool:
    """Lets go of the action running for a job at once; it stops itself at its next checkpoint.

    The browser or model call in hand runs to its end in the background, but
    nothing it returns is written. Returns True when an action was running.
    """
    entry = ACTIONS.get(job_id)
    if not entry or entry["state"] != "running":
        return False
    ACTIONS.pop(job_id, None)
    with _lock:
        for live in list(_LIVE.values()):
            if live.entry is entry:
                _stop_live(live)
    return True


def _classic_begin(job_id: str, name: str, *, replace: bool, why: str) -> _Live:
    entry = ACTIONS.get(job_id)
    if entry and entry["state"] == "running":
        if not replace:
            raise RuntimeError(f"Hal is still busy with '{entry['name']}' for this job")
        stop_action(job_id)
    return _begin(_classic_kind(job_id, name), subject=job_id, job_id=None if job_id.startswith("_") else job_id,
                  params={}, why=why, priority=None, replace=True, session_id=None, helper_id=None, name=name)


def run_action(job_id: str, name: str, fn, *args, replace: bool = False) -> dict:
    """Starts fn(con, *args) in the background, as a persisted action mirrored in `ACTIONS[job_id]`.

    Args:
        replace: Stop an action already running for the job in favour of this one, rather than refuse.
    """
    live = _classic_begin(job_id, name, replace=replace, why=CLASSIC_WHY)
    POOL.submit(_execute, live, lambda ctx: fn(ctx.con, *args), None, classic=True)
    return live.entry


def _carry_out(con, job_id: str, fn, *args) -> None:
    """Runs an action already set in `ACTIONS[job_id]` in this thread, and records how it ended.

    The form watch sets the entry itself; this adopts it as a persisted action, so it has steps and Stop.
    """
    entry = ACTIONS[job_id]
    ACTIONS.pop(job_id, None)
    live = _classic_begin(job_id, entry["name"], replace=True, why=WATCH_WHY)
    live.entry = entry
    ACTIONS[job_id] = entry
    _execute(live, lambda ctx: fn(con, *args), None, classic=True)


def _say(job_id: str, text: str) -> None:
    """Shows what Hal is doing for a job, on its page and in the queue, and adds it as a step."""
    _checkpoint(job_id)
    if job_id in ACTIONS:
        ACTIONS[job_id]["message"] = text
    ctx = getattr(_RUN, "ctx", {}).get(job_id)
    if ctx is not None:
        ctx.step(text)


def _note(con, job_id: str, state: str, message: str) -> None:
    """Records where an application stands, for its page: working, ready, needs, stuck or sent."""
    try:
        form = _app(con, job_id)["form"]
        if form is not None:
            form.update(state=state, message=message)
            _set_app(con, job_id, form=form)
    except Exception:  # noqa: BLE001 - the action's own message still reaches the page
        pass


from ..apply.queries import _app, _set_app  # noqa: E402 - imported last; queries imports this module
