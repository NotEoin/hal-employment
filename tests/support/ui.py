"""Playwright helpers for the UI tests: start Chromium, open the app, and collect what went wrong.

Browser tests run only with `HAL_TEST_BROWSER=1` and Playwright installed (they need a real browser), so
the default test run stays fast. A page opened with `open_app` records console errors, page errors and
Content-Security-Policy refusals; `sideways` and `serious_axe` are the checks of gate G2.
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe.min.js"
LOAD_TIMEOUT_MS = 10000   # the shell draws in well under a second; this only bounds a broken page
SIZES = {"wide": {"width": 1400, "height": 900}, "narrow": {"width": 400, "height": 860},
         # Hal's Edge app window as it opens on a 1280 x 800 laptop (Windows 11 test VM)
         "laptop": {"width": 1033, "height": 690}}
SERIOUS = ("serious", "critical")
# Preact 10.24 runs effects after the next frame, or after 100 ms when no frame comes (`afterNextFrame` in
# preact/hooks); `late_effects` holds both back, as a busy computer does.
EFFECT_FALLBACK_MS = 100
LATE_EFFECTS_MS = 1500

# Every deep link, with made-up ids where a route takes one.
ROUTES = ["home", "jobs", "jobs-saved", "jobs-hidden", "jobs-where", "job-demo%3A1", "apps", "app-1", "inbox",
          "cv", "cv-sugg", "cv-style", "cv-checks", "cv-versions", "docs", "docs-1", "docs-details", "record",
          "record-work", "record-inconsistencies", "record-files", "check", "activity", "memory", "memory-learned",
          "memory-sites", "memory-answers", "settings", "settings-goal",
          "setup", "setup-cv", "setup-goal"]


def browser_tests_on() -> bool:
    """True when browser tests were asked for and Playwright can be imported."""
    if os.environ.get("HAL_TEST_BROWSER") != "1":
        return False
    try:
        import playwright.sync_api  # noqa: F401 - only checking that it is installed
    except ImportError:
        return False
    return True


def skip_unless_browser(cls: type) -> type:
    """Class decorator: skips the tests unless browser tests are on."""
    return unittest.skipUnless(browser_tests_on(), "browser tests need HAL_TEST_BROWSER=1 and Playwright")(cls)


def console_line(message: Any) -> str:
    """A console error's text, then the address it came from, so a failed load names the resource."""
    url = (message.location or {}).get("url")
    return f"{message.text} ({url})" if url else message.text


