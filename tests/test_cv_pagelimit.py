"""A page limit per CV ("Keep this CV to") and the real page counts it is held to.

The limit has one source: the CV's own choice, else its CV type's rule. Counts come from a printed PDF only;
here a stand-in printer counts the made-up filler lines of the page (`FILLER_PER_PAGE` to a page), so no
browser starts, and a printer that fails stands for a computer with no browser. Every CV and person is made up.
"""
from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import people
from support.api import HalServer
from support.fake_ai import fake_ai
from support.world import World
from test_cv_fresh import NO_MARKS
from test_cv_printer import NoBrowsersHere
from test_cv_render import made_up_cv, profile_of, type_for
from test_cv_suggest import PROFILE, STAR, TABLE, CvCase
from test_cv_tailor import JOB, NEW_PROFILE, NEW_TABLE, JobCase, tailoring_ai

from jobhunt.apply import queries
from jobhunt.cvstudio import checks, export, fresh, model, pagelimit, printer, store, suggest, tailor
from jobhunt.cvstudio import types as cvtypes

FILLER = "Filler line about a made-up archive"
FILLER_PER_PAGE = 10           # the stand-in printer's page: ten filler lines
LONG_PAGES = 3                 # pages of `long_cv`
SIMPLE_PDF_LINES = 160         # lines enough for Hal's simple PDF to run over two pages


def long_cv(lines: int = FILLER_PER_PAGE * (LONG_PAGES - 1)) -> model.CVDoc:
    """Marnie's made-up CV with `lines` filler sentences in her profile (a type cuts lines, never the profile)."""
    doc = made_up_cv(people.HUMANITIES.key)
    doc.sections[0].text += "".join(f" {FILLER} {i}." for i in range(lines))
    return doc


class StandIn:
    """A printer that counts `FILLER` lines; `fail` makes it fail as a computer with no browser does."""

    def __init__(self, fail: bool = False) -> None:
        self.fail, self.calls = fail, 0

    def __call__(self, html: str, out: Path, expect_name: str, expect_text: list[str]) -> int:
        self.calls += 1
        if self.fail:
            raise printer.PrintError("no browser that can print was found")
        return 1 + html.count(FILLER) // FILLER_PER_PAGE


@contextlib.contextmanager
def stand_in(fail: bool = False) -> Iterator[StandIn]:
    p = StandIn(fail)
    # each test starts with no remembered counts: one left by an earlier test hid a count kept under the wrong
    # page (the fresh CV test passed in its class and failed alone, 2026-11-19)
    with mock.patch.object(pagelimit, "PRINT", p), mock.patch.dict(pagelimit._COUNTED, clear=True):
        yield p


def humanities_cv_in_use() -> str:
    """Saves Marnie's CV as the lead CV's version, typed humanities_grad (two pages); returns the CV's id."""
    vid = store.new_version(long_cv(), origin="imported", what="Made-up CV", by="baseline")
    doc_id = store.version(vid)["doc_id"]
    store.set_type("humanities_grad", doc_id=doc_id)
    return doc_id


