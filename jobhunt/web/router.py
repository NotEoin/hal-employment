"""API v2 routing: handlers register with `@route`, `dispatch` finds one, checks its auth and shapes its reply.

A handler takes a `Request` and returns a dict or list (sent as JSON), None (`{"ok": true}`) or one of the
response types below. Errors become `{"error": {"code", "message", "field", "fix"}}` with the status of
its code; an unexpected error is logged with its traceback and recorded in Activity, and the person sees
plain words only. Handlers never do slow work: anything that may take over a second starts an action.
"""
from __future__ import annotations

import email.parser
import email.policy
import json
import logging
import re
import sqlite3
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

if TYPE_CHECKING:
    from .app import AppHandler
    from .auth import Helper, Session

Auth = Literal["session", "helper", "session_or_helper", "local", "session_or_local", "pairing", "none"]
AUTH_KINDS = ("session", "helper", "session_or_helper", "local", "session_or_local", "pairing", "none")
MB = 1024 * 1024
MAX_BODY = 30 * MB                 # CV and record uploads stay well under this; a route may raise it
PREFIX = "/api/v2"
UNEXPECTED = "Something went wrong. Hal kept the details in Activity."

log = logging.getLogger(__name__)


class HTTPError(Exception):
    """An error with a status and plain words for the person."""

    status = 500
    code = "error"

    def __init__(self, message: str, *, field: str | None = None, fix: dict | None = None) -> None:
        super().__init__(message)
        self.message, self.field, self.fix = message, field, fix


class BadRequest(HTTPError):
    """The request cannot be used (400)."""

    status = 400
    code = "invalid"


class Unauthorized(HTTPError):
    """No session or key (401): the app shows "For your safety, Hal opens from its shortcut"."""

    status = 401
    code = "signed_out"


class Forbidden(HTTPError):
    """Not allowed (403)."""

    status = 403
    code = "forbidden"


class NotFound(HTTPError):
    """No such thing (404)."""

    status = 404
    code = "not_found"


class Conflict(HTTPError):
    """Not now (409)."""

    status = 409
    code = "conflict"


class NotHere(Conflict):
    """Only on the computer Hal runs on (409, code "remote")."""

    code = "remote"


@dataclass
class Upload:
    """One uploaded file from a multipart form."""

    name: str
    mime: str
    data: bytes


@dataclass
class FileResponse:
    """A file from disk."""

    path: Path
    name: str
    mime: str
    download: bool = False


@dataclass
class BytesResponse:
    """Bytes made in memory."""

    data: bytes
    name: str
    mime: str
    download: bool = False


@dataclass
class HtmlResponse:
    """A small HTML page; `policy` replaces the default Content-Security-Policy, which blocks fonts and images."""

    text: str
    status: int = 200
    policy: str | None = None


@dataclass
class Redirect:
    """A 302 to a path on this server, with cookies to set."""

    location: str
    cookies: list[str] = field(default_factory=list)


@dataclass
class Stream:
    """A reply that writes itself (the event stream)."""

    writer: Callable[[AppHandler], None]


@dataclass
class Request:
    """What a handler gets. `con` opens one database connection on first use; it is closed after the reply."""

    method: str
    path: str
    params: dict[str, str]
    query: dict[str, str]
    body: Any
    files: dict[str, Upload]
    session: Session | None
    helper: Helper | None
    local: bool
    _con: sqlite3.Connection | None = None

    @property
    def con(self) -> sqlite3.Connection:
        """This request's database connection."""
        if self._con is None:
            from .. import db

            self._con = db.connect()
        return self._con

    @property
    def remote(self) -> bool:
        """True when the person is on another computer (an SSH tunnel)."""
        return bool((self.session and self.session.remote) or (self.helper and self.helper.remote))

    def close(self) -> None:
        """Closes the connection, if one was opened."""
        if self._con is not None:
            self._con.close()
            self._con = None


@dataclass(frozen=True)
class Route:
    """One registered route."""

    method: str
    pattern: str
    regex: re.Pattern
    fn: Callable[[Request], Any]
    auth: str
    local_only: bool
    slow: bool
    max_body: int = MAX_BODY


ROUTES: dict[tuple[str, str], Route] = {}
_PARAM = re.compile(r"\{(\w+)\}")


def _compile(pattern: str) -> re.Pattern:
    parts, last = [], 0
    for m in _PARAM.finditer(pattern):
        parts += [re.escape(pattern[last:m.start()]), f"(?P<{m.group(1)}>[^/]+)"]
        last = m.end()
    parts.append(re.escape(pattern[last:]))
    return re.compile("".join(parts) + r"\Z")


def route(method: str, pattern: str, *, auth: Auth = "session", local_only: bool = False,
          slow: bool = False, max_body: int = MAX_BODY) -> Callable:
    """Registers a handler for `method` and `pattern` ("/api/v2/jobs/{job_id}/save"); a duplicate raises.

    `max_body` raises the request size cap for this route alone, for uploads larger than a CV.
    """
    method = method.upper()
    if auth not in AUTH_KINDS:
        raise ValueError(f"unknown auth {auth!r} for {method} {pattern}")

    def wrap(fn):
        key = (method, pattern)
        if key in ROUTES:
            raise ValueError(f"{method} {pattern} is registered twice ({ROUTES[key].fn.__module__}, {fn.__module__})")
        ROUTES[key] = Route(method, pattern, _compile(pattern), fn, auth, local_only, slow, max_body)
        return fn
    return wrap


