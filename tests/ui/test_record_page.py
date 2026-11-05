"""The Your record page in a real browser, against a real server in a temporary home with a made-up record.

The profile is the made-up humanities graduate's, added through the store as an ingest would add it, and a
running ingest is a made-up action row with its items. Needs `HAL_TEST_BROWSER=1` and Playwright. The AI is
the fake one, and no link is fetched: starting a read of links is replaced by a stand-in.
"""
from __future__ import annotations

import contextlib
import json
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest import mock

from support.api import HalServer
from support.fake_ai import fake_ai
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db, record
from jobhunt.core import clock, paths
from jobhunt.evidence import routes
from jobhunt.ingest import actions as ingest_actions
from jobhunt.ingest import items as ingest_items
from jobhunt.profile import store

ANSWER = "On 5 March 2025 [Certificates/First Aid.md]."
# The shell asks for Home's summary, which this test does not serve; its 404 is not this page's fault.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"
CV = "Sources/CVs and letters/Marnie Quillfeather CV.md"
LETTER = "Sources/CVs and letters/Museum letter.md"
EMPLOYER = "Fenwick Lantern Museum Trust"
STARTED_S = 120            # the made-up ingest started this long ago; 2 read and 2 to go leaves 2 minutes
CLOUD = ["Cloud/Offer letter.pdf", "Cloud/Reference.docx"]


def found(kind: str, fields: dict, source: str = CV, doc_type: str = "cv", quote: str = "",
          verified: bool = True) -> int:
    """Adds one fact as the ingest would; returns its item's id."""
    with closing(db.connect()) as con:
        return store.add_found(con, kind, fields, {"source_rel": source, "quote": quote, "doc_type": doc_type,
                                                   "field": "", "verified": verified})


def item(item_id: int) -> dict:
    """The item as the store has it now."""
    with closing(db.connect()) as con:
        return store.item(con, item_id)


def running_ingest() -> None:
    """A made-up ingest in the middle of its run: 2 files read, 1 not downloaded, 2 to go."""
    started = clock.iso(clock.now_utc() - timedelta(seconds=STARTED_S))
    with closing(db.connect()) as con, con:
        con.execute("""INSERT INTO actions (id, kind, subject, state, priority, params, message, created, started)
                       VALUES ('ing1', ?, 'record', 'running', 2, ?, 'Reading Essays/Letters essay.docx', ?, ?)""",
                    (ingest_items.KIND, json.dumps({"batch": "b1"}), started, started))
        for n, (rel, state, stage, category) in enumerate([
                ("CV.docx", "done", "written", "CVs and letters"), ("Transcript.pdf", "done", "written", "Education"),
                (CLOUD[0], "not_downloaded", "discovered", ""), (CLOUD[1], "pending", "discovered", ""),
                ("Essays/Letters essay.docx", "pending", "discovered", "")]):
            ingest_items.add(con, "b1", path=f"/made/up/{n}", rel=rel, state=state, category=category)
            con.execute("UPDATE ingest_items SET stage = ? WHERE batch = 'b1' AND rel = ?", (stage, rel))