class OneSourceOfTruth(unittest.TestCase):
    def test_a_cv_follows_its_type_until_the_person_chooses_a_limit(self):
        with World():
            doc_id = humanities_cv_in_use()
            self.assertEqual(pagelimit.limit_of(doc_id), (2, False))
            store.set_type("business_grad", doc_id=doc_id)
            self.assertEqual(pagelimit.limit_of(doc_id), (1, False), "an unchosen limit moves with the type")
            store.set_page_limit(0, doc_id=doc_id)
            store.set_type("humanities_grad", doc_id=doc_id)
            self.assertEqual(pagelimit.limit_of(doc_id), (0, True), "the person's choice stays")
            for bad in (5, -1, True, "2", None):
                with self.assertRaises(model.CVError):
                    store.set_page_limit(bad, doc_id=doc_id)

    def test_the_length_check_holds_the_printed_count_to_the_cvs_own_limit(self):
        cv, t = made_up_cv(people.HUMANITIES.key), type_for("humanities_grad")
        profile = profile_of(people.HUMANITIES)
        items = {i["id"]: i for i in checks.type_checks(cv, t, profile, pages=3, limit=1)["items"]}
        self.assertEqual((items["pages_within"]["name"], items["pages_within"]["ok"]), ("One page", False))
        self.assertEqual(items["pages_within"]["note"], "3 pages.")
        unknown = checks.type_checks(cv, t, profile, pages=None, limit=2)
        item = next(i for i in unknown["items"] if i["id"] == "pages_within")
        self.assertIsNone(item["ok"], "no count is not known, never a word estimate")
        self.assertEqual(item["note"], checks.PAGES_UNKNOWN)
        self.assertEqual(unknown["passed"], sum(i["ok"] is True for i in unknown["items"]))
        none = checks.type_checks(cv, t, profile, pages=5, limit=0)["items"]
        self.assertNotIn("pages_within", [i["id"] for i in none], "no limit, no length check")

    def test_the_writing_card_no_longer_holds_a_length(self):
        self.assertNotIn("pages", cvtypes.WRITING_FIELDS)
        self.assertNotIn("pages", cvtypes.writing_card(cvtypes.load("humanities_grad")))


class RealCounts(unittest.TestCase):
    def test_a_version_is_printed_once_and_its_count_kept_until_its_look_changes(self):
        with World(), stand_in() as p:
            doc_id = humanities_cv_in_use()
            vid = store.current(doc_id=doc_id)[0]
            self.assertEqual(pagelimit.known(vid), (None, False))
            self.assertEqual(pagelimit.measure(vid), LONG_PAGES)
            self.assertEqual(pagelimit.measure(vid), LONG_PAGES)
            self.assertEqual(p.calls, 1, "a count is kept with the version")
            self.assertEqual(pagelimit.known(vid), (LONG_PAGES, True))
            store.set_look("modern", doc_id=doc_id)
            self.assertEqual(pagelimit.known(vid), (None, False), "a new look prints a new page")
            pagelimit.measure(vid)
            self.assertEqual(p.calls, 2)

    def test_the_warning_after_a_save_names_the_real_numbers(self):
        with World(), stand_in():
            doc_id = humanities_cv_in_use()
            got = pagelimit.state(doc_id, now=True)
            self.assertEqual((got["limit"], got["pages"], got["over"]), (2, LONG_PAGES, True))
            self.assertEqual(got["words"], "This CV is now 3 pages. You asked for 2.")
            store.set_page_limit(0, doc_id=doc_id)
            self.assertEqual(pagelimit.state(doc_id)["words"], "", "no limit, no warning")

    def test_with_no_browser_the_count_is_not_known_and_said_so(self):
        with World(), stand_in(fail=True) as p:
            doc_id = humanities_cv_in_use()
            got = pagelimit.state(doc_id)
            self.assertEqual((got["pages"], got["counted"], got["over"]), (None, False, False))
            self.assertEqual(got["words"], pagelimit.UNKNOWN)
            pagelimit.state(doc_id)
            self.assertEqual(p.calls, 1, "a page that could not print is not tried again until it changes")

    def test_a_test_run_never_starts_a_browser_to_count(self):
        with World(), mock.patch.object(printer, "to_pdf", side_effect=AssertionError("a browser started")):
            doc_id = humanities_cv_in_use()
            self.assertIsNone(pagelimit.state(doc_id)["pages"])

    def test_a_cv_over_its_types_limit_still_prints_whole(self):
        # 2.3.0 failed the print of a CV more than a page over its type's rule, the simple PDF too
        doc = long_cv(SIMPLE_PDF_LINES)
        out = Path(self.enterContext(tempfile.TemporaryDirectory())) / "long.pdf"
        with World(), mock.patch.object(printer, "_has_playwright", lambda: False), \
                mock.patch.object(printer, "_platform", NoBrowsersHere):    # the simple PDF, browser tests on or off
            done = export.cv_pdf(doc, type_for("business_grad"), "classic", profile_of(people.HUMANITIES), out)
        self.assertGreater(done["pages"], 2)


