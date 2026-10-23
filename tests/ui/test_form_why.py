"""The Form step's "Why this answer?", the notes Hal used there, and the Saved answers tab, in a real browser.

They run the real files of `jobhunt/web/static/app/` against the fake server of `tests/support/fake_api.py`
with a made-up person and a made-up application. The Saved answers tab is drawn on its own, because What
Hal remembers holds it. They need `HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import json
import unittest

from support.fake_api import MADE_UP_APP, FakeServer, FakeState
from support.ui import AppPage, chromium, skip_unless_browser

APP = "app-demo%3A1"
APP_PATH = "/applications/demo:1"
PRIVATE_WHY = "From your private answers. These are never shown to the AI or saved in logs."
CANARY = "SYNTH-PRIVATE-5521"
CITY_NOTE = {"id": "db:1", "text": "In \"Location\", type only the city", "origin": "you",
             "what": "Typed only the city for Location, because of your note for madeup-trust.example."}
ESCAPE_NOTE = {"id": "seed:workable-no-escape", "text": "Close dropdown lists by clicking the form, never with Escape",
               "origin": "builtin"}


def field(hid: str, q: str, a: str, src: str, label: str, why: str, ok: bool = True, notes=()) -> dict:
    return {"hid": hid, "q": q, "a": a, "ok": ok, "src": src, "src_label": label, "why": why,
            "notes_used": list(notes), "problem": ""}


FIELDS = [
    field("n", "Full name", "Robin Testwell", "profile", "Your profile",
          "From your profile (Settings, You): your name."),
    field("l", "Location", "Leeds", "profile", "Your profile", "From your profile (Settings, You): your town or city.",
          notes=[CITY_NOTE]),
    field("h", "How did you hear about this role?", "Company website", "note", "Site note",
          'Answered with "Company website" because of your note for madeup-trust.example.'),
    field("p", "Do you need any adjustments for the interview?", "Filled in from your private answers", "private",
          "Private answer", f"{PRIVATE_WHY} {CANARY}"),
    field("w", "Why this job?", "A made-up answer.", "hal", "Written by Hal",
          'Written for this job by Hal from your CV ("Archive Volunteer, Made-up Record Office"). Please read it.',
          ok=False),
]
ANSWERS = [
    {"id": "3", "q": "Are you willing to travel?", "a": "Yes, within the made-up county", "scope": "review",
     "scope_key": "", "scope_label": "Choose where to use it", "uses": 0, "kind": "fixed", "needs_choice": True,
     "auto": False, "choices": [{"scope": "everywhere", "scope_key": "", "label": "Everywhere"},
                                {"scope": "never", "scope_key": "", "label": "Never reused"}]},
    {"id": "1", "q": "Do you hold a full driving licence?", "a": "Yes", "scope": "everywhere", "scope_key": "",
     "scope_label": "Everywhere", "uses": 4, "kind": "fixed", "needs_choice": False, "auto": False,
     "choices": [{"scope": "everywhere", "scope_key": "", "label": "Everywhere"},
                 {"scope": "never", "scope_key": "", "label": "Never reused"}]},
    {"id": "auto:why_us", "q": "Why do you want to work for us?", "a": "Written fresh for each job. Never reused.",
     "scope": "never", "scope_key": "", "scope_label": "Never reused", "uses": 0, "kind": "derived",
     "needs_choice": False, "auto": True, "choices": []},
]
# Draws the Saved answers tab into the page on its own, with the app's own Preact.
MOUNT = """async () => {
  const [{ html, render }, { AnswersTab }] = await Promise.all([import("/app/core/html.js"),
                                                                 import("/app/memory/answers-tab.js")]);
  const host = document.createElement("section");
  host.id = "answers-host";
  host.setAttribute("aria-label", "Saved answers");
  document.body.append(host);
  render(html`<${AnswersTab} />`, host);
}"""


def form_state() -> FakeState:
    state = FakeState()
    app = json.loads(json.dumps(MADE_UP_APP))
    app["form"].update(fields=FIELDS, to_check=1, filled=5, total=5, notes_used=[ESCAPE_NOTE])
    state.pages[APP_PATH] = app
    state.pages["/answers"] = ANSWERS
    return state


@skip_unless_browser
class FormWhyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def tab(self, size: str = "wide", scheme: str = "light") -> tuple[AppPage, FakeState]:
        state = form_state()
        srv = FakeServer(state)
        self.addCleanup(srv.__exit__, None, None, None)
        srv.__enter__()
        p = AppPage(self.browser, srv.url, size, scheme)
        self.addCleanup(p.close)
        return p, state

    def why(self, p: AppPage, hid: str) -> str:
        row = p.page.locator(f".field-row[data-hid='{hid}']")
        row.locator("button.why").click()
        return row.locator(".whyline").inner_text()

    def test_each_answer_shows_where_it_came_from(self) -> None:
        p, _ = self.tab()
        p.open(APP)
        p.page.wait_for_selector(".field-row")
        chips = {hid: p.page.locator(f".field-row[data-hid='{hid}'] .chip").first.inner_text() for hid in "nlhpw"}
        self.assertEqual(chips, {"n": "Your profile", "l": "Your profile", "h": "Site note", "p": "Private answer",
                                 "w": "Written by Hal"})
        self.assertIn("Site note", p.page.locator(".field-row[data-hid='l']").inner_text())
        self.assertEqual(self.why(p, "n"), "From your profile (Settings, You): your name.")
        self.assertIn("Typed only the city for Location", self.why(p, "l"))
        self.assertIn("because of your note for madeup-trust.example", self.why(p, "h"))
        self.assertIn('from your CV ("Archive Volunteer', self.why(p, "w"))
        self.assertEqual(p.errors, [])

    def test_a_private_answer_says_only_that_it_is_private(self) -> None:
        p, _ = self.tab("narrow", "dark")
        p.open(APP)
        p.page.wait_for_selector(".field-row")
        self.assertEqual(self.why(p, "p"), PRIVATE_WHY)
        self.assertNotIn(CANARY, p.page.locator("#main").inner_text())
        self.assertTrue(p.page.locator(".field-row[data-hid='p'] input").is_disabled())
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(p.errors, [])

    def test_something_wrong_here_opens_the_report_for_this_application(self) -> None:
        """Regression: the Form step opened the report without its job, so "Saved. What now?" offered no way out."""
        p, _ = self.tab()
        p.open(APP)
        p.page.wait_for_selector(".field-row")
        p.page.get_by_role("button", name="Something wrong here?").click()
        p.page.get_by_role("dialog").wait_for()
        shown = p.page.evaluate("""async () => { const { store } = await import("/app/core/store.js");
          const d = store.s.dialog; return { kind: d.kind, site: d.site, job_id: d.job_id }; }""")
        self.assertEqual(shown, {"kind": "report", "site": "jobs.example.org", "job_id": "demo:1"})
        self.assertEqual(p.errors, [])

    def test_the_notes_hal_used_on_this_site_are_listed_once_each(self) -> None:
        p, _ = self.tab()
        p.open(APP)
        box = p.page.locator("[data-notes-used]")
        box.wait_for()
        lines = box.locator(".li").all_inner_texts()
        self.assertEqual(len(lines), 2)
        self.assertIn("Close dropdown lists by clicking the form, never with Escape", lines[0])
        self.assertIn("Built into Hal.", lines[0])
        self.assertIn("You told Hal.", lines[1])
        box.locator("text=See all notes for this site").click()
        p.page.wait_for_url("**#memory-sites")
        self.assertEqual(p.errors, [])

    def test_saved_answers_say_where_hal_may_use_each(self) -> None:
        p, state = self.tab("narrow", "light")
        p.open("home")
        p.page.evaluate(MOUNT)
        tab = p.page.locator("[data-answers-tab]")
        tab.wait_for()
        travel = tab.locator("[data-answer='3']")
        self.assertIn("Choose where to use it", travel.inner_text())
        travel.locator("select").select_option(label="Everywhere")
        p.page.wait_for_selector("text=Saved. Everywhere.")
        self.assertIn(("/api/v2/answers/3", {"scope": "everywhere", "scope_key": ""}), state.posts)
        self.assertIn("Used 4 times", tab.locator("[data-answer='1']").inner_text())
        auto = tab.locator("[data-answer='auto:why_us']")
        self.assertTrue(auto.locator("select").is_disabled())
        self.assertFalse(auto.locator("input").is_editable())
        self.assertEqual(auto.locator("button").count(), 0)
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
