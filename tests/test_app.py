"""The new server (web/app.py): a browser that closes the page mid-reply is not an error, `stop` waits for the
requests in flight, and the health check gives a closed port only a short time.

Regression: on Windows a closed page raises ConnectionAbortedError (WinError 10053), which the replies did not
catch, so every early close printed a traceback and the handler then tried to write a 500 to the dead socket.
"""
import contextlib
import http.client
import io
import logging
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.core import paths
from jobhunt.web import app
from jobhunt.web.router import Request, route

GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
WAIT_S = 10       # every wait here ends in well under a second; this bounds a broken server
HOLD_S = 0.3      # how long the slow route keeps its request in flight
BURST = 24        # about what a page opens at once: its modules, styles, data and event stream
CONNECT_S = 2     # a connection the backlog has room for is made at once
STATIC = "/app/core/store.js"   # one of the app's own files
ENTERED, RELEASE = threading.Event(), threading.Event()


@route("GET", "/api/v2/test-app/slow", auth="none")
def _slow(req: Request) -> dict:
    ENTERED.set()
    RELEASE.wait(WAIT_S)
    return {"ok": True}


class Gone(io.RawIOBase):
    """A socket file whose browser has gone: every write raises `error`."""

    def __init__(self, error: type[OSError]) -> None:
        self.error = error

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        raise self.error("the browser closed the page")


def handler(cls, error: type[OSError]):
    """A request handler with no socket, writing to a `Gone` file."""
    h = cls.__new__(cls)
    h.wfile = Gone(error)
    h.request_version = "HTTP/1.1"
    h.requestline = "GET / HTTP/1.1"
    h.command = "GET"
    h.client_address = ("127.0.0.1", 0)
    h.close_connection = False
    return h


class ClosedPage(unittest.TestCase):
    def test_a_reply_to_a_closed_page_is_dropped_quietly(self):
        for error in GONE:
            with self.subTest(error=error.__name__):
                handler(app.AppHandler, error).send_json({"ok": True})

    def test_the_server_logs_no_error_when_a_browser_disconnects(self):
        for error in GONE:
            with self.subTest(error=error.__name__), self.assertNoLogs("jobhunt.web.app", logging.ERROR), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    raise error("the browser closed the page")
                except OSError:
                    app.AppServer.handle_error(None, None, ("127.0.0.1", 0))
                self.assertEqual(err.getvalue(), "")

    def test_any_other_failure_is_logged(self):
        with self.assertLogs("jobhunt.web.app", logging.ERROR) as logs:
            try:
                raise ZeroDivisionError("a made-up bug")
            except ZeroDivisionError:
                app.AppServer.handle_error(None, None, ("127.0.0.1", 0))
        self.assertIn("ZeroDivisionError", logs.output[0])


class Serving(unittest.TestCase):
    def setUp(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="hal-app-")))
        self.enterContext(paths.set_override(home))
        paths.ensure()
        self.httpd = app.AppServer(0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="test-app-server", daemon=True).start()
        self.addCleanup(self.httpd.stop)   # a second stop does nothing

    def get(self, path: str, out: list) -> None:
        con = http.client.HTTPConnection(app.BIND, self.port, timeout=WAIT_S)
        try:
            con.request("GET", path, headers={"Host": f"{app.BIND}:{self.port}"})
            out.append(con.getresponse().status)
        finally:
            con.close()

    def test_a_burst_of_connections_waits_its_turn(self):
        # regression: with socketserver's backlog of 5, Windows refused part of a page's first burst of requests
        self.httpd.shutdown()          # nothing accepts, so every connection waits in the backlog
        socks = []
        try:
            for _ in range(BURST):
                socks.append(socket.create_connection((app.BIND, self.port), timeout=CONNECT_S))
        finally:
            for s in socks:
                s.close()
        self.assertEqual(len(socks), BURST)

    def test_stop_waits_for_the_requests_in_flight(self):
        # regression: a request thread still held the database when a test removed its home on Windows
        ENTERED.clear()
        RELEASE.clear()
        got: list[int] = []
        client = threading.Thread(target=self.get, args=("/api/v2/test-app/slow", got))
        client.start()
        self.assertTrue(ENTERED.wait(WAIT_S))
        threading.Timer(HOLD_S, RELEASE.set).start()
        started = time.monotonic()
        self.assertTrue(self.httpd.stop())
        self.assertGreaterEqual(time.monotonic() - started, HOLD_S / 2)
        client.join(WAIT_S)
        self.assertEqual(got, [200])

    def connection(self) -> http.client.HTTPConnection:
        con = http.client.HTTPConnection(app.BIND, self.port, timeout=WAIT_S)
        self.addCleanup(con.close)
        return con

    def ask(self, con: http.client.HTTPConnection, method: str, path: str, **headers: str):
        con.request(method, path, headers={"Host": f"{app.BIND}:{self.port}", **headers})
        r = con.getresponse()
        return r, r.read()

    # Regression (CI run 44372137723): one connection per request, about a hundred per page load, used up
    # Windows' ports in the browser tests (net::ERR_NO_BUFFER_SPACE)
    def test_one_connection_carries_a_pages_requests(self):
        con = self.connection()
        for _ in range(3):
            r, _ = self.ask(con, "GET", STATIC)
            self.assertEqual(r.status, 200)
            self.assertIsNotNone(con.sock, "the server closed the connection after one reply")

    def test_an_unchanged_app_file_is_answered_without_its_body(self):
        con = self.connection()
        r, body = self.ask(con, "GET", STATIC)
        self.assertEqual(r.getheader("Cache-Control"), app.STATIC_CACHE)
        self.assertTrue(body)
        again, body = self.ask(con, "GET", STATIC, **{"If-None-Match": r.getheader("ETag")})
        self.assertEqual((again.status, body), (304, b""))
        self.assertEqual(self.ask(con, "GET", STATIC, **{"If-None-Match": '"older"'})[0].status, 200)

    def test_a_refused_write_closes_its_connection_so_its_body_is_never_read_as_a_request(self):
        con = self.connection()
        con.request("POST", "/api/v2/test-app/slow", body=b'{"made": "up"}',
                    headers={"Host": f"{app.BIND}:{self.port}", "Content-Type": "application/json"})
        r = con.getresponse()
        r.read()
        self.assertEqual((r.status, r.getheader("Connection")), (403, "close"))

    def test_stop_does_not_wait_for_an_idle_connection(self):
        con = self.connection()
        self.assertEqual(self.ask(con, "GET", "/healthz")[0].status, 200)
        started = time.monotonic()
        self.assertTrue(self.httpd.stop())
        self.assertLess(time.monotonic() - started, app.KEEP_ALIVE_S / 2)

    def test_a_running_server_answers_its_health_check(self):
        self.assertTrue(app.answering(self.port))
        self.httpd.stop()
        self.assertFalse(app.answering(self.port))

    def test_a_closed_port_is_given_only_the_short_connect_time(self):
        # Windows takes about 2 s to refuse a loopback connection; the status line asked on every refresh
        with mock.patch.object(app.socket, "create_connection", side_effect=TimeoutError) as connect:
            self.assertFalse(app.answering(1))
        self.assertEqual(connect.call_args.kwargs["timeout"], app.CONNECT_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
