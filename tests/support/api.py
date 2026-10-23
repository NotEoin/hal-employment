"""A real Hal server for tests, in its own temporary home, and a client that signs in the way a window does.

`with HalServer() as hal:` starts the new server on 127.0.0.1 and a free port, in a fresh
`HAL_EMPLOYMENT_HOME`, without the queue worker. `hal.client()` is signed out; `hal.signed_in()` swaps a
one-time open link for a session cookie first. The client sends `X-Hal: 1` on writes unless told not to,
and lets a test set any header (Host, Origin) to check the server's refusals.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import tempfile
import threading
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jobhunt.core import paths
from jobhunt.core.settings import store
from jobhunt.web import app, auth

from .actions import wait_for_actions

TIMEOUT_S = 10   # every request in these tests answers at once; a hang fails the test instead


@dataclass
class Reply:
    """A response, read in full."""

    status: int
    headers: http.client.HTTPMessage
    body: bytes

    def json(self) -> Any:
        """The body as JSON."""
        return json.loads(self.body)

    @property
    def text(self) -> str:
        """The body as text."""
        return self.body.decode("utf-8", "replace")

    def cookies(self) -> dict[str, str]:
        """Every Set-Cookie header, by cookie name, as sent."""
        out = {}
        for raw in self.headers.get_all("Set-Cookie") or []:
            out[raw.split("=", 1)[0]] = raw
        return out


class Client:
    """Talks to one server, keeping the cookies it is given."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.jar: dict[str, str] = {}

    def request(self, method: str, path: str, body: Any = None, *, headers: dict[str, str] | None = None,
                csrf: bool = True) -> Reply:
        """Sends one request; JSON bodies are encoded and writes carry `X-Hal: 1` unless `csrf` is False."""
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=TIMEOUT_S)
        sent = {"Host": f"127.0.0.1:{self.port}"}
        if self.jar:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            sent["Content-Type"] = "application/json"
        if csrf and method in ("POST", "PUT", "DELETE"):
            sent["X-Hal"] = "1"
        sent.update(headers or {})
        try:
            con.request(method, path, body=data, headers=sent)
            r = con.getresponse()
            reply = Reply(r.status, r.headers, r.read())
        finally:
            con.close()
        for raw in reply.headers.get_all("Set-Cookie") or []:
            jar = SimpleCookie()
            jar.load(raw)
            self.jar.update({k: m.value for k, m in jar.items()})
        return reply

    def get(self, path: str, **kw) -> Reply:
        """GET `path`."""
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw) -> Reply:
        """POST `path` with a JSON body."""
        return self.request("POST", path, {} if body is None else body, **kw)


class HalServer:
    """The new server in a temporary home, on a free port, for the length of a `with` block."""

    def __init__(self) -> None:
        self._stack = contextlib.ExitStack()

    def __enter__(self) -> HalServer:
        tmp = self._stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-api-"))
        self.home = Path(tmp).resolve()
        self._stack.enter_context(paths.set_override(self.home))
        # runs after the server stops and before the home goes: Windows cannot remove a file an action holds open
        self._stack.callback(wait_for_actions)
        store.drop_cache()
        self._stack.callback(store.drop_cache)
        paths.ensure()
        self.httpd = app.make_server(0)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, name="test-server", daemon=True)
        thread.start()
        self._stack.callback(self.httpd.stop)
        return self

    def __exit__(self, *exc) -> None:
        self._stack.close()

    def open_url(self, **kw) -> str:
        """A one-time open link for this server."""
        return auth.mint_open_url(port=self.port, **kw)

    def client(self) -> Client:
        """A client with no session."""
        return Client(self.port)

    def signed_in(self, **kw) -> Client:
        """A client that opened a link and holds the session cookie it was given."""
        c = self.client()
        url = urlparse(self.open_url(**kw))
        reply = c.get(f"{url.path}?{url.query}")
        if reply.status != 302:
            raise AssertionError(f"the open link answered {reply.status}: {reply.text}")
        return c
