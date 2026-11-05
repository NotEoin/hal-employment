"""Tests of the new app's shell: routes, the rail, search or jump, shortcuts, dialogs and the no-session screen.

Each test runs the real files of `jobhunt/web/static/app/` in headless Chromium against the fake server of
`tests/support/fake_api.py`, with a made-up person. They need `HAL_TEST_BROWSER=1` and Playwright.

Run with: HAL_TEST_BROWSER=1 .venv/bin/python -m unittest discover tests
"""
from __future__ import annotations

import contextlib
import time
import unittest
from pathlib import Path

from support.fake_api import FakeServer, FakeState, made_up_session, made_up_status
from support.ui import LOAD_TIMEOUT_MS, ROUTES, AppPage, chromium, skip_unless_browser

TITLES = {"home": "Home", "jobs": "Jobs", "job": "Job", "apps": "Applications", "app": "Application",
          "inbox": "Messages", "cv": "My CV", "docs": "Documents", "record": "Your record", "activity": "Activity",
          "check": "Check your record", "memory": "What Hal remembers", "settings": "Settings",
          "setup": "Setup guide"}
FULL_MENU = ["Home", "Jobs", "Applications", "Messages", "My CV", "Documents", "Your record", "Activity",
             "What Hal remembers", "Settings", "Help and shortcuts"]
NAV_TEXTS = """() => [...document.querySelectorAll('nav.rail .nav')].map(b => {
  const c = b.querySelector('.count');
  const words = b.textContent.replace(c ? c.textContent : '', '').trim();
  return [words, c ? c.textContent : null, !!(c && c.classList.contains('hot'))];
})"""
APP_DIR = Path(__file__).resolve().parents[2] / "jobhunt" / "web" / "static" / "app"
STUB_MARKS = ("/* STUB:", "ui/stub.js", "This part is being built")
SETTLE_S = 10.0   # the status reply can land well after the heading on a loaded machine
POLL_S = 0.1


def settled(read, want, seconds: float = SETTLE_S):
    """`read()` once it equals `want`, or its last value after `seconds`.

    Polled from Python, because `wait_for_function` evaluates strings, which the app's CSP refuses.
    """
    end = time.monotonic() + seconds
    got = read()
    while got != want and time.monotonic() < end:
        time.sleep(POLL_S)
        got = read()
    return got


class NoStubs(unittest.TestCase):
    """Gate G2: no stub left in the app."""

    def test_no_file_of_the_app_is_or_uses_a_stub(self) -> None:
        left = [f"{p.relative_to(APP_DIR).as_posix()}: {m}" for p in sorted(APP_DIR.rglob("*.js"))
                if not p.relative_to(APP_DIR).as_posix().startswith("lib/")
                for m in STUB_MARKS if m in p.read_text(encoding="utf-8")]
        self.assertEqual(left, [])


TAB_PRESSES = 12  # more than the focusable elements in the shortcuts dialog, so the focus has to wrap


STREAM_DROP_MS = 1500   # longer than the fake stream's 1 s ping, so every open stream ends
RECONNECT_MS = 15000    # the event client waits 3 s, then 6 s, before it connects again


