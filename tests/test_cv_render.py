"""CV pages as HTML: the type sets order, titles and limits, looks differ by CSS, the profile gives
the name, and the preview route serves the very page the printer prints.

The three CVs here belong to the made-up people of `support/people.py`; `made_up_cv` is shared with the
printer, export and look tests.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import people

from jobhunt.cvstudio import printer, render, types
from jobhunt.cvstudio.model import Bullet, CVDoc, Entry, Module, Section

ACCENTED = "Zo\u00eb \u0141ukasiewicz-M\u00fcller"   # a made-up name with Latin-1 and Latin Extended letters


def _bullets(prefix: str, lines: list[str]) -> list[Bullet]:
    return [Bullet(f"{prefix}-b{i}", text) for i, text in enumerate(lines, 1)]


def made_up_cv(key: str) -> CVDoc:
    """A full one-to-two page CV for one made-up person of `support/people.py`, by its type key."""
    if key == "humanities_grad":
        return CVDoc(headline="History graduate for museum and archive roles", sections=[
            Section("summary", "summary", "Profile", text=(
                "History graduate who catalogued 400 items for a made-up record office and wrote a dissertation "
                "on market towns in the 1850s, marked 72. Looking for museum and archive roles.")),
            Section("experience", "experience", "Experience", entries=[
                Entry("ex-1", heading="Collections volunteer", org="Made-up Record Office", location="Harrowgate Vale",
                      start="Sep 2023", end="Present", bullets=_bullets("ex-1", [
                          "Catalogued 400 items from a parish collection using the office's database.",
                          "Gave 15 tours to school groups and wrote two short visitor guides.",
                          "Answered 60 enquiries from the public by email, using the catalogue."])),
                Entry("ex-2", heading="Bookseller (part time)", org="Made-up Books", start="Jun 2022", end="Aug 2023",
                      bullets=_bullets("ex-2", ["Ran the history shelves and two author evenings.",
                                                "Handled about 120 sales each Saturday."])),
                Entry("ex-3", heading="Student ambassador", org="University of Nowhere Fen", start="2022",
                      end="2024", bullets=_bullets("ex-3", ["Led 20 campus tours for visiting families."])),
                Entry("ex-4", heading="Cafe assistant", org="Made-up Cafe", start="2021", end="2022",
                      bullets=_bullets("ex-4", ["Served customers."])),
                Entry("ex-5", heading="Paper round", org="Made-up Newsagent", start="2018", end="2020")]),
            Section("education", "education", "Education", entries=[
                Entry("ed-1", heading="BA (Hons) History and Imaginary Studies", grade="2:1",
                      org="University of Nowhere Fen", start="Sep 2021", end="Jun 2024",
                      bullets=_bullets("ed-1", ["Dissertation: market towns in the 1850s, marked 72."]),
                      modules=[Module("Archives and their users", "74"), Module("Early modern towns", "70"),
                               Module("Historiography", "58")])]),
            Section("skills", "skills", "Skills", entries=[
                Entry("sk-1", heading="Research", items=["Archival research", "Cataloguing", "Oral history"]),
                Entry("sk-2", heading="Tools", items=["Excel", "NVivo", "Collections databases"])]),
            Section("interests", "interests", "Interests", text="Local history walks and choir singing."),
        ])
    if key == "technical_grad":
        return CVDoc(headline="Software graduate", sections=[
            Section("summary", "summary", "Profile", text=(
                "Software graduate who built a timetable reader with 120 tests and cut its parse time from "
                "9 s to 2 s.")),
            Section("education", "education", "Education", entries=[
                Entry("ed-1", heading="BSc (Hons) Software Studies", grade="First",
                      org="Made-up Institute of Technology", start="2022", end="2026",
                      modules=[Module("Compilers", "81"), Module("Databases", "74"), Module("Networks", "66")])]),
            Section("projects", "projects", "Projects", entries=[
                Entry("pr-1", heading="Timetable reader", meta="Python, SQLite", bullets=_bullets("pr-1", [
                    "Built a parser for 40 timetable layouts, with 120 tests.",
                    "Cut parse time from 9 s to 2 s by caching layouts."])),
                Entry("pr-2", heading="Allotment planner", meta="TypeScript", bullets=_bullets("pr-2", [
                    "Made a planner used by 30 plot holders at a made-up allotment society."]))],
                also="a chess clock app and a weather logger"),
            Section("experience", "experience", "Experience", entries=[
                Entry("ex-1", heading="Tutor", org="Made-up Learning", start="Sep 2023", end="Jun 2025",
                      bullets=_bullets("ex-1", ["Taught 12 pupils Python each week; 3 reached a top grade."]))]),
            Section("skills", "skills", "Skills", entries=[
                Entry("sk-1", heading="Languages", items=["Python", "SQL", "TypeScript"]),
                Entry("sk-2", heading="Tools", items=["Git", "Docker", "Linux"])]),
        ])
    return CVDoc(headline="Retail manager moving into project coordination", sections=[
        Section("summary", "summary", "Profile", text=(
            "Store manager who planned three shop refits on time and on budget, now moving into project "
            "coordination.")),
        Section("experience", "experience", "Experience", entries=[
            Entry("ex-1", heading="Store manager", org="Made-up Grocers", start="2019", end="Present",
                  bullets=_bullets("ex-1", ["Planned three refits, each finished within its two-week window.",
                                            "Managed a team of 14 and a weekly rota.",
                                            "Cut waste by 18% in a year with a new ordering routine."])),
            Entry("ex-2", heading="Shift leader", org="Made-up Grocers", start="2015", end="2019",
                  bullets=_bullets("ex-2", ["Trained 20 new starters on the tills and stock system."]))]),
        Section("education", "education", "Education", entries=[
            Entry("ed-1", heading="A levels", grade="B, B, C", org="Made-up Sixth Form College", start="2013",
                  end="2015")]),
        Section("skills", "skills", "Skills", entries=[
            Entry("sk-1", heading="Planning", items=["Rotas", "Budgets", "Supplier contacts"])]),
    ])


def profile_of(person: people.Person, name: str | None = None) -> dict:
    """A render profile from a made-up person's `you.*` values."""
    you = person.you
    return {"name": name or you["name"], "email": you["email"], "phone": you["phone"], "location": you["city"],
            "links": {}}


