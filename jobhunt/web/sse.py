"""The event stream at /api/v2/events and the poller that brings other processes' work into it.

Each stream sends `retry: 3000`, then events as `id`, `event` and `data` lines, and a `: ping` comment every
`PING_S` seconds so a dead connection is noticed; a failed write ends that stream's thread. `Last-Event-ID`
replays from the ring buffer, or sends `resync` when the ring no longer reaches back. A session keeps at
most `MAX_STREAMS` streams: a newer one closes the oldest with `event: bye`.

The command line and scheduled children write to the database, not to this process, so `DbPoller` reads
`activity`, `action_steps` and `alerts` once a second for rows newer than it has seen and publishes them.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import TYPE_CHECKING

from ..core import events

if TYPE_CHECKING:
    from .app import AppHandler

PING_S = 15.0
RETRY_MS = 3000
MAX_STREAMS = 4
WAKE_S = 0.5                 # how often a quiet stream checks whether it should end
POLL_S = 1.0                 # other processes' rows reach the page within a second

log = logging.getLogger(__name__)
STOPPING = threading.Event()
_lock = threading.Lock()
_open: dict[str, list[threading.Event]] = {}


def _frame(ev: events.Event) -> bytes:
    data = json.dumps(ev.data, default=str)
    if ev.type == events.RESYNC:
        return f"event: {events.RESYNC}\ndata: {{}}\n\n".encode()
    return f"id: {ev.seq}\nevent: {ev.type}\ndata: {data}\n\n".encode()


def _register(session_id: str) -> threading.Event:
    bye = threading.Event()
    with _lock:
        mine = _open.setdefault(session_id, [])
        mine.append(bye)
        while len(mine) > MAX_STREAMS:
            mine.pop(0).set()
    return bye


def _unregister(session_id: str, bye: threading.Event) -> None:
    with _lock:
        mine = _open.get(session_id, [])
        if bye in mine:
            mine.remove(bye)
        if not mine:
            _open.pop(session_id, None)


def end_all() -> None:
    """Says bye to every open stream, so each ends at its next wake-up (a server that stops)."""
    with _lock:
        for byes in _open.values():
            for bye in byes:
                bye.set()


def open_count(session_id: str) -> int:
    """How many streams this session has open."""
    with _lock:
        return len(_open.get(session_id, []))


def stream(handler: AppHandler, session_id: str) -> None:
    """Writes the event stream to `handler` until the browser goes, a newer stream replaces it, or Hal stops."""
    bye = _register(session_id)
    sub = events.subscribe()
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.end_headers()
        out = handler.wfile
        out.write(f"retry: {RETRY_MS}\n\n".encode())
        sent = 0
        last = (handler.headers.get("Last-Event-ID") or "").strip()
        if last:
            replay = events.since(int(last)) if last.isdigit() else None
            if replay is None:
                out.write(_frame(events.Event(0, events.RESYNC, {})))
            for ev in replay or []:
                out.write(_frame(ev))
                sent = ev.seq
        out.flush()
        quiet_since = time.monotonic()
        while not STOPPING.is_set() and not bye.is_set():
            ev = sub.get(timeout=WAKE_S)
            if ev is not None and (ev.type == events.RESYNC or ev.seq > sent):
                out.write(_frame(ev))
                out.flush()
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= PING_S:
                out.write(b": ping\n\n")
                out.flush()
                quiet_since = time.monotonic()
        if bye.is_set():
            out.write(b"event: bye\ndata: {}\n\n")
            out.flush()
    except OSError:
        pass  # the browser closed the page; this thread ends
    finally:
        sub.close()
        _unregister(session_id, bye)


class DbPoller(threading.Thread):
    """Publishes Activity rows, action steps and alerts written by other processes."""

    TABLES = ("activity", "action_steps", "alerts")

    def __init__(self, connect, stop: threading.Event) -> None:
        super().__init__(name="event-poller", daemon=True)
        self.connect, self.stop = connect, stop
        self.seen: dict[str, int] = {}

    def _max(self, con: sqlite3.Connection, table: str) -> int | None:
        try:
            return con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            return None  # the table comes with a later migration

    def poll(self, con: sqlite3.Connection) -> None:
        """One pass: publishes rows newer than the last pass (the first pass only remembers where it is)."""
        for table in self.TABLES:
            top = self._max(con, table)
            if top is None:
                continue
            if table not in self.seen:
                self.seen[table] = top
                continue
            if top <= self.seen[table]:
                continue
            rows = con.execute(f"SELECT * FROM {table} WHERE id > ? ORDER BY id", (self.seen[table],)).fetchall()
            for r in rows:
                if table == "activity":
                    events.publish("activity", id=r["id"])
                elif table == "action_steps":
                    events.publish("step", action_id=r["action_id"], n=r["n"], text=r["text"], state=r["state"])
                else:
                    events.publish("alert", id=r["id"], level=r["level"], title=r["title"], body=r["body"],
                                   route=None)
            self.seen[table] = top

    def run(self) -> None:
        """Polls until stopped; a failing pass is logged and the next one tries again."""
        while not self.stop.wait(POLL_S):
            try:
                con = self.connect()
                try:
                    self.poll(con)
                finally:
                    con.close()
            except sqlite3.Error:
                log.exception("event poller pass failed")
