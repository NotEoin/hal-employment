"""Paired browser helpers: pairing codes, helper keys, the helper version and who carries what.

A helper earns a key by swapping a one-time pairing code that a signed-in window asked for. The code
belongs to that window's session and browser id, so a helper paired from it carries the applications that
window starts, even when the app window and the helper live in different browsers (Edge and Opera on
Windows). Keys and codes are stored only as SHA-256 hashes.

Design decisions, each with its reason:

- The checks reach `web.auth` only through `auth.register_verifier`, so auth never learns how
  helpers are stored.
- The router hands the request's handler to verifiers only, so the pairing check keeps the request's Host
  for the reply's `base` in a thread-local; the pair route runs on the same thread straight after it.
- A request from a helper carries `X-Hal-Key` and, when the browser sends one, an `Origin` of an allowed
  extension id. A web page always sends its own Origin on a cross-site request and can never learn a key.
- The version Hal wants is read from `<code>/extension/manifest.json` once (decision 16): the file and the
  server are updated together by git, and the updater always restarts Hal.
- A poll's `last_seen` and the carrier of an application are bookkeeping, written with a short wait and
  skipped while another write holds the database. The form driver can hold a write while it waits on this
  very helper, so a full busy wait would hold up the poll that is to bring the driver its answer.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..core import clock, events, paths, settings
from ..web import auth

log = logging.getLogger(__name__)

PROTOCOL = 1                 # the helper protocol this server speaks
CODE_S = 120                 # a pairing code lasts two minutes and works once
CODE_BYTES = 16
KEY_BYTES = 32
KEY_HEADER = "X-Hal-Key"
EXTENSION_SCHEME = "chrome-extension://"
# Chromium makes an unpacked extension's id from its manifest key: the first 128 bits of the key's SHA-256,
# written with the letters a to p for the hex digits 0 to f.
ID_HEX_DIGITS = 32
ID_ALPHABET_START = ord("a")
OLD_HELPER = "old"           # the queue of a 0.4 or 0.5 helper, which has no key and polls /api/ext/*
BOOKKEEPING_WAIT_MS = 500    # how long a poll's bookkeeping waits for another write before it skips
VERSION_PART = re.compile(r"\d+")

_pairing = threading.local()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def manifest_path() -> Path:
    """The helper's manifest in this checkout."""
    return paths.code_dir() / "extension" / "manifest.json"


@functools.lru_cache(maxsize=1)
def manifest() -> dict:
    """The helper's manifest, read once per process."""
    return json.loads(manifest_path().read_text(encoding="utf-8"))


def wanted_version() -> str:
    """The helper version this copy of Hal ships ("1.0.0")."""
    return str(manifest()["version"])


def version_key(version: str | None) -> tuple[int, ...]:
    """A version as numbers for comparing ("1.0.0" -> (1, 0, 0)); an unknown version sorts first."""
    return tuple(int(p) for p in VERSION_PART.findall(version or "")) or (0,)


def older(version: str | None, than: str | None = None) -> bool:
    """True when `version` is older than `than` (default: the version Hal wants)."""
    a, b = version_key(version), version_key(than or wanted_version())
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))


def extension_id(key_b64: str) -> str:
    """The id Chromium gives an unpacked extension whose manifest has this public `key`."""
    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:ID_HEX_DIGITS]
    return "".join(chr(ID_ALPHABET_START + int(c, 16)) for c in digest)


def manifest_id() -> str:
    """The helper's id on every computer, fixed by the manifest key."""
    return extension_id(manifest()["key"])


def allowed_ids() -> set[str]:
    """Extension ids Hal accepts: the manifest's, plus any in `helper.allowed_ids` (a store id later)."""
    try:
        extra = settings.get("helper.allowed_ids") or []
    except KeyError:
        extra = []
    return {manifest_id(), *(str(x) for x in extra)}


def origin_ok(origin: str | None) -> bool:
    """True when the request sent no Origin, or the origin of an allowed helper."""
    if not origin:
        return True
    return origin.startswith(EXTENSION_SCHEME) and origin[len(EXTENSION_SCHEME):].strip("/") in allowed_ids()


# ------------------------------------------------------------------------------------------------ pairing

