"""The "Add" panels of Your record in a real browser, against a real server in a temporary home.

The cards are the made-up history graduate's (Marnie Quillfeather) and software graduate's (Avery Quillon),
added through the store as a read would add them. Needs `HAL_TEST_BROWSER=1` and Playwright. No AI is used:
the only reader written so far, for Documents, reads no facts.
"""
from __future__ import annotations

import contextlib
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from support.api import HalServer
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db, record
from jobhunt.core import paths
from jobhunt.evidence import routes
from jobhunt.profile import store
from jobhunt.sections import store as sources

# The shell asks for Home's summary, which this test does not serve; its 404 is not this page's fault.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"
CV_REL = "Sources/CVs/Marnie CV.md"
AWARD = "# Volunteer of the year\n\nAwarded to Marnie Quillfeather on 5 March 2025.\n"
ESSAY = "# The parish registers of Harrowgate Vale\n\nAn essay on baptisms, 1750 to 1850.\n"
CV = ("# Marnie Quillfeather\n\nCurriculum vitae\n\n## Work experience\n\nCollections volunteer.\n\n"
      "## Education\n\nBA History.\n")


def found(kind: str, fields: dict) -> int:
    """Adds one fact as a read of her CV would; returns its card's id."""
    with closing(db.connect()) as con:
        return store.add_found(con, kind, fields, {"source_rel": CV_REL, "doc_type": "cv"})


@skip_unless_browser
class RecordSections(unittest.TestCase):
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
        routes.make_default()
        self.files = Path(self.hal.home) / "to-upload"
        self.files.mkdir()

    def file(self, name: str, text: str) -> str:
        """A made-up file to upload; its path."""
        path = self.files / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def page(self, route: str, size: str = "wide", scheme: str = "light") -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", size, scheme)
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open(route)
        return p

    def clean(self, p: AppPage) -> None:
        """No console errors, no sideways scroll and no serious accessibility findings."""
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET)]
        self.assertEqual([e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours, [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def panel(self, p: AppPage, heading: str):
        """The panel with this heading."""
        return p.page.locator("section.rec-panel", has=p.page.get_by_role("heading", name=heading, exact=True))

    def listed(self, section: str) -> list[dict]:
        with closing(db.connect()) as con:
            return sources.listing(con, section)

    def test_the_cvs_panel_takes_cvs_and_letters_and_the_old_drop_zone_is_folded_away(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("record", size, scheme)
                cvs = self.panel(p, "Your CVs")
                p.shows(cvs, "the CVs panel")
                kinds = cvs.get_by_label("What is it?")
                self.assertEqual(kinds.locator("option").all_inner_texts(),
                                 ["CV", "Cover letter", "Personal statement"])
                fold = p.page.locator("details.rec-more")
                self.assertIsNone(fold.get_attribute("open"))
                fold.locator("summary", has_text="More ways to add").click()
                p.shows(fold.get_by_text("Drop files here to add them"), "the old drop zone")
                self.clean(p)
        cvs.locator("input[type=file]").first.set_input_files(self.file("Marnie CV.md", CV))
        row = cvs.locator("li.rec-source", has_text="Marnie CV")
        p.shows(row.get_by_text("Read", exact=True), "the read chip")
        self.assertEqual([s["section"] for s in self.listed("cv")], ["cv"])
        row.get_by_role("button", name="Make this my current CV").click()
        p.shows(row.get_by_text("Current CV"), "the current chip")

    def test_a_document_is_read_listed_and_removed_on_the_documents_tab(self) -> None:
        p = self.page("record-documents")
        docs = self.panel(p, "Add documents")
        docs.locator("input[type=file]").first.set_input_files(self.file("award.md", AWARD))
        row = docs.locator("li.rec-source", has_text="Volunteer of the year")
        p.shows(row.get_by_text("Read", exact=True), "the read chip")
        self.assertIn("Last read", row.inner_text())
        self.assertIn("What is in it", p.page.inner_text("#main"))
        self.clean(p)
        row.get_by_role("button", name="Remove: Volunteer of the year").click()
        p.shows(p.page.get_by_text("Removed, with what Hal took from it."), "the toast")
        self.assertEqual(self.listed("document"), [])

    def test_an_old_link_to_the_files_tab_opens_documents(self) -> None:
        p = self.page("record-files")
        p.page.get_by_role("tab", name="Documents", selected=True).wait_for()

    def test_an_education_card_takes_coursework_which_can_be_starred(self) -> None:
        found("education", {"qualification": "BA", "subject": "History", "institution": "University of Nowhere Fen"})
        p = self.page("record-education")
        card = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="BA History"))
        card.locator("summary", has_text="Coursework, lecture material and official papers").click()
        work = self.panel(p, "Coursework")
        for heading in ("Lecture material", "Official papers"):
            p.shows(self.panel(p, heading), heading)
        work.get_by_label("What is it?").select_option(label="Dissertation")
        work.locator("input[type=file]").first.set_input_files(self.file("dissertation.md", ESSAY))
        row = work.locator("li.rec-source", has_text="dissertation.md")
        p.shows(row, "the coursework")
        row.get_by_role("button", name="Showcase: dissertation.md").click()
        p.shows(row.get_by_role("button", name="Take out of your showcase: dissertation.md"), "the showcase")
        got = self.listed("coursework")[0]
        self.assertEqual((got["doc_kind"], got["starred"]), ("dissertation", True))
        self.clean(p)

    def test_a_job_card_takes_its_documents(self) -> None:
        found("job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust"})
        p = self.page("record-work")
        card = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="Collections volunteer"))
        card.locator("summary", has_text="Add a document for this job").click()
        docs = self.panel(p, "Documents for this job")
        self.assertEqual(docs.get_by_label("What is it?").locator("option").all_inner_texts(),
                         ["Reference", "Contract", "Job description", "Something else"])
        self.clean(p)

    def test_the_ways_to_add_a_project_show_and_wait_as_coming_soon_and_a_project_is_starred(self) -> None:
        project = found("project", {"name": "Tide gauge"})
        p = self.page("record-projects")
        panel = self.panel(p, "Add projects")
        for words in ("Your GitHub account", "Link to one code project", "A folder on this computer", "Upload a folder",
                      "Portfolio site", "Web page or online document", "Add one by hand"):
            self.assertTrue(panel.get_by_role("button", name=words).is_disabled(), words)
        self.assertEqual(panel.get_by_text("Coming soon").count(), 7)
        card = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="Tide gauge"))
        card.get_by_role("button", name="Showcase: Tide gauge").click()
        p.shows(card.get_by_role("button", name="Take out of your showcase: Tide gauge"), "the showcase button")
        with closing(db.connect()) as con:
            self.assertTrue(store.item(con, project)["star"])
        self.clean(p)


if __name__ == "__main__":
    unittest.main()
