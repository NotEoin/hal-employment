"""An education card's coursework, lecture material and official papers in a real browser, against a real server
in a temporary home.

The cards are the made-up graduates' of `support.graduates`, added through the store as a read of their CV
would add them. Needs `HAL_TEST_BROWSER=1` and Playwright. No AI is used, so coursework gives its title, mark,
summary and suggested module from rules alone.
"""
from __future__ import annotations

import contextlib
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from support import graduates
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


def course(key: str) -> int:
    """Adds one of the made-up courses as a read of a CV would; its card's id."""
    with closing(db.connect()) as con:
        return graduates.education(con, key)


@skip_unless_browser
class RecordEducation(unittest.TestCase):
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
        path.write_text(text, encoding="utf-8")
        return str(path)

    def page(self) -> AppPage:
        p = AppPage(self.browser, f"http://127.0.0.1:{self.hal.port}", "wide", "light")
        self.addCleanup(p.close)
        p.missing = []
        p.page.on("response", lambda r: r.status >= 400 and p.missing.append(r.url))
        p.page.goto(self.hal.open_url())
        p.open("record-education")
        return p

    def clean(self, p: AppPage, refused: tuple[str, ...] = ()) -> None:
        """No console errors, no sideways scroll and no serious accessibility findings; `refused` are the paths
        the test expects the server to refuse."""
        ours = [u for u in p.missing if not any(n in u for n in NOT_YET) and not u.endswith(refused)]
        self.assertEqual([e for e in p.errors if ours or not e.startswith(FAILED_LOAD)] + ours, [])
        self.assertEqual(p.sideways(), 0)
        self.assertEqual(p.serious_axe(), [])

    def open_card(self, p: AppPage, heading: str):
        """The education card with this heading, its uploads open."""
        card = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text=heading))
        card.locator("summary", has_text="Coursework, lecture material and official papers").click()
        return card

    def panel(self, card, heading: str):
        """The panel with this heading inside a card."""
        return card.locator("section.rec-panel", has=card.page.get_by_role("heading", name=heading, exact=True))

    def listed(self, section: str) -> list[dict]:
        with closing(db.connect()) as con:
            return sources.listing(con, section)

    def test_coursework_shows_its_mark_and_the_person_confirms_the_module_hal_suggests(self) -> None:
        course("bsc")
        p = self.page()
        work = self.panel(self.open_card(p, "BSc Made-up Computing"), "Coursework")
        work.get_by_label("What is it?").select_option(label="Report or lab report")
        work.locator("input[type=file]").first.set_input_files(self.file("rainfall.md", graduates.REPORT))
        row = work.locator("li.rec-source", has_text="Rainfall and ferry delays on the Fen")
        p.shows(row.get_by_text("Mark: 68"), "the mark")
        p.shows(row.get_by_text("Hal thinks this is for Data Analysis (COMP2040)."), "the suggested module")
        row.get_by_role("button", name="Yes, that is right").click()
        p.shows(p.page.get_by_text("Saved: this is for Data Analysis (COMP2040)."), "the saved note")
        row.get_by_text("Hal thinks this is for").wait_for(state="hidden")
        self.assertEqual(row.get_by_label("Module").input_value(), "Data Analysis (COMP2040)")
        self.assertEqual(self.listed("coursework")[0]["module"], "Data Analysis (COMP2040)")
        self.clean(p)


    def upload(self, panel, name: str, text: str, kind: str) -> None:
        """Chooses what the file is and adds it to the panel."""
        panel.get_by_label("What is it?").select_option(label=kind)
        panel.locator("input[type=file]").first.set_input_files(self.file(name, text))

    def test_a_showcase_essay_is_a_degree_project_and_one_piece_is_the_dissertation(self) -> None:
        course("ba")
        p = self.page()
        work = self.panel(self.open_card(p, "BA History"), "Coursework")
        self.upload(work, "registers.md", graduates.ESSAY, "Essay")
        essay = work.locator("li.rec-source", has_text="The parish registers of Harrowgate Vale")
        p.shows(essay.get_by_text("Mark: 72%"), "the essay's mark")
        self.upload(work, "bread.md", graduates.HISTORY_DISSERTATION, "Essay")
        thesis = work.locator("li.rec-source", has_text="Bread, rivers and riot")
        p.shows(thesis.get_by_text("Read", exact=True), "the dissertation read")
        thesis.get_by_role("button", name="This is my dissertation: Bread, rivers and riot", exact=False).click()
        p.shows(thesis.get_by_role("button", name="My dissertation"), "the dissertation mark")
        essay.get_by_role("button", name="This is my dissertation: The parish registers", exact=False).click()
        p.shows(p.page.get_by_text("is already this course's dissertation", exact=False), "the one-per-course note")
        title = "The parish registers of Harrowgate Vale"
        essay.get_by_role("button", name="Showcase: " + title).click()
        p.shows(essay.get_by_role("button", name="Take out of your showcase: " + title), "the showcase")
        p.open("record-projects")
        project = p.page.locator("article.rec-item", has=p.page.locator("h3", has_text=title))
        p.shows(project.get_by_role("button", name="Take out of your showcase: " + title),
                "the degree project card in the showcase")
        with closing(db.connect()) as con:
            (card,) = store.items(con, "project")
        self.assertEqual((card["context"], card["star"]), ("degree", True))
        listed = self.listed("coursework")
        self.assertEqual([s["dissertation"] for s in listed], [False, True])
        self.clean(p, refused=(f"/api/v2/record/sources/{listed[0]['id']}",))


if __name__ == "__main__":
    unittest.main()
