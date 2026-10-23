"""Applying through the browser helper, in a browser that loads it unpacked as the applicant's own does.

The helper is paired the way a person pairs it: a signed-in window, here a plain HTTP client
standing for the app window in another browser, asks for a code, and the helper's browser opens Hal's
/pair page, where pair.js hands the code to the helper. Every request after that carries the helper's key.
The form tests are the InYourBrowser tests moved from tests/test_apply.py, now over a paired helper.

The helper's browser runs on a thread of its own that keeps Playwright serviced between calls
(`support.practice_site.HelperBrowser`): a test waits in plain Python while Hal works through the helper, and
on Windows an unserviced browser held up a page's own form post. Every Playwright call goes
through `on_helper` or `in_tab`.
"""
import concurrent.futures
import json
import os
import queue
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
from support.practice_site import LAUNCH_S, HelperBrowser

from jobhunt import db, extension
from jobhunt.apply import driver, helpers
from jobhunt.web import app, auth, server

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule

PAIR_WAIT_MS = 20000      # a fresh browser's helper may take a while to start
CONNECT_WAIT_S = 30
SHORT_POLL_S = 2          # keeps the reload test quick: a held poll ends this soon
STEP_S = 0.25
ADOPT_WAIT_S = 60         # finding a handed-over tab's job and filling it


