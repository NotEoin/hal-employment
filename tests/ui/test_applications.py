"""The Applications screens in a real browser: the board, approving with the first-application guide, the
Form step's marks, and "I sent it myself".

They run the real files of `jobhunt/web/static/app/` against the fake server of `tests/support/fake_api.py`,
taught here to answer the two Applications GET routes with a made-up person and made-up applications, so
the shared fake server stays as its owner left it. They need `HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer

from support.fake_api import FakeServer, FakeState, Handler
from support.ui import AppPage, chromium, skip_unless_browser

APP = "app-demo%3A1"

MADE_UP_STEPS = [{"name": "Documents", "note": "Approved", "state": "done"},
                 {"name": "Form", "note": "1 answer needs you", "state": "now"},
                 {"name": "Sent", "note": "", "state": "later"}, {"name": "Interview", "note": "", "state": "later"}]
MADE_UP_APP = {
    "id": "demo:1", "number": 1, "step": 2, "steps": MADE_UP_STEPS, "first_time": True, "column": "form",
    "job": {"id": "demo:1", "title": "Museum Assistant", "org": "Riverside Trust", "place": "Leeds", "pay": "",
            "url": "https://jobs.example.org/riverside/1", "status": "applying", "score": 80, "source": "greenhouse"},
    "nav": {"index": 1, "total": 2, "prev": None, "next": "demo:2"},
    "docs": {"state": "approved", "writing": None, "approved_at": "2026-10-22T09:00:00Z",
             "cv": {"changes": [], "preview_url": None, "version_n": None, "look": None, "sent_as": "hal_copy"},
             "letter": {"text": "A made-up letter.", "words": 3, "tone": None, "evidence": None, "checked": None,
                        "preview_url": None},
             "answers": [], "message": {"text": "", "to": "", "to_why": ""}},
    "form": {"state": "needs", "message": "", "filled": 1, "total": 2, "to_check": 1, "site": "jobs.example.org",
             "platform": "", "mode": "helper", "last_page": True, "handed_over": False, "notes_used": [],
             "words": "Hal filled 1 of 2 answers. 1 needs you. Check them here or in the form.",
             "fields": [{"hid": "n", "q": "Full name", "a": "Robin Testwell", "ok": True, "src": "profile",
                         "src_label": "Your profile", "why": "From your profile.", "notes_used": [], "problem": ""},
                        {"hid": "w", "q": "Why this job?", "a": "A made-up answer.", "ok": False, "src": "hal",
                         "src_label": "Written by Hal", "why": "Written for this job by Hal.", "notes_used": [],
                         "problem": ""}]},
    "sent": None, "interview": None}
MADE_UP_BOARD = {"columns": [
    {"key": "ready", "title": "Getting ready", "help": "Waiting for you to approve them.", "items": [
        {"id": "demo:2", "number": 2, "org": "Hillside Library", "title": "Library Assistant",
         "next": "Check and approve documents", "tone": "info", "note": "", "hal_busy": False}]},
    {"key": "form", "title": "Form filled", "help": "Check it and press Submit on the site yourself.", "items": [
        {"id": "demo:1", "number": 1, "org": "Riverside Trust", "title": "Museum Assistant",
         "next": "1 answer needs you", "tone": "warn", "note": "1 of 2 filled", "hal_busy": False}]},
    {"key": "sent", "title": "Sent", "help": "You sent it.", "items": []},
    {"key": "interview", "title": "Interview", "help": "An interview or test is booked.", "items": []},
    {"key": "offer", "title": "Offer", "help": "Offers you have received.", "items": []}],
    "closed": [{"id": "demo:3", "number": 3, "title": "Archive Intern", "org": "Old Mill Museum",
                "why": "You discarded it: too much effort"}]}


@dataclass
class AppsState(FakeState):
    """The fake server's state with the board and one application."""

    applications: dict = field(default_factory=lambda: json.loads(json.dumps(MADE_UP_BOARD)))
    application: dict = field(default_factory=lambda: json.loads(json.dumps(MADE_UP_APP)))


class AppsHandler(Handler):
    """The fake server's handler, answering GET /applications and GET /applications/{id} too."""

    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        st = self.server.state
        if st.session is not None and path == "/applications":
            self._json(st.applications)
        elif st.session is not None and path.startswith("/applications/"):
            self._json(st.application)
        else:
            super()._api_get(path, query)


class AppsServer(FakeServer):
    """The fake server with `AppsHandler`."""

    def __init__(self, state: AppsState | None = None) -> None:
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), AppsHandler)
        self.state = state or AppsState()
        self._thread = None


@skip_unless_browser
class ApplicationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def serve(self, state: AppsState | None = None) -> AppsServer:
        srv = AppsServer(state or AppsState())
        self.addCleanup(srv.__exit__, None, None, None)
        return srv.__enter__()

    def tab(self, srv: AppsServer, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, srv.url, size, scheme)
        self.addCleanup(p.close)
        return p

    def test_the_board_shows_each_column_and_the_closed_list(self) -> None:
        p = self.tab(self.serve()).open("apps")
        p.page.wait_for_selector(".board .colm")
        heads = p.page.locator(".board .colm h3").all_inner_texts()
        self.assertEqual(heads, ["Getting ready", "Form filled", "Sent", "Interview", "Offer"])
        self.assertIn("1 answer needs you", p.page.locator(".colm", has_text="Form filled").inner_text())
        self.assertIn("too much effort", p.page.locator("section.card", has_text="Closed").inner_text())
        p.page.click("text=Museum Assistant")
        p.page.wait_for_selector("h1:text('Museum Assistant')")
        self.assertEqual(p.errors, [])

    def test_approving_shows_the_first_application_guide_then_starts_the_form(self) -> None:
        state = AppsState()
        state.application.update(step=1, column="ready", form=None)
        state.application["docs"]["state"] = "ready"
        state.application["steps"][0].update(state="now", note="Waiting for you")
        srv = self.serve(state)
        p = self.tab(srv).open(APP)
        p.page.wait_for_selector(".approve")
        p.page.click("text=Approve and fill in the form")
        dialog = p.page.locator("[role=dialog]")
        self.assertIn("Your first application with Hal", dialog.inner_text())
        self.assertIn("press Submit on the site yourself", dialog.inner_text())
        self.assertEqual([path for path, _ in state.posts], [])
        p.page.click("text=Got it, open the form")
        p.page.wait_for_selector("h1:text('Applications')")
        self.assertEqual([path for path, _ in state.posts], ["/api/v2/applications/demo%3A1/documents/approve"])
        self.assertEqual(p.errors, [])

    def test_the_form_step_marks_each_answer_and_i_sent_it_myself_moves_it_on(self) -> None:
        state = AppsState()
        srv = self.serve(state)
        p = self.tab(srv, "narrow", "dark").open(APP)
        p.page.wait_for_selector(".field-row")
        marks = p.page.locator(".field-row .flag").all_inner_texts()
        # The answer to check comes first.
        self.assertEqual(marks, ["Check this", "Fine"])
        self.assertIn("Hal filled 1 of 2 answers. 1 needs you.", p.page.locator(".approve").inner_text())
        self.assertIn("Written by Hal", p.page.locator(".field-row").first.inner_text())
        p.page.click("text=I sent it myself")
        p.page.wait_for_selector("text=Moved to Sent")
        self.assertIn(("/api/v2/applications/demo%3A1/sent", {}), state.posts)
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