@contextmanager
def chromium() -> Iterator[Any]:
    """A headless Chromium for the length of the block."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


def late_effects(page: Any, delay_ms: int = LATE_EFFECTS_MS) -> None:
    """Makes the app's effects run `delay_ms` late in `page`, so a test can act before they have run.

    Call it before the page loads. It delays every animation frame and every 100 ms timer of the page's own
    scripts; Playwright's scripts run in a world of their own and keep their timing.
    """
    page.add_init_script(
        f"(() => {{ const later = window.setTimeout.bind(window), frame = window.requestAnimationFrame.bind(window);"
        f" window.setTimeout = (fn, ms, ...args) => later(fn, ms === {EFFECT_FALLBACK_MS} ? {delay_ms} : ms, ...args);"
        f" window.requestAnimationFrame = fn => frame(t => later(() => fn(t), {delay_ms})); }})()")


class AppPage:
    """One browser tab on the app, with the problems it reported."""

    def __init__(self, browser: Any, base_url: str, size: str = "wide", scheme: str = "light") -> None:
        self.ctx = browser.new_context(viewport=SIZES[size], color_scheme=scheme)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(LOAD_TIMEOUT_MS)
        self.base_url = base_url
        self.errors: list[str] = []
        self.failed: list[str] = []   # requests that got no answer at all, with the browser's reason
        self._asked: dict[Any, float] = {}   # requests still waiting for an answer, with when they were sent
        self.page.on("request", lambda r: self._asked.__setitem__(r, time.monotonic()))
        self.page.on("requestfinished", lambda r: self._asked.pop(r, None))
        self.page.on("requestfailed", self._failed)
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(self._named(console_line(m))))
        self.page.on("pageerror", lambda e: self.errors.append(self._named(str(e))))

    def _failed(self, request: Any) -> None:
        self._asked.pop(request, None)
        self.failed.append(f"{request.url}: {request.failure}")

    def unanswered(self) -> list[str]:
        """The requests still waiting for an answer and how long each has waited (the event stream always is)."""
        now = time.monotonic()
        return [f"{r.method} {r.url.removeprefix(self.base_url)} ({now - at:.1f} s)"
                for r, at in list(self._asked.items())]

    def problems(self) -> str:
        """What the page reported and what it was still waiting for, for a failure message."""
        return (f"errors: {self.errors}; requests with no answer: {self.failed}; requests still waiting: "
                f"{self.unanswered()}")

    def shows(self, locator: Any, what: str) -> None:
        """Waits until `locator` is visible; the failure says what the page was still waiting for."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        try:
            locator.first.wait_for(state="visible")
        except PlaywrightTimeout as e:
            raise AssertionError(f"{what} never showed; {self.problems()}") from e

    def _named(self, line: str) -> str:
        """`line`, then the browser's reason for any failed request it mentions (a module that never came)."""
        why = [f for f in self.failed if f.split(": ", 1)[0] in line]
        return f"{line} [{'; '.join(why)}]" if why else line

    def open(self, route: str = "") -> AppPage:
        """Loads the app afresh at `#route` and waits until the shell or the no-session screen is drawn."""
        self.page.goto("about:blank")  # a hash change alone would not reload the app
        self.page.goto(self.base_url + "/" + ("#" + route if route else ""))
        self.settle()
        return self

    def go(self, route: str) -> None:
        """Changes the hash as a deep link would, and waits for the page's heading."""
        self.page.evaluate("r => { location.hash = r; }", route)
        self.settle()

    def settle(self) -> None:
        """Waits until the page's own module has drawn and its heading (or the setup guide) is shown.

        Waits use selectors, never wait_for_function: its polling evaluates strings, which the app's
        Content-Security-Policy rightly refuses.
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        try:
            self.page.wait_for_selector("#main .loading", state="detached")
            self.page.wait_for_selector("#main h1, .setup", state="visible")
        except PlaywrightTimeout as e:
            raise AssertionError(f"the page never drew; {self.problems()}") from e

    def sideways(self) -> int:
        """How many pixels the page scrolls sideways (0 is right)."""
        return self.page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")

    def serious_axe(self) -> list[str]:
        """axe-core's serious and critical findings on the page as it is now, one line each."""
        if not self.page.evaluate("typeof window.axe === 'object'"):
            self.page.evaluate(AXE.read_text(encoding="ascii"))
        found = self.page.evaluate("async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
                                   ".map(v => ({id: v.id, impact: v.impact, help: v.help,"
                                   " nodes: v.nodes.map(n => n.target.join(' ')).slice(0, 3)}))")
        return [f"{v['impact']} {v['id']}: {v['help']} at {', '.join(v['nodes'])}"
                for v in found if v["impact"] in SERIOUS]

    def focused(self) -> str:
        """A short description of the focused element: tag, then its aria-label or text."""
        return self.page.evaluate("() => { const e = document.activeElement; if (!e) return '';"
                                  " const words = e.getAttribute('aria-label') || e.textContent || '';"
                                  " return e.tagName.toLowerCase() + ':' + words.trim().slice(0, 60); }")

    def close(self) -> None:
        """Closes the tab and its context."""
        self.ctx.close()


@contextmanager
def real_app(browser, size: str = "wide", scheme: str = "light") -> Iterator[AppPage]:
    """The new server inside the made-up world, and a signed-in tab on it."""
    # imported here, so a test module sets its temporary home before jobhunt loads
    from jobhunt.core.settings import store
    from jobhunt.web import app, auth

    store.drop_cache()
    httpd = app.make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, name="ui-test-server", daemon=True).start()
    p = AppPage(browser, f"http://127.0.0.1:{port}", size, scheme)
    try:
        p.page.goto(auth.mint_open_url(port=port))
        yield p
    finally:
        p.close()
        httpd.stop()   # waits for its requests, which hold the world's database open until they end
