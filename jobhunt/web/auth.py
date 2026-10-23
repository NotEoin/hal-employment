"""Who may use the server: one-time open links, session cookies, the local API key and helper verifiers.

Threat model: this protects against web pages in any browser (CSRF, reading responses, DNS
rebinding), other user accounts and the network (the server listens on 127.0.0.1 only). It cannot protect
against programs run by the same user, who can read the same files.

- An open link carries a 32-byte token, stored only as its SHA-256, valid for `OPEN_TOKEN_S` and once.
  `mint_open_url` writes the database directly, so it works from the CLI whether or not the server runs.
- `/open` swaps the token for a session cookie named for this install (`hal_s_<id8>`), because cookies for
  127.0.0.1 are shared across ports: a worktree's test server never overwrites the real session.
- The local API key (config/local-api-key, 0600) lets the CLI, updater and launcher in while no browser is.
- Helpers and pairing codes register their checks with `register_verifier`, so this file never learns
  their storage.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import quote, urlencode

from ..core import clock, paths

OPEN_TOKEN_S = 120                         # long enough to open a window, too short to reuse
SESSION_DAYS = 30                          # a session unused this long ends
BROWSER_ID_DAYS = 400                      # the longest cookie lifetime browsers allow
RENEW_EVERY = timedelta(days=1)            # sessions are renewed once a day while used
TOKEN_BYTES = 32
DEFAULT_PORT = 8737
INSTALL_FILE = "install.json"
KEY_FILE = "local-api-key"
SECRET_MODE = 0o600

_lock = threading.Lock()
_verifiers: dict[str, Callable[[Any], Any]] = {}


@dataclass(frozen=True)
class Session:
    """A signed-in window. `id` is the SHA-256 of the cookie value; the value itself is never stored."""

    id: str
    remote: bool
    remote_host: str | None
    browser_id: str
    created: str


@dataclass(frozen=True)
class Helper:
    """A paired browser helper."""

    id: str
    browser: str | None
    version: str | None
    protocol: int | None
    remote: bool


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_private(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRET_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def install_id() -> str:
    """This install's UUID, made at first use and kept in config/install.json."""
    file = paths.config_dir() / INSTALL_FILE
    with _lock:
        if not file.exists():
            try:
                _write_private(file, json.dumps({"install_id": str(uuid.uuid4())}) + "\n")
            except FileExistsError:
                pass  # another process made it first; read theirs
        return json.loads(file.read_text(encoding="utf-8"))["install_id"]


def id8() -> str:
    """The first 8 hex digits of the install id, for cookie names."""
    return install_id().replace("-", "")[:8]


def session_cookie_name() -> str:
    """`hal_s_<id8>`."""
    return f"hal_s_{id8()}"


def browser_cookie_name() -> str:
    """`hal_b_<id8>`: a long-lived id that links a helper to this browser."""
    return f"hal_b_{id8()}"


def local_api_key() -> str:
    """The key the CLI, updater and launcher send as `Authorization: Bearer <key>`; made once, mode 0600."""
    file = paths.config_dir() / KEY_FILE
    with _lock:
        if not file.exists():
            try:
                _write_private(file, secrets.token_urlsafe(TOKEN_BYTES) + "\n")
            except FileExistsError:
                pass
        return file.read_text(encoding="utf-8").strip()


