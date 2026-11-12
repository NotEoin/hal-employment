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
from support.ui import SIZES, AppPage, chromium, skip_unless_browser

from jobhunt import db, record
from jobhunt.core import clock, paths
from jobhunt.evidence import routes
from jobhunt.ingest import actions as ingest_actions
from jobhunt.ingest import items as ingest_items
from jobhunt.ingest import privacy
from jobhunt.profile import store

ANSWER = "On 5 March 2025 [Certificates/First Aid.md]."
# The shell asks for Home's summary, which this test does not serve; its 404 is not this page's fault.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"
CV = "Sources/CVs and letters/Marnie Quillfeather CV.md"
LETTER = "Sources/CVs and letters/Museum letter.md"
EMPLOYER = "Fenwick Lantern Museum Trust"
STARTED_S = 120            # the made-up ingest started this long ago
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
        # rich documents read two minutes apart, one rich and one light file left: about two minutes to go
        start = clock.parse(started)
        for n, (rel, state, stage, category, rank, done_s) in enumerate([
                ("CV.docx", "done", "written", "CVs and letters", 0, 60),
                ("Transcript.pdf", "done", "written", "Education", 1, 180),
                (CLOUD[0], "not_downloaded", "discovered", "", 15, None),
                (CLOUD[1], "pending", "discovered", "", 15, None),
                ("Essays/Letters essay.docx", "pending", "discovered", "", 6, None)]):
            ingest_items.add(con, "b1", path=f"/made/up/{n}", rel=rel, state=state, category=category)
            at = clock.iso(start + timedelta(seconds=done_s)) if done_s is not None else started
            con.execute("UPDATE ingest_items SET stage = ?, rank = ?, updated = ? WHERE batch = 'b1' AND rel = ?",
                        (stage, rank, at, rel))


