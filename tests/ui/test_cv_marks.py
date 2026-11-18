"""The page's reading of bold, italic and links (`cv/marks.js`) against the server's (`cvstudio/inline.py`),
in a real browser: both must read every line the same way, or the editor and the cards would show what the
CV does not print.

Needs `HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import unittest
from dataclasses import asdict

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt.cvstudio import inline

LINES = ["Led **twelve** volunteers on *Saturday* tours", "***Winner***", "**ran *every* shift**",
         "*read **all** of it*", "C* and A*A*A, D*D*D*, 5* rating, 2*3*4", "*nix and *BSD", "(*) required",
         "Rated *****.", r"\*Note\* and \[1\]", "snake_case and __init__", "**a *b** c*", "*a **b* c**",
         "*a **b***", "***a** b*", "**a*", "See [my **portfolio**](https://example.org/avery).",
         "[C++ [advanced]](https://example.org/wiki/Avery_(writer))", "[x](javascript:alert(1))",
         "[mail](mailto:a@example.org) and [x](example.org)", "one *star\nand* another",
         "[x](https://example.org/a\x01b) and [y](https://example.org/\x7f)"]
RUNS = "async (lines) => { const m = await import('/app/cv/marks.js'); return lines.map(t => m.runs(t)); }"


@skip_unless_browser
class SameReading(unittest.TestCase):
    def test_the_page_reads_every_line_as_the_server_does(self) -> None:
        with contextlib.ExitStack() as stack:
            browser = stack.enter_context(chromium())
            hal = stack.enter_context(HalServer())
            p = AppPage(browser, f"http://127.0.0.1:{hal.port}", "wide", "light")
            stack.callback(p.close)
            p.page.goto(hal.open_url())
            p.open("cv")
            got = p.page.evaluate(RUNS, LINES)
        for text, page_runs in zip(LINES, got, strict=True):
            with self.subTest(text=text):
                self.assertEqual(page_runs, [asdict(r) for r in inline.runs(text)])


if __name__ == "__main__":
    unittest.main()
