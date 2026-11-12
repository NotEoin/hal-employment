"""The Check your record page in a real browser, against a real server in a temporary home.

The facts are added through the store as an ingest would add them; the person, her files and employers are
made up. Needs `HAL_TEST_BROWSER=1` and Playwright.
"""
from __future__ import annotations

import contextlib
import time
import unittest
from contextlib import closing

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db
from jobhunt.profile import contexts, store

CV = "Sources/CVs and letters/Alex Doe CV.md"
LETTER = "Sources/CVs and letters/Museum letter.md"
PROJECT = "Sources/Projects/Mill town letters.md"
MODULE = "Sources/Study notes/Archive methods week 3.md"
OLD_CV = "Sources/CVs and letters/Alex Doe CV 2022.md"
PLAN = "Sources/Projects/Mill town plan.md"
REPO = "Sources/Code/spinning-room repository.md"
EMPLOYER = "Fenwick Lantern Museum Trust"
# The shell asks for Home's summary, which this test does not serve; its 404 is not this page's fault.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"


def found(kind: str, fields: dict, source: str = CV, doc_type: str = "cv", quote: str = "", role: str = "") -> int:
    """Adds one fact as the ingest would, `role` saying whose its file is; returns its item's id."""
    with closing(db.connect()) as con:
        return store.add_found(con, kind, fields, {"source_rel": source, "quote": quote, "doc_type": doc_type,
                                                   "field": "", "role": role})


def judged(item_id: int, tier: str, why: str) -> None:
    """Puts an item in `tier` as Hal's AI would, with its reason."""
    with closing(db.connect()) as con, con:
        con.execute("UPDATE profile_items SET tier = ?, tier_by = 'hal', tier_why = ?, tier_basis = 'made-up' "
                    "WHERE id = ?", (tier, why, item_id))


def state(item_id: int) -> str:
    """The item's state as the store has it now."""
    with closing(db.connect()) as con:
        return store.item(con, item_id)["state"]


SAVE_WAIT_S = 5.0     # a press saves after the page moves on; a loaded CI machine took over a second


def state_soon(item_id: int, want: str) -> str:
    """The item's state once it is `want`, or as it stands after `SAVE_WAIT_S`: the page shows the next card
    before the press's save has landed, so reading at once raced it (3.13 local CI, 2026-11-12)."""
    deadline = time.monotonic() + SAVE_WAIT_S
    while (now := state(item_id)) != want and time.monotonic() < deadline:
        time.sleep(0.05)
    return now


