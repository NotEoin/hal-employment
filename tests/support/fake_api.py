"""A stand-in for Hal's server that the UI tests run the new app against.

It serves `jobhunt/web/static/app/` exactly as the real server will (the page at `/`, its files under
`/app/`, the same Content-Security-Policy with the import map's hash) and answers the few API v2 routes
the shell calls with made-up data, plus a made-up reply for each built page's own GET routes
(`MADE_UP_PAGES`), so the shell's route test draws every page without a 404. Stdlib only, bound to
127.0.0.1 on a free port; nothing here reads the person's data or Hal's database.

The real server replaces this for the pages' own tests; the shell's tests keep using it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

APP_DIR = Path(__file__).resolve().parents[2] / "jobhunt" / "web" / "static" / "app"
IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.S)
SSE_PING_S = 1.0          # often enough that a stopping server ends its streams quickly
SSE_MAX_S = 120           # no test page stays open longer; the stream thread always ends
MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
        ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


def csp_header(index_html: str) -> str:
    """The Content-Security-Policy for the app page: scripts only from this server plus the inline import map."""
    m = IMPORT_MAP.search(index_html)
    if not m:
        raise ValueError("index.html has no inline import map")
    digest = base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode("ascii")
    return ("default-src 'self'; script-src 'self' 'sha256-" + digest + "'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; frame-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'")


def made_up_session(**over: Any) -> dict:
    """A /session reply for a made-up person on their own computer."""
    s = {"remote": False, "remote_host": None, "developer": False, "version": "2.0.0.dev0", "platform": "linux",
         "setup_done": True, "first_name": "Robin", "simple_menu": False, "theme": "system", "classic": True,
         "install_id": "0f1e2d3c-0000-4000-8000-000000000000",
         "caps": {"open_folder": True, "window_mode": True, "sign_in_here": True, "start_at_login": True}}
    s.update(over)
    return s


def made_up_status(**over: Any) -> dict:
    """A /status reply: Hal idle, with its next two jobs."""
    s = {"busy": False, "text": "", "step_n": 0, "step_total": None, "action_id": None, "why_activity_id": None,
         "next": [{"what": "job search", "at": "13:30"}, {"what": "email check", "at": "12:45"}], "paused": False,
         "problems": 1, "ai_limited_until": None, "reconnecting": False}
    s.update(over)
    return s


MADE_UP_HOME = {"date_words": "Thursday 22 October", "greeting": "Good morning, Robin", "summary": "",
                "needs": [{"id": "n1", "icon": "file", "tone": "hal", "title": "Check a cover letter", "detail": "",
                           "due": None, "act": {"label": "Open", "route": "apps"}}],
                "new_matches": [], "first_search": None,
                "counts": {"jobs": 3, "apps": 1, "inbox": 2, "cv": 4, "memory": 1}}

MADE_UP_SEARCH = [{"label": "Museum Assistant, Riverside Trust", "kind": "Job", "route": "job-demo%3A1"},
                  {"label": "Search for jobs now", "kind": "Action", "action": "/jobs/search"}]

# Made-up replies for the built pages' own GET routes, by path without the query. A page that lands adds
# the routes it calls when it first draws; a page's own tests run it against the real server instead.
MADE_UP_PAGES = {
    # Documents, Messages and Activity
    "/documents": {"apps": [{"id": 1, "number": 1, "job_id": "demo:1", "title": "Museum Assistant",
                             "org": "Riverside Trust", "column": "ready", "column_title": "Getting ready",
                             "kinds": ["CV", "Letter"]}]},
    "/documents/1": {"id": 1, "number": 1, "job_id": "demo:1", "title": "Museum Assistant", "org": "Riverside Trust",
                     "column": "ready", "column_title": "Getting ready",
                     "cv": {"version_n": None, "type": None, "look": None, "sent_as": None},
                     "approved_at": "2026-10-22T10:44:00Z", "folder_words": "0001 Riverside Trust - Museum Assistant",
                     "files": [{"kind": "cv_pdf", "name": "CV (PDF)", "sub": "Robin Example CV.pdf", "file_id": "f1",
                                "file_name": "Robin Example CV.pdf", "has_text": False, "text_kind": None,
                                "acts": ["open", "download"]}],
                     "answers_text": "", "message_text": "", "sent_myself_ok": True},
    "/documents/details": {"rows": [{"label": "Full name", "value": "Robin Example"}]},
    "/messages": {"items": [{"id": "a:1", "kind": "alert", "unread": True, "title": "A made-up alert", "from": "Hal",
                             "at": "2026-10-22T09:00:00Z", "when": "09:00", "text": "Nothing was sent.",
                             "action": None, "tone": "warn", "employer": False}]},
    "/activity": {"days": [{"day": "Today", "items": []}], "more": None},
    # Your record
    "/record": {"exists": True, "folder_words": "~/Documents/Employment Record", "can_open": True, "groups": [],
                "last_read": None, "files": 0, "notes": 0, "private_folders": ["Private"]},
}


@dataclass
class FakeState:
    """What the fake server answers; tests change it between page loads."""

    session: dict | None = field(default_factory=made_up_session)
    status: dict = field(default_factory=made_up_status)
    home: dict = field(default_factory=lambda: json.loads(json.dumps(MADE_UP_HOME)))
    search: list = field(default_factory=lambda: list(MADE_UP_SEARCH))
    pages: dict = field(default_factory=lambda: json.loads(json.dumps(MADE_UP_PAGES)))
    posts: list = field(default_factory=list)      # (path, body) of every POST, PUT and DELETE received
    stopping: threading.Event = field(default_factory=threading.Event)


class Handler(BaseHTTPRequestHandler):
    """Serves the app's files and the shell's API routes from the server's FakeState."""

    server: FakeServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # the test output stays readable
        return

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def _error(self, status: int, code: str, message: str) -> None:
        self._json({"error": {"code": code, "message": message}}, status)

    def _static(self, rel: str) -> None:
        path = (APP_DIR / rel).resolve()
        if APP_DIR not in path.parents or not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = MIME.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        extra = {}
        if path.name == "index.html":
            extra["Content-Security-Policy"] = csp_header(path.read_text(encoding="utf-8"))
        self._send(200, path.read_bytes(), ctype, extra)

    def do_GET(self) -> None:
        """Serves files and the GET routes."""
        url = urlparse(self.path)
        if url.path == "/":
            self._static("index.html")
        elif url.path.startswith("/app/"):
            self._static(url.path[len("/app/"):])
        elif url.path.startswith("/api/v2/"):
            self._api_get(url.path[len("/api/v2"):], parse_qs(url.query))
        else:
            self._send(404, b"not found", "text/plain")

    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        st = self.server.state
        if st.session is None:
            self._error(401, "signed_out", "Open Hal Employment from its shortcut.")
        elif path == "/session":
            self._json(st.session)
        elif path == "/status":
            self._json(st.status)
        elif path == "/home":
            self._json(st.home)
        elif unquote(path) in st.pages:
            self._json(st.pages[unquote(path)])
        elif path == "/search":
            q = (query.get("q") or [""])[0].lower()
            self._json([r for r in st.search if q in r["label"].lower()])
        elif path == "/events":
            self._events()
        else:
            self._error(404, "not_found", "Hal has nothing at that address yet.")

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            end = time.monotonic() + SSE_MAX_S
            while not self.server.state.stopping.is_set() and time.monotonic() < end:
                self.server.state.stopping.wait(SSE_PING_S)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            return

    def _change(self) -> None:
        if self.headers.get("X-Hal") != "1":
            self._error(403, "forbidden", "Changes need the X-Hal header.")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        self.server.state.posts.append((self.path, body))
        if self.server.state.session is None:
            self._error(401, "signed_out", "Open Hal Employment from its shortcut.")
        else:
            self._json({"ok": True})

    do_POST = do_PUT = do_DELETE = _change


class FakeServer(ThreadingHTTPServer):
    """The fake server on 127.0.0.1 and a free port; use it as a context manager."""

    daemon_threads = True

    def __init__(self, state: FakeState | None = None) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.state = state or FakeState()
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """The server's base address, without a trailing slash."""
        return f"http://127.0.0.1:{self.server_address[1]}"

    def __enter__(self) -> FakeServer:
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.state.stopping.set()
        self.shutdown()
        self.server_close()
        if self._thread:
            self._thread.join(timeout=5)