def type_for(key: str):
    """The made-up person's built-in CV type (every key in `support.people` is one)."""
    return types.load(key)


def text_of(page: str) -> str:
    """The words of a page, without tags or the style block."""
    body = page.split("<main", 1)[1]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


class CvPages(unittest.TestCase):
    def setUp(self) -> None:
        self.person = people.HUMANITIES
        self.doc = made_up_cv(self.person.key)
        self.type = types.load("humanities_grad")
        self.profile = profile_of(self.person)

    def page(self, look: str = "classic", **kw) -> str:
        return render.render_cv_html(self.doc, self.type, look, self.profile, **kw)

    def test_the_type_sets_the_order_titles_and_limits(self):
        page = self.page()
        titles = re.findall(r"<h2>([^<]+)</h2>", page)
        self.assertEqual(titles, ["Profile", "Education", "Work experience", "Skills", "Interests"])
        self.assertEqual(page.count('class="cv-entry"'), 1 + 4)     # experience is cut to the type's 4
        self.assertNotIn("Paper round", page)

    def test_two_types_order_the_same_version_differently_without_changing_it(self):
        before = repr(self.doc)
        technical = render.render_cv_html(self.doc, types.load("technical_grad"), "classic", self.profile)
        humanities = self.page()
        self.assertEqual(repr(self.doc), before)
        self.assertNotEqual(re.findall(r"<h2>([^<]+)</h2>", technical), re.findall(r"<h2>([^<]+)</h2>", humanities))

    def test_strong_modules_follow_the_types_lowest_mark(self):
        page = self.page()
        self.assertIn("Archives and their users 74; Early modern towns 70", page)
        self.assertNotIn("Historiography", page)

    def test_the_name_and_contact_line_come_from_the_profile(self):
        page = self.page(look="modern")
        self.assertIn('<h1 class="cv-name">Marnie Quillfeather</h1>', page)
        self.assertIn("marnie.quillfeather@example.org", page)
        self.assertIn("07700 900461", page)

    def test_highlights_mark_only_the_named_parts_and_never_print(self):
        page = self.page(highlight=["s:experience/e:ex-1/b:ex-1-b2"])
        self.assertEqual(page.count('<mark class="hl">'), 1)
        self.assertIn('<mark class="hl">Gave 15 tours', page)
        printed = self.page(highlight=["ex-1-b2"], for_print=True)
        self.assertNotIn("<mark", printed)
        self.assertEqual(printed, self.page())

    def test_every_value_is_escaped(self):
        self.doc.sections[0].text = '<script>alert("x")</script> & more'
        page = self.page()
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_looks_differ_only_by_style_in_one_column(self):
        words = {look.id: text_of(self.page(look.id)) for look in render.looks() if look.columns == 1}
        self.assertEqual(len(set(words.values())), 1, words.keys())

    def test_the_two_column_look_puts_the_side_sections_aside_and_says_it_may_confuse(self):
        two = render.look("two")
        self.assertFalse(two.safe)
        page = self.page("two")
        aside = page.split('<aside class="cv-side">', 1)[1].split("</aside>", 1)[0]
        self.assertIn("<h2>Skills</h2>", aside)
        self.assertNotIn("<h2>Education</h2>", aside)

    def test_the_gallery_has_the_four_looks_of_the_mock(self):
        self.assertEqual([(x.id, x.name, x.safe) for x in render.looks()],
                         [("classic", "Classic", True), ("modern", "Modern", True), ("compact", "Compact", True),
                          ("two", "Two columns", False)])

    def test_the_page_links_only_its_own_fonts_and_runs_no_script(self):
        page = self.page()
        self.assertNotIn("<script", page)
        linked = set(re.findall(r'url\("fonts/([^"]+)"\)', page))
        self.assertEqual(linked, {f.name for f in render.font_files()})
        self.assertLess(sum(f.stat().st_size for f in render.font_files()), 300_000)

    def test_a_letter_has_the_same_header_as_the_cv_in_that_look(self):
        letter = render.render_letter_html("Dear team,\n\nA made-up letter.\n\nKind regards,\nMarnie", self.profile,
                                           ["Made-up Museum"], "classic", when="22 October 2026",
                                           subject="Museum assistant", headline=self.doc.headline)
        head = re.search(r'<header class="cv-head">.*?</header>', self.page()).group(0)
        self.assertIn(head, letter)
        self.assertIn("Kind regards,<br>Marnie", letter)


