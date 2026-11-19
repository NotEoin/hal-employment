"""Measuring a CV PDF's layout for "Match my file's layout": each measure from made-up PDFs with a known
layout (`support/layout_pdf.py`), what Hal says it approximated, and, with a browser, Hal's own pages printed
in a known look and measured back."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import layout_pdf, people
from support.layout_pdf import Layout, Row, Run
from support.make_cv_fixtures import scanned_pdf_bytes
from support.ui import browser_tests_on
from test_cv_render import made_up_cv, profile_of, type_for

from jobhunt.cvstudio import match, measures, printer, render

MM = 0.6        # measured margins and spaces are within this many millimetres of the page's own
EM = 0.015      # letter spacing within this share of an em


class OneLineFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        data, cls.lay = layout_pdf.one_line_cv()
        cls.found = match.measure(data)
        cls.m = cls.found.measures

    def test_the_page_size_and_side_margins_are_the_files(self) -> None:
        self.assertEqual(self.m.page, "a4")
        side = self.lay.margin / layout_pdf.PT_PER_MM
        self.assertAlmostEqual(self.m.margin_left, side, delta=MM)
        self.assertAlmostEqual(self.m.margin_right, side, delta=MM, msg="the rules show the right edge")

    def test_a_one_page_file_takes_its_top_margin_for_the_bottom_and_says_so(self) -> None:
        self.assertEqual(self.m.margin_bottom, self.m.margin_top)
        self.assertIn(match.ONE_PAGE_BOTTOM, self.found.notes)
        self.assertEqual(self.found.pages, 1)

    def test_the_body_text_size_line_height_and_fonts(self) -> None:
        self.assertEqual(self.m.size, self.lay.body)
        self.assertAlmostEqual(self.m.line_height, self.lay.pitch / self.lay.body, delta=0.02)
        self.assertEqual((self.m.font, self.m.heading_font), ("serif", "sans"))

    def test_the_name_and_contact_line(self) -> None:
        self.assertEqual((self.m.name_size, self.m.name_bold, self.m.head_align), (18.0, True, "left"))
        self.assertEqual((self.m.contact_size, self.m.contact_sep, self.m.contact_first), (8.5, "bar", True))
        self.assertEqual(self.m.muted_colour, "#333333")

    def test_section_headings_in_capitals_with_spacing_over_a_rule(self) -> None:
        m = self.m
        self.assertEqual((m.heading_size, m.heading_bold, m.heading_caps, m.heading_rule), (10.5, True, True, True))
        self.assertAlmostEqual(m.heading_spacing, self.lay.spacing_em, delta=EM)
        self.assertEqual((m.rule_colour, m.heading_colour), ("#444444", "#111111"))

    def test_entries_written_as_one_line_with_their_dates_in_it(self) -> None:
        self.assertEqual((self.m.entry_form, self.m.dates, self.m.entry_bold), ("line", "inline", True))

    def test_list_items_indent_and_spacing(self) -> None:
        self.assertAlmostEqual(self.m.bullet_indent, self.lay.indent / layout_pdf.PT_PER_MM, delta=MM)
        self.assertAlmostEqual(self.m.bullet_gap, 1.0 / layout_pdf.PT_PER_MM, delta=MM)

    def test_text_and_link_colours_and_links_without_underline(self) -> None:
        self.assertEqual((self.m.text_colour, self.m.link_colour, self.m.link_underline),
                         ("#111111", "#333333", False))

    def test_no_text_from_the_file_reaches_the_measures_or_the_css(self) -> None:
        css = measures.css(self.m)
        for word in ("Avery", "Quillon", "example.org", "Made-up", "PROFILE", "Helvetica", "Times"):
            self.assertNotIn(word, css)
            self.assertNotIn(word, str(self.m.to_dict()))


class OtherLayouts(unittest.TestCase):
    def test_stacked_entries_with_dates_at_the_right(self) -> None:
        m = match.measure(layout_pdf.one_line_cv(stacked=True)[0]).measures
        self.assertEqual((m.entry_form, m.dates), ("stacked", "right"))

    def test_entries_with_and_without_a_list_are_measured_apart(self) -> None:
        cases = {("", False): ("line", "stacked"),           # no listed entry shown: stacked, the safe default
                 ("stacked", False): ("line", "stacked"),    # the mixed file: a degree stacked, jobs on one line
                 ("line", False): ("line", "line"),
                 ("", True): ("stacked", "stacked"),
                 ("line", True): ("stacked", "line")}
        for (listed, stacked), want in cases.items():
            with self.subTest(listed=listed, stacked=stacked):
                m = match.measure(layout_pdf.one_line_cv(listed=listed, stacked=stacked)[0]).measures
                self.assertEqual((m.entry_form, m.bullet_form), want)

    def test_a_stacked_listed_entry_with_dates_in_its_place_line_keeps_dates_inline(self) -> None:
        m = match.measure(layout_pdf.one_line_cv(listed="stacked")[0]).measures
        self.assertEqual(m.dates, "inline")

    def test_headings_without_a_rule_are_found_by_their_capitals(self) -> None:
        m = match.measure(layout_pdf.one_line_cv(rule="")[0]).measures
        self.assertEqual((m.heading_rule, m.heading_caps, m.heading_size), (False, True, 10.5))

    def test_without_a_rule_or_dates_at_the_right_the_sides_are_taken_as_equal(self) -> None:
        m = match.measure(layout_pdf.one_line_cv(rule="")[0]).measures
        self.assertEqual(m.margin_right, m.margin_left)

    def test_a_full_first_page_shows_the_bottom_margin(self) -> None:
        lay = Layout()
        rows = [Row(lay.margin + 18, 18, [Run("Avery Quillon", True, "sans")])]
        rows += [Row(lay.margin + 40 + i * lay.pitch, lay.body, [Run(f"Made-up line {i} of a long page.")])
                 for i in range(64)]
        second = [Row(lay.margin + 10, lay.body, [Run("Made-up last line.")])]
        found = match.measure(layout_pdf.page_pdf([rows, second], lay.margin))
        lowest = layout_pdf.A4[1] - rows[-1].top
        self.assertAlmostEqual(found.measures.margin_bottom, lowest / layout_pdf.PT_PER_MM, delta=1.0)
        self.assertNotIn(match.ONE_PAGE_BOTTOM, found.notes)

    def test_text_side_by_side_is_read_as_one_column_and_said_so(self) -> None:
        lay = Layout()
        rows = [Row(lay.margin + 18, 18, [Run("Avery Quillon", True, "sans")])]
        rows += [Row(lay.margin + 40 + i * lay.pitch, lay.body, [Run("Made-up left column words here")],
                     right=Run("Made-up right column words")) for i in range(12)]
        found = match.measure(layout_pdf.page_pdf([rows], lay.margin))
        self.assertIn(match.TWO_COLUMNS, found.notes)

    def test_a_scan_with_no_text_cannot_be_matched(self) -> None:
        with self.assertRaises(match.CannotMatch) as caught:
            match.measure(scanned_pdf_bytes())
        self.assertIn("no text", str(caught.exception))

    def test_odd_numbers_in_a_file_are_brought_into_range(self) -> None:
        lay = Layout()
        rows = [Row(lay.margin + 60, 60, [Run("Avery Quillon", True, "sans")]),
                Row(lay.margin + 90, 5, [Run("Made-up tiny words that nobody could read in print.")])]
        m = match.measure(layout_pdf.page_pdf([rows], lay.margin)).measures
        self.assertEqual((m.name_size, m.size), (36.0, 8.0))


@unittest.skipUnless(browser_tests_on(), "browser tests need HAL_TEST_BROWSER=1 and Playwright")
class HalsOwnPages(unittest.TestCase):
    """Hal's copy printed in a known personal look and measured back: what "Match my file's layout" sees of
    a file printed by a browser."""

    def measured(self, m: measures.Measures) -> measures.Measures:
        person = people.HUMANITIES
        v = render.view(made_up_cv(person.key), type_for(person.key), profile_of(person))
        out = Path(self.enterContext(tempfile.TemporaryDirectory())) / "cv.pdf"
        printer.to_pdf(render.html_of(v, render.personal_look("my-t", "Test", m)), out, expect_name=v.name,
                       max_pages=0, expect_text=[], why="test")
        return match.measure(out.read_bytes()).measures

    def assert_close(self, want: measures.Measures, got: measures.Measures, exact: tuple[str, ...]) -> None:
        for name in exact:
            self.assertEqual(getattr(got, name), getattr(want, name), name)
        for name in ("margin_left", "margin_top", "heading_below", "bullet_indent"):
            self.assertAlmostEqual(getattr(got, name), getattr(want, name), delta=MM, msg=name)
        self.assertAlmostEqual(got.line_height, want.line_height, delta=0.04)
        self.assertAlmostEqual(got.heading_spacing, want.heading_spacing, delta=EM)

    def test_the_classic_measures_print_and_read_back(self) -> None:
        want = measures.Measures()
        self.assert_close(want, self.measured(want), (
            "page", "font", "heading_font", "size", "name_size", "contact_size", "contact_sep", "heading_size",
            "heading_caps", "heading_rule", "entry_form", "bullet_form", "dates", "text_colour", "muted_colour",
            "rule_colour"))

    def test_a_serif_one_line_look_prints_and_reads_back(self) -> None:
        # the serif body read as bold before 2026-11-19, so no entry read as one line
        want = measures.validate({"font": "serif", "heading_font": "sans", "size": 9.2, "entry_size": 9.2,
                                  "line_height": 1.25, "entry_form": "line", "dates": "inline", "contact_sep": "dot",
                                  "contact_first": True, "heading_spacing": 0.1, "heading_rule": False,
                                  "heading_colour": "#0f5a6b", "margin_left": 12, "margin_top": 11,
                                  "bullet_indent": 6})
        self.assert_close(want, self.measured(want), (
            "font", "heading_font", "size", "entry_form", "bullet_form", "dates", "contact_sep", "contact_first",
            "heading_rule", "heading_colour"))

    def test_a_mixed_look_prints_and_reads_back(self) -> None:
        # entries with a list stacked, the others on one line, as a CV made from Markdown writes them
        want = measures.validate({"entry_form": "line", "bullet_form": "stacked", "dates": "inline",
                                  "size": 9.2, "entry_size": 9.2, "heading_font": "sans"})
        self.assert_close(want, self.measured(want), ("entry_form", "bullet_form", "dates"))


if __name__ == "__main__":
    unittest.main()
