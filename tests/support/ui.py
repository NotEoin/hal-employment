"""Playwright helpers for the UI tests: start Chromium, open the app, and collect what went wrong.

Browser tests run only with `HAL_TEST_BROWSER=1` and Playwright installed (they need a real browser), so
the default test run stays fast. A page opened with `open_app` records console errors, page errors and
Content-Security-Policy refusals; `sideways` and `serious_axe` are the checks of gate G2.
"""
from __future__ import annotations

import os
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe.min.js"
LOAD_TIMEOUT_MS = 10000   # the shell draws in well under a second; this only bounds a broken page
SIZES = {"wide": {"width": 1400, "height": 900}, "narrow": {"width": 400, "height": 860}}
SERIOUS = ("serious", "critical")

# Every deep link of the app, with made-up ids where a route takes one.
ROUTES = ["home", "jobs", "jobs-saved", "jobs-hidden", "jobs-where", "job-demo%3A1", "apps", "app-1", "inbox",
          "cv", "cv-sugg", "cv-style", "cv-checks", "cv-versions", "docs", "docs-1", "docs-details", "record",
          "activity", "memory", "memory-learned", "memory-sites", "memory-answers", "settings", "settings-goal",
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


class AppPage:
    """One browser tab on the app, with the problems it reported."""

    def __init__(self, browser: Any, base_url: str, size: str = "wide", scheme: str = "light") -> None:
        self.ctx = browser.new_context(viewport=SIZES[size], color_scheme=scheme)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(LOAD_TIMEOUT_MS)
        self.base_url = base_url
        self.errors: list[str] = []
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

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
        self.page.wait_for_selector("#main .loading", state="detached")
        self.page.wait_for_selector("#main h1, .setup", state="visible")

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