def new_code(con: sqlite3.Connection, session: auth.Session, browser: str | None) -> str:
    """A one-time pairing code for this window's session, valid for `CODE_S` seconds."""
    code = secrets.token_urlsafe(CODE_BYTES)
    now = clock.now_utc()
    with con:
        con.execute("INSERT INTO pairing_codes (hash, session_id, browser, created, expires) VALUES (?, ?, ?, ?, ?)",
                    (_hash(code), session.id, browser, clock.iso(now), clock.iso(now + timedelta(seconds=CODE_S))))
    return code


def pair(con: sqlite3.Connection, code: str, *, browser: str | None, version: str | None, protocol: int | None,
         extension_id: str | None) -> dict:
    """Swaps a pairing code for a helper id and key; raises `PermissionError` for a used, old or unknown code."""
    now = clock.iso()
    with con:
        row = con.execute("SELECT * FROM pairing_codes WHERE hash = ?", (_hash(code or ""),)).fetchone()
        if row is None or row["used"] or row["expires"] <= now:
            raise PermissionError("This pairing code is not valid any more. Press Connect again.")
        took = con.execute("UPDATE pairing_codes SET used = ? WHERE hash = ? AND used IS NULL", (now, row["hash"]))
        if took.rowcount != 1:
            raise PermissionError("This pairing code is not valid any more. Press Connect again.")
        session = con.execute("SELECT browser_id, remote FROM sessions WHERE id_hash = ?",
                              (row["session_id"],)).fetchone()
        helper_id = uuid.uuid4().hex[:16]
        key = secrets.token_urlsafe(KEY_BYTES)
        con.execute("""INSERT INTO helpers (id, key_hash, browser, version, protocol, extension_id, session_id,
                                            browser_id, remote, paired)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (helper_id, _hash(key), browser or row["browser"], version, protocol, extension_id,
                     row["session_id"], session["browser_id"] if session else None,
                     int(bool(session and session["remote"])), now))
    log.info("paired browser helper %s (%s)", helper_id, browser or row["browser"] or "browser not named")
    return {"helper_id": helper_id, "key": key}


def pairing_base() -> str | None:
    """The address the helper paired through ("http://127.0.0.1:8737"), kept by the pairing check."""
    host = getattr(_pairing, "host", None)
    return f"http://{host}" if host else None


def _verify_pairing(handler: Any) -> bool:
    """A pair request must come from an allowed helper (the code itself is checked by the route)."""
    _pairing.host = handler.headers.get("Host")
    return origin_ok(handler.headers.get("Origin")) and bool(handler.headers.get("Origin"))


# ------------------------------------------------------------------------------------------------ keys

def check_key(con: sqlite3.Connection, header: str | None, origin: str | None = None) -> auth.Helper | None:
    """The helper named by an `X-Hal-Key: <helper_id>:<key>` header, or None for a wrong, revoked or missing key."""
    if not header or ":" not in header or not origin_ok(origin):
        return None
    helper_id, _, key = header.partition(":")
    try:
        row = con.execute("SELECT * FROM helpers WHERE id = ?", (helper_id.strip(),)).fetchone()
    except sqlite3.OperationalError:
        return None   # before the auth tables exist no helper is paired
    if row is None or row["revoked"] or not hmac.compare_digest(row["key_hash"], _hash(key.strip())):
        return None
    return auth.Helper(row["id"], row["browser"], row["version"], row["protocol"], bool(row["remote"]))


def _verify_helper(handler: Any) -> auth.Helper | None:
    from .. import db

    header = handler.headers.get(KEY_HEADER)
    if not header:
        return None
    con = db.connect()
    try:
        return check_key(con, header, handler.headers.get("Origin"))
    finally:
        con.close()


auth.register_verifier("helper", _verify_helper)
auth.register_verifier("pairing", _verify_pairing)


# ------------------------------------------------------------------------------------------------ rows

def _locked(e: sqlite3.OperationalError) -> bool:
    return "locked" in str(e) or "busy" in str(e)


def seen(con: sqlite3.Connection, helper_id: str, version: str | None, protocol: int | None) -> None:
    """Records a poll: when it came and the version it carried; skipped while another write holds the database."""
    con.execute(f"PRAGMA busy_timeout = {BOOKKEEPING_WAIT_MS}")
    try:
        with con:
            con.execute("UPDATE helpers SET last_seen = ?, version = COALESCE(?, version), "
                        "protocol = COALESCE(?, protocol) WHERE id = ?", (clock.iso(), version, protocol, helper_id))
    except sqlite3.OperationalError as e:
        if not _locked(e):
            raise
        log.info("a poll of helper %s was not recorded: another write held the database", helper_id)


def row(con: sqlite3.Connection, helper_id: str) -> sqlite3.Row | None:
    """One helper's row."""
    return con.execute("SELECT * FROM helpers WHERE id = ?", (helper_id,)).fetchone()


def paired(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every helper not forgotten, most recently seen first."""
    try:
        return con.execute("SELECT * FROM helpers WHERE revoked IS NULL "
                           "ORDER BY COALESCE(last_seen, paired) DESC").fetchall()
    except sqlite3.OperationalError:
        return []


def any_paired(con: sqlite3.Connection) -> bool:
    """True once a 1.0 helper is paired; from then on an old helper's polls get no commands."""
    return bool(paired(con))


def old_helper() -> dict | None:
    """The old 0.4 or 0.5 helper, when one polled recently, for `GET /setup` `found.old_helper`; else None."""
    from .. import extension

    return {"protocol": 0} if extension.old_helper_seen() else None


def forget(con: sqlite3.Connection, helper_id: str) -> None:
    """Forgets a helper: its key stops working at once."""
    with con:
        done = con.execute("UPDATE helpers SET revoked = ? WHERE id = ? AND revoked IS NULL", (clock.iso(), helper_id))
    if done.rowcount != 1:
        raise LookupError("There is no such browser helper.")
    events.publish("helper", id=helper_id, connected=False)


def listing(con: sqlite3.Connection, session: auth.Session | None) -> list[dict]:
    """The paired helpers as the setup step and Settings show them."""
    from .. import extension

    out = []
    for r in paired(con):
        out.append({"id": r["id"], "browser": r["browser"], "version": r["version"], "label": r["label"],
                    "last_seen": r["last_seen"], "remote": bool(r["remote"]),
                    "this_browser": bool(session and r["browser_id"] == session.browser_id),
                    "connected": extension.connected(r["id"]), "older": older(r["version"]),
                    "reloaded_still_older": extension.still_older(r["id"]), "old": False})
    if extension.old_helper_seen():
        out.append({"id": OLD_HELPER, "browser": None, "version": None, "label": "An older Hal helper",
                    "last_seen": None, "remote": False, "this_browser": False,
                    "connected": extension.connected(OLD_HELPER), "older": True, "reloaded_still_older": False,
                    "old": True})
    return out


def choose(con: sqlite3.Connection, job_id: str | None, connected: set[str]) -> str | None:
    """The helper that should carry this job's application, or None when none can.

    In order: the one the application already uses; the one paired in the browser of the session that
    started it; the most recently seen helper of the same kind (local or remote).
    """
    app = None
    if job_id:
        try:
            app = con.execute("SELECT helper_id, session_id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        except sqlite3.OperationalError:
            app = None
    if app and app["helper_id"] in connected:
        return app["helper_id"]
    remote = False
    rows = paired(con)
    if app and app["session_id"]:
        session = con.execute("SELECT browser_id, remote FROM sessions WHERE id_hash = ?",
                              (app["session_id"],)).fetchone()
        if session:
            remote = bool(session["remote"])
            mine = [r["id"] for r in rows if r["browser_id"] == session["browser_id"] and r["id"] in connected]
            if mine:
                return mine[0]
    same_kind = [r["id"] for r in rows if bool(r["remote"]) == remote and r["id"] in connected]
    return same_kind[0] if same_kind else None


def carry(con: sqlite3.Connection, job_id: str, helper_id: str) -> bool:
    """Keeps which helper carries a job's application; False while the job has no application row yet, or
    while another write holds the database (the next command tries again)."""
    con.execute(f"PRAGMA busy_timeout = {BOOKKEEPING_WAIT_MS}")
    try:
        with con:
            done = con.execute("UPDATE applications SET helper_id = ? WHERE job_id = ?", (helper_id, job_id))
    except sqlite3.OperationalError as e:
        if _locked(e):
            log.info("the helper of %s is kept at the next command: another write held the database", job_id)
        else:
            log.warning("could not keep the helper of %s: %s", job_id, e)
        return False
    return done.rowcount > 0