class Routes(unittest.TestCase):
    def test_keep_this_cv_to_is_set_and_read_for_one_cv(self):
        with HalServer() as hal, stand_in():
            doc_id = humanities_cv_in_use()
            other = store.create("Made-up second CV", made_up_cv(people.HUMANITIES.key), origin="imported",
                                 what="Made-up CV", by="baseline", star=False)
            client = hal.signed_in()
            got = client.get(f"/api/v2/cv/pages?cv={doc_id}").json()
            self.assertEqual((got["limit"], got["chosen"], got["pages"]), (2, False, LONG_PAGES))
            put = client.request("PUT", f"/api/v2/cv/pages?cv={doc_id}", {"pages": 1}).json()
            self.assertEqual((put["limit"], put["chosen"]), (1, True))
            self.assertIn("You asked for 1", client.get(f"/api/v2/cv/pages?cv={doc_id}&saved=1").json()["words"])
            self.assertFalse(client.get(f"/api/v2/cv/pages?cv={other}").json()["chosen"], "each CV has its own")
            self.assertEqual(client.request("PUT", f"/api/v2/cv/pages?cv={doc_id}", {"pages": 4}).status, 400)

    def test_a_copied_type_of_three_pages_is_offered_as_three_pages_and_no_other_cv_gets_it(self):
        with HalServer() as hal, stand_in():
            doc_id = humanities_cv_in_use()
            other = store.create("Made-up second CV", made_up_cv(people.HUMANITIES.key), origin="imported",
                                 what="Made-up CV", by="baseline", star=False)
            store.set_type("humanities_grad", doc_id=other)
            store.set_type(three_page_type(), doc_id=doc_id)
            client = hal.signed_in()
            got = client.get(f"/api/v2/cv/pages?cv={doc_id}").json()
            self.assertEqual((got["limit"], got["chosen"]), (3, False))
            self.assertEqual(got["choices"], [[0, "No limit"], [1, "One page"], [2, "Two pages"],
                                              [3, "Three pages"]])
            self.assertEqual(client.request("PUT", f"/api/v2/cv/pages?cv={doc_id}", {"pages": 1}).status, 200)
            back = client.request("PUT", f"/api/v2/cv/pages?cv={doc_id}", {"pages": 3}).json()
            self.assertEqual((back["limit"], back["chosen"]), (3, True), "the type's longer limit can be chosen")
            self.assertEqual(len(client.get(f"/api/v2/cv/pages?cv={other}").json()["choices"]), 3)
            self.assertEqual(client.request("PUT", f"/api/v2/cv/pages?cv={other}", {"pages": 3}).status, 400)


def three_page_type() -> str:
    """A copy of the humanities type with a three-page rule; its id."""
    t = cvtypes.copy("humanities_grad", "Made-up long CV")
    cvtypes.save_source(t.id, t.source.replace("\npages = 2\n", "\npages = 3\n"))
    assert cvtypes.load(t.id).pages == 3, "the made-up copy keeps its three-page rule"
    return t.id


def job_pages(base: int):
    """A stand-in for drawing a job's CV: `base` pages, and one more for each of Hal's two rewordings on it."""
    def count(v1: dict) -> int:
        text = json.dumps(v1)
        return base + (NEW_PROFILE in text) + (NEW_TABLE in text)
    return mock.patch.object(pagelimit, "drawn_pages", count)


