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

from support import repos
from support.api import HalServer
from support.fake_github import FakeGitHub
from support.fake_site import FakeSite
from support.ui import AppPage, chromium, skip_unless_browser

from jobhunt import db, record
from jobhunt.core import paths, settings
from jobhunt.evidence import routes
from jobhunt.profile import store
from jobhunt.sections import pipeline
from jobhunt.sections import store as sources

# The shell asks for Home's summary, which this test does not serve; its 404 is not this page's fault.
NOT_YET = ("/api/v2/home",)
FAILED_LOAD = "Failed to load resource"
CV_REL = "Sources/CVs/Marnie CV.md"
AWARD = "# Volunteer of the year\n\nAwarded to Marnie Quillfeather on 5 March 2025.\n"
ESSAY = "# The parish registers of Harrowgate Vale\n\nAn essay on baptisms, 1750 to 1850.\n"
CV = ("# Marnie Quillfeather\n\nCurriculum vitae\n\n## Work experience\n\nCollections volunteer.\n\n"
      "## Education\n\nBA History.\n")
REFERENCE = ("# Reference for Marnie\n\nTo whom it may concern,\n\nMarnie has volunteered as a Collections "
             "volunteer at the Fenwick Lantern Museum Trust since 2023.\n")
TRANSCRIPT = "# Transcript of study\n\nProgramme: BA (Hons) History\n\nHIS1010 Medieval Europe 66\n"
OLDER_CV = "# Marnie Quillfeather\n\nCV, May 2019\n\n## Work experience\n\nPaper round, Harrowgate Gazette, 2018\n"


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

    def test_the_cvs_panel_takes_cvs_and_letters_and_the_old_drop_zone_is_gone(self) -> None:
        for size, scheme in (("wide", "light"), ("narrow", "dark")):
            with self.subTest(size=size):
                p = self.page("record", size, scheme)
                cvs = self.panel(p, "Your CVs")
                p.shows(cvs, "the CVs panel")
                kinds = cvs.get_by_label("What is it?")
                self.assertEqual(kinds.locator("option").all_inner_texts(),
                                 ["CV", "Cover letter", "Personal statement"])
                self.assertEqual(p.page.get_by_text("More ways to add").count(), 0, "the old drop zone is gone")
                p.shows(p.page.get_by_role("heading", name="Sort a big folder for me"), "the sort card instead")
                self.clean(p)
        cvs.locator("input[type=file]").first.set_input_files(self.file("Marnie CV.md", CV))
        row = cvs.locator("li.rec-source", has_text="Marnie CV")
        p.shows(row.get_by_text("Read", exact=True), "the read chip")
        self.assertEqual([s["section"] for s in self.listed("cv")], ["cv"])
        p.shows(row.get_by_text("Current CV"), "the current chip: the only CV is the newest")
        cvs.locator("input[type=file]").first.set_input_files(self.file("Marnie CV 2019.md", OLDER_CV))
        older = cvs.locator("li.rec-source", has_text="Marnie CV 2019")
        p.shows(older.get_by_text("Older CV, fills gaps"), "the older CV's chip")
        older.get_by_role("button", name="Make this my current CV").click()
        p.shows(older.get_by_text("Current CV"), "the current chip on the CV the person chose")
        p.shows(row.get_by_text("Older CV, fills gaps"), "the other CV is older now")

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

    def test_a_document_can_name_its_card_and_hal_suggests_its_kind(self) -> None:
        job = found("job", {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust"})
        p = self.page("record-documents")
        docs = self.panel(p, "Add documents")
        docs.get_by_label("What is it?").select_option(label="Reference")
        docs.get_by_label("Is it about one of these? (optional)").select_option(label="Collections volunteer")
        docs.locator("input[type=file]").first.set_input_files(self.file("reference.md", REFERENCE))
        row = docs.locator("li.rec-source", has_text="Reference for Marnie")
        p.shows(row.get_by_text("Read", exact=True), "the read chip")
        self.assertEqual(self.listed("document")[0]["target_id"], job)
        docs.get_by_label("What is it?").select_option(label="Something else")
        docs.get_by_label("Is it about one of these? (optional)").select_option(label="None of them")
        docs.locator("input[type=file]").first.set_input_files(self.file("marks.md", TRANSCRIPT))
        marks = docs.locator("li.rec-source", has_text="Transcript of study")
        marks.get_by_role("button", name="Mark it as a transcript").click()
        p.shows(marks.get_by_text("Transcript", exact=True), "the new kind")
        self.clean(p)

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
        title = "The parish registers of Harrowgate Vale"     # the coursework reader takes it from the heading
        row = work.locator("li.rec-source", has_text=title)
        p.shows(row, "the coursework")
        row.get_by_role("button", name="Showcase: " + title).click()
        p.shows(row.get_by_role("button", name="Take out of your showcase: " + title), "the showcase")
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

    def test_every_way_to_add_a_project_is_offered_and_a_project_is_starred(self) -> None:
        project = found("project", {"name": "Tide gauge"})
        p = self.page("record-projects")
        panel = self.panel(p, "Add projects")
        for words in ("Upload a folder", "Your GitHub account", "Link to one code project",
                      "A folder on this computer", "Portfolio site", "Web page or online document", "Add one by hand"):
            self.assertTrue(panel.get_by_role("button", name=words).is_enabled(), words)
        self.assertEqual(panel.get_by_text("Coming soon").count(), 0)
        card = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="Tide gauge"))
        p.shows(card.get_by_text("from your CV only"), "the mark on a project only a CV gives")
        card.get_by_role("button", name="Showcase: Tide gauge").click()
        p.shows(card.get_by_role("button", name="Take out of your showcase: Tide gauge"), "the showcase button")
        with closing(db.connect()) as con:
            self.assertTrue(store.item(con, project)["star"])
        self.clean(p)

    def test_a_code_project_with_none_of_your_changes_is_marked_yours_with_this_is_mine(self) -> None:
        settings.set("you.name", repos.AVERY[0])
        settings.set("you.email", repos.AVERY[1])
        folder = repos.make(self.files / "lent", {"README.md": "# Lent\n", "a.py": "1\n"},
                            [(repos.ROBIN, "2023-02-01T00:00:00Z", {})])
        with closing(db.connect()) as con:
            source_id = pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0]
            pipeline.read(con, source_id)
        p = self.page("record-projects")
        row = self.panel(p, "Add projects").locator("li.rec-source", has_text="lent")
        p.shows(row.get_by_text("None of the changes are yours"), "the note")
        self.clean(p)
        row.get_by_role("button", name="This is mine: lent").click()
        p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="lent")).wait_for()
        self.assertTrue(self.listed("project")[0]["theirs"])

    def test_a_github_account_lists_each_project_under_it_with_its_own_card(self) -> None:
        settings.set("you.name", repos.AVERY[0])
        settings.set("you.email", repos.AVERY[1])
        gh = self.enterContext(FakeGitHub())
        gh.api["/users/averyq"] = (200, {"login": "averyq"})
        gh.api["/users/averyq/repos"] = (200, [gh.project("averyq", "tide", {"README.md": "# Tide\n"}),
                                               gh.project("averyq", "harbour", {"README.md": "# Harbour\n"})])
        p = self.page("record-projects")
        panel = self.panel(p, "Add projects")
        panel.get_by_role("button", name="Your GitHub account").click()
        panel.get_by_label("Your GitHub account").fill("averyq")
        panel.get_by_role("button", name="Add", exact=True).click()
        for name in ("tide", "harbour"):
            p.shows(panel.locator("li.rec-source.rec-child", has_text=name), name)
            p.page.locator("article.rec-item", has=p.page.locator("h3", has_text=name)).wait_for()
        self.clean(p)

    def test_a_portfolio_offers_its_pieces_and_a_ticked_piece_becomes_a_project(self) -> None:
        site = self.enterContext(FakeSite())
        index = site.saved("/work/", "portfolio.html")
        site.saved("/work/harbour-posters/", "harbour-posters.html")
        p = self.page("record-projects")
        panel = self.panel(p, "Add projects")
        panel.get_by_role("button", name="Portfolio site").click()
        panel.get_by_label("Portfolio site").fill(index)
        panel.get_by_role("button", name="Add", exact=True).click()
        picker = panel.locator("details.rec-pieces")
        picker.locator("summary").click()
        picker.get_by_label("Three screen-printed harbour posters").check()
        self.clean(p)
        picker.get_by_role("button", name="Add the ticked pieces").click()
        p.shows(panel.locator("li.rec-source.rec-child", has_text="Harbour posters"), "the piece")
        p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="Harbour posters")).wait_for()

    def test_a_project_added_by_hand_with_a_file_becomes_a_card(self) -> None:
        p = self.page("record-projects", "narrow", "dark")
        panel = self.panel(p, "Add projects")
        panel.get_by_role("button", name="Add one by hand").click()
        form = panel.get_by_role("form", name="Add a project by hand")
        form.get_by_label("Name").fill("Harbour clean-up day")
        form.get_by_label("Your part").fill("Organiser")
        form.get_by_label("Tools or methods").fill("Event planning, Canva")
        form.get_by_label("Files and pictures").set_input_files(self.file("poster.md", "# Poster\n\nMade-up.\n"))
        self.clean(p)
        form.get_by_role("button", name="Add the project").click()
        card = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="Harbour clean-up day"))
        card.wait_for()
        p.shows(card.get_by_text("Organiser"), "the part")
        self.assertEqual(card.get_by_text("From your CV only").count(), 0)
        with closing(db.connect()) as con:
            self.assertEqual(sources.listing(con, "project")[0]["files"], 1)

    def test_what_changed_shows_on_the_project_and_its_card_and_the_last_read_can_be_put_back(self) -> None:
        settings.set("you.name", repos.AVERY[0])
        settings.set("you.email", repos.AVERY[1])
        folder = repos.make(self.files / "tide", {"README.md": "# Tide\n", "requirements.txt": "flask\n"})
        with closing(db.connect()) as con:
            source_id = pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0]
            pipeline.read(con, source_id)
            repos.write(folder, {"requirements.txt": "flask\ndjango\n"})
            repos.git(folder, "add", "-A", date="2024-09-01T10:00:00Z")
            repos.git(folder, "commit", "-q", "-m", "more", date="2024-09-01T10:00:00Z")
            pipeline.read(con, source_id)
        p = self.page("record-projects")
        row = self.panel(p, "Add projects").locator("li.rec-source", has_text="tide")
        p.shows(row.get_by_text("Tools added: Django."), "what changed on the source")
        card = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text="tide"))
        p.shows(card.get_by_text("Tools added: Django."), "what changed on the card")
        self.clean(p)
        row.get_by_role("button", name="Go back to the last read: tide").click()
        p.shows(p.page.get_by_text("Hal put back what the last read found."), "the toast")

    def test_a_code_project_is_split_into_projects_by_folder(self) -> None:
        settings.set("you.name", repos.AVERY[0])
        settings.set("you.email", repos.AVERY[1])
        folder = repos.make(self.files / "suite", {"README.md": "# Suite\n", "package.json": '{"name": "suite"}',
                                                   "web/package.json": '{"name": "web"}', "web/app.js": "1\n",
                                                   "api/package.json": '{"name": "api"}', "api/index.js": "1\n"})
        with closing(db.connect()) as con:
            pipeline.read(con, pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0])
        p = self.page("record-projects")
        panel = self.panel(p, "Add projects")
        panel.get_by_role("button", name="Split into projects by folder: suite").click()
        for name in ("web", "api"):
            p.shows(panel.locator("li.rec-source.rec-child", has_text=name), name)
        self.clean(p)


if __name__ == "__main__":
    unittest.main()