class PreviewRoute(unittest.TestCase):
    def test_the_preview_is_the_html_that_is_printed(self):
        from support.api import HalServer

        from jobhunt.cvstudio import export, store

        person = people.TECHNICAL
        with HalServer() as hal, people.as_person(person):
            version = store.new_version(made_up_cv(person.key), origin="imported", what="Made-up CV", by="baseline")
            client = hal.signed_in()
            reply = client.get(f"/api/v2/cv/versions/{version}/preview.html?look=modern")
            self.assertEqual(reply.status, 200)
            self.assertIn("Content-Security-Policy", reply.text)    # the page's own policy, in a meta tag
            font = client.get(f"/api/v2/cv/versions/{version}/fonts/{render.font_files()[0].name}")
            self.assertEqual((font.status, font.headers["Content-Type"]), (200, "font/woff2"))

            printed: list[str] = []

            def capture(page: Path, pdf: Path) -> None:
                printed.append(page.read_text(encoding="utf-8"))
                self.assertTrue((page.parent / "fonts" / render.font_files()[0].name).exists())
                raise printer.PrintError("captured")

            row = store.doc()
            with mock.patch.object(printer, "_has_playwright", lambda: True), \
                    mock.patch.object(printer, "_print_playwright", capture):
                export.cv_pdf(store.get(version), type_for(row["type_id"]), "modern", render.profile_from_settings(),
                              Path(hal.home) / "cv.pdf")
            self.assertEqual(printed, [reply.text])


if __name__ == "__main__":
    unittest.main()