class PairedHelper(HelperBrowser):
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
    def launch(cls, folder: Path, profile_home: Path) -> None:
        """Starts the browser with the helper in `folder` loaded unpacked, on its own thread (`HelperBrowser`).

        The browser's profile is `profile_home / "helper-browser"`; `cls.ctx` and `cls.worker` live on that thread.
        """
        cls._calls = queue.Queue()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        cls._owner = threading.Thread(target=cls._own_browser, args=(profile_home, folder, ready),
                                      name="helper-browser", daemon=True)
        cls._owner.start()
        ready.result(LAUNCH_S)

    @classmethod
    def close_browser(cls) -> None:
        """Closes the browser `launch` started and ends its thread."""
        cls._calls.put(None)
        cls._owner.join(LAUNCH_S)

    @classmethod
    def developer_mode(cls) -> None:
        """Switches on "Developer mode", as the person does to load the helper.

        A helper loaded from the command line runs without it, but `chrome.runtime.reload()` re-loads it as an
        ordinary unpacked helper, which Chromium turns off while Developer mode is off (checked 2026-10-23).
        """
        def switch():
            page = cls.ctx.new_page()
            page.goto("chrome://extensions")
            page.evaluate("() => chrome.developerPrivate.updateProfileConfiguration({inDeveloperMode: true})")
            page.close()

        cls.on_helper(switch)

    @classmethod
    def pair_in(cls, window: Client, host: str = "127.0.0.1") -> str:
        """Pairs the helper from `window`, as the setup step's Connect does; returns its id."""
        code = window.post("/api/v2/helpers/pairing-code", {"browser": "chrome"}).json()["code"]

        def pair():
            page = cls.ctx.new_page()
            page.goto(f"http://{host}:{cls.port}/pair#code={code}")
            page.wait_for_selector('#hal-pair-result[data-ok="yes"]', timeout=PAIR_WAIT_MS)
            page.close()

        cls.on_helper(pair)
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
        super().setUpClass()
        extension.reset()
        cls.start_hal()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "extension"
        cls.launch(EXTENSION, Path(cls.tmp.name))
        cls.helper_id = cls.pair_in(cls.window())

    @classmethod
    def tearDownClass(cls):
        cls.close_browser()
        cls.stop_hal()
        extension.reset()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"
        super().tearDownClass()

    def tearDown(self):
        # A test's tabs and watches end with it, so the next test meets no tab at an address it uses.
        driver.stop_watching()
        self.close_tabs()
        super().tearDown()

    def in_worker(self, script: str, arg=None):
        """Runs `script` in the helper's service worker and returns its result."""
        return self.on_helper(lambda: self.worker.evaluate(script, arg))

    def test_the_paired_helper_keeps_hals_address_and_its_own_key(self):
        stored = self.in_worker("() => chrome.storage.local.get('pairing').then(s => s.pairing)")
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
        tabs = self.in_worker("() => chrome.tabs.query({}).then(ts => ts.map(t => [t.url, t.windowId]))")
        own = self.in_worker("() => chrome.storage.session.get('halWindow').then(s => s.halWindow)")
        # Earlier tests may have opened the window already; either way both jobs are in it, and it is not the only one.
        self.assertEqual({w for url, w in tabs if "ext-window" in url}, {own})
        self.assertGreater(len({w for _, w in tabs}), 1)

    def test_fills_the_first_page_and_leaves_what_it_cannot_answer(self):
        job_id = self.job("ext-fill", "form.html")
        said = server.act_auto(self.con, job_id)
        self.assertIn("1 required question", said)
        shown = self.in_tab("form.html", lambda page: (
            page.input_value("#fn"), page.inner_text("#uni-shown"), page.is_checked("#rtw-y"),
            page.input_value("#heard"), page.input_value("#notice"), page.input_value("#dob")))
        self.assertEqual(shown, ("Ada", "Fernvale University", True, "Company website", "0", ""))

    def test_marks_the_page_then_follows_the_applicant_through_to_submitting(self):
        job_id = self.job("ext-journey", "form.html")
        server.act_auto(self.con, job_id)
        driver.stop_watching()   # the test takes each look at the tab itself, so no watch races it
        mark_of = "s => document.querySelector(s).closest('[data-hal-mark]')?.getAttribute('data-hal-mark') || null"
        marks = self.in_tab("form.html", lambda page: {s: page.evaluate(mark_of, s)
                                                       for s in ("#fn", "#dob", "#notice")})
        self.assertEqual(marks, {"#fn": "ok", "#dob": "answer", "#notice": "ok"})
        counts, listed = self.in_tab("form.html", lambda page: (
            page.inner_text("#hal-panel .counts"), page.inner_text("#hal-panel ul")))
        self.assertIn("1 to answer", counts)
        self.assertIn("Date of birth", listed)

        # The applicant answers what Hal could not, changes one of its answers, and moves on.
        seen = {}
        self.in_tab("form.html", lambda page: (page.fill("#dob", "01/02/2003"), page.fill("#ln", "Byron")))
        self.assertEqual(self.in_tab("form.html", lambda page: page.evaluate(mark_of, "#dob")), "edited")
        self.assertTrue(server._watch_step(self.con, job_id, seen))
        self.in_tab("form.html", lambda page: page.click("#next"))
        self.assertTrue(server._watch_step(self.con, job_id, seen))
        form = self.form(job_id)
        self.assertEqual((form["step"], form["state"]), (2, "ready"))
        self.assertEqual(self.in_tab("form.html", lambda page: page.input_value("#why")), PROSE)
        self.assertEqual(self.in_tab("form.html", lambda page: page.evaluate(mark_of, "#why")), "check")
        # The first page's upload, hidden with the rest of that page, is not outlined.
        self.assertIsNone(self.in_tab("form.html", lambda page: page.evaluate(
            "() => document.querySelector('#app').getAttribute('data-hal-mark')")))

        # Hal never submits; once the applicant does, the site's thanks marks the job applied.
        self.assertEqual(self.status(job_id), "applying")
        self.in_tab("form.html", lambda page: page.click("#send"))
        self.assertFalse(server._watch_step(self.con, job_id, seen))
        self.assertEqual(self.status(job_id), "applied")
        rows = self.con.execute("SELECT * FROM answers2 WHERE scope = 'everywhere'")
        kept = {r["question"]: db.json_value(r["answer"]) for r in rows}
        self.assertEqual((kept["date of birth"], kept["last name"]), ("01/02/2003", "Byron"))

    def test_fills_a_tab_the_applicant_opened_and_handed_over(self):
        job_id = self.job("ext-adopt", "advert.html?jobId=7")
        server.ACTIONS.pop(job_id)
        self.on_helper(lambda: self.ctx.new_page().goto(f"{self.base}/form.html?jobId=7"))
        self.in_worker("""async () => {
            const [tab] = await chrome.tabs.query({ url: '*://127.0.0.1/form.html?jobId=7' });
            await handTab(tab);
        }""")
        deadline = time.monotonic() + ADOPT_WAIT_S
        while server.ACTIONS.get(job_id, {}).get("state") in (None, "running") and time.monotonic() < deadline:
            time.sleep(STEP_S)
        self.assertEqual(server.ACTIONS[job_id]["state"], "done", server.ACTIONS[job_id]["message"])
        mine = "form.html?jobId=7"
        self.assertEqual(self.in_tab(mine, lambda page: page.input_value("#fn")), "Ada")
        self.assertIn("1 to answer", self.in_tab(mine, lambda page: page.inner_text("#hal-panel .counts")))
        # the tab is in this helper's browser, so the application stays with this helper
        self.assertEqual(self.con.execute("SELECT helper_id FROM applications WHERE job_id = ?",
                                          (job_id,)).fetchone()["helper_id"], self.helper_id)


