"""The new server: one process on 127.0.0.1 serving the new app, API v2, the event stream and Classic.

`AppHandler` subclasses the legacy `server.Handler`, so every Classic route keeps its exact code. Each
request goes through, in order: the Host check, `/healthz`, `/open`, `/pair`, the CSRF check for writes,
the session or local key, `/api/v2/*` through the router (the event stream is a route), `/classic/*`, the
legacy `/api/*`, `/files`, `/pages` and `/doc/*`, then the new app's files.

The address is never a setting: Hal listens on `BIND` only, and over SSH the tunnel reaches it there.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import __version__, db
from ..core import paths, plugins
from . import auth, router, server, sse

BIND = "127.0.0.1"
HOSTS = ("127.0.0.1", "localhost")
APP_STATIC = Path(__file__).resolve().parent / "static" / "app"
MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
        ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".txt": "text/plain; charset=utf-8"}
IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.S)
UNSAFE_METHODS = ("POST", "PUT", "DELETE", "PATCH")
HELPER_PATHS = ("/api/v2/helpers/", "/api/ext/")   # where a browser extension's origin may write
LEGACY_PREFIXES = ("/api/", "/doc/")
LEGACY_PATHS = ("/files", "/pages")
HEALTH_TIMEOUT_S = 2
GIT_TIMEOUT_S = 5
LOG_BYTES = 2 * 1024 * 1024        # 5 files of 2 MB
LOG_COUNT = 5
LOCK_FILE = "server.lock"

LINK_RAN_OUT = "This link has run out. Open Hal Employment from its shortcut."
LINK_RAN_OUT_REMOTE = "This link has run out. Run the launcher on this computer again."
FROM_SHORTCUT = "For your safety, Hal opens from its shortcut."
CLASSIC_OFF = "The classic screens are switched off. Turn them on in Settings, Advanced."

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


def setting(key: str, fallback: Any) -> Any:
    """A live setting, or `fallback` while no code registers `key` yet."""
    from ..core import settings

    try:
        return settings.get(key)
    except KeyError:
        return fallback


def classic_on() -> bool:
    """Classic is served when `advanced.classic` is on, or in developer mode (the owner's way back)."""
    return bool(setting("advanced.classic", False) or setting("advanced.developer_mode", False))


def csp(index_html: str) -> str:
    """The app page's Content-Security-Policy, with the hash of its inline import map."""
    m = IMPORT_MAP.search(index_html)
    script = "'self'"
    if m:
        digest = base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode("ascii")
        script += f" 'sha256-{digest}'"
    return (f"default-src 'self'; script-src {script}; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")


class AppHandler(server.Handler):
    """The legacy handler with the new server's checks and routes in front of it."""

    server_version = "HalEmployment/2"
    session: auth.Session | None = None
    local: bool = False

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
        self.end_headers()
        if self.command != "HEAD":
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
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
        if self.headers.get(server.CSRF_HEADER) != "1":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin == f"http://{self.headers.get('Host', '')}":
            return True
        # a helper's own checks (its key, or the old-helper setting) decide whether it may write
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
        if path == "/classic" or path.startswith("/classic/"):
            return self._classic(method, path)
        if path.startswith(LEGACY_PREFIXES) or path in LEGACY_PATHS:
            return self._legacy(method, path)
        if method == "GET":
            return self._static(path)
        return self._reply_bytes(404, b"not found", "text/plain")

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

    def _classic(self, method: str, path: str) -> None:
        self._who()
        if self.session is None and not self.local:
            return self.send_html(_page("Hal Employment", FROM_SHORTCUT), 401)
        if not classic_on():
            return self.send_html(_page("Hal Employment", CLASSIC_OFF), 404)
        if path == "/classic":
            return self.reply(router.Redirect("/classic/"))
        name = path.removeprefix("/classic/") or "index.html"
        target = (server.STATIC / name).resolve()
        if server.STATIC.resolve() not in target.parents or not target.is_file():
            return self._reply_bytes(404, b"not found", "text/plain")
        ctype = MIME.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._reply_bytes(200, target.read_bytes(), ctype)

    def _old_helper(self, path: str) -> bool:
        """The 0.5 helper polls /api/ext/* with `X-Hal: 1` and no key, accepted only while the setting allows it."""
        return path.startswith("/api/ext/") and bool(setting("advanced.allow_old_helper", False))

    def _legacy(self, method: str, path: str) -> None:
        self._who()
        if self.session is None and not self.local and not self._old_helper(path):
            return self.send_json({"error": FROM_SHORTCUT}, 401)
        if method == "GET":
            return super().do_GET()
        if method == "POST":
            return super().do_POST()
        return self._reply_bytes(405, b"method not allowed", "text/plain")

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

    def __init__(self, port: int) -> None:
        super().__init__((BIND, port), AppHandler)


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
    """True when a Hal Employment server answers /healthz on `port`."""
    try:
        with urllib.request.urlopen(f"http://{BIND}:{port}/healthz", timeout=HEALTH_TIMEOUT_S) as r:
            return bool(json.loads(r.read()).get("ok"))
    except (OSError, ValueError):
        return False


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
        from ..core.worker import worker as legacy_worker

        threading.Thread(target=legacy_worker, name="worker", daemon=True).start()
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