def find(method: str, path: str) -> tuple[Route, dict[str, str]] | None:
    """The route matching `method` and `path`, with its unquoted params, or None."""
    for r in ROUTES.values():
        if r.method != method:
            continue
        m = r.regex.match(path)
        if m:
            return r, {k: unquote(v) for k, v in m.groupdict().items()}
    return None


def _multipart(ctype: str, raw: bytes) -> tuple[dict, dict[str, Upload]]:
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        f"Content-Type: {ctype}\r\n\r\n".encode() + raw)
    fields: dict[str, str] = {}
    files: dict[str, Upload] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            files[name] = Upload(filename, part.get_content_type(), data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def _read_body(handler: AppHandler, limit: int = MAX_BODY) -> tuple[Any, dict[str, Upload]]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError as e:
        raise BadRequest("The request's length cannot be read.") from e
    if length > limit:
        raise BadRequest(f"That file is too big. Files up to {limit // MB} MB can be added.")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return None, {}
    ctype = handler.headers.get("Content-Type") or ""
    if ctype.startswith("multipart/form-data"):
        return _multipart(ctype, raw)
    try:
        return json.loads(raw), {}
    except json.JSONDecodeError as e:
        raise BadRequest("The request is not valid JSON.") from e


def _allowed(r: Route, req: Request, handler: AppHandler) -> None:
    from . import auth

    if r.auth == "none":
        return
    if r.auth == "pairing":
        if not auth.verify("pairing", handler):
            raise Forbidden("This pairing code is not valid any more.")
        return
    if r.auth in ("helper", "session_or_helper"):
        req.helper = auth.verify("helper", handler) or None
    ok = {"session": req.session is not None, "helper": req.helper is not None,
          "session_or_helper": req.session is not None or req.helper is not None, "local": req.local,
          "session_or_local": req.session is not None or req.local}[r.auth]
    if not ok:
        raise Unauthorized("For your safety, Hal opens from its shortcut.")
    if r.local_only and req.remote:
        host = (req.session.remote_host if req.session else None) or "the computer Hal runs on"
        raise NotHere(f"Do this on {host}, the computer Hal runs on.")


def _error_body(e: HTTPError) -> dict:
    out: dict[str, Any] = {"code": e.code, "message": e.message}
    if e.field:
        out["field"] = e.field
    if e.fix:
        out["fix"] = e.fix
    return {"error": out}


def _as_http_error(e: Exception) -> HTTPError | None:
    """Today's mapping of plain exceptions, plus `SettingError` as a field problem."""
    key = getattr(e, "key", None)
    if isinstance(e, ValueError) and type(e).__name__ == "SettingError" and key:
        return BadRequest(str(e), field=key)
    if isinstance(e, LookupError):
        return NotFound(str(e).strip("'\""))
    if isinstance(e, PermissionError):
        return Forbidden(str(e))
    if isinstance(e, (RuntimeError, ValueError)):
        return Conflict(str(e))
    return None


def _record_failure(req: Request, e: Exception) -> None:
    """Logs the traceback and adds an Activity row in area system, when the activity table exists."""
    log.error("%s %s failed\n%s", req.method, req.path, traceback.format_exc())
    try:
        from ..core import clock

        req.con.execute("""INSERT INTO activity (at, area, kind, icon, tone, text, why, detail, session_id)
                           VALUES (?, 'system', 'system.error', 'warn', 'bad', ?, ?, ?, ?)""",
                        (clock.iso(), "Something went wrong on a page", f"{req.method} {req.path} failed",
                         f"{type(e).__name__}", req.session.id if req.session else None))
        req.con.commit()
    except sqlite3.Error:
        log.exception("could not record the failure in Activity")


def dispatch(handler: AppHandler, method: str) -> bool:
    """Answers the request if a route matches its path; False when none does."""
    url = urlparse(handler.path)
    found = find(method, url.path)
    if found is None:
        return False
    r, params = found
    query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
    req = Request(method=method, path=url.path, params=params, query=query, body=None, files={},
                  session=handler.session, helper=None, local=handler.local)
    try:
        _allowed(r, req, handler)
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            req.body, req.files = _read_body(handler, r.max_body)
        result = r.fn(req)
        try:
            handler.reply(result)
        except ConnectionError:
            return True  # the browser closed the page before the reply went; not a failure to record
    except HTTPError as e:
        handler.send_json(_error_body(e), e.status)
    except Exception as e:
        mapped = _as_http_error(e)
        if mapped is None:
            _record_failure(req, e)
            mapped = HTTPError(UNEXPECTED)
        handler.send_json(_error_body(mapped), mapped.status)
    finally:
        req.close()
    return True
