"""API v2 routing: patterns with params, replies, the error shape, and auth per route."""
import contextlib
import http.client
import unittest

from support.api import HalServer

from jobhunt import db
from jobhunt.web import router
from jobhunt.web.router import BadRequest, NotFound, Request, route

PREFIX = "/api/v2/test-router"


@route("GET", f"{PREFIX}/echo/{{job_id}}")
def _echo(req: Request) -> dict:
    return {"job_id": req.params["job_id"], "q": req.query.get("q")}


@route("POST", f"{PREFIX}/body")
def _body(req: Request) -> dict:
    return {"got": req.body}


@route("GET", f"{PREFIX}/nothing")
def _nothing(req: Request) -> None:
    return None


@route("GET", f"{PREFIX}/bad")
def _bad(req: Request) -> None:
    raise BadRequest("Hal could not read that. Try 24000 or 24k.", field="goal.min_pay")


@route("GET", f"{PREFIX}/missing")
def _missing(req: Request) -> None:
    raise LookupError("no job made-up:1")


@route("GET", f"{PREFIX}/busy")
def _busy(req: Request) -> None:
    raise RuntimeError("Hal is already filling that form in")


@route("GET", f"{PREFIX}/boom")
def _boom(req: Request) -> None:
    raise ZeroDivisionError("a made-up bug")


@route("GET", f"{PREFIX}/open", auth="none")
def _open(req: Request) -> dict:
    return {"signed_in": req.session is not None}


@route("GET", f"{PREFIX}/local", auth="local")
def _local(req: Request) -> dict:
    return {"local": req.local}


@route("POST", f"{PREFIX}/here", local_only=True)
def _here(req: Request) -> None:
    return None


@route("GET", f"{PREFIX}/html")
def _html(req: Request) -> router.HtmlResponse:
    return router.HtmlResponse("<p>made up</p>")


def _page_closed(handler) -> None:
    raise ConnectionAbortedError("the browser closed the page")   # what Windows raises (WinError 10053)


@route("GET", f"{PREFIX}/closed")
def _closed(req: Request) -> router.Stream:
    return router.Stream(_page_closed)


@route("GET", f"{PREFIX}/refused")
def _refused(req: Request) -> None:
    raise ConnectionRefusedError("a made-up local model is not running")


class Routing(unittest.TestCase):
    """Handlers registered with `@route` answer through the real server."""

    @classmethod
    def setUpClass(cls):
        cls.hal = HalServer().__enter__()
        cls.c = cls.hal.signed_in()

    @classmethod
    def tearDownClass(cls):
        cls.hal.__exit__(None, None, None)

    def test_params_are_unquoted_so_job_ids_keep_their_colon(self):
        body = self.c.get(f"{PREFIX}/echo/reed%3A123?q=made%20up").json()
        self.assertEqual(body, {"job_id": "reed:123", "q": "made up"})

    def test_a_json_body_reaches_the_handler(self):
        self.assertEqual(self.c.post(f"{PREFIX}/body", {"a": [1, 2]}).json(), {"got": {"a": [1, 2]}})

    def test_none_becomes_ok(self):
        self.assertEqual(self.c.get(f"{PREFIX}/nothing").json(), {"ok": True})

    def test_errors_have_one_shape_and_the_right_status(self):
        cases = {"bad": (400, "invalid"), "missing": (404, "not_found"), "busy": (409, "conflict")}
        for name, (status, code) in cases.items():
            with self.subTest(name):
                reply = self.c.get(f"{PREFIX}/{name}")
                self.assertEqual(reply.status, status)
                self.assertEqual(reply.json()["error"]["code"], code)
        self.assertEqual(self.c.get(f"{PREFIX}/bad").json()["error"]["field"], "goal.min_pay")

    def test_an_unexpected_error_shows_plain_words_and_is_kept_in_activity(self):
        with self.assertLogs("jobhunt.web.router", "ERROR") as logged:
            reply = self.c.get(f"{PREFIX}/boom")
        self.assertEqual(reply.status, 500)
        self.assertEqual(reply.json()["error"], {"code": "error", "message": router.UNEXPECTED})
        self.assertNotIn("made-up bug", reply.text)
        self.assertIn("ZeroDivisionError", "\n".join(logged.output))
        con = db.connect()
        try:
            row = con.execute("SELECT area, why FROM activity WHERE kind = 'system.error' ORDER BY id DESC").fetchone()
        finally:
            con.close()
        self.assertEqual(row["area"], "system")
        self.assertTrue(row["why"])

    def test_a_page_closed_mid_reply_is_not_recorded_as_a_failure(self):
        # regression: on Windows every closed page added "Something went wrong on a page" to Activity
        def failures() -> int:
            con = db.connect()
            try:
                return con.execute("SELECT count(*) FROM activity WHERE kind = 'system.error'").fetchone()[0]
            finally:
                con.close()
        before = failures()
        with self.assertNoLogs("jobhunt.web.router", "ERROR"), contextlib.suppress(http.client.HTTPException):
            self.c.get(f"{PREFIX}/closed")
        self.assertEqual(failures(), before)
        with self.assertLogs("jobhunt.web.router", "ERROR"):
            self.assertEqual(self.c.get(f"{PREFIX}/refused").status, 500)   # a route's own failed call still is
        self.assertEqual(failures(), before + 1)

    def test_a_route_open_to_all_and_one_for_the_local_key(self):
        self.assertEqual(self.hal.client().get(f"{PREFIX}/open").json(), {"signed_in": False})
        self.assertEqual(self.c.get(f"{PREFIX}/local").status, 401)

    def test_a_local_only_route_says_where_to_do_it_from_another_computer(self):
        remote = self.hal.signed_in(remote=True, remote_host="desktop")
        reply = remote.post(f"{PREFIX}/here")
        self.assertEqual(reply.status, 409)
        self.assertEqual(reply.json()["error"]["code"], "remote")
        self.assertIn("desktop", reply.json()["error"]["message"])
        self.assertEqual(self.c.post(f"{PREFIX}/here").status, 200)

    def test_unknown_api_paths_are_404(self):
        self.assertEqual(self.c.get(f"{PREFIX}/no-such-thing").status, 404)

    def test_html_replies_carry_a_strict_content_security_policy(self):
        reply = self.c.get(f"{PREFIX}/html")
        self.assertEqual(reply.text, "<p>made up</p>")
        self.assertIn("default-src 'none'", reply.headers["Content-Security-Policy"])
        self.assertEqual(reply.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(reply.headers["Cache-Control"], "no-store")


class Registration(unittest.TestCase):
    """Mistakes in registering a route fail at import."""

    def test_a_duplicate_route_raises(self):
        with self.assertRaises(ValueError):
            route("GET", f"{PREFIX}/nothing")(lambda req: None)

    def test_an_unknown_auth_kind_raises(self):
        with self.assertRaises(ValueError):
            route("GET", f"{PREFIX}/other", auth="anyone")

    def test_a_pattern_matches_whole_segments_only(self):
        self.assertIsNone(router.find("GET", f"{PREFIX}/echo/a/b"))
        self.assertIsNotNone(router.find("GET", f"{PREFIX}/echo/a"))

    def test_not_found_is_an_http_error(self):
        self.assertEqual(NotFound("x").status, 404)


if __name__ == "__main__":
    unittest.main()
