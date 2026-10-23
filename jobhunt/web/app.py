"""The server: one process on 127.0.0.1 serving the app, API v2 and the event stream.

Each request goes through, in order: the Host check, `/healthz`, `/open`, `/pair`, the CSRF check for writes,
the session or local key, `/api/v2/*` through the router (the event stream is a route), the refusal of a
helper from before pairing at `/api/ext/*`, a document's `/files` and `/doc/*`, then the app's own files.

The address is never a setting: Hal listens on `BIND` only, and over SSH the tunnel reaches it there.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.client
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, db
from ..core import naming, paths, plugins
from . import auth, router, sse

BIND = "127.0.0.1"
HOSTS = ("127.0.0.1", "localhost")
APP_STATIC = Path(__file__).resolve().parent / "static" / "app"
MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
        ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".txt": "text/plain; charset=utf-8"}
IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.S)
UNSAFE_METHODS = ("POST", "PUT", "DELETE", "PATCH")
CSRF_HEADER = "X-Hal"     # a cross-site page cannot add a custom header without CORS, which is never granted
# Where a browser helper from before pairing (0.4 or 0.5) asks for work. It may write here only to be refused.
OLD_HELPER_PREFIX = "/api/ext/"
HELPER_PATHS = (router.PREFIX + "/helpers/", OLD_HELPER_PREFIX)   # where a browser extension's origin may write
OLD_HELPER_GONE = ("This browser helper is too old for this Hal. Remove it on your browser's extensions page, "
                   "then load the new one in the Browser helper step of Hal's setup guide.")
DOC_PATH = re.compile(r"/doc/([^/]+)/(cv|letter)")
HEALTH_TIMEOUT_S = 2
LISTEN_BACKLOG = 64        # waiting connections; see AppServer
STOP_WAIT_S = 10           # an event stream ends within its wake-up time; other replies are quicker still
CONNECT_TIMEOUT_S = 0.5    # a loopback connect to a listening port is instant; Windows needs about 2 s to refuse
GIT_TIMEOUT_S = 5
LOG_BYTES = 2 * 1024 * 1024        # 5 files of 2 MB
LOG_COUNT = 5
LOCK_FILE = "server.lock"

LINK_RAN_OUT = "This link has run out. Open Hal Employment from its shortcut."
LINK_RAN_OUT_REMOTE = "This link has run out. Run the launcher on this computer again."
FROM_SHORTCUT = "For your safety, Hal opens from its shortcut."

log = logging.getLogger(__name__)
_commit: list[str | None] = []


def _page(title: str, text: str, *, extra_head: str = "") -> str:
    return (f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8">{extra_head}<title>{title}</title>'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            f'<body style="font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 36rem">'
            f"<h1>{title}</h1><p>{text}</p></body></html>")


def commit() -> str | None:
    """The checkout's commit, read once with git; None when the code is not a git checkout."""
    if not _commit:
        try:
            done = subprocess.run(["git", "-C", str(paths.code_dir()), "rev-parse", "--short", "HEAD"],
                                  capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False)
            _commit.append(done.stdout.strip() or None if done.returncode == 0 else None)
        except (OSError, subprocess.TimeoutExpired):
            _commit.append(None)
    return _commit[0]


def csp(index_html: str) -> str:
    """The app page's Content-Security-Policy, with the hash of its inline import map."""
    m = IMPORT_MAP.search(index_html)
    script = "'self'"
    if m:
        digest = base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode("ascii")
        script += f" 'sha256-{digest}'"
    return (f"default-src 'self'; script-src {script}; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")


def servable() -> tuple[Path, ...]:
    """The folders `/files` may serve from: the application folders, old and new."""
    return tuple(p.resolve() for p in (paths.applications_dir(), paths.legacy_packs_dir()))