@skip_unless_browser
class RecordPage(unittest.TestCase):
    def errors(self, p: AppPage) -> list[str]:
        """Console errors, leaving out failed loads that are all of addresses still to come."""
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET)]
        return [e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours

    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(record, "VAULT", Path(paths.record_default()), create=True))
        self.addCleanup(record._CACHE.clear)
        folder = routes.make_default()
        (folder / "Certificates" / "First Aid.md").write_text(
            "# First Aid\n\nEmergency first aid at work, completed 5 March 2025.\n", encoding="utf-8")
        (folder / "Private" / "Health.md").write_text("# Health\n\nA private note.\n", encoding="utf-8")
        record.sync()

    def page(self, route: str = "record", size: str = "wide", scheme: str = "light", **open_kw) -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        if open_kw:
            p.page.goto(self.hal.open_url(next="/#" + route, **open_kw))
            p.settle()
        else:
            p.page.goto(self.hal.open_url())
            p.open(route)
        return p

    def clean(self, p: AppPage) -> None:
        """No console errors, no sideways scroll and no serious accessibility findings."""
        self.assertEqual(self.errors(p), [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def profile(self) -> dict:
        """The made-up humanities graduate's profile: a job two files disagree on, a skill, a name, a link."""
        ids = {"job": found("job", {"title": "Collections volunteer", "employer": EMPLOYER,
                                    "period": "Sept 2023 - present", "duties": ["Catalogued donated letters"]},
                            quote="Collections volunteer, Sept 2023 - present"),
               "skill": found("skill", {"name": "Archival research", "category": "research"},
                              quote="archival research in parish registers", verified=False),
               "basic": found("basic", {"key": "name", "value": "Marnie Quillfeather"}, quote="Marnie Quillfeather"),
               "link": found("link", {"label": "Blog", "url": "https://example.org/marnie"},
                             quote="example.org/marnie")}
        found("job", {"title": "Collections volunteer", "employer": EMPLOYER, "start": "October 2023",
                      "current": True}, LETTER, "letter", "since October 2023")
        return ids

    def card(self, p: AppPage, title: str):
        """The card of the item with this title."""
        return p.page.locator("article.rec-item", has=p.page.locator("h3", has_text=title))

    def test_every_tab_draws_cleanly_at_both_sizes(self) -> None:
        self.profile()
        tabs = {"Overview": "Your profile", "Work history": "Collections volunteer", "Education": "Add a qualification",
                "Skills": "Archival research", "Projects": "Add a project",
                "Certificates and achievements": "Add a certificate", "About you and links": "Marnie Quillfeather",
                "Inconsistencies": "different start dates", "Files": "What is in it"}
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("record", size, scheme)
                self.assertEqual(p.page.inner_text("#main h1"), "Your record")
                for tab, words in tabs.items():
                    p.page.get_by_role("tab", name=tab).click()
                    p.shows(p.page.locator("#main").get_by_text(words), f"{words} on {tab}")
                    self.clean(p)

    def test_the_files_tab_shows_the_groups(self) -> None:
        p = self.page("record-files")
        p.page.wait_for_selector("text=What is in it")
        body = p.page.inner_text("#main")
        for words in ("Certificates", "Private", "Private files you mark as private are never sent to the AI."):
            self.assertIn(words, body)
        self.assertEqual(self.errors(p), [])

    def test_the_overview_counts_each_kind_and_what_is_left_to_check(self) -> None:
        self.profile()
        p = self.page()
        summary = p.page.locator("section", has=p.page.locator("h2", has_text="Your profile"))
        p.shows(summary.get_by_text("Work history"), "the counts")
        words = summary.inner_text()
        for expected in ("4 things to check.", "1 to check", "1 to sort out", "Marnie Quillfeather",
                         "https://example.org/marnie"):
            self.assertIn(expected, words)
        summary.get_by_role("button", name="Inconsistencies").click()
        p.page.get_by_role("tab", name="Inconsistencies", selected=True).wait_for()
        self.assertEqual(self.errors(p), [])

    def test_editing_a_job_saves_it_as_yours(self) -> None:
        ids = self.profile()
        p = self.page("record-work")
        card = self.card(p, "Collections volunteer")
        card.get_by_role("button", name="Edit: Collections volunteer").click()
        card.get_by_label("Job title").fill("Collections assistant")
        card.get_by_label("Place").fill("Harrowgate Vale")
        card.get_by_label("What you did").fill("Catalogued donated letters\nRan two family history days")
        card.get_by_role("button", name="Save").click()
        edited = self.card(p, "Collections assistant")
        p.shows(edited.get_by_text("Edited by you"), "the edited chip")
        self.assertIn("Ran two family history days", edited.inner_text())
        fields = item(ids["job"])["fields"]
        self.assertEqual((fields["title"], fields["location"], fields["duties"]),
                         ("Collections assistant", "Harrowgate Vale",
                          ["Catalogued donated letters", "Ran two family history days"]))
        self.assertEqual(item(ids["job"])["state"], "edited")
        self.clean(p)

    def test_confirm_and_hide_change_the_state_and_fold_hidden_items_away(self) -> None:
        ids = self.profile()
        p = self.page("record-skills")
        self.card(p, "Archival research").get_by_role("button", name="Confirm: Archival research").click()
        p.shows(self.card(p, "Archival research").get_by_text("Checked by you"), "the checked chip")
        self.assertEqual(item(ids["skill"])["state"], "confirmed")
        self.card(p, "Archival research").get_by_role("button", name="Hide: Archival research").click()
        p.shows(p.page.locator("details.rec-hidden summary", has_text="Hidden (1)"), "the hidden fold")
        self.assertFalse(p.page.locator("details.rec-hidden").get_attribute("open") is not None)
        self.assertEqual(item(ids["skill"])["state"], "hidden")
        self.assertIn("Nothing here yet", p.page.inner_text("#main"))
        self.assertEqual(self.errors(p), [])

    def test_a_quote_not_found_in_its_file_is_marked(self) -> None:
        self.profile()
        p = self.page("record-skills")
        card = self.card(p, "Archival research")
        p.shows(card.get_by_text("Quote not found in the file"), "the mark")
        self.assertIn("archival research in parish registers", card.inner_text())
        link = card.get_by_role("link", name="Marnie Quillfeather CV")
        self.assertTrue(link.get_attribute("href").startswith("/api/v2/record/file/"))
        job = self.page("record-work")
        self.assertEqual(self.card(job, "Collections volunteer").get_by_text("Quote not found in the file").count(), 0)

    def test_choosing_a_value_settles_the_conflict(self) -> None:
        ids = self.profile()
        p = self.page("record-work")
        card = self.card(p, "Collections volunteer")
        p.shows(card.get_by_text("different start dates"), "the conflict")
        self.assertIn("since October 2023", card.locator(".rec-conflict").inner_text())
        card.get_by_role("button", name="Use October 2023").click()
        p.shows(self.card(p, "Collections volunteer").get_by_text("Edited by you"), "the edited chip")
        self.assertEqual(item(ids["job"])["fields"]["start"], "2023-10")
        p.page.get_by_role("tab", name="Inconsistencies").click()
        p.shows(p.page.get_by_text("Nothing to sort out"), "the empty list")
        p.page.locator("summary", has_text="Settled (1)").click()
        p.shows(p.page.get_by_text("You chose 2023-10."), "the settled conflict")
        self.clean(p)

    def test_keeping_it_as_it_is_from_the_inconsistencies_tab(self) -> None:
        self.profile()
        p = self.page("record-inconsistencies")
        p.page.get_by_role("button", name="Keep it as it is").click()
        p.shows(p.page.get_by_text("Nothing to sort out"), "the empty list")
        self.assertEqual(self.errors(p), [])

    def test_an_item_added_by_hand_can_be_deleted(self) -> None:
        p = self.page("record-projects")
        p.page.get_by_role("button", name="Add a project").click()
        p.page.get_by_label("Name").fill("Dissertation: letters of a made-up mill town")
        p.page.get_by_label("What it was").fill("Read 300 letters in a record office.")
        p.page.get_by_label("Tools, methods or materials").fill("Palaeography\nOral history")
        p.page.get_by_role("button", name="Save").click()
        card = self.card(p, "Dissertation")
        p.shows(card.get_by_text("Edited by you"), "the new card")
        self.assertIn("Oral history", card.inner_text())
        self.clean(p)
        card.get_by_role("button", name="Delete: Dissertation: letters of a made-up mill town").click()
        p.shows(p.page.get_by_text("Nothing here yet"), "the empty list")
        with closing(db.connect()) as con:
            self.assertEqual(store.items(con, "project"), [])

    def test_a_running_read_shows_its_bar_time_left_and_files_not_downloaded(self) -> None:
        running_ingest()
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("record", size, scheme)
                card = p.page.locator(".rec-ingest")
                p.shows(card, "the progress card")
                self.assertEqual(card.get_by_role("progressbar", name="Files read").get_attribute("aria-valuenow"),
                                 "60")
                words = card.inner_text()
                for expected in ("Reading your files", "3 of 5 read (60%)", "About 2 minutes left",
                                 "Now: Reading Essays/Letters essay.docx", "CVs and letters: 1", "Education: 1",
                                 "1 file is not downloaded.", "Download them first", CLOUD[0]):
                    self.assertIn(expected, words)
                self.assertTrue(card.get_by_role("button", name="Stop").is_visible())
                self.clean(p)

    def test_pasted_links_start_a_read(self) -> None:
        queued = []
        self.enterContext(mock.patch.object(ingest_actions, "queue",
                                            side_effect=lambda **kw: queued.append(kw) or "ing2"))
        p = self.page()
        p.page.get_by_label("Web links").fill("https://example.org/marnie\n\nhttps://github.com/made-up-marnie\n")
        p.page.get_by_role("button", name="Read these links").click()
        p.shows(p.page.get_by_text("Hal is reading the links into your record."), "the toast")
        self.assertEqual(queued[0]["urls"], ["https://example.org/marnie", "https://github.com/made-up-marnie"])
        self.assertEqual(self.errors(p), [])

    def test_folders_can_be_read_here_but_not_from_another_computer(self) -> None:
        here = self.page()
        p_folders = here.page.get_by_label("Folders on this computer")
        here.shows(p_folders, "the folders box")
        away = self.page("record", remote=True, remote_host="deskbox")
        away.shows(away.page.get_by_label("Web links"), "the links box")
        self.assertEqual(away.page.get_by_label("Folders on this computer").count(), 0)
        self.assertEqual(self.errors(away), [])

    def test_asking_shows_the_answer_and_the_files_it_came_from(self) -> None:
        p = self.page("record-files")
        with fake_ai({"record_ask": ANSWER}):
            p.page.fill("input[aria-label=Question]", "When did I do first aid at work?")
            p.page.click("button:has-text('Ask')")
            p.page.wait_for_selector(".answer")
        self.assertIn("5 March 2025", p.page.inner_text(".answer"))
        self.assertIn("From: Certificates/First Aid.md", p.page.inner_text("#main"))
        self.assertEqual(self.errors(p), [])

    def test_without_the_ai_the_passage_is_not_called_hals_answer(self) -> None:
        # a raw passage was labelled "Hal's answer", and the note saying why was never shown
        p = self.page("record-files")
        p.page.fill("input[aria-label=Question]", "When did I do first aid at work?")
        p.page.click("button:has-text('Ask')")
        p.page.wait_for_selector(".answer")
        self.assertIn("From your record", p.page.text_content(".answer"))
        self.assertIn("Hal could not write an answer", p.page.inner_text("#main"))

    def test_a_dropped_file_is_added_to_the_inbox(self) -> None:
        p = self.page()
        upload = Path(paths.cache_dir()) / "Reference.txt"
        upload.parent.mkdir(parents=True, exist_ok=True)
        upload.write_text("A made-up reference from a made-up archive.", encoding="utf-8")
        p.page.set_input_files("input[type=file]", str(upload))
        p.page.wait_for_selector("text=Added Reference.txt")
        self.assertTrue((Path(record._live("VAULT")) / "Inbox" / "Reference.txt").exists())

    def test_a_group_opens_to_its_files_with_downloads(self) -> None:
        p = self.page("record-files")
        p.page.click(".li button:has-text('Certificates')")
        link = p.page.wait_for_selector("a:has-text('Download')")
        with p.page.expect_download() as got:
            link.click()
        self.assertEqual(got.value.suggested_filename, "First Aid.md")