class Forgetting(unittest.TestCase):
    """`extension.reset` forgets every helper, even one whose poll is still held open."""

    def test_a_poll_held_open_when_everything_is_forgotten_does_not_bring_its_helper_back(self):
        """Regression: a stopped server's last held poll ended after `reset` and marked its helper in touch, so
        the next test class took that gone helper for its own and its commands found no helper."""
        extension.reset()
        self.addCleanup(extension.reset)
        with mock.patch.object(extension, "POLL_WAIT", SHORT_POLL_S), \
                mock.patch.object(extension, "_old_allowed", lambda: True):
            held = threading.Thread(target=extension.poll, name="held-poll")
            held.start()
            deadline = time.monotonic() + CONNECT_WAIT_S
            while not extension.connected() and time.monotonic() < deadline:
                time.sleep(STEP_S / 10)
            self.assertTrue(extension.connected(), "the poll never began")
            extension.reset()
            held.join(SHORT_POLL_S * 5)
        self.assertFalse(held.is_alive())
        self.assertFalse(extension.connected())


@unittest.skipUnless(os.environ.get("HAL_TEST_BROWSER") == "1", "browser tests run with HAL_TEST_BROWSER=1")
class Reloading(PairedHelper, Practice):
    """A helper loaded from a folder that git updates."""

    def test_an_older_helper_reloads_itself_and_comes_back_newer(self):
        extension.reset()
        self.addCleanup(extension.reset)
        tmp = Path(self.tmp.name)
        folder = tmp / "Browser helper"
        shutil.copytree(EXTENSION, folder)
        self.start_hal()
        self.addCleanup(self.stop_hal)
        with mock.patch.object(extension, "POLL_WAIT", SHORT_POLL_S):
            self.launch(folder, tmp / "reload")
            try:
                self.developer_mode()
                helper_id = self.pair_in(self.window())
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
                self.close_browser()

    def test_the_helper_pairs_through_localhost_and_keeps_that_address(self):
        extension.reset()
        self.addCleanup(extension.reset)
        self.start_hal()
        self.addCleanup(self.stop_hal)
        self.launch(EXTENSION, Path(self.tmp.name) / "localhost")
        try:
            self.pair_in(self.window(), host="localhost")
            stored = self.on_helper(lambda: self.worker.evaluate(
                "() => chrome.storage.local.get('pairing').then(s => s.pairing)"))
            self.assertEqual(stored["base"], f"http://localhost:{self.port}")
        finally:
            self.close_browser()


if __name__ == "__main__":
    unittest.main()