class AppHandler(BaseHTTPRequestHandler):
    """One request: the checks, then the route that answers it."""

    server_version = "HalEmployment/2"
    session: auth.Session | None = None
    local: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:
        """Keeps the log to errors only; `_handle` logs those with their traceback."""

    # replies

    def end_headers(self) -> None:
        """Every reply says not to send this address to other sites."""
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _reply_bytes(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        # A browser that closes the page mid-reply is normal: Linux says BrokenPipe or ConnectionReset,
        # Windows says ConnectionAborted (WinError 10053) or ConnectionReset (10054); all are ConnectionError.
        with contextlib.suppress(ConnectionError):
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    def send_json(self, data: Any, status: int = 200) -> None:
        """A JSON reply."""
        self._reply_bytes(status, json.dumps(data, default=str).encode("utf-8"), "application/json")

    def send_html(self, text: str, status: int = 200, *, policy: str | None = None) -> None:
        """An HTML reply, with its own Content-Security-Policy."""
        self._reply_bytes(status, text.encode("utf-8"), "text/html; charset=utf-8",
                          {"Content-Security-Policy": policy or "default-src 'none'; style-src 'unsafe-inline'"})

    def reply(self, result: Any) -> None:
        """Sends what a route handler returned."""
        if result is None:
            return self.send_json({"ok": True})
        if isinstance(result, (dict, list)):
            return self.send_json(result)
        if isinstance(result, router.Redirect):
            self.send_response(302)
            self.send_header("Location", result.location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            for c in result.cookies:
                self.send_header("Set-Cookie", c)
            return self.end_headers()
        if isinstance(result, router.HtmlResponse):
            return self.send_html(result.text, result.status, policy=result.policy)
        if isinstance(result, router.Stream):
            return result.writer(self)
        if isinstance(result, (router.FileResponse, router.BytesResponse)):
            data = result.path.read_bytes() if isinstance(result, router.FileResponse) else result.data
            how = "attachment" if result.download else "inline"
            name = result.name.replace('"', "")
            return self._reply_bytes(200, data, result.mime, {"Content-Disposition": f'{how}; filename="{name}"'})
        raise TypeError(f"a route returned {type(result).__name__}, which the server cannot send")

    # methods

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    # checks

    def _write_allowed(self, path: str) -> bool:
        """Writes need `X-Hal: 1` and, when an Origin is sent, this server's own origin (or a helper's)."""
        if self.headers.get(CSRF_HEADER) != "1":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin == f"http://{self.headers.get('Host', '')}":
            return True
        # a paired helper's key decides whether it may write; a helper from before pairing is only refused
        return origin.startswith("chrome-extension://") and path.startswith(HELPER_PATHS)

    def _who(self) -> None:
        cookies = auth.read_cookies(self.headers.get("Cookie"))
        self.local = auth.is_local_key(self.headers.get("Authorization"))
        value = cookies.get(auth.session_cookie_name())
        self.session = None
        if value:
            con = db.connect()
            try:
                self.session = auth.session_for(con, value)
            finally:
                con.close()

    def _handle(self, method: str) -> None:
        try:
            self._route(method)
        except ConnectionError:
            return  # the browser went away; there is no one to send the error to
        except Exception:
            log.exception("%s %s failed", method, self.path)
            with contextlib.suppress(OSError):
                self.send_json({"error": {"code": "error", "message": router.UNEXPECTED}}, 500)

    def _route(self, method: str) -> None:
        if not self._host_ok():
            return self._reply_bytes(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        path = url.path
        if method == "GET" and path == "/healthz":
            return self.send_json({"ok": True, "version": __version__, "commit": commit()})
        if method == "GET" and path == "/open":
            return self._open(parse_qs(url.query))
        if method == "GET" and path == "/pair":
            return self._pair()
        if method in UNSAFE_METHODS and not self._write_allowed(path):
            return self._reply_bytes(403, b"forbidden", "text/plain")
        if path.startswith(router.PREFIX + "/"):
            self._who()
            if not router.dispatch(self, method):
                self.send_json({"error": {"code": "not_found", "message": "There is nothing here."}}, 404)
            return None
        if path.startswith(OLD_HELPER_PREFIX):
            return self._old_helper(path)
        if method == "GET" and (path == "/files" or DOC_PATH.fullmatch(path)):
            return self._document(path, parse_qs(url.query))
        if method == "GET" and not path.startswith("/api/"):
            return self._static(path)
        return self._reply_bytes(404, b"not found", "text/plain")

    def _host_ok(self) -> bool:
        """Refuses DNS-rebinding requests, which reach the port under another name."""
        return (self.headers.get("Host") or "").split(":")[0] in HOSTS

    # routes outside the router

    def _open(self, query: dict[str, list[str]]) -> None:
        token = (query.get("t") or [""])[0]
        con = db.connect()
        try:
            row = auth.consume_open_token(con, token) if token else None
            if row is None:
                remote = bool(token) and auth.token_was_remote(con, token)
                return self.send_html(_page("Hal Employment", LINK_RAN_OUT_REMOTE if remote else LINK_RAN_OUT), 410)
            cookies = auth.read_cookies(self.headers.get("Cookie"))
            known = cookies.get(auth.browser_cookie_name())
            value, browser_id, _ = auth.create_session(con, remote=bool(row["remote"]), remote_host=row["remote_host"],
                                                       browser_id=known, user_agent=self.headers.get("User-Agent"))
            from ..core import routes as core_routes

            target = row["next"] or ("/" if core_routes.setup_done(con) else "/#setup")
        finally:
            con.close()
        self.reply(router.Redirect(target, auth.cookies_for(value, browser_id, new_browser=not known)))

    def _pair(self) -> None:
        meta = f'<meta name="hal-app" content="{auth.install_id()}">'
        self.send_html(_page("Connecting the browser helper",
                             "Hal is connecting the browser helper in this browser. You can close this tab when "
                             "Hal says it is connected.", extra_head=meta))

    def _signed_in(self) -> bool:
        self._who()
        return self.session is not None or self.local

    def _old_helper(self, path: str) -> None:
        """Refuses a helper from before pairing, in words that tell the person to load the new one.

        That helper reads a poll's reply as JSON and polls again at once when the reply holds no command, so
        the poll's refusal is plain text, which makes it wait 5 s first. Its other requests show the reply's
        `error` on its toolbar button.
        """
        if path == OLD_HELPER_PREFIX + "poll":
            return self._reply_bytes(HTTPStatus.GONE, OLD_HELPER_GONE.encode("utf-8"), "text/plain; charset=utf-8")
        return self.send_json({"error": OLD_HELPER_GONE}, HTTPStatus.GONE)

    def _document(self, path: str, query: dict[str, list[str]]) -> None:
        """An application's file by its path (`/files`), or its CV or letter under the name employers see."""
        if not self._signed_in():
            return self.send_json({"error": FROM_SHORTCUT}, 401)
        m = DOC_PATH.fullmatch(path)
        if m is None:
            target = Path(unquote((query.get("path") or [""])[0])).resolve()
            if not any(root in target.parents for root in servable()) or not target.is_file():
                return self._reply_bytes(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return self._reply_bytes(200, target.read_bytes(), ctype)
        from .. import documents
        from ..apply.queries import _job

        job_id, which = unquote(m.group(1)), m.group(2)
        con = db.connect()
        try:
            try:
                found = documents.files(_job(con, job_id)).get(which)
            except LookupError:
                found = None
        finally:
            con.close()
        if not found:
            return self._reply_bytes(404, f"no {which} for this job yet".encode(), "text/plain")
        # a saved copy carries the name employers see, never the name the file has in its folder
        self._reply_bytes(200, Path(found).read_bytes(), "application/pdf",
                          {"Content-Disposition": f'inline; filename="{naming.file_name(which)}"'})

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            index = APP_STATIC / "index.html"
            if not index.is_file():
                return self.send_html(_page("Hal Employment", "The new screens are not installed in this copy."))
            text = index.read_text(encoding="utf-8")
            return self.send_html(text, policy=csp(text))
        if not path.startswith("/app/"):
            return self._reply_bytes(404, b"not found", "text/plain")
        target = (APP_STATIC / path.removeprefix("/app/")).resolve()
        if APP_STATIC.resolve() not in target.parents or not target.is_file():
            return self._reply_bytes(404, b"not found", "text/plain")
        ctype = MIME.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._reply_bytes(200, target.read_bytes(), ctype)


class AppServer(ThreadingHTTPServer):
    """The HTTP server, on `BIND` only, one thread per request."""

    daemon_threads = True
    # socketserver's default backlog is 5; a page opening its modules, styles and event stream at once can
    # need more, and Windows refuses the extra connections outright (Chromium: ERR_CONNECTION_REFUSED)
    request_queue_size = LISTEN_BACKLOG

    def __init__(self, port: int) -> None:
        super().__init__((BIND, port), AppHandler)
        self._in_flight: set[threading.Thread] = set()
        self._in_flight_lock = threading.Lock()

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Handles one request on its own thread, remembered until it ends so `stop` can wait for it."""
        me = threading.current_thread()
        with self._in_flight_lock:
            self._in_flight.add(me)
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._in_flight_lock:
                self._in_flight.discard(me)

    def stop(self, timeout: float = STOP_WAIT_S) -> bool:
        """Stops serving, ends the event streams and waits for requests in flight; True when every one ended.

        A request thread holds its database connection until it ends, and Windows cannot move or remove an
        open file, so a restart or a test's clean-up waits here rather than racing the last replies.
        """
        self.shutdown()
        sse.end_all()
        deadline = time.monotonic() + timeout
        with self._in_flight_lock:
            threads = list(self._in_flight)
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        self.server_close()
        return not any(t.is_alive() for t in threads)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that disconnected is not an error; anything else is logged with its traceback."""
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        log.exception("a request from %s failed", client_address)


def load_routes() -> None:
    """Imports every area's routes module, so their `@route` handlers are registered."""
    plugins.modules("routes")


def make_server(port: int) -> AppServer:
    """A server ready to `serve_forever`, with every route loaded; port 0 picks a free one (tests)."""
    load_routes()
    return AppServer(port)


def _hold_lock() -> Any | None:
    """Takes state/server.lock without waiting; returns the open file, or None when another server holds it."""
    file = open(paths.state_dir() / LOCK_FILE, "a+")
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


def answering(port: int) -> bool:
    """True when a Hal Employment server answers /healthz on `port`.

    The connection gets `CONNECT_TIMEOUT_S` and the answer `HEALTH_TIMEOUT_S`: Windows reports a closed port
    only after its connection retries, so one timeout for both made every "is Hal running?" cost 2 s there.
    """
    try:
        sock = socket.create_connection((BIND, port), timeout=CONNECT_TIMEOUT_S)
    except OSError:
        return False
    con = http.client.HTTPConnection(BIND, port, timeout=HEALTH_TIMEOUT_S)
    try:
        sock.settimeout(HEALTH_TIMEOUT_S)
        con.sock = sock
        con.request("GET", "/healthz", headers={"Host": f"{BIND}:{port}"})
        reply = con.getresponse()
        return reply.status == HTTPStatus.OK and bool(json.loads(reply.read()).get("ok"))
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        con.close()


def _logging(background: bool) -> None:
    handler = logging.handlers.RotatingFileHandler(paths.logs_dir() / "server.log", maxBytes=LOG_BYTES,
                                                   backupCount=LOG_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if not background:
        root.addHandler(logging.StreamHandler(sys.stderr))
    root.setLevel(logging.INFO)


def serve(port: int | None = None, open_browser: bool = False, background: bool = False, *,
          worker: bool = True) -> int:
    """Runs the server until interrupted; returns the exit code (0 also when one is already running)."""
    paths.ensure()
    port = port or auth.server_port()
    lock = _hold_lock()
    if lock is None:
        if answering(port):
            print("Hal Employment is already running", flush=True)
            return 0
        print("Another Hal Employment is starting. Try again in a minute.", flush=True)
        return 1
    _logging(background)
    db.connect().close()   # migrates before anything else reads the database
    httpd = make_server(port)
    failed = plugins.run_start_tasks()
    if failed:
        log.warning("start tasks failed: %s", ", ".join(failed))
    if worker:
        from ..core.worker import worker as queue_worker

        threading.Thread(target=queue_worker, name="worker", daemon=True).start()
    stop = threading.Event()
    sse.DbPoller(db.connect, stop).start()
    url = f"http://{BIND}:{port}/"
    print(f"Hal Employment on {url}", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(auth.mint_open_url(port=port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        sse.STOPPING.set()
        httpd.server_close()
        lock.close()
    return 0
