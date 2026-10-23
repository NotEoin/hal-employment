"""Gate G6: Hal never presses Submit and never sends anything to an employer.

Three kinds of proof. The static checks read the code for any way to press Submit or Enter. The practice
site (tests/support/practice_site.py) counts every form post on its server: each flow runs in Hal's own
browser and with the browser helper, and the count of sent applications stays 0 after Hal fills, the
person approves and Hal moves on; then the person's own Submit still works. The mail check proves the
only email Hal sends is the person's own daily summary, to the person.

Hal's own window is driven headless here; it is the same code as the window mode. The flows are browser
tests, run with `HAL_TEST_BROWSER=1` (the full checkpoint sets it).
"""
import os
import re
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from typing import ClassVar
from unittest import mock

from support import legacy
from support.legacy import Practice
from support.practice_site import HelperBrowser, PracticeSite
from support.world import World

from jobhunt import autofill, browser, digest, extension
from jobhunt.apply import driver, guard
from jobhunt.core import runner, settings

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule

ROOT = Path(__file__).resolve().parent.parent
CODE = [*sorted((ROOT / "jobhunt").rglob("*.py")), *sorted((ROOT / "extension").glob("*.js"))]
# Pages whose flows end with the form handed over to the person.
HANDED = ("single", "multi", "next-submits", "code", "only-apply", "untyped-button", "enter-submits")
MAX_PAGES = 4        # approvals a practice form needs, at most, before it is handed over
WAIT_S = 60          # the longest a background action may take here
POLL_S = 0.25
# Browser tests run when asked for, as docs/dev/testing.md says; the full checkpoint asks.
BROWSER_TESTS = os.environ.get("HAL_TEST_BROWSER") == "1"


class StaticChecks(unittest.TestCase):
    """The code holds no way to press Submit or Enter (guard 2)."""

    # Each with why it is forbidden. Executor calls such as POOL.submit( are allowed by name.
    FORBIDDEN: ClassVar[dict[str, str]] = {
        r"\bbrowser\.submit\b": "Hal's browser has no submit",
        r"\bextension\.submit\b": "the helper has no submit",
        r"\bdef submit\b": "nothing defines a submit",
        r"(?<!POOL)(?<!pool)(?<!executor)\.submit\(": "a form, page, locator or element is never submitted",
        r"\.requestSubmit\.(call|apply)\b|\brequestSubmit\s*\(": "requestSubmit is only ever overridden",
        r"""press\(\s*['"][^'"]*Enter""": "nothing presses Enter",
        r"""\bkey:\s*['"]Enter""": "no Enter key event is made",
        r"\benter_code\b": "the code is typed, never entered with a submit",
        r"\bmay_send\b": "nothing may send",
        r"\bsigned_in_apply\b": "Apply is never pressed on a signed-in site",
    }
    # The settings shim records the old key as removed, so an old config.toml line is reported, not used, and
    # `config.CONFIG` answers it with [] whatever the file says.
    ALLOWED: ClassVar[set[tuple[str, str]]] = {(r"\bsigned_in_apply\b", "jobhunt/core/settings/legacy_map.py"),
                                               (r"\bsigned_in_apply\b", "jobhunt/config.py")}

    def test_the_code_holds_no_way_to_press_submit_or_enter(self):
        found = []
        for path in CODE:
            rel = path.relative_to(ROOT).as_posix()
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for pattern, why in self.FORBIDDEN.items():
                    if (pattern, rel) not in self.ALLOWED and re.search(pattern, line):
                        found.append(f"{rel}:{n}: {why}: {line.strip()[:100]}")
        self.assertEqual(found, [])

    def test_the_engines_export_no_submit(self):
        for module in (browser, extension):
            for name in ("submit", "enter_code", "_submit", "_enter_code"):
                self.assertFalse(hasattr(module, name), f"{module.__name__}.{name}")

    def test_the_checks_catch_what_they_are_for(self):
        caught = ["page.locator('#send').submit()", "form.requestSubmit()", "box.press('Enter')",
                  "el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))", "browser.submit(job_id)"]
        let_through = ["POOL.submit(wrapper)", "pool.submit(fn)", "proto.requestSubmit = function (by) {",
                       "box.press('ArrowDown')"]
        hit = lambda line: any(re.search(p, line) for p in self.FORBIDDEN)  # noqa: E731
        self.assertEqual([line for line in caught if not hit(line)], [])
        self.assertEqual([line for line in let_through if hit(line)], [])