@skip_unless_browser
class CheckPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._stack = contextlib.ExitStack()
        cls.browser = cls._stack.enter_context(chromium())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stack.close()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())

    def page(self, route: str = "check", size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and not any(n in r.url for n in NOT_YET)
                  and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def clean(self, p: AppPage) -> None:
        """No console errors, no sideways scroll and no serious accessibility findings."""
        self.assertEqual(p.missing, [])
        self.assertEqual([e for e in p.errors if not e.startswith(FAILED_LOAD)], [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def card(self, p: AppPage):
        """The card on show."""
        return p.page.locator("section.rec-check").first

    def skills(self) -> dict:
        """Two skills both a project and a CV name, so they are asked about, and one only an older CV names,
        which Hal is less sure of."""
        ids = {name: found("skill", {"name": name}, PROJECT, "notes", name.lower())
               for name in ("Oral history interviewing", "Transcription")}
        for name in ids:
            found("skill", {"name": name}, CV, "cv", name.lower())
        ids["Palaeography"] = found("skill", {"name": "Palaeography"}, OLD_CV, "cv", "palaeography")
        return ids

    def test_the_queue_asks_the_conflict_then_each_fact_then_the_skills_and_ends_ready(self) -> None:
        job = found("job", {"title": "Collections volunteer", "employer": EMPLOYER, "start": "Sept 2023"},
                    quote="Collections volunteer, Sept 2023 - present")
        found("job", {"title": "Collections volunteer", "employer": EMPLOYER, "start": "October 2023"}, LETTER,
              "letter", "since October 2023")
        name = found("basic", {"key": "name", "value": "Alex Doe"}, quote="Alex Doe")
        ids = self.skills()
        p = self.page()
        self.assertEqual(p.page.inner_text("#main h1"), "Check your record")
        p.shows(self.card(p).get_by_text("Which is right?"), "the conflict first")
        self.assertIn("0 of 4 checked.", p.page.inner_text("#main"))
        self.clean(p)
        self.card(p).get_by_role("button", name="Use October 2023").click()
        p.shows(self.card(p).get_by_text("Alex Doe"), "the name")
        self.assertEqual(state_soon(job, "edited"), "edited", "choosing a value checks the job too")
        self.card(p).get_by_role("button", name="Yes, that's right").click()
        p.shows(self.card(p).get_by_text("Skills from Alex Doe CV"), "the skills group, from the file trusted most")
        self.assertEqual(state_soon(name, "confirmed"), "confirmed")
        self.assertIn("2 of 4 checked.", p.page.inner_text("#main"))
        self.card(p).get_by_label("Transcription").uncheck()
        self.card(p).get_by_role("button", name="Keep the ticked ones").click()
        p.shows(p.page.get_by_text("Your record is ready"), "the end")
        self.assertEqual([state(ids[n]) for n in ("Oral history interviewing", "Transcription", "Palaeography")],
                         ["confirmed", "hidden", "hal"])
        self.clean(p)

    def test_fix_it_saves_the_persons_words_and_not_me_hides_the_fact(self) -> None:
        job = found("job", {"title": "Collections volunteer", "employer": EMPLOYER}, quote="Collections volunteer")
        cert = found("certificate", {"name": "Forklift licence"}, quote="Forklift licence")
        p = self.page()
        card = self.card(p)
        p.shows(card.get_by_text("Collections volunteer").first, "the job")
        self.assertIn('"Collections volunteer"', card.inner_text(), "the quote it came from")
        card.get_by_role("button", name="Fix it").click()
        card.get_by_label("Job title").fill("Collections assistant")
        card.get_by_role("button", name="Save").click()
        p.shows(self.card(p).get_by_text("Forklift licence").first, "the certificate")
        self.assertEqual(state(job), "edited")
        self.card(p).get_by_role("button", name="Not me").click()
        p.shows(p.page.get_by_text("Your record is ready"), "the end")
        self.assertEqual(state(cert), "hidden")

    def test_skills_hal_is_less_sure_of_wait_under_show_more(self) -> None:
        ids = self.skills()
        with closing(db.connect()) as con:
            store.confirm_many(con, [ids["Oral history interviewing"], ids["Transcription"]])
        p = self.page()
        p.shows(p.page.get_by_text("Your record is ready"), "the end of the main queue")
        more = p.page.locator("details.rec-check-more")
        more.locator("summary").click()
        more.get_by_role("button", name="None of these").click()
        p.page.locator("details.rec-check-more").wait_for(state="detached")
        self.assertEqual(state(ids["Palaeography"]), "hidden")

    def test_skills_only_course_material_names_are_listed_apart_and_one_can_be_claimed(self) -> None:
        stream = found("skill", {"name": "Stream processing"}, MODULE, "notes", "stream processing", "taught")
        found("skill", {"name": "Batch joins"}, MODULE, "notes", "batch joins", "taught")
        p = self.page()
        p.shows(p.page.get_by_text("Your record is ready"), "nothing else to check")
        studied = p.page.locator("details.rec-check-studied")
        self.assertIn("Studied in your course (2 skills)", studied.inner_text())
        studied.locator("summary").click()
        self.clean(p)
        studied.get_by_role("button", name="I have used this: Stream processing").click()
        p.page.get_by_text("Studied in your course (1 skill)").wait_for()
        self.assertEqual(state(stream), "confirmed")

    def test_skills_only_notes_mention_are_folded_apart_and_one_can_be_claimed(self) -> None:
        noted = found("skill", {"name": "Beam logging"}, PLAN, "notes", "beam logging", "own")
        p = self.page()
        p.shows(p.page.get_by_text("Your record is ready"), "nothing else to check")
        mentioned = p.page.locator("details.rec-check-mentioned")
        self.assertIn("Mentioned in your files (1 skill)", mentioned.inner_text())
        mentioned.locator("summary").click()
        self.clean(p)
        mentioned.get_by_role("button", name="I have used this: Beam logging").click()
        mentioned.wait_for(state="detached")
        self.assertEqual(state(noted), "confirmed")

    def test_cards_hal_merged_are_listed_and_undo_shows_them_apart_again(self) -> None:
        kept = found("project", {"name": "Mill town letters"}, CV, "cv", "Mill town letters", "own")
        twin = found("project", {"name": "Letters from the mill"}, PROJECT, "project", "Letters from the mill", "own")
        with closing(db.connect()) as con:
            store.merge_into(con, twin, kept, "Merged into Mill town letters")
        p = self.page()
        merged = p.page.locator("details.rec-check-merged")
        p.shows(merged, "the merges, apart from the queue")
        self.assertIn("Hal merged these (1 experience)", merged.inner_text())
        merged.locator("summary").click()
        self.assertIn("also found as Letters from the mill", merged.inner_text())
        self.clean(p)
        merged.get_by_role("button", name="Undo: Mill town letters").click()
        merged.wait_for(state="detached")
        self.assertEqual(state(twin), "hal")

    def test_the_person_says_a_project_is_the_same_as_another_keeps_one_name_and_can_undo_it(self) -> None:
        # one project was two cards, and hiding either lost its files (2026-11-08)
        listed = found("project", {"name": "Mill town letters"}, PROJECT, "project", "Mill town letters", "own")
        real = found("project", {"name": "Spinning room archive - letters edition"}, REPO, "repository",
                     "Spinning room archive sorts the letters by mill.", "own")
        with closing(db.connect()) as con:
            store.confirm(con, real)
        judged(listed, "core", "Work in the field you want")
        p = self.page("check", "narrow")
        card = p.page.locator(f"li[data-best='{listed}']")
        card.get_by_role("button", name="Same as...").click()
        pick = card.get_by_label("Which card is the same as this one?")
        p.shows(pick, "the picker")
        pick.select_option(label="Spinning room archive - letters edition")
        keep = card.get_by_role("group", name="Which name should Hal keep?")
        p.shows(keep, "the choice of name")
        self.assertTrue(keep.get_by_label("Mill town letters").is_checked(), "this card's name by default")
        self.clean(p)
        card.get_by_role("button", name="Merge them").click()
        mine = p.page.locator("details.rec-check-mine")
        p.shows(mine, "the merge listed as the person's")
        self.assertIn("You merged these (1 experience)", mine.inner_text())
        with closing(db.connect()) as con:
            gone, kept = store.item(con, real), store.item(con, listed)
        self.assertEqual((gone["state"], gone["merged_into"], kept["fields"]["name"]),
                         ("hidden", listed, "Mill town letters"))
        self.assertIn(REPO, [e["source_rel"] for e in kept["evidence"]])
        mine.locator("summary").click()
        self.assertIn("also found as Spinning room archive - letters edition", mine.inner_text())
        mine.get_by_role("button", name="Undo: Mill town letters").click()
        mine.wait_for(state="detached")
        self.assertEqual(state(real), "confirmed", "back as the person checked it")

    def test_a_hidden_card_from_a_shown_cards_own_files_is_offered_as_the_same(self) -> None:
        shown = found("project", {"name": "Mill town letters"}, REPO, "repository", "Mill town letters", "own")
        found("project", {"name": "Mill town letters"}, CV, "cv", "Mill town letters", "own")
        twin = found("project", {"name": "spinning_room"}, REPO, "repository", "spinning_room", "own")
        with closing(db.connect()) as con:
            store.hide(con, twin)
        p = self.page()
        twins = p.page.locator("details.rec-check-twins")
        p.shows(twins, "the hidden card offered")
        self.assertIn("Hidden cards from the same files (1)", twins.inner_text())
        twins.locator("summary").click()
        self.clean(p)
        twins.get_by_role("button", name="Same as Mill town letters: spinning_room").click()
        twins.wait_for(state="detached")
        with closing(db.connect()) as con:
            self.assertEqual(store.item(con, twin)["merged_into"], shown)

    def test_the_best_experiences_come_first_and_each_can_be_confirmed_archived_or_moved_up(self) -> None:
        job = found("job", {"title": "Collections volunteer", "employer": EMPLOYER, "start": "2023-09",
                            "current": True}, quote="Collections volunteer", role="own")
        letters = found("project", {"name": "Mill town letters"}, PROJECT, "notes", "mill town letters", "own")
        judged(job, "core", "Work in the field you want")
        judged(letters, "supporting", "A long piece of research")
        p = self.page()
        best = p.page.locator("section.rec-best")
        p.shows(best.get_by_role("heading", name="Your best experiences"), "the best experiences")
        first = best.locator("li.rec-best-item").first
        self.assertIn("Collections volunteer", first.inner_text())
        self.assertIn("Work in the field you want", first.inner_text())
        self.clean(p)
        first.get_by_role("button", name="Confirm").click()
        p.shows(best.locator("li.rec-best-item").first.get_by_text("Checked by you"), "the job checked")
        self.assertEqual(state(job), "confirmed")
        best.locator(f"li[data-best='{letters}']").get_by_role("button", name="Move to archive").click()
        archive = p.page.locator("details.rec-best-archive")
        p.shows(archive.get_by_text("Archived (1)"), "the project archived")
        archive.locator("summary").click()
        archive.get_by_role("button", name="Move up a tier: Mill town letters").click()
        p.shows(best.locator(f"li[data-best='{letters}']"), "the project back in the list")
        with closing(db.connect()) as con:
            self.assertEqual(store.item(con, letters)["tier"], "supporting")

    def test_the_person_says_where_a_best_experience_was_done_in_one_plain_choice(self) -> None:
        letters = found("project", {"name": "Mill town letters"}, PROJECT, "repository", "mill town letters", "own")
        judged(letters, "core", "Work in the field you want")
        with closing(db.connect()) as con:
            contexts.refresh(con)              # as the sort after each read does
        p = self.page("check", "narrow")
        where = p.page.locator(f"li[data-best='{letters}']").get_by_label("Where was this done?")
        p.shows(where, "the choice on the card")
        self.assertEqual(where.input_value(), "personal", "Hal's reading of a repository")
        self.clean(p)
        where.select_option(label="University team project")
        p.shows(p.page.get_by_text("Saved. Hal will describe it this way."), "the choice saved")
        with closing(db.connect()) as con:
            item = store.item(con, letters)
        self.assertEqual((item["context"], item["context_by"]), ("team", "person"))

    def test_at_phone_width_the_buttons_are_large_and_the_keyboard_moves_on(self) -> None:
        found("basic", {"key": "name", "value": "Alex Doe"}, quote="Alex Doe")
        found("basic", {"key": "email", "value": "alex@example.org"}, quote="alex@example.org")
        p = self.page("check", "narrow", "dark")
        yes = self.card(p).get_by_role("button", name="Yes, that's right")
        p.shows(yes, "the first card")
        self.assertGreaterEqual(yes.bounding_box()["height"], 44)
        self.clean(p)
        yes.focus()
        p.page.keyboard.press("Enter")
        p.shows(self.card(p).get_by_text("alex@example.org").first, "the next card")
        p.page.locator("#main .rec-check-h:focus").wait_for()
        self.assertEqual(p.focused(), "h2:alex@example.org")

    def test_your_record_links_to_the_check_page(self) -> None:
        found("basic", {"key": "name", "value": "Alex Doe"}, quote="Alex Doe")
        p = self.page("record")
        p.page.get_by_role("button", name="Check your record").click()
        p.shows(p.page.locator("#main h1", has_text="Check your record"), "the check page")
        self.assertTrue(p.page.url.endswith("#check"))


if __name__ == "__main__":
    unittest.main()
