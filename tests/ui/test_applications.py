"""The Applications screens in a real browser: the board, approving with the first-application guide, the
Form step's marks, and "I sent it myself".

They run the real files of `jobhunt/web/static/app/` against the fake server of `tests/support/fake_api.py`,
which answers the Applications GET routes with a made-up person and made-up applications. They need
`HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import json
import time
import unittest

from support.fake_api import MADE_UP_APP, MADE_UP_BOARD, FakeServer, FakeState
from support.ui import AppPage, chromium, skip_unless_browser

APP = "app-demo%3A1"
APP_PATH = "/applications/demo:1"   # what the page asks for at APP
HELD_WAIT_S = 5.0     # how long a test waits for the page's request to reach a held route
POLL_MS = 20          # how often it looks
SETTLE_MS = 300       # how long it gives a second click to send a request of its own
# A page picture the fake server need not serve: one white pixel, which the app's img-src allows as data:
PIXEL = ("data:image/png;base64,"
         "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")



def apps_state() -> FakeState:
    """The fake server's state with one application of its own, which a test may change."""
    state = FakeState()
    state.pages[APP_PATH] = json.loads(json.dumps(MADE_UP_APP))
    return state


@skip_unless_browser
class ApplicationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def serve(self, state: FakeState | None = None) -> FakeServer:
        srv = FakeServer(state or apps_state())
        self.addCleanup(srv.__exit__, None, None, None)
        return srv.__enter__()

    def tab(self, srv: FakeServer, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, srv.url, size, scheme)
        self.addCleanup(p.close)
        return p

    def test_the_board_shows_each_column_and_the_closed_list(self) -> None:
        p = self.tab(self.serve()).open("apps")
        p.page.wait_for_selector(".board .colm")
        heads = p.page.locator(".board .colm h3").all_inner_texts()
        self.assertEqual(heads, ["Getting ready", "Form filled", "Sent", "Interview", "Offer"])
        self.assertIn("1 answer needs you", p.page.locator(".colm", has_text="Form filled").inner_text())
        self.assertIn("too much effort", p.page.locator("section.card", has_text="Closed").inner_text())
        p.page.click("text=Museum Assistant")
        p.page.wait_for_selector("h1:text('Museum Assistant')")
        self.assertEqual(p.errors, [])

    def test_the_missing_documents_button_keeps_its_text_inside_its_pill_in_a_narrow_column(self) -> None:
        p = self.tab(self.serve(), "narrow").open("apps")
        ready = p.page.locator(".colm", has_text="Getting ready")
        button = ready.get_by_role("button", name="Make the missing documents")
        button.wait_for()
        fit = button.evaluate("b => [b.scrollWidth, b.clientWidth]")
        self.assertLessEqual(fit[0], fit[1])
        box, column = button.bounding_box(), ready.bounding_box()
        self.assertGreaterEqual(box["x"], column["x"])
        self.assertLessEqual(box["x"] + box["width"], column["x"] + column["width"])
        self.assertEqual(round(column["width"]), 230)

    def test_out_of_date_cards_say_why_and_the_column_writes_them_again_after_asking(self) -> None:
        state = apps_state()
        board = json.loads(json.dumps(MADE_UP_BOARD))
        board["columns"][0]["items"][0].update(out_of_date="Your CV changed since Hal wrote these", rewritable=True)
        state.pages["/applications"] = board
        p = self.tab(self.serve(state), "narrow").open("apps")
        ready = p.page.locator(".colm", has_text="Getting ready")
        button = ready.get_by_role("button", name="Write the out-of-date documents again (1)")
        button.wait_for()
        card = ready.locator(".acard", has_text="Library Assistant")
        self.assertIn("Out of date", card.inner_text())
        self.assertIn("Your CV changed since Hal wrote these.", card.inner_text())
        fit = button.evaluate("b => [b.scrollWidth, b.clientWidth]")
        self.assertLessEqual(fit[0], fit[1], "the button's words wrap inside it in a narrow column")
        button.click()
        dialog = p.page.locator("[role=dialog]")
        dialog.wait_for()
        self.assertIn("1 application?", dialog.inner_text())
        self.assertIn("keeps your own edits", dialog.inner_text())
        self.assertIn("behind anything you ask Hal for on screen", dialog.inner_text())
        self.assertEqual(state.posts, [], "nothing is written before you say yes")
        dialog.get_by_role("button", name="Write them again").click()
        p.page.wait_for_selector("text=Hal will write the documents again")
        self.assertEqual(state.posts, [("/api/v2/applications/rewrite-out-of-date", {"ids": ["demo:2"]})])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.errors, [])

    def test_approving_shows_the_first_application_guide_then_the_form_step(self) -> None:
        state = apps_state()
        state.pages[APP_PATH].update(step=1, column="ready", form=None)
        state.pages[APP_PATH]["docs"]["state"] = "ready"
        state.pages[APP_PATH]["steps"][0].update(state="now", note="Waiting for you")
        srv = self.serve(state)
        p = self.tab(srv).open(APP)
        p.page.wait_for_selector(".approve")
        p.page.click("text=Approve and fill in the form")
        dialog = p.page.locator("[role=dialog]")
        self.assertIn("Your first application with Hal", dialog.inner_text())
        self.assertIn("press Submit on the site yourself", dialog.inner_text())
        self.assertEqual([path for path, _ in state.posts], [])
        p.page.click("text=Got it, open the form")
        # The page stays on the application and shows its Form step; it went back to the board (testers, 2026-11-04).
        p.page.wait_for_selector("[role=tab][aria-selected=true]:has-text('Form')")
        self.assertEqual(p.page.locator("h1").inner_text(), "Museum Assistant")
        self.assertEqual([path for path, _ in state.posts], ["/api/v2/applications/demo%3A1/documents/approve"])
        self.assertEqual(p.errors, [])

    def test_the_form_step_fills_from_the_tab_or_goes_back_to_change_the_documents(self) -> None:
        # testers: when Hal was stuck before the form there was no way to start it from the right page, and no
        # way back to change approved documents (2026-11-04)
        state = apps_state()
        srv = self.serve(state)
        p = self.tab(srv).open(APP)
        p.page.wait_for_selector(".field-row")
        p.page.click("text=Fill in from this page")
        p.page.wait_for_selector("text=Hal is filling in the page your tab shows")
        p.page.click("text=Change the documents")
        p.page.wait_for_selector("[role=tab][aria-selected=true]:has-text('Documents')")
        self.assertEqual([path for path, _ in state.posts],
                         ["/api/v2/applications/demo%3A1/form/fill", "/api/v2/applications/demo%3A1/documents/reopen"])
        self.assertEqual(state.posts[0][1], {"here": True})
        self.assertEqual(p.errors, [])

    def test_a_closed_application_says_why_and_offers_back_to_matches_not_its_step(self) -> None:
        # testers: an application whose advert had closed showed its Documents step and "Approve and fill in the
        # form" (2026-11-04)
        state = apps_state()
        state.pages[APP_PATH].update(closed="The advert closed while you were applying")
        srv = self.serve(state)
        p = self.tab(srv).open(APP)
        p.page.wait_for_selector("text=This application is closed.")
        self.assertEqual(p.page.locator("text=Fill in from this page").count(), 0)
        p.page.click("text=Back to Matches")
        p.page.wait_for_selector("text=Back in Matches: Museum Assistant")
        self.assertEqual([path for path, _ in state.posts], ["/api/v2/applications/demo%3A1/restore"])
        self.assertEqual(p.errors, [])

    def documents_step(self, **docs) -> FakeState:
        """An application on its Documents step, not approved, with `docs` over the made-up documents."""
        state = apps_state()
        state.pages[APP_PATH].update(step=1, column="ready", form=None, first_time=False)
        state.pages[APP_PATH]["docs"].update({"state": "ready", "approved_at": None, **docs})
        state.pages[APP_PATH]["steps"][0].update(state="now", note="Waiting for you")
        return state

    def test_out_of_date_documents_say_why_and_ask_before_replacing_your_edits(self) -> None:
        state = self.documents_step(out_of_date="Your record changed since Hal wrote these", own_edits=True)
        p = self.tab(self.serve(state), "narrow").open(APP)
        note = p.page.locator(".note.again")
        note.wait_for()
        self.assertIn("Your record changed since Hal wrote these.", note.inner_text())
        note.get_by_role("button", name="Write the documents again").click()
        dialog = p.page.locator("[role=dialog]")
        dialog.get_by_role("radio", name="Keep my edits").wait_for()
        self.assertEqual(dialog.get_by_role("radio", name="Keep my edits").get_attribute("aria-checked"), "true")
        self.assertEqual(state.posts, [], "nothing is written before you choose")
        dialog.get_by_role("radio", name="Write everything fresh").click()
        dialog.get_by_role("button", name="Write them again").click()
        p.page.wait_for_selector("text=Hal is writing everything fresh.")
        self.assertEqual(state.posts, [("/api/v2/applications/demo%3A1/documents/redo", {"fresh": True})])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.errors, [])

    def test_with_no_edits_of_your_own_hal_writes_again_without_asking(self) -> None:
        state = self.documents_step()
        p = self.tab(self.serve(state)).open(APP)
        p.page.wait_for_selector(".approve")
        self.assertEqual(p.page.locator(".note.again").count(), 0)
        p.page.locator(".approve").get_by_role("button", name="Write the documents again").click()
        p.page.wait_for_selector("text=Hal is writing the documents again.")
        self.assertEqual(state.posts, [("/api/v2/applications/demo%3A1/documents/redo", {})])
        self.assertEqual(p.page.locator("[role=dialog]").count(), 0)
        self.assertEqual(p.errors, [])

    def test_a_double_click_on_write_the_documents_again_sends_one_write(self) -> None:
        # review, 2026-11-18: without the dialog the button had no busy guard, and a second write replaced the first
        state = self.documents_step()
        p = self.tab(self.serve(state)).open(APP)
        p.page.wait_for_selector(".approve")
        held = []
        p.page.route("**/documents/redo", lambda route: held.append(route))
        button = p.page.locator(".approve").get_by_role("button", name="Write the documents again")
        button.dblclick()
        deadline = time.monotonic() + HELD_WAIT_S
        while not held and time.monotonic() < deadline:
            p.page.wait_for_timeout(POLL_MS)
        p.page.wait_for_timeout(SETTLE_MS)   # time for a second request, were one sent
        self.assertEqual(len(held), 1)
        self.assertTrue(button.is_disabled(), "the button waits for the write to start")
        held[0].continue_()
        p.page.wait_for_selector("text=Hal is writing the documents again.")
        self.assertEqual(state.posts, [("/api/v2/applications/demo%3A1/documents/redo", {})])
        self.assertEqual(p.errors, [])

    def test_with_one_cv_in_use_the_cv_tab_only_names_it(self) -> None:
        p = self.tab(self.serve(self.documents_step())).open(APP)
        line = p.page.locator(".base-cv")
        line.wait_for()
        self.assertEqual(line.inner_text().strip(), "Built from your Museum CV.")
        self.assertEqual(line.get_by_role("button").count(), 0)
        self.assertEqual(p.errors, [])

    def test_with_several_cvs_the_reason_shows_and_another_cv_can_be_chosen(self) -> None:
        reason = "Covers 6 of the 7 skills this advert asks for; your Library CV covers 2."
        state = self.documents_step()
        state.pages[APP_PATH]["docs"]["cv"]["base"].update(reason=reason, several=True)
        state.pages[APP_PATH + "/cv/bases"] = {"cvs": [
            {"id": "main", "name": "Museum CV", "label": "your Museum CV", "chosen": True, "pick": True, "fit": 80,
             "have": 6, "of": 7, "lost": 0},
            {"id": "cv-2", "name": "Library CV", "label": "your Library CV", "chosen": False, "pick": False,
             "fit": 40, "have": 2, "of": 7, "lost": 1}]}
        p = self.tab(self.serve(state), "narrow").open(APP)
        line = p.page.locator(".base-cv")
        line.wait_for()
        self.assertIn("Built from your Museum CV. " + reason, line.inner_text())
        line.get_by_role("button", name="Change CV").click()
        dialog = p.page.locator("[role=dialog]")
        museum = dialog.get_by_role("radio", name="Museum CV")
        museum.wait_for()
        self.assertIn("Hal's pick", museum.inner_text())
        self.assertIn("Covers 2 of the 7 skills", dialog.get_by_role("radio", name="Library CV").inner_text())
        self.assertTrue(dialog.get_by_role("button", name="Use this CV").is_disabled(), "it is in use already")
        dialog.get_by_role("radio", name="Library CV").click()
        self.assertIn("1 edit of your own", dialog.locator("[role=status]").inner_text())
        self.assertEqual(state.posts, [], "nothing changes before you confirm")
        dialog.get_by_role("button", name="Use it and drop 1 edit").click()
        p.page.wait_for_selector("text=Hal is making your CV for this job from your Library CV.")
        self.assertEqual(state.posts, [("/api/v2/applications/demo%3A1/cv/base", {"doc_id": "cv-2"})])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(p.errors, [])

    def test_the_cv_tab_names_the_cv_hal_used_with_several_in_use_and_your_main_cv_with_one(self) -> None:
        # review, 2026-11-18: the step said "your main CV" for an application built on another CV
        for several, said in ((True, "Hal used your Museum CV as it is for this job."),
                              (False, "Hal used your main CV as it is for this job.")):
            with self.subTest(several=several):
                state = self.documents_step()
                state.pages[APP_PATH]["docs"]["cv"]["base"].update(several=several)
                p = self.tab(self.serve(state)).open(APP)
                p.page.wait_for_selector(".base-cv")
                self.assertEqual(p.page.get_by_text(said).count(), 1)
                self.assertEqual(p.errors, [])

    def test_a_cv_that_sends_your_own_file_shows_that_files_pages_or_says_hal_shows_its_reading(self) -> None:
        # 2026-11-19: a Word file sent as it is showed only "cannot be shown here", and a PDF only in the
        # browser's viewer
        pdf = {"kind": "pdf", "name": "Robin-CV.pdf", "pages": 2, "page_urls": [PIXEL, PIXEL + "#2"], "why": "",
               "version_id": "v1-demo"}
        word = {**pdf, "kind": "docx", "name": "Robin-CV.docx", "pages": None, "page_urls": []}
        for original, size in ((pdf, "narrow"), (word, "wide")):
            with self.subTest(kind=original["kind"]):
                state = self.documents_step()
                state.pages[APP_PATH]["docs"]["cv"].update(sent_as="original", original=original)
                p = self.tab(self.serve(state), size).open(APP)
                p.page.wait_for_selector(".sent-preview")
                pages = p.page.locator(".sent-preview img")
                if original["page_urls"]:
                    self.assertEqual([pages.nth(i).get_attribute("alt") for i in range(2)],
                                     ["Page 1 of 2 of your CV file", "Page 2 of 2 of your CV file"])
                    self.assertIn("This is your file, Robin-CV.pdf, as employers get it.",
                                  p.page.inner_text(".sent-preview"))
                else:
                    self.assertEqual(pages.count(), 0)
                    self.assertIn("Employers get your file, Robin-CV.docx, as you uploaded it. Hal cannot show a "
                                  "Word file as pages, so this is Hal's reading of it.",
                                  p.page.inner_text(".sent-preview"))
                    self.assertEqual(p.page.locator(".sent-preview iframe[title='CV preview']").count(), 1)
                self.assertEqual(p.page.locator("iframe.pdfview").count(), 0)
                self.assertEqual(p.sideways(), 0)
                self.assertEqual(p.serious_axe(), [])
                self.assertEqual(p.errors, [])

    def test_a_write_that_failed_says_why_on_the_documents_step(self) -> None:
        # a regression: the step showed empty tabs and "ready", and only the board said what went wrong (2026-11-18)
        state = self.documents_step(state="failed", stopped="Hal's AI did not answer")
        p = self.tab(self.serve(state)).open(APP)
        p.page.wait_for_selector("text=Hal could not write the documents for this job")
        self.assertIn("Hal's AI did not answer.", p.page.locator("section.card[role=status]").inner_text())
        self.assertEqual(p.page.locator("text=Approve and fill in the form").count(), 0)
        p.page.click("text=Write the documents again")
        p.page.wait_for_selector("text=Hal is writing the documents again.")
        self.assertEqual([path for path, _ in state.posts], ["/api/v2/applications/demo%3A1/documents/redo"])
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(p.errors, [])

    def test_while_hal_works_the_form_step_shows_no_stuck_hint_and_no_submit_words(self) -> None:
        state = apps_state()
        state.pages[APP_PATH].update(action_id="a1")
        state.pages[APP_PATH]["form"].update(fields=[], filled=0, total=0, to_check=0, state="working",
                                             words="Hal is working on the form: answering 7 questions.")
        p = self.tab(self.serve(state)).open(APP)
        p.page.wait_for_selector("text=Hal is working on the form: answering 7 questions.")
        self.assertEqual(p.page.locator("text=If Hal is stuck before the form").count(), 0)
        self.assertIn("Working on the form.", p.page.locator(".halpanel").inner_text())
        self.assertNotIn("Submit", p.page.locator(".halpanel").inner_text())
        self.assertEqual(p.errors, [])

    def test_the_form_step_marks_each_answer_and_i_sent_it_myself_moves_it_on(self) -> None:
        state = apps_state()
        srv = self.serve(state)
        p = self.tab(srv, "narrow", "dark").open(APP)
        p.page.wait_for_selector(".field-row")
        marks = p.page.locator(".field-row .flag").all_inner_texts()
        # The answer to check comes first.
        self.assertEqual(marks, ["Check this", "Fine"])
        self.assertIn("Hal filled 1 of 2 answers. 1 needs you.", p.page.locator(".approve").inner_text())
        self.assertIn("Written by Hal", p.page.locator(".field-row").first.inner_text())
        p.page.click("text=I sent it myself")
        p.page.wait_for_selector("text=Moved to Sent")
        self.assertIn(("/api/v2/applications/demo%3A1/sent", {}), state.posts)
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