def finished_ingest() -> None:
    """A made-up ingest that ended with two lecture notes filed by rules, the AI not having read them."""
    at = clock.iso(clock.now_utc() - timedelta(seconds=STARTED_S))
    with closing(db.connect()) as con, con:
        con.execute("""INSERT INTO actions (id, kind, subject, state, priority, params, created, started, ended)
                       VALUES ('ing0', ?, 'record', 'done', 2, ?, ?, ?, ?)""",
                    (ingest_items.KIND, json.dumps({"batch": "b0"}), at, at, at))
        for n in range(2):
            ingest_items.add(con, "b0", path=f"/made/up/notes {n}.md", rel=f"Lectures/notes {n}.md", state="done",
                             category="Study notes")
        con.execute("UPDATE ingest_items SET stage = 'written', note = 'Sources/Study notes/x.md' WHERE batch = 'b0'")


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
                "Inconsistencies": "different start dates", "Documents": "What is in it"}
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

    def test_an_empty_record_says_what_to_do_first(self) -> None:
        """A first visit said "Everything is checked." beside nothing at all, and "No CVs yet." twice
        (2026-11-12)."""
        p = self.page()
        summary = p.page.locator("section", has=p.page.locator("h2", has_text="Your profile"))
        p.shows(summary.get_by_text("Nothing yet. Add your CV and Hal fills this in from it."), "the first step")
        self.assertNotIn("Everything is checked", summary.inner_text())
        cvs = p.page.locator("section", has=p.page.locator("h2", has_text="Your CVs"))
        p.shows(cvs.get_by_text("No CVs yet."), "the empty CV list")
        self.assertEqual(cvs.get_by_text("No CVs yet.").count(), 1)

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

    def test_a_tool_only_a_plan_gives_is_marked_planned_in_its_document(self) -> None:
        # a team's design document planned tools the built app never used, and every letter said the person
        # used them (2026-11-08)
        design = "Sources/Projects/Ferry planner/Design Document.md"
        found("project", {"name": "Ferry planner", "tools": ["Docker"]}, design, "report",
              "The app will use Docker")
        found("project", {"name": "Ferry planner", "tools": ["SQLite"]}, "Sources/Code/ferry/README.md",
              "repository", "Stores timetables in SQLite")
        p = self.page("record-projects")
        card = self.card(p, "Ferry planner")
        p.shows(card.get_by_text("planned in Design Document"), "the mark")
        self.assertEqual(card.locator("li", has_text="SQLite").get_by_text("planned in").count(), 0)
        self.clean(p)

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

    def test_skills_only_lectures_name_are_folded_under_your_own(self) -> None:
        # a degree's lecture notes drew 6,300 topic cards in the Skills tab, above the person's own (2026-11-12)
        with closing(db.connect()) as con:
            store.add_found(con, "skill", {"name": "Two-phase commit"},
                            {"source_rel": "Courses/Week 9.md", "quote": "two-phase commit", "doc_type": "notes",
                             "role": "taught"})
        found("skill", {"name": "Python"}, quote="Python")
        p = self.page("record-skills")
        p.shows(self.card(p, "Python"), "the person's own skill as a card")
        self.assertEqual(self.card(p, "Two-phase commit").count(), 0, "no card for a lecture topic")
        studied = p.page.locator("details.rec-check-studied")
        self.assertIn("Studied in your course (1 skill)", studied.inner_text())
        self.assertEqual(studied.get_by_text("Two-phase commit").count(), 0, "the list is drawn when opened")
        studied.locator("summary").click()
        p.shows(studied.get_by_text("Two-phase commit"), "the lecture topic, folded apart")
        self.clean(p)

    def test_a_long_keep_button_wraps_on_a_phone_and_the_page_never_scrolls_sideways(self) -> None:
        # "Keep Teams software project (degree module) (March 2025 to May 2025)" was 493 px wide on a 390 px
        # screen, so the whole Work history page scrolled sideways (2026-11-12)
        for title, employer in (("Teams software project for the collections catalogue (degree module)", EMPLOYER),
                                ("Volunteer guide and front of house assistant", "Harbour Gallery")):
            found("job", {"title": title, "employer": employer, "start": "March 2025", "end": "July 2025"},
                  quote=title)
        p = self.page("record-work", "narrow")
        keep = p.page.get_by_role("button", name="Keep Teams software project").first
        p.shows(keep, "the long keep button")
        self.assertLessEqual(keep.bounding_box()["width"], SIZES["narrow"]["width"])
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
        # a card made by hand went at one press, with nothing to read it back from (2026-11-12)
        asked = p.page.get_by_role("dialog")
        p.shows(asked.get_by_text("You added this yourself"), "the question before deleting")
        asked.get_by_role("button", name="Delete", exact=True).click()
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

    def test_notes_filed_without_the_ai_can_be_read_with_it(self) -> None:
        finished_ingest()
        queued = []
        self.enterContext(mock.patch.object(ingest_actions, "queue",
                                            side_effect=lambda **kw: queued.append(kw) or "ing2"))
        p = self.page()
        card = p.page.locator(".rec-ingest")
        p.shows(card.get_by_text("2 notes were filed by headings and words only"), "the note")
        card.get_by_role("button", name="Read them with the AI").click()
        p.shows(p.page.get_by_text("Hal is reading the rest of your documents."), "the toast")
        self.assertTrue(queued[0]["again"])
        self.assertEqual(self.errors(p), [])

    def test_someone_elses_files_are_named_as_left_out(self) -> None:
        finished_ingest()
        with closing(db.connect()) as con, con:
            ingest_items.add(con, "b0", path="/made/up/Friend CV.pdf", rel="Downloads/Friend CV.pdf",
                             state="not_mine")
        p = self.page()
        card = p.page.locator(".rec-ingest")
        p.shows(card.get_by_text("1 file looks like someone else's"), "the note")
        p.shows(card.get_by_text("Downloads/Friend CV.pdf"), "its name")
        self.assertEqual(self.errors(p), [])

    def test_supporting_files_are_counted_in_plain_words(self) -> None:
        finished_ingest()
        with closing(db.connect()) as con, con:
            for n in range(3):
                ingest_items.add(con, "b0", path=f"/made/up/fig{n}.png", rel=f"Project/fig{n}.png", state="attached")
        p = self.page()
        card = p.page.locator(".rec-ingest")
        p.shows(card.get_by_text("3 data files, figures and photos listed with their project or course"), "the count")
        self.assertEqual(self.errors(p), [])

    def test_skipped_files_are_counted_by_why(self) -> None:
        finished_ingest()
        with closing(db.connect()) as con, con:
            for n, why in enumerate(("an empty note", "an empty note", "a picture with no words")):
                row = ingest_items.add(con, "b0", path=f"/made/up/stub{n}.md", rel=f"Vault/stub{n}.md", state="skipped")
                ingest_items.update(con, row, error=why)
        p = self.page()
        card = p.page.locator(".rec-ingest")
        p.shows(card.get_by_text("2 empty notes", exact=False), "the counts")
        self.assertIn("1 pictures with no words", card.inner_text())
        self.assertNotIn("skipped", card.inner_text())
        self.assertEqual(self.errors(p), [])

    def test_files_kept_out_as_private_are_named_and_can_be_added_anyway(self) -> None:
        finished_ingest()
        kept = self.hal.home / "Old laptop" / "statement.txt"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text("A made-up statement.\n", encoding="utf-8")
        with closing(db.connect()) as con, con:
            item_id = ingest_items.add(con, "b0", path=str(kept), rel="Old laptop/statement.txt")
            privacy.hold(con, item_id, privacy.BANK)
        queued = []
        self.enterContext(mock.patch.object(ingest_actions, "queue",
                                            side_effect=lambda **kw: queued.append(kw) or "ing2"))
        p = self.page()
        card = p.page.locator(".rec-ingest")
        p.shows(card.get_by_text("1 file was kept out because it looks private"), "the note")
        p.shows(card.get_by_text("bank details"), "the reason")
        self.assertNotIn("made-up statement", card.inner_text(), "names only, never the text")
        self.clean(p)
        card.get_by_role("button", name="Add Old laptop/statement.txt anyway").click()
        p.shows(p.page.get_by_text("Hal is reading Old laptop/statement.txt into your record."), "the toast")
        self.assertEqual(queued[0]["allow"], [item_id])
        self.assertEqual(self.errors(p), [])

    def test_tidy_my_record_starts_a_tidy(self) -> None:
        finished_ingest()
        queued = []
        self.enterContext(mock.patch.object(ingest_actions, "queue",
                                            side_effect=lambda **kw: queued.append(kw) or "ing2"))
        p = self.page()
        p.page.locator(".rec-ingest").get_by_role("button", name="Tidy my record").click()
        p.shows(p.page.get_by_text("Hal is tidying your record."), "the toast")
        self.assertTrue(queued[0]["tidy"])
        self.assertEqual(self.errors(p), [])

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

    def test_a_group_opens_to_its_files_with_downloads(self) -> None:
        p = self.page("record-files")
        p.page.click(".li button:has-text('Certificates')")
        link = p.page.wait_for_selector("a:has-text('Download')")
        with p.page.expect_download() as got:
            link.click()
        self.assertEqual(got.value.suggested_filename, "First Aid.md")