class HalKeepsAJobsCvToTheLimit(JobCase):
    """Adjusting more for a job with the stand-in AI (`test_cv_tailor.tailoring_ai`): it rewords the profile
    and a line of the bookshop job, and each rewording adds a page by the stand-in count."""

    def changes(self) -> dict:
        return {c["target"]: c for c in tailor.shown(JOB["id"])["changes"]}

    def test_hals_rewordings_that_run_over_are_undone_newest_first_and_said(self):
        store.set_page_limit(2)
        with fake_ai({"cv_suggest": tailoring_ai}), job_pages(2):
            tailor.prepare(JOB, level="more")
            got = self.changes()
            self.assertEqual((got[TABLE]["on"], got[TABLE]["held"]), (False, tailor.HELD_FIT), "the newest first")
            self.assertEqual((got[PROFILE]["on"], got[PROFILE]["held"]), (False, tailor.HELD_FIT))
            self.assertTrue(all(c["on"] for c in got.values() if c["kind"] == "Moved"), "a reorder adds nothing")
            state = pagelimit.job_state(JOB["id"])
            self.assertEqual((state["pages"], state["words"]), (2, ""))
            self.assertEqual(state["note"], "Hal undid 2 of its own changes to keep this CV to 2 pages. "
                                            "Press Redo to use one anyway.")
            tailor.set_change(JOB["id"], got[TABLE]["id"], True)
            state = pagelimit.job_state(JOB["id"])
            self.assertEqual(state["words"], "This CV is 3 pages. You asked for 2.", "Redo is the person's choice")
            self.assertEqual(self.changes()[TABLE]["held"], "")

    def test_a_cv_over_the_limit_before_hal_changed_anything_is_kept_as_it_was(self):
        store.set_page_limit(2)
        with fake_ai({"cv_suggest": tailoring_ai}), job_pages(3):
            tailor.prepare(JOB, level="more")
            self.assertTrue(all(not c["on"] and c["held"] == tailor.HELD_BASE for c in self.changes().values()))
            state = pagelimit.job_state(JOB["id"])
            self.assertTrue(state["kept_base"])
            self.assertEqual(state["words"], "This CV is 3 pages. You asked for 2.")
            self.assertIn("kept it as it is for this job", state["note"])

    def test_with_no_limit_hal_keeps_every_change(self):
        store.set_page_limit(0)
        with fake_ai({"cv_suggest": tailoring_ai}), job_pages(2):
            tailor.prepare(JOB, level="more")
        self.assertTrue(all(c["on"] for c in self.changes().values()))

    def test_the_persons_own_edits_are_never_undone_only_warned_about(self):
        changes = [{"id": "c1", "kind": "Reworded", "op": "replace", "target": PROFILE, "value": NEW_PROFILE,
                    "before": "Short", "after": NEW_PROFILE},
                   {"id": "y1", "by": tailor.YOU, "op": "replace", "target": TABLE, "value": NEW_TABLE}]
        pages = iter([4, 3])                 # all on, then the person's edit alone: over before Hal
        tailor.fit(store.current()[1], changes, 2, lambda doc: next(pages))
        self.assertEqual((changes[0].get("undone"), changes[0].get("held")), (True, tailor.HELD_BASE))
        self.assertNotIn("undone", changes[1])

    def test_the_documents_step_shows_the_count_before_approve(self):
        store.set_page_limit(1)
        with fake_ai({"cv_suggest": tailoring_ai}), job_pages(2):
            tailor.prepare(JOB, level="little")
            got = queries.application(self.con, JOB["id"])["docs"]["cv"]["pages"]
        self.assertEqual((got["over"], got["words"]), (True, "This CV is 2 pages. You asked for 1."))


