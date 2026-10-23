"""Windows opened from another computer: the list Settings shows, Sign out, and whether one is open now.

A remote window is a session made from an `open-url --remote` link; traffic through a tunnel
looks local, so the session's `remote` flag is the only signal. Desktop pop-ups are held back while a
remote window has its event stream open. Alerts are raised by other processes too (scheduled
searches, the mail check), which cannot see the server's streams, so the server writes `PRESENCE_FILE`
with an expiry while such a stream is open and any process reads it. The expiry means a server that dies
never leaves pop-ups held back for long.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import timedelta

from ..core import clock, paths
from ..web import auth

log = logging.getLogger(__name__)

PRESENCE_FILE = "remote-window.json"
PRESENCE_S = 10.0            # how often the server rewrites the file while a remote window is open
PRESENCE_LIFE_S = 3 * PRESENCE_S   # a missed beat or two never lets a pop-up through by mistake
BROWSERS = (("Edg/", "Microsoft Edge"), ("OPR/", "Opera"), ("Vivaldi/", "Vivaldi"), ("Brave", "Brave"),
            ("Chrome/", "Google Chrome"), ("Firefox/", "Firefox"), ("Safari/", "Safari"))
SYSTEMS = (("Windows", "Windows"), ("Mac OS X", "a Mac"), ("Linux", "Linux"))
SAFE_ID = re.compile(r"^[0-9a-f]{64}$")

_stop = threading.Event()


def describe(user_agent: str | None) -> str:
    """"Google Chrome on Linux" from a browser's User-Agent; "A browser" when it says nothing useful."""
    ua = user_agent or ""
    browser = next((name for mark, name in BROWSERS if mark in ua), "A browser")
    system = next((name for mark, name in SYSTEMS if mark in ua), "")
    return f"{browser} on {system}" if system else browser


def _live(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("""SELECT id_hash, created, last_seen, remote_host, user_agent FROM sessions
                          WHERE remote = 1 AND ended IS NULL AND expires > ? ORDER BY created DESC""",
                       (clock.iso(),)).fetchall()


def listing(con: sqlite3.Connection, this_session: str | None = None) -> list[dict]:
    """The windows open from another computer, newest first, as `GET /remote/sessions` answers them."""
    return [{"id": r["id_hash"], "since": r["created"], "last_seen": r["last_seen"],
             "host": r["remote_host"], "browser": describe(r["user_agent"]), "this_one": r["id_hash"] == this_session}
            for r in _live(con)]


def sign_out(con: sqlite3.Connection, session_id: str) -> None:
    """Ends one remote window's session; LookupError when there is no such open window."""
    if not SAFE_ID.match(session_id or ""):
        raise LookupError("That window is not open any more.")
    row = con.execute("SELECT remote, ended FROM sessions WHERE id_hash = ?", (session_id,)).fetchone()
    if row is None or not row["remote"] or row["ended"]:
        raise LookupError("That window is not open any more.")
    auth.end_session(con, session_id)
    log.info("signed out a window opened from another computer")


def _streaming_here(con: sqlite3.Connection) -> bool:
    """True when a remote window has an event stream open in this process (the server)."""
    from ..web import sse

    return any(sse.open_count(r["id_hash"]) for r in _live(con))


def _file():
    return paths.state_dir() / PRESENCE_FILE


def beat(con: sqlite3.Connection) -> bool:
    """Writes or removes the presence file from this server's open streams; True when a remote window is open."""
    open_now = _streaming_here(con)
    file = _file()
    if open_now:
        tmp = file.with_suffix(".tmp")
        until = clock.iso(clock.now_utc() + timedelta(seconds=PRESENCE_LIFE_S))
        tmp.write_text(json.dumps({"until": until}) + "\n", encoding="utf-8")
        os.replace(tmp, file)
    else:
        file.unlink(missing_ok=True)
    return open_now


def window_open(con: sqlite3.Connection | None = None) -> bool:
    """True while a window opened from another computer is showing Hal, as far as any process can tell."""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        if clock.parse(data["until"]) > clock.now_utc():
            return True
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no file, or one half-written: fall through to this process's own streams
    if con is None:
        return False
    try:
        return _streaming_here(con)
    except sqlite3.Error:
        return False


def _beat_forever(connect) -> None:
    from ..web import sse

    while not _stop.wait(PRESENCE_S) and not sse.STOPPING.is_set():
        try:
            con = connect()
            try:
                beat(con)
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            log.exception("could not note whether a remote window is open")


def start_presence(connect) -> threading.Thread:
    """Starts the server's thread that keeps the presence file current."""
    _stop.clear()
    thread = threading.Thread(target=_beat_forever, args=(connect,), name="remote-presence", daemon=True)
    thread.start()
    return thread


def stop_presence() -> None:
    """Stops the presence thread at its next wake-up."""
    _stop.set()