@skip_unless_browser
class ShellTest(unittest.TestCase):
    """The shell in a real browser."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def serve(self, **state: object) -> FakeServer:
        srv = FakeServer(FakeState(**state))
        self.addCleanup(srv.__exit__, None, None, None)
        return srv.__enter__()

    def tab(self, srv: FakeServer, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, srv.url, size, scheme)
        self.addCleanup(p.close)
        return p

    def test_the_shell_draws_every_route_without_errors_at_both_sizes(self) -> None:
        srv = self.serve()
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            p = self.tab(srv, size, scheme)
            for route in ROUTES:
                with self.subTest(size=size, route=route):
                    p.open(route)
                    page = route.split("-")[0]
                    title = "(1) " + TITLES[page] + " - Hal Employment"
                    self.assertEqual(settled(p.page.title, title), title)
                    self.assertEqual(p.sideways(), 0)
                    if page != "setup":
                        p.page.wait_for_selector("#main h1:focus")
            self.assertEqual(p.errors, [], f"console or CSP errors at {size}; answered 404: {srv.state.missing}")

    def test_the_shell_has_no_serious_accessibility_findings(self) -> None:
        srv = self.serve(session=made_up_session(developer=True), status=made_up_status(
            busy=True, text="Hal is writing a cover letter", step_n=2, step_total=4, action_id="a1", why_activity_id=7))
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            p = self.tab(srv, size, scheme)
            for route in ("home", "jobs-hidden", "settings-goal", "setup"):
                with self.subTest(size=size, route=route):
                    p.open(route)
                    self.assertEqual(p.serious_axe(), [])
            p.open("home")
            for keys, name in (("Control+k", "Search or jump"), ("?", "Help and shortcuts")):
                with self.subTest(size=size, dialog=name):
                    p.page.keyboard.press(keys)
                    p.page.wait_for_selector('[role="dialog"]')
                    self.assertEqual(p.serious_axe(), [])
                    p.page.keyboard.press("Escape")
            self.assertEqual(p.errors, [])

    def test_ctrl_k_and_question_mark_open_the_palette_and_shortcuts(self) -> None:
        srv = self.serve(session=made_up_session(developer=True))
        p = self.tab(srv).open("home")
        pg = p.page
        pg.keyboard.press("Control+k")
        box = pg.get_by_role("dialog", name="Search or jump")
        box.wait_for()
        pg.wait_for_selector('[role="dialog"] input:focus')
        self.assertEqual(pg.evaluate("document.activeElement.getAttribute('aria-label')"), "Search or jump")
        pg.keyboard.type("?")  # typed into the box, so it must not open the shortcuts
        self.assertEqual(pg.get_by_role("dialog").count(), 1)
        pg.keyboard.press("Backspace")
        pg.keyboard.type("museum")
        pg.get_by_role("button", name="Museum Assistant, Riverside Trust Job").wait_for()
        pg.keyboard.press("Enter")
        pg.wait_for_url("**/#job-demo%3A1")
        pg.wait_for_selector("#main h1:focus")
        self.assertEqual(pg.get_by_role("dialog").count(), 0)
        self.assertEqual(pg.title(), "(1) Job - Hal Employment")

        pg.keyboard.press("?")
        pg.get_by_role("dialog", name="Help and shortcuts").wait_for()
        self.assertIn("Search or jump anywhere", pg.get_by_role("dialog").inner_text())
        pg.keyboard.press("Escape")
        self.assertEqual(pg.get_by_role("dialog").count(), 0)

        pg.keyboard.press("Control+k")
        pg.wait_for_selector('[role="dialog"] input:focus')
        pg.keyboard.type("search for jobs")
        pg.get_by_role("button", name="Search for jobs now Action").wait_for()
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".toast")
        self.assertIn(("/api/v2/jobs/search", {}), srv.state.posts, "an action is posted with the X-Hal header")
        self.assertEqual(p.errors, [])

    def test_a_dialog_traps_focus_and_esc_returns_it(self) -> None:
        srv = self.serve()
        p = self.tab(srv).open("home")
        pg = p.page
        opener = pg.get_by_role("button", name="Help and shortcuts")
        opener.focus()
        pg.keyboard.press("Enter")
        dialog = pg.get_by_role("dialog", name="Help and shortcuts")
        dialog.wait_for()
        pg.wait_for_selector('[role="dialog"] :focus')
        inside = "() => !!document.activeElement.closest('[role=dialog]')"
        for key in ("Tab", "Shift+Tab"):
            for _ in range(TAB_PRESSES):
                pg.keyboard.press(key)
                self.assertTrue(pg.evaluate(inside), f"{key} left the dialog at {p.focused()}")
        self.assertEqual(dialog.get_attribute("aria-modal"), "true")
        pg.keyboard.press("Escape")
        self.assertEqual(pg.get_by_role("dialog").count(), 0)
        self.assertEqual(p.focused(), "button:Help and shortcuts")
        self.assertEqual(p.errors, [])

    def test_help_opens_the_first_application_guide_again(self) -> None:
        # the guide said "It is also in Help." while Help had only the shortcuts
        srv = self.serve()
        p = self.tab(srv).open("home")
        pg = p.page
        pg.keyboard.press("?")
        help_box = pg.get_by_role("dialog", name="Help and shortcuts")
        help_box.get_by_role("button", name="How applying with Hal works").click()
        guide = pg.get_by_role("dialog", name="Your first application with Hal")
        guide.wait_for()
        guide.get_by_role("button", name="Got it", exact=True).click()
        self.assertEqual(pg.get_by_role("dialog").count(), 0)
        self.assertEqual(srv.state.posts, [], "with no application, Got it approves nothing")
        self.assertEqual(p.errors, [])

    def test_the_rail_matches_the_mock_groups_and_the_simple_menu(self) -> None:
        srv = self.serve(session=made_up_session(developer=True))
        p = self.tab(srv).open("home")
        nav = p.page.evaluate(NAV_TEXTS)
        self.assertEqual([n[0] for n in nav], FULL_MENU)
        counts = {n[0]: (n[1], n[2]) for n in nav if n[1]}
        self.assertEqual(counts, {"Jobs": ("3", False), "Applications": ("1", True), "Messages": ("2", True),
                                  "My CV": ("4", False), "What Hal remembers": ("1", False)})
        groups = p.page.eval_on_selector_all("nav.rail .navgroup", "els => els.map(e => e.textContent)")
        self.assertEqual(groups, ["Your materials", "Hal"])
        p.page.get_by_role("button", name="Jobs").click()
        p.page.wait_for_url("**/#jobs")
        self.assertEqual(p.page.get_attribute("nav.rail .nav.on", "aria-current"), "page")

        srv.state.session = made_up_session(developer=True, simple_menu=True)
        p.open("home")
        nav = p.page.evaluate(NAV_TEXTS)
        self.assertEqual([n[0] for n in nav], ["Home", "Jobs", "Applications", "My CV", "Documents", "More",
                                               "Help and shortcuts"])
        self.assertEqual(nav[5][1:], ["3", True], "More carries the count of what it hides")
        p.page.get_by_role("button", name="More").click()
        nav = p.page.evaluate(NAV_TEXTS)
        self.assertEqual([n[0] for n in nav][6:11], ["Messages", "Your record", "Activity", "What Hal remembers",
                                                     "Settings"])
        self.assertEqual(p.errors, [])

    def test_a_persons_own_rail_shows_every_page(self) -> None:
        srv = self.serve()
        p = self.tab(srv).open("home")
        self.assertEqual(settled(lambda: [n[0] for n in p.page.evaluate(NAV_TEXTS)], FULL_MENU), FULL_MENU)

    def test_on_a_narrow_window_the_menu_slides_in_and_esc_closes_it(self) -> None:
        srv = self.serve(session=made_up_session(developer=True))
        p = self.tab(srv, "narrow", "dark").open("home")
        self.assertEqual(p.page.get_attribute("nav.rail", "inert"), "", "the closed menu cannot be tabbed into")
        p.page.get_by_role("button", name="Menu").click()
        p.page.wait_for_selector("nav.rail .nav:focus")
        p.page.keyboard.press("Escape")
        p.page.wait_for_selector("nav.rail[inert]", state="attached")
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.errors, [])

    def test_without_a_session_the_app_says_to_use_the_shortcut(self) -> None:
        srv = self.serve(session=None)
        p = self.tab(srv).open("jobs")
        self.assertEqual(p.page.inner_text("#main h1"), "For your safety, Hal opens from its shortcut.")
        self.assertEqual(p.page.locator("nav.rail").count(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual([e for e in p.errors if "401" not in e], [])

    def test_a_page_left_open_across_an_update_reloads_or_offers_to(self) -> None:
        # testers: an open page kept its old screens after Hal was updated and restarted (2026-11-04)
        srv = self.serve()
        p = self.tab(srv).open("home")
        p.page.wait_for_function("() => navigator.locks !== undefined")
        p.page.evaluate("() => { window.__before = 1; }")

        def restart(commit: str) -> None:
            """The stream drops as a restart drops it, and comes back running `commit`."""
            srv.state.code = {**srv.state.code, "commit": commit}
            srv.state.stopping.set()
            p.page.wait_for_timeout(STREAM_DROP_MS)
            srv.state.stopping.clear()

        restart("made-up-1")   # the same code: the page stays as it is
        p.page.wait_for_timeout(RECONNECT_MS)
        self.assertEqual(p.page.evaluate("() => window.__before"), 1)

        p.page.evaluate("""() => { const box = document.createElement('input'); box.id = 'typing';
                                   document.querySelector('#main').append(box); box.focus(); }""")
        restart("made-up-2")
        p.page.wait_for_selector(".toast:has-text('Hal was updated')", timeout=RECONNECT_MS)
        self.assertEqual(p.page.evaluate("() => window.__before"), 1, "nothing typed is lost")

        p.page.evaluate("() => document.activeElement.blur()")
        restart("made-up-3")
        p.page.wait_for_function("() => window.__before === undefined", timeout=RECONNECT_MS)
        self.assertEqual(p.errors, [])

    def test_with_reloading_off_in_preferences_an_update_only_offers_reload(self) -> None:
        srv = self.serve()
        srv.state.pages["/settings"]["values"]["updates.reload_pages"] = False
        p = self.tab(srv).open("home")
        p.page.evaluate("() => { window.__before = 1; }")
        srv.state.code = {**srv.state.code, "commit": "made-up-2"}
        srv.state.stopping.set()
        p.page.wait_for_timeout(STREAM_DROP_MS)
        srv.state.stopping.clear()
        p.page.wait_for_selector(".toast:has-text('Hal was updated')", timeout=RECONNECT_MS)
        self.assertEqual(p.page.evaluate("() => window.__before"), 1)
        self.assertEqual(p.errors, [])

    def test_unknown_routes_go_home(self) -> None:
        srv = self.serve()
        p = self.tab(srv).open("nowhere-at-all")
        # the count in the title comes with the status line, a moment after the page itself
        p.page.wait_for_function("t => document.title === t", arg="(1) Home - Hal Employment",
                                 timeout=LOAD_TIMEOUT_MS)
        self.assertEqual(p.page.title(), "(1) Home - Hal Employment")
        self.assertEqual(p.errors, [])

    def test_the_status_line_shows_what_hal_is_doing_with_why_and_stop(self) -> None:
        srv = self.serve(status=made_up_status(busy=True, text="Hal is writing a cover letter", step_n=2,
                                               step_total=4, action_id="a1", why_activity_id=7, problems=0))
        p = self.tab(srv).open("home")
        line = p.page.get_by_role("status").first
        self.assertIn("Hal is writing a cover letter", line.inner_text())
        self.assertIn("Step 2 of 4", line.inner_text())
        p.page.get_by_role("button", name="Stop").click()
        p.page.wait_for_selector(".toast")
        self.assertIn(("/api/v2/actions/a1/stop", {}), srv.state.posts)
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