class SuggestionsKeepToTheLimit(CvCase):
    """Accepting Hal's wording on My CV with the stand-in printer counting `FILLER` sentences."""

    def suggestion(self, afters: list[str], target: str = PROFILE, kind: str = "Wording") -> str:
        vid, doc = store.current()
        draft = suggest.Draft(target=target, op="replace", kind=kind, title="Say more", why="Made up.",
                              trigger={"id": f"made-up-{target}"}, before=suggest.shown_at(doc, "replace", target),
                              afters=afters, section_id=target.split("/")[0][2:])
        sid = suggest._insert(self.con, draft, vid)
        self.con.commit()
        return sid

    def setUp(self) -> None:
        super().setUp()
        store.set_page_limit(1)
        self.long = "Catalogued parish letters." + "".join(f" {FILLER} {i}." for i in range(FILLER_PER_PAGE))

    def test_a_wording_that_runs_over_gives_way_to_a_shorter_one_that_fits(self):
        sid = self.suggestion([self.long, "Catalogued parish letters for a records office."])
        with stand_in():
            out = suggest.accept(self.con, sid)
        self.assertEqual(out["note"], "Hal used its shorter wording, so this CV keeps to 1 page.")
        self.assertEqual(self.text_at(PROFILE), "Catalogued parish letters for a records office.")

    def test_a_wording_that_runs_over_with_nothing_shorter_is_not_used(self):
        sid = self.suggestion([self.long])
        first = store.current()[0]
        with stand_in(), self.assertRaises(ValueError) as held:
            suggest.accept(self.con, sid)
        self.assertEqual(str(held.exception), "Hal's wording would make this CV 2 pages, and you asked for 1, so Hal "
                                              "did not use it. Edit the words to use them anyway.")
        self.assertEqual(store.current()[0], first)
        self.assertEqual(suggest._row(self.con, sid)["status"], "pending")

    def test_your_own_edit_of_a_wording_is_used_however_long(self):
        sid = self.suggestion([self.long])
        with stand_in():
            suggest.accept(self.con, sid, text=self.long)
        self.assertEqual(self.text_at(PROFILE), self.long)

    def fixes(self, profile_lines: int, star_lines: int) -> None:
        """Two Accept all fixes ("Dates"), each adding that many filler sentences to its line."""
        for target, n in ((PROFILE, profile_lines), (STAR, star_lines)):
            self.suggestion([self.text_at(target) + f" {FILLER}." * n], target, "Dates")

    def test_accept_all_leaves_out_the_last_fixes_that_run_over_and_says_so(self):
        self.fixes(5, 6)
        before = self.text_at(STAR)
        with stand_in():
            out = suggest.accept_kind(self.con, "Dates")
        self.assertEqual(out["accepted"], 1)
        self.assertEqual(out["note"], "Hal left out 1 fix that would make this CV longer than 1 page.")
        self.assertEqual(self.text_at(STAR), before)
        self.assertIn(FILLER, self.text_at(PROFILE))

    def test_accept_all_applies_nothing_when_even_one_fix_runs_over(self):
        self.fixes(FILLER_PER_PAGE, FILLER_PER_PAGE)
        first = store.current()[0]
        with stand_in():
            out = suggest.accept_kind(self.con, "Dates")
        self.assertEqual((out["accepted"], out["version_id"]), (0, first))
        self.assertEqual(out["note"], "Hal left out 2 fixes that would make this CV longer than 1 page.")

    def test_accept_all_with_no_limit_applies_every_fix(self):
        store.set_page_limit(0)
        self.fixes(FILLER_PER_PAGE, FILLER_PER_PAGE)
        with stand_in() as p:
            out = suggest.accept_kind(self.con, "Dates")
        self.assertEqual((out["accepted"], out["note"], p.calls), (2, "", 0))


class FreshCvKeepsToTheLimit(unittest.TestCase):
    """Build me a fresh CV (`test_cv_fresh.NO_MARKS`) for a CV of one page, with the stand-in printer."""

    def setUp(self) -> None:
        super().setUp()
        store.set_page_limit(1, doc_id=store.doc()["id"])

    def test_the_ais_long_profile_gives_way_to_the_plain_one_that_fits(self):
        long_profile = "History graduate who catalogued parish letters." + f" {FILLER}." * FILLER_PER_PAGE
        with fake_ai({"cv_compose": json.dumps({"summary": long_profile})}), stand_in() as p:
            out = fresh.make(NO_MARKS, "humanities_grad")
        self.assertEqual(out["note"], "Hal wrote a shorter profile, so this CV keeps to 1 page.")
        self.assertEqual(store.current()[1].sections[0].text, fresh.profile_line(NO_MARKS))
        self.assertEqual(p.calls, 2, "a print for each draft tried, no more")

    def test_answers_that_alone_run_over_are_all_kept_and_the_real_numbers_said(self):
        answers = {**NO_MARKS, "interests": "Lead a monthly local history walk." + f" {FILLER}." * FILLER_PER_PAGE}
        with stand_in():
            out = fresh.make(answers, "humanities_grad")
        self.assertEqual(out["note"], "This CV is 2 pages, and you asked for 1. Your answers alone take more room "
                                      "than that, so Hal kept them all.")
        doc = store.current()[1]
        self.assertEqual(doc.sections[-1].text, answers["interests"])
        self.assertEqual(doc.sections[0].text, fresh.profile_line(NO_MARKS))

    def test_with_no_limit_nothing_is_printed(self):
        with stand_in() as p:
            store.set_page_limit(0, doc_id=store.doc()["id"])
            out = fresh.make(NO_MARKS, "humanities_grad")
        self.assertEqual((out["note"], p.calls), ("", 0))

    def test_the_draft_saved_keeps_its_count_and_is_not_printed_again(self):
        long_profile = "History graduate who catalogued parish letters." + f" {FILLER}." * FILLER_PER_PAGE
        with fake_ai({"cv_compose": json.dumps({"summary": long_profile})}), stand_in() as p:
            out = fresh.make(NO_MARKS, "humanities_grad")
            calls = p.calls
            self.assertEqual(pagelimit.known(out["version_id"]), (1, True))
            self.assertEqual(pagelimit.measure(out["version_id"]), 1)
        self.assertEqual(p.calls, calls)


