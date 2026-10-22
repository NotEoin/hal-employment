"""Applying through the browser extension, in a browser that loads it as the applicant's own does.

Moved by class from tests/test_apply.py; the test bodies are unchanged.
"""
import http.server
import os
import threading
import time
import unittest
from pathlib import Path

from support import legacy
from support.legacy import EXTENSION, PROSE, Practice

from jobhunt import db, extension
from jobhunt.web import server

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class InYourBrowser(Practice):
    """Forms filled through the extension, in a browser that loads it as the applicant's Brave does."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        super().setUpClass()
        tmp = Path(cls.tmp.name)
        server.SERVABLE, cls.servable = (*server.SERVABLE, tmp.resolve()), server.SERVABLE
        cls.helper = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.helper.serve_forever, daemon=True).start()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "extension"
        cls.pw = sync_playwright().start()
        cls.ctx = cls.pw.chromium.launch_persistent_context(
            str(tmp / "brave"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"])
        worker = cls.ctx.service_workers[0] if cls.ctx.service_workers else cls.ctx.wait_for_event("serviceworker")
        worker.evaluate("url => chrome.storage.local.set({hal: url})", f"http://127.0.0.1:{cls.helper.server_address[1]}")
        cls.worker = worker
        for _ in range(60):
            if extension.connected():
                break
            cls.ctx.pages[0].wait_for_timeout(500)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.close()
        cls.pw.stop()
        cls.helper.shutdown()
        cls.helper.server_close()
        server.SERVABLE = cls.servable
        os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"
        super().tearDownClass()

    def tab(self, address: str):
        # Playwright hears of tabs the extension opened only while it is waiting on something.
        self.ctx.pages[0].wait_for_timeout(300)
        return next(t for t in reversed(self.ctx.pages) if address in t.url)

    def test_a_slow_page_in_one_tab_does_not_hold_up_another(self):
        """2026-10-15 10:25: with six applications open, one timed out queued behind another tab's page load."""
        quick, slow = self.job("ext-quick", "form.html"), self.job("ext-slow", "form.html")
        extension._ask("open", timeout=60, job=quick, url=f"{self.base}/form.html")
        opening = threading.Thread(target=extension._ask, args=("open",),
                                   kwargs={"timeout": 60, "job": slow, "url": f"{self.base}/slow"})
        opening.start()
        time.sleep(0.5)
        started = time.time()
        extension._ask("read", timeout=4, job=quick, patterns={**extension.PATTERNS, "banners": None, "options": False})
        self.assertLess(time.time() - started, 4)
        opening.join()

    def test_applications_open_together_in_a_window_of_their_own(self):
        """apply_window = "separate": job tabs share one window, apart from the one the applicant works in."""
        for name in ("ext-window-1", "ext-window-2"):
            server.act_auto(self.con, self.job(name, f"form.html?{name}"))
        self.ctx.pages[0].wait_for_timeout(300)
        tabs = self.worker.evaluate("() => chrome.tabs.query({}).then(ts => ts.map(t => [t.url, t.windowId]))")
        own = self.worker.evaluate("() => chrome.storage.session.get('halWindow').then(s => s.halWindow)")
        # Earlier tests may have opened the window already; either way both jobs are in it, and it is not the only one.
        self.assertEqual({w for url, w in tabs if "ext-window" in url}, {own})
        self.assertGreater(len({w for _, w in tabs}), 1)

    def test_fills_the_first_page_and_leaves_what_it_cannot_answer(self):
        job_id = self.job("ext-fill", "form.html")
        said = server.act_auto(self.con, job_id)
        self.assertIn("1 required question", said)
        page = self.tab("form.html")
        self.assertEqual(page.input_value("#fn"), "Ada")
        self.assertEqual(page.inner_text("#uni-shown"), "University")
        self.assertTrue(page.is_checked("#rtw-y"))
        self.assertEqual(page.input_value("#heard"), "Company website")
        self.assertEqual(page.input_value("#notice"), "0")
        self.assertEqual(page.input_value("#dob"), "")

    def test_marks_the_page_then_follows_the_applicant_through_to_submitting(self):
        job_id = self.job("ext-journey", "form.html")
        server.act_auto(self.con, job_id)
        page = self.tab("form.html")
        marks = page.evaluate("""() => Object.fromEntries(['#fn', '#dob', '#notice'].map(
            s => [s, document.querySelector(s).closest('[data-hal-mark]')?.getAttribute('data-hal-mark') || null]))""")
        self.assertEqual(marks, {"#fn": "ok", "#dob": "answer", "#notice": "ok"})
        self.assertIn("1 to answer", page.inner_text("#hal-panel .counts"))
        self.assertIn("Date of birth", page.inner_text("#hal-panel ul"))

        # The applicant answers what Hal could not, changes one of its answers, and moves on.
        seen = {}
        page.fill("#dob", "01/02/2003")
        page.fill("#ln", "Byron")
        self.assertEqual(page.evaluate("() => document.querySelector('#dob').closest('[data-hal-mark]').dataset.halMark"),
                         "edited")
        self.assertTrue(server._watch_step(self.con, job_id, seen))
        page.click("#next")
        self.assertTrue(server._watch_step(self.con, job_id, seen))
        form = self.form(job_id)
        self.assertEqual((form["step"], form["state"]), (2, "ready"))
        self.assertEqual(page.input_value("#why"), PROSE)
        self.assertEqual(page.evaluate("() => document.querySelector('#why').closest('[data-hal-mark]').dataset.halMark"),
                         "check")
        # The first page's upload, hidden with the rest of that page, is not outlined.
        self.assertIsNone(page.evaluate("() => document.querySelector('#app').getAttribute('data-hal-mark')"))

        # Hal never submits; once the applicant does, the site's thanks marks the job applied.
        self.assertEqual(self.status(job_id), "applying")
        page.click("#send")
        self.assertFalse(server._watch_step(self.con, job_id, seen))
        self.assertEqual(self.status(job_id), "applied")
        kept = {r["question"]: db.json_value(r["answer"]) for r in self.con.execute("SELECT * FROM answers")}
        self.assertEqual((kept["date of birth"], kept["last name"]), ("01/02/2003", "Byron"))


    def test_fills_a_tab_the_applicant_opened_and_handed_over(self):
        job_id = self.job("ext-adopt", "advert.html?jobId=7")
        server.ACTIONS.pop(job_id)
        mine = self.ctx.new_page()
        mine.goto(f"{self.base}/form.html?jobId=7")
        worker = self.ctx.service_workers[0]
        worker.evaluate("""async () => {
            const [tab] = await chrome.tabs.query({ url: '*://127.0.0.1/form.html?jobId=7' });
            await handTab(tab);
        }""")
        for _ in range(120):
            if server.ACTIONS.get(job_id, {}).get("state") not in (None, "running"):
                break
            mine.wait_for_timeout(500)
        self.assertEqual(server.ACTIONS[job_id]["state"], "done", server.ACTIONS[job_id]["message"])
        self.assertEqual(mine.input_value("#fn"), "Ada")
        self.assertIn("1 to answer", mine.inner_text("#hal-panel .counts"))


if __name__ == "__main__":
    unittest.main()