def server_port() -> int:
    """The port: `HAL_EMPLOYMENT_PORT`, else the `advanced.port` setting, else 8737."""
    raw = os.environ.get("HAL_EMPLOYMENT_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError as e:
            raise ValueError(f"HAL_EMPLOYMENT_PORT={raw!r} is not a port number") from e
    from ..core.settings import store

    return int(store.stored("advanced.port") or DEFAULT_PORT)


def _safe_next(next_path: str | None) -> str | None:
    """`next` only when it is a path on this server (never another site)."""
    if not next_path or not next_path.startswith("/") or next_path.startswith("//") or "\\" in next_path:
        return None
    return next_path


def mint_open_url(*, remote: bool = False, remote_host: str | None = None, port: int | None = None,
                  next: str | None = None, con: sqlite3.Connection | None = None) -> str:
    """A one-time link that opens a signed-in window: `http://127.0.0.1:<port>/open?t=<token>&next=<path>`."""
    from .. import db

    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = clock.now_utc()
    own = con is None
    con = con or db.connect()
    try:
        con.execute("""INSERT INTO open_tokens (hash, created, expires, remote, remote_host, next)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (_hash(token), clock.iso(now), clock.iso(now + timedelta(seconds=OPEN_TOKEN_S)), int(remote),
                     remote_host, _safe_next(next)))
        con.commit()
    finally:
        if own:
            con.close()
    query = {"t": token}
    if _safe_next(next):
        query["next"] = _safe_next(next)
    return f"http://127.0.0.1:{port or server_port()}/open?{urlencode(query, quote_via=quote)}"


def consume_open_token(con: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """The token's row, marked used; None when it is unknown, used or expired."""
    now = clock.iso()
    with con:
        row = con.execute("SELECT * FROM open_tokens WHERE hash = ?", (_hash(token),)).fetchone()
        if row is None or row["used"] or row["expires"] <= now:
            return None
        cur = con.execute("UPDATE open_tokens SET used = ? WHERE hash = ? AND used IS NULL", (now, row["hash"]))
        return row if cur.rowcount == 1 else None


def token_was_remote(con: sqlite3.Connection, token: str) -> bool:
    """True when a known (used or expired) token was made for another computer, for the page's words."""
    row = con.execute("SELECT remote FROM open_tokens WHERE hash = ?", (_hash(token),)).fetchone()
    return bool(row and row["remote"])


def create_session(con: sqlite3.Connection, *, remote: bool, remote_host: str | None, browser_id: str | None,
                   user_agent: str | None) -> tuple[str, str, Session]:
    """A new session row; returns (cookie value, browser id, session)."""
    value = secrets.token_urlsafe(TOKEN_BYTES)
    browser_id = browser_id or secrets.token_urlsafe(TOKEN_BYTES)
    now = clock.now_utc()
    created = clock.iso(now)
    with con:
        con.execute("""INSERT INTO sessions (id_hash, created, last_seen, expires, remote, remote_host, browser_id,
                                             user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (_hash(value), created, created, clock.iso(now + timedelta(days=SESSION_DAYS)), int(remote),
                     remote_host, browser_id, (user_agent or "")[:300]))
    return value, browser_id, Session(_hash(value), remote, remote_host, browser_id, created)


def cookies_for(value: str, browser_id: str, *, new_browser: bool) -> list[str]:
    """The Set-Cookie values for a new session (and the browser id when the browser had none)."""
    flags = "HttpOnly; SameSite=Strict; Path=/"
    out = [f"{session_cookie_name()}={value}; {flags}; Max-Age={SESSION_DAYS * 86400}"]
    if new_browser:
        out.append(f"{browser_cookie_name()}={browser_id}; {flags}; Max-Age={BROWSER_ID_DAYS * 86400}")
    return out


def read_cookies(header: str | None) -> dict[str, str]:
    """The request's cookies by name."""
    jar = SimpleCookie()
    try:
        jar.load(header or "")
    except Exception:
        return {}
    return {k: m.value for k, m in jar.items()}


def session_for(con: sqlite3.Connection, cookie_value: str | None) -> Session | None:
    """The live session for a cookie value, renewed once a day; None when unknown, ended or expired."""
    if not cookie_value:
        return None
    try:
        row = con.execute("SELECT * FROM sessions WHERE id_hash = ?", (_hash(cookie_value),)).fetchone()
    except sqlite3.OperationalError:
        return None  # before the auth tables exist nobody is signed in
    now = clock.now_utc()
    if row is None or row["ended"] or row["expires"] <= clock.iso(now):
        return None
    if clock.parse(row["last_seen"]) <= now - RENEW_EVERY:
        with con:
            con.execute("UPDATE sessions SET last_seen = ?, expires = ? WHERE id_hash = ?",
                        (clock.iso(now), clock.iso(now + timedelta(days=SESSION_DAYS)), row["id_hash"]))
    return Session(row["id_hash"], bool(row["remote"]), row["remote_host"], row["browser_id"], row["created"])


def end_session(con: sqlite3.Connection, session_id: str) -> None:
    """Signs a window out."""
    with con:
        con.execute("UPDATE sessions SET ended = ? WHERE id_hash = ?", (clock.iso(), session_id))


def is_local_key(header: str | None) -> bool:
    """True when `Authorization` carries this install's local API key."""
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):].strip().encode(), local_api_key().encode())


def register_verifier(kind: str, fn: Callable[[Any], Any]) -> None:
    """Registers the check for `kind` ("helper" returns a `Helper` or None; "pairing" returns a bool)."""
    with _lock:
        if kind in _verifiers:
            raise ValueError(f"a {kind} verifier is already registered")
        _verifiers[kind] = fn


def verify(kind: str, handler: Any) -> Any:
    """Runs the registered check for `kind` on a request; None when nothing is registered."""
    fn = _verifiers.get(kind)
    return fn(handler) if fn else None