class DraftsPrintAsTheyWillBeSaved(unittest.TestCase):
    """A draft is counted with the layout it will have once saved, not the version in use's (2026-11-19)."""

    def test_a_draft_on_a_cv_whose_version_in_use_differs_keeps_its_count_when_saved(self):
        with World(), stand_in() as p:
            doc_id = humanities_cv_in_use()
            first = store.current(doc_id=doc_id)[0]
            draft = made_up_cv(people.HUMANITIES.key)
            draft.sections = [s for s in reversed(draft.sections)]        # other sections, in another order
            draft.sections[0].title = "Things I enjoy"
            self.assertEqual(pagelimit.count_doc(draft, doc_id), 1)
            self.assertEqual(store.current(doc_id=doc_id)[0], first, "counting saves nothing")
            vid = store.new_version(draft, origin="manual", what="Made-up draft", by="you", doc_id=doc_id)
            self.assertEqual(pagelimit.keep(vid), 1)
            self.assertEqual((pagelimit.measure(vid), p.calls), (1, 1), "not printed again")

    def test_counting_a_draft_on_a_cv_with_no_version_makes_none(self):
        with World(), stand_in():
            doc_id = store.doc()["id"]
            pagelimit.count_doc(made_up_cv(people.HUMANITIES.key), doc_id)
            self.assertIsNone(store.doc(doc_id=doc_id)["current_version_id"])


class CountsKeptWithTheVersion(CvCase):
    """A count printed while Hal weighs its wording is kept with the version it produced (`pagelimit.keep`)."""

    suggestion = SuggestionsKeepToTheLimit.suggestion   # its helpers, not its tests
    fixes = SuggestionsKeepToTheLimit.fixes

    def setUp(self) -> None:
        super().setUp()
        store.set_page_limit(1)
        self.long = "Catalogued parish letters." + "".join(f" {FILLER} {i}." for i in range(FILLER_PER_PAGE))

    def test_an_accepted_suggestion_keeps_the_count_it_was_weighed_with(self):
        sid = self.suggestion([self.long, "Catalogued parish letters for a records office."])
        with stand_in() as p:
            vid = suggest.accept(self.con, sid)["version_id"]
            calls = p.calls
            self.assertEqual(pagelimit.known(vid), (1, True))
            self.assertEqual(pagelimit.state()["pages"], 1)
        self.assertEqual(p.calls, calls, "no second print for the same page")

    def test_accept_all_keeps_the_count_of_the_fixes_it_applied(self):
        self.fixes(5, 6)
        with stand_in() as p:
            vid = suggest.accept_kind(self.con, "Dates")["version_id"]
            calls = p.calls
            pagelimit.measure(vid)
        self.assertEqual((pagelimit.known(vid), p.calls), ((1, True), calls))

    def test_a_version_with_no_count_made_keeps_none_and_prints_when_asked(self):
        vid = store.current()[0]
        self.assertIsNone(pagelimit.keep(vid))
        self.assertEqual(pagelimit.known(vid), (None, False))


if __name__ == "__main__":
    unittest.main()
