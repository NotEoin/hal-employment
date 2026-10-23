"""Applying through the browser helper, in a browser that loads it unpacked as the applicant's own does.

The helper is paired the way a person pairs it: a signed-in window, here a plain HTTP client
standing for the app window in another browser, asks for a code, and the helper's browser opens Hal's
/pair page, where pair.js hands the code to the helper. Every request after that carries the helper's key.
The form tests are the InYourBrowser tests moved from tests/test_apply.py, now over a paired helper.
"""
import json
import os
import shutil
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from support import legacy
from support.api import Client
from support.legacy import EXTENSION, PROSE, Practice

from jobhunt import db, extension
from jobhunt.apply import driver, helpers
from jobhunt.web import app, auth, server

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule

PAIR_WAIT_MS = 20000      # a fresh browser's helper may take a while to start
CONNECT_WAIT_S = 30
SHORT_POLL_S = 2          # keeps the reload test quick: a held poll ends this soon
STEP_S = 0.25


class PairedHelper:
    """Mixin: Hal's real server, and Playwright's Chromium with the helper loaded and paired."""

    @classmethod
    def start_hal(cls) -> None:
        cls.hal = app.make_server(0)
        cls.port = cls.hal.server_address[1]
        threading.Thread(target=cls.hal.serve_forever, name="hal-for-helper", daemon=True).start()

    @classmethod
    def stop_hal(cls) -> None:
        cls.hal.stop()

    @classmethod
    def window(cls) -> Client:
        """A signed-in app window: the person's Edge, say, where the helper is not."""
        c = Client(cls.port)
        url = urlparse(auth.mint_open_url(port=cls.port))
        if c.get(f"{url.path}?{url.query}").status != 302:
            raise AssertionError("the open link did not sign the window in")
        return c

    @classmethod
    def launch(cls, pw, folder: Path, profile: Path):
        """The browser with the helper in `folder` loaded unpacked, and the helper's service worker."""
        ctx = pw.chromium.launch_persistent_context(
            str(profile), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={folder}", f"--load-extension={folder}"])
        worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        return ctx, worker

    @staticmethod
    def developer_mode(ctx) -> None:
        """Switches on "Developer mode", as the person does to load the helper.

        A helper loaded from the command line runs without it, but `chrome.runtime.reload()` re-loads it as an
        ordinary unpacked helper, which Chromium turns off while Developer mode is off (checked 2026-10-23).
        """
        page = ctx.new_page()
        page.goto("chrome://extensions")
        page.evaluate("() => chrome.developerPrivate.updateProfileConfiguration({inDeveloperMode: true})")
        page.close()

    @classmethod
    def pair_in(cls, ctx, window: Client, host: str = "127.0.0.1") -> str:
        """Pairs the helper in `ctx` from `window`, as the setup step's Connect does; returns its id."""
        code = window.post("/api/v2/helpers/pairing-code", {"browser": "chrome"}).json()["code"]
        page = ctx.new_page()
        page.goto(f"http://{host}:{cls.port}/pair#code={code}")
        page.wait_for_selector('#hal-pair-result[data-ok="yes"]', timeout=PAIR_WAIT_MS)
        page.close()
        with closing(db.connect()) as con:
            helper_id = con.execute("SELECT id FROM helpers ORDER BY rowid DESC LIMIT 1").fetchone()["id"]
        deadline = time.monotonic() + CONNECT_WAIT_S
        while not extension.connected(helper_id) and time.monotonic() < deadline:
            time.sleep(STEP_S)
        if not extension.connected(helper_id):
            raise AssertionError("the paired helper never polled")
        return helper_id


class InYourBrowser(PairedHelper, Practice):
    """Forms filled through the paired helper, in a browser that loads it as the applicant's own does."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        super().setUpClass()
        extension.reset()
        tmp = Path(cls.tmp.name)
        cls.start_hal()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "extension"
        cls.pw = sync_playwright().start()
        cls.ctx, cls.worker = cls.launch(cls.pw, EXTENSION, tmp / "helper-browser")
        cls.helper_id = cls.pair_in(cls.ctx, cls.window())

    @classmethod
    def tearDownClass(cls):
        cls.ctx.close()
        cls.pw.stop()
        cls.stop_hal()
        extension.reset()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"
        super().tearDownClass()

    def tab(self, address: str):
        # Playwright hears of tabs the extension opened only while it is waiting on something.
        self.ctx.pages[0].wait_for_timeout(300)
        return next(t for t in reversed(self.ctx.pages) if address in t.url)

    def test_the_paired_helper_keeps_hals_address_and_its_own_key(self):
        stored = self.worker.evaluate("() => chrome.storage.local.get('pairing').then(s => s.pairing)")
        self.assertEqual(stored["base"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(stored["helper_id"], self.helper_id)
        self.assertEqual(stored["install_id"], auth.install_id())
        with closing(db.connect()) as con:
            row = helpers.row(con, self.helper_id)
        self.assertEqual((row["version"], row["protocol"], row["extension_id"]),
                         (helpers.wanted_version(), 1, helpers.manifest_id()))

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
        self.assertEqual(page.inner_text("#uni-shown"), "Fernvale University")
        self.assertTrue(page.is_checked("#rtw-y"))
        self.assertEqual(page.input_value("#heard"), "Company website")
        self.assertEqual(page.input_value("#notice"), "0")
        self.assertEqual(page.input_value("#dob"), "")

    def test_marks_the_page_then_follows_the_applicant_through_to_submitting(self):
        job_id = self.job("ext-journey", "form.html")
        server.act_auto(self.con, job_id)
        driver.stop_watching()   # the test takes each look at the tab itself, so no watch races it
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
        rows = self.con.execute("SELECT * FROM answers2 WHERE scope = 'everywhere'")
        kept = {r["question"]: db.json_value(r["answer"]) for r in rows}
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
        # the tab is in this helper's browser, so the application stays with this helper
        self.assertEqual(self.con.execute("SELECT helper_id FROM applications WHERE job_id = ?",
                                          (job_id,)).fetchone()["helper_id"], self.helper_id)


@unittest.skipUnless(os.environ.get("HAL_TEST_BROWSER") == "1", "browser tests run with HAL_TEST_BROWSER=1")
class Reloading(PairedHelper, Practice):
    """A helper loaded from a folder that git updates."""

    def test_an_older_helper_reloads_itself_and_comes_back_newer(self):
        from playwright.sync_api import sync_playwright

        extension.reset()
        self.addCleanup(extension.reset)
        tmp = Path(self.tmp.name)
        folder = tmp / "Browser helper"
        shutil.copytree(EXTENSION, folder)
        self.start_hal()
        self.addCleanup(self.stop_hal)
        with mock.patch.object(extension, "POLL_WAIT", SHORT_POLL_S), sync_playwright() as pw:
            ctx, _ = self.launch(pw, folder, tmp / "reload-browser")
            try:
                self.developer_mode(ctx)
                helper_id = self.pair_in(ctx, self.window())
                # git brings a newer helper: the folder changes, and the server now wants that version
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                manifest["version"] = "1.0.1"
                (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with mock.patch.object(helpers, "wanted_version", return_value="1.0.1"):
                    deadline = time.monotonic() + CONNECT_WAIT_S
                    version = None
                    while time.monotonic() < deadline:
                        with closing(db.connect()) as con:
                            version = helpers.row(con, helper_id)["version"]
                        if version == "1.0.1":
                            break
                        time.sleep(STEP_S)
                    self.assertEqual(version, "1.0.1")
                    self.assertFalse(extension.still_older(helper_id))
            finally:
                ctx.close()

    def test_the_helper_pairs_through_localhost_and_keeps_that_address(self):
        from playwright.sync_api import sync_playwright

        extension.reset()
        self.addCleanup(extension.reset)
        tmp = Path(self.tmp.name)
        self.start_hal()
        self.addCleanup(self.stop_hal)
        with sync_playwright() as pw:
            ctx, worker = self.launch(pw, EXTENSION, tmp / "localhost-browser")
            try:
                self.pair_in(ctx, self.window(), host="localhost")
                stored = worker.evaluate("() => chrome.storage.local.get('pairing').then(s => s.pairing)")
                self.assertEqual(stored["base"], f"http://localhost:{self.port}")
            finally:
                ctx.close()


if __name__ == "__main__":
    unittest.main()
