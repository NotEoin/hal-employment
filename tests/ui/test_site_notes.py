"""Notes about job sites and "Something wrong here?" in a real browser against the real server.

The tab is drawn inside What Hal remembers, whose page may not be there yet, so these tests draw the
tab and open the dialog themselves through the app's own modules. They need `HAL_TEST_BROWSER=1` and
Playwright, as every browser test does (docs/dev/testing.md). The site, employer and applicant are made up.
"""
from __future__ import annotations

import contextlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.fake_ai import fake_ai
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db
from jobhunt.core import paths, runner
from jobhunt.core.settings import store
from jobhunt.sites import notes
from jobhunt.web import app, auth

JOB = "manual:civic-example"
FORM_URL = "https://apply.workable.com/civic-example/j/AB12CD/apply/"
DRAFT = {"kind": "field_transform", "cond": {"label": "location"}, "act": {"transform": "city_only"},
         "text": "In \"Location\", type only the city (for example \"Leeds\")"}
WAIT_S = 10
MOUNT_TAB = """async () => {
  const { html, render } = await import("/app/core/html.js");
  const { SitesTab } = await import("/app/memory/sites-tab.js");
  let el = document.getElementById("sites-under-test");
  if (!el) { el = document.createElement("div"); el.id = "sites-under-test"; el.setAttribute("role", "region");
             el.setAttribute("aria-label", "Notes about job sites"); document.querySelector("#main").prepend(el); }
  render(html`<${SitesTab} />`, el);
}"""
OPEN_REPORT = """async d => {
  const { openDialog } = await import("/app/core/store.js");
  openDialog("report", d);
}"""


@contextlib.contextmanager
def app_tab(browser, size: str = "wide", scheme: str = "light"):
    """The new server in a fresh home, and a signed-in tab on it."""
    store.drop_cache()
    httpd = app.make_server(0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, name="ui-test-server", daemon=True).start()
    p = AppPage(browser, f"http://127.0.0.1:{port}", size, scheme)
    try:
        p.page.goto(auth.mint_open_url(port=port))
        p.settle()
        yield p
    finally:
        p.close()
        httpd.stop()


def until(check, timeout: float = WAIT_S):
    """Waits for `check()` to be truthy and returns it, failing after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


@skip_unless_browser
class SitesTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="hal-ui-sites-"))
        self.enterContext(paths.set_override(Path(tmp).resolve()))
        self.addCleanup(runner.idle, WAIT_S)
        store.drop_cache()
        paths.ensure()
        self.con = self.enterContext(contextlib.closing(db.connect()))
        self.con.execute("INSERT INTO jobs (id, source, company, title, url, status, first_seen, last_seen) "
                         "VALUES (?, 'manual', 'Civic Example Trust', 'Research Assistant', ?, 'applying', "
                         "'2026-10-20', '2026-10-20')", (JOB, FORM_URL))
        self.con.execute("INSERT INTO applications (job_id, stage, form, updated) VALUES (?, 'form', ?, '2026-10-20')",
                         (JOB, json.dumps({"url": FORM_URL, "fields": [{"label": "Location", "kind": "text"}]})))
        self.con.commit()
        notes.seen(FORM_URL, account="none", con=self.con)
        self.mine = notes.add("site", "workable.com", "note", {}, {}, "Their form times out after 20 minutes",
                              con=self.con)

    def status(self, note_id: str) -> str:
        return next(s.status for s in notes.stored(self.con) if s.note.id == note_id)

    def test_every_note_says_where_it_came_from_and_can_be_switched_off(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size), app_tab(self.browser, size, scheme) as p:
                pg = p.page
                pg.evaluate(MOUNT_TAB)
                card = pg.locator("[data-site='workable.com']")
                card.locator("[data-note]").first.wait_for()
                words = card.inner_text()
                for expected in ("workable.com", "Workable", "No account needed", "Last visit: Today",
                                 "Their form times out after 20 minutes", "You told Hal",
                                 "Something wrong on this site?"):
                    self.assertIn(expected, words)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                row = card.locator(f"[data-note='{self.mine}']")
                want = "off" if self.status(self.mine) == "active" else "active"
                row.get_by_role("switch").click()
                until(lambda want=want: self.status(self.mine) == want)
                self.assertEqual(row.get_by_role("switch").get_attribute("aria-checked"),
                                 "false" if want == "off" else "true")
                # the global notes are on their own card; built-in notes switch off but have no Delete
                every = pg.locator("[data-site='global']")
                every.get_by_role("button", name="Every site").click()
                built_in = every.locator("[data-note='seed:workable-no-escape']")
                built_in.wait_for()
                self.assertIn("Close dropdown lists by clicking the form, never with Escape", built_in.inner_text())
                self.assertIn("Built in", built_in.inner_text())
                self.assertEqual(built_in.get_by_role("switch").count(), 1)
                self.assertEqual(built_in.get_by_role("button", name="Delete").count(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_something_wrong_here_shows_the_note_before_saving_and_offers_three_ways_out(self) -> None:
        with fake_ai({"site_note": json.dumps(DRAFT)}), app_tab(self.browser) as p, \
                mock.patch("jobhunt.apply.queries._close_quietly", lambda job_id: None):
            pg = p.page
            pg.evaluate(OPEN_REPORT, {"site": "workable.com", "platform": "Workable", "job_id": JOB})
            dialog = pg.get_by_role("dialog")
            dialog.get_by_text("Tell Hal what happened in your own words.").wait_for()
            self.assertIn("On workable.com (Workable)", dialog.inner_text())
            dialog.get_by_role("button", name="This answer is wrong").click()
            dialog.get_by_label("What happened?").fill("The Location box only takes the city.")
            dialog.get_by_role("button", name="Next").click()
            dialog.get_by_text("Here is what Hal will remember").wait_for()
            self.assertIn("On workable.com: In \"Location\", type only the city", dialog.inner_text())
            self.assertEqual(notes.for_page(FORM_URL, con=self.con).all("field_transform", label="Location"), [])
            self.assertEqual(p.serious_axe(), [])
            dialog.get_by_role("radio", name="All Workable sites").click()
            self.assertIn("On every site that uses Workable:", dialog.inner_text())
            dialog.get_by_role("button", name="Save this note").click()
            dialog.get_by_text("Saved. What now?").wait_for()
            saved = notes.for_page("https://apply.workable.com/moss-row/j/1/", con=self.con).first(
                "field_transform", label="Location")
            self.assertEqual((saved.scope_kind, saved.scope_key), ("ats", "workable"))
            for choice in ("Try again with this note", "I'll finish it myself", "Skip this job for now"):
                self.assertEqual(dialog.get_by_role("button", name=choice).count(), 1, choice)
            dialog.get_by_role("button", name="Skip this job for now").click()
            pg.get_by_text("Skipped. The job stays in Applications if you want to come back.").wait_for()
            self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