class Email(unittest.TestCase):
    """The only email Hal sends is the person's own daily summary, to the person (guard 4)."""

    SENDS = re.compile(r"\bsmtplib\b|\.send_message\(|\.sendmail\(")

    def test_the_only_email_hal_sends_is_your_own_summary_to_you(self):
        senders = sorted({p.relative_to(ROOT).as_posix() for p in CODE if p.suffix == ".py"
                          and self.SENDS.search(p.read_text(encoding="utf-8"))})
        self.assertEqual(senders, ["jobhunt/digest.py"])
        sent: list[EmailMessage] = []

        class FakeSmtp:
            def __init__(self, host, port, timeout):
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def login(self, user, password):
                pass

            def send_message(self, msg):
                sent.append(msg)

        with World() as w, mock.patch.object(digest.smtplib, "SMTP_SSL", FakeSmtp):
            password = w.root / "config" / "no-gmail-password"
            password.parent.mkdir(parents=True, exist_ok=True)
            password.write_text("made-up password")
            inbox = digest.gmail.ADDRESS
            own = {inbox, "rowan.made-up@example.org"}
            digest.send(w.con)
            settings.set("notify.daily_to", "rowan.made-up@example.org", by="you")
            digest.send(w.con)
        self.assertEqual(len(sent), 2)
        for msg in sent:
            self.assertIn(msg["To"], own)
            self.assertIsNone(msg["Cc"])
            self.assertIsNone(msg["Bcc"])
            self.assertEqual(msg["From"], inbox)


class Routes(unittest.TestCase):
    """No route of the server sends a form: Hal only hands the last page over."""

    def test_no_route_is_for_submitting(self):
        from jobhunt.web import app, router

        app.load_routes()
        self.assertTrue(router.ROUTES)
        self.assertEqual([pattern for _, pattern in router.ROUTES if "submit" in pattern.lower()], [])


