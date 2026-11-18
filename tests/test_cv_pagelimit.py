"""A page limit per CV ("Keep this CV to") and the real page counts it is held to.

The limit has one source: the CV's own choice, else its CV type's rule. Counts come from a printed PDF only;
here a stand-in printer counts the made-up filler lines of the page (`FILLER_PER_PAGE` to a page), so no
browser starts, and a printer that fails stands for a computer with no browser. Every CV and person is made up.
"""
from __future__ import annotations

import contextlib
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import people
from support.api import HalServer
from support.world import World
from test_cv_printer import NoBrowsersHere
from test_cv_render import made_up_cv, profile_of, type_for

from jobhunt.cvstudio import checks, export, model, pagelimit, printer, store
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
    with mock.patch.object(pagelimit, "PRINT", p):
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
            for bad in (3, True, "2", None):
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


if __name__ == "__main__":
    unittest.main()