class Flows:
    """Every practice flow, run by a subclass in one mode. `helper` says which."""

    helper = False

    @classmethod
    def start_site(cls):
        cls.site = PracticeSite().__enter__()

    def setUp(self):
        self.site.reset()

    def practice_job(self, name: str, page: str) -> str:
        job_id = self.job(f"{'helper' if self.helper else 'window'}-{name}", page)
        self.con.execute("UPDATE jobs SET url = ? WHERE id = ?", (f"{self.site.base}/{page}", job_id))
        self.con.commit()
        return job_id

    def on_page(self, job_id: str, page: str, act):
        """Runs `act(tab)` in the job's tab, as the person would."""
        if self.helper:
            return self.in_tab(page, act)
        return browser.BROWSER.call(lambda b: act(b.tab(job_id)))

    def to_hand_over(self, job_id: str) -> str:
        """Fills the form and approves each page, as the person would, until Hal hands it over."""
        said = driver.act_auto(self.con, job_id)
        for _ in range(MAX_PAGES):
            if (self.form(job_id) or {}).get("handed_over"):
                return said
            said = driver.act_approve(self.con, job_id, [])
        self.fail(f"never handed over: {said}")

    def would_submit(self, job_id: str) -> list[str]:
        return [r["detail"] for r in self.con.execute(
            "SELECT detail FROM site_events WHERE what = 'would_submit' AND job_id = ?", (job_id,))]

    def finish(self, job_id: str) -> str:
        """Waits for a background action to end and returns its message."""
        deadline = time.monotonic() + WAIT_S
        while runner.ACTIONS.get(job_id, {}).get("state") == "running" and time.monotonic() < deadline:
            time.sleep(POLL_S)
        return runner.ACTIONS[job_id]["message"]

    def test_hal_never_presses_submit_in_any_mode(self):
        for page in HANDED:
            with self.subTest(page=page):
                job_id = self.practice_job(f"any-{page}", f"{page}.html")
                self.assertEqual(self.to_hand_over(job_id), driver.HANDED_OVER)
                self.assertEqual(self.site.sent(), 0, self.site.posts)
                form = self.form(job_id)
                self.assertEqual((form["state"], form["message"]), ("ready", driver.HANDED_OVER))
                self.assertEqual(self.status(job_id), "applying")
        # A form that moves on with a real submit was moved on by Hal pressing its Next, and only that.
        self.assertEqual(self.site.steps("next-submits"), 1)

    def test_your_own_submit_still_works_after_hal_hands_over(self):
        job_id = self.practice_job("yours", "single.html")
        self.to_hand_over(job_id)
        driver.stop_watching()   # the test takes each look at the tab itself, so no watch races it
        self.on_page(job_id, "single.html", lambda tab: tab.click("text=Submit application"))
        self.assertEqual(self.site.sent("single"), 1)
        self.assertFalse(driver._watch_step(self.con, job_id, {}))
        self.assertEqual(self.status(job_id), "applied")

    def test_types_the_emailed_code_and_leaves_the_button_to_you(self):
        job_id = self.practice_job("code", "code.html")
        self.to_hand_over(job_id)
        driver.stop_watching()   # the test takes each look at the tab itself, so no watch races it
        self.on_page(job_id, "code.html", lambda tab: tab.click("#send"))
        seen = {}
        # a look while the helper cannot read the tab yet ("busy") changes nothing; the watch looks again, as here
        deadline = time.monotonic() + WAIT_S
        while True:
            self.assertTrue(driver._watch_step(self.con, job_id, seen))
            if seen.get("code"):
                break
            if time.monotonic() > deadline:
                self.fail("the helper never showed Hal the code box")
            time.sleep(POLL_S)
        self.assertEqual(self.on_page(job_id, "code.html", lambda tab: tab.input_value("#code")), "A1b2C3d4")
        self.assertEqual(self.site.sent(), 0)
        self.on_page(job_id, "code.html", lambda tab: tab.click("#send"))
        self.assertEqual(self.site.sent("code"), 1)
        self.assertFalse(driver._watch_step(self.con, job_id, seen))
        self.assertEqual(self.status(job_id), "applied")

    def test_a_signed_in_apply_waits_for_you_to_press_it_then_follows(self):
        job_id = self.practice_job("signed-in", "signed-in.html")
        runner.ACTIONS.pop(job_id, None)
        runner.run_action(job_id, "applying", driver.act_auto, job_id)
        deadline = time.monotonic() + WAIT_S
        while "press Apply" not in runner.ACTIONS[job_id]["message"] and time.monotonic() < deadline:
            time.sleep(POLL_S)
        self.assertIn("press Apply on", runner.ACTIONS[job_id]["message"])
        time.sleep(1)
        self.assertEqual(self.site.sent(), 0)
        self.on_page(job_id, "signed-in.html", lambda tab: tab.click("text=Apply now"))
        said = self.finish(job_id)
        self.assertEqual(runner.ACTIONS[job_id]["state"], "done", said)
        # Hal followed through to the form the board showed next, filled it, and pressed nothing.
        self.assertEqual(self.site.posts["/sent/signed-in"], 1)
        self.assertEqual(self.site.sent("after-apply"), 0)
        self.assertEqual(self.field(job_id, "First name")["value"], "Ada")
        driver.act_approve(self.con, job_id, [])
        self.assertEqual(self.site.sent("after-apply"), 0)

    def test_the_guard_refuses_submit_like_controls_the_ai_picks(self):
        job_id = self.practice_job("review", "review.html")
        offered = []
        picks = mock.patch.object(autofill, "choose_control", lambda job, found: (
            offered.append([c["name"] for c in found["controls"]]) or "Confirm and submit"))
        with picks:
            said = driver.act_auto(self.con, job_id)
            for _ in range(MAX_PAGES):
                if guard.STOPPED in said:
                    break
                said = driver.act_approve(self.con, job_id, [])
        self.assertIn(guard.STOPPED, said)
        self.assertNotIn("Confirm and submit", offered[0])
        self.assertIn("Back", offered[0])
        self.assertTrue(self.would_submit(job_id))
        with self.assertRaises(guard.WouldSubmit):
            browser.press_control(job_id, "Confirm and submit")
        if self.helper:
            # The helper's own guard refuses it too, whatever the server asks.
            out = extension._ask("press", job=job_id, kind="named", name="Confirm and submit",
                                 page={"form": False, "final": False})
            self.assertFalse(out["pressed"])
            self.assertIn("would send", out["refused"])
        self.assertEqual(self.site.sent(), 0)

    def test_hal_never_presses_enter_in_any_field(self):
        job_id = self.practice_job("enter", "enter-submits.html")
        self.to_hand_over(job_id)
        self.assertEqual(self.field(job_id, "Which city")["value"], "London")
        note = {**self.field(job_id, "Anything else"), "value": "First line\nsecond line", "skip": False}
        out = browser.fill(job_id, [note], Path(tempfile.mkdtemp()) / "shot.png")
        self.assertEqual(out["errors"], {})
        page = "enter-submits.html"
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.input_value("#note")), "First line second line")
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.evaluate("() => window.halEnters")), 0)
        self.assertEqual(self.site.sent(), 0)

    def test_a_button_with_no_type_in_a_form_counts_as_submit(self):
        job_id = self.practice_job("untyped", "untyped-button.html")
        self.to_hand_over(job_id)
        with self.assertRaises(guard.WouldSubmit):
            browser.press_control(job_id, "Add another referee")
        self.assertEqual(self.site.sent(), 0)
        self.assertTrue(any("Add another referee" in d for d in self.would_submit(job_id)))

    def test_a_page_submit_during_a_fill_is_cancelled_and_logged(self):
        job_id = self.practice_job("auto", "auto-submit.html")
        self.to_hand_over(job_id)
        self.assertEqual(self.site.sent(), 0)
        self.assertTrue(any("send the form" in d for d in self.would_submit(job_id)), self.would_submit(job_id))
        # The block went with the command: the page's own form methods are the browser's again.
        native = "() => HTMLFormElement.prototype.submit.toString().includes('[native code]')"
        self.assertTrue(self.on_page(job_id, "auto-submit.html", lambda tab: tab.evaluate(native)))
        self.on_page(job_id, "auto-submit.html", lambda tab: tab.click("text=Submit application"))
        self.assertEqual(self.site.sent("auto-submit"), 1)


@unittest.skipUnless(BROWSER_TESTS, "browser test: set HAL_TEST_BROWSER=1")
class InHalsWindow(Flows, Practice):
    """Every flow in Hal's own browser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_site()

    @classmethod
    def tearDownClass(cls):
        try:
            browser.BROWSER.call(lambda b: b._ctx and b._ctx.close())
        except Exception:  # the browser never started
            pass
        cls.site.__exit__(None, None, None)
        super().tearDownClass()


@unittest.skipUnless(BROWSER_TESTS, "browser test: set HAL_TEST_BROWSER=1")
class WithTheHelper(Flows, HelperBrowser, Practice):
    """Every flow through the browser helper, in a browser that loads it as the applicant's own does."""

    helper = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_site()
        cls.start_helper(Path(cls.tmp.name))

    def test_the_helpers_browser_keeps_up_while_the_test_waits_on_hal(self):
        """Regression: Playwright was serviced only inside a call, so on Windows the post of Hal's own Next press
        waited for the test's next call into the browser, and the form "would not move on" (CI, 2026-10-23)."""
        seen: list[str] = []

        def listen(tab):
            seen.append(tab.url)

        self.on_helper(lambda: self.ctx.on("page", listen))
        try:
            # the helper opens a tab a second from now, while this thread waits in plain Python as it does on Hal
            self.on_helper(lambda: self.worker.evaluate(
                "url => { setTimeout(() => chrome.tabs.create({ url }), 1000); return true; }",
                f"{self.site.base}/single.html"))
            deadline = time.monotonic() + WAIT_S
            while not seen and time.monotonic() < deadline:
                time.sleep(POLL_S)
        finally:
            self.on_helper(lambda: self.ctx.remove_listener("page", listen))
        self.assertTrue(seen, "Playwright heard nothing while the test waited")

    def tearDown(self):
        # A test's tabs and watches end with it: an old job's watch keeps bringing its own tab to the front in
        # Hal's window, and two tabs at one address would leave it to chance which one `in_tab` acts on.
        driver.stop_watching()
        self.close_tabs()
        super().tearDown()

    @classmethod
    def tearDownClass(cls):
        cls.stop_helper()
        cls.site.__exit__(None, None, None)
        super().tearDownClass()


del Flows  # the mixin's tests run only through the two modes above

if __name__ == "__main__":
    unittest.main()
