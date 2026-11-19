"""A personal look's measures: each one checked, the CSS written from them by code alone, and the markup a
personal look changes (one-line entries, dates in the line, the contact line's separator)."""
from __future__ import annotations

import dataclasses
import re
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import people
from test_cv_render import made_up_cv, profile_of

from jobhunt.cvstudio import measures, render, types
from jobhunt.cvstudio.measures import BadMeasure, Measures


def humanities_view() -> render.View:
    """The made-up humanities graduate's CV as a view."""
    person = people.HUMANITIES
    return render.view(made_up_cv(person.key), types.load(person.key), profile_of(person))


class Checking(unittest.TestCase):
    def test_every_number_out_of_its_range_is_refused(self) -> None:
        for name, spec in measures.FIELDS.items():
            if spec[0] != "num":
                continue
            for value in (spec[1] - 0.5, spec[2] + 0.5):
                with self.subTest(name=name, value=value), self.assertRaises(BadMeasure) as caught:
                    measures.validate({name: value})
                self.assertEqual(caught.exception.name, name)

    def test_a_colour_must_be_six_hex_digits(self) -> None:
        for value in ("red", "#fff", "#FFFFFF", "#123456; } body { display: none", "url(x)", "#12345g", 123):
            with self.subTest(value=value), self.assertRaises(BadMeasure):
                measures.validate({"text_colour": value})
        self.assertEqual(measures.validate({"text_colour": "#0a0b0c"}).text_colour, "#0a0b0c")

    def test_a_choice_must_be_one_hal_knows(self) -> None:
        for name, value in (("font", "Comic Sans"), ("entry_form", "grid"), ("contact_sep", " / "),
                            ("page", "a3"), ("dates", "left")):
            with self.subTest(name=name), self.assertRaises(BadMeasure):
                measures.validate({name: value})

    def test_a_value_of_the_wrong_kind_or_an_unknown_name_is_refused(self) -> None:
        for data in ({"size": "10"}, {"size": True}, {"name_bold": 1}, {"font_url": "x"}, {"size": None}):
            with self.subTest(data=data), self.assertRaises(BadMeasure):
                measures.validate(data)
        with self.assertRaises(BadMeasure):
            measures.validate(["size", 10])

    def test_missing_measures_keep_the_classic_looks_values(self) -> None:
        self.assertEqual(measures.validate({}), Measures())
        self.assertEqual(measures.validate({"size": 9.2}).size, 9.2)

    def test_a_measured_number_is_brought_into_its_range(self) -> None:
        data = measures.clamped({"size": 3.0, "name_size": 90, "margin_left": 2, "font": "sans"})
        self.assertEqual(measures.validate(data), dataclasses.replace(
            Measures(), size=8.0, name_size=36.0, margin_left=5.0))


class Css(unittest.TestCase):
    def test_the_css_says_each_measure_in_its_own_place(self) -> None:
        m = measures.validate({"page": "letter", "margin_top": 12, "margin_right": 14, "margin_bottom": 16,
                               "margin_left": 15, "font": "serif", "heading_font": "sans", "size": 9.2,
                               "line_height": 1.25, "name_size": 18, "heading_caps": False, "heading_rule": False,
                               "text_colour": "#111111", "link_underline": False, "contact_first": True})
        css = measures.css(m)
        self.assertIn("@page { size: letter; margin: 12mm 14mm 16mm 15mm; }", css)
        self.assertIn('body { font-family: "Source Serif 4", Georgia, serif; font-size: 9.2pt; line-height: 1.25; '
                      "color: #111111; }", css)
        self.assertIn('.cv-name { font-family: "Source Sans 3", Arial, sans-serif; font-size: 18pt;', css)
        self.assertIn("text-transform: none;", css)
        self.assertIn("border-bottom: 0;", css)
        self.assertIn("text-decoration: none;", css)
        self.assertIn(".cv-contact { order: 1;", css)
        self.assertIn("width: 215.9mm; min-height: 279.4mm; padding: 12mm 14mm 16mm 15mm;", css)

    def test_the_css_holds_nothing_but_its_own_words_and_the_measures(self) -> None:
        css = measures.css(Measures())
        for word in ("url(", "@import", "expression", "<", "\\"):
            self.assertNotIn(word, css)
        self.assertTrue(css.isascii())

    def test_measures_made_by_hand_with_a_bad_value_never_reach_the_css(self) -> None:
        for bad in (Measures(text_colour="red; } * { display: none"), Measures(size=400.0),
                    Measures(font="x; } body { background: url(http://example.org/x)")):
            with self.subTest(bad=bad), self.assertRaises(BadMeasure):
                measures.css(bad)
            with self.assertRaises(BadMeasure):
                render.personal_look("my-x", "Mine", bad)

    def test_a_letter_takes_the_looks_type_size(self) -> None:
        css = measures.letter_css(measures.validate({"size": 9.4, "line_height": 1.2}))
        self.assertIn(".letter-body { margin-top: 4mm; font-size: 9.4pt; line-height: 1.3; }", css)


class Tighter(unittest.TestCase):
    def test_each_step_takes_a_little_off_and_there_are_two_at_most(self) -> None:
        m = measures.validate({"size": 10.0, "line_height": 1.4, "heading_size": 11.0})
        once = measures.tighter(m, 0)
        self.assertEqual((once.size, once.line_height), (9.7, 1.35))
        self.assertLess(once.heading_size, 11.0)
        twice = measures.tighter(once, 1)
        self.assertEqual((twice.size, twice.line_height), (9.4, 1.3))
        self.assertIsNone(measures.tighter(twice, measures.TIGHTEN_STEPS))

    def test_type_is_never_made_smaller_than_a_readable_size(self) -> None:
        m = measures.validate({"size": 8.6, "line_height": 1.12})
        once = measures.tighter(m, 0)
        self.assertEqual((once.size, once.line_height), (measures.READABLE_PT, measures.LINE_FLOOR))
        self.assertIsNone(measures.tighter(once, 1))


class Markup(unittest.TestCase):
    def test_a_one_line_look_writes_each_entry_as_one_line(self) -> None:
        look = render.personal_look("my-one", "Mine", measures.validate({"entry_form": "line", "dates": "inline"}))
        page = render.html_of(humanities_view(), look)
        self.assertIn('<div class="cv-line"><h3>Collections volunteer</h3>, <span class="cv-org">Made-up Record '
                      'Office, Harrowgate Vale</span>, <span class="cv-dates">Sep 2023 - Present</span></div><ul>',
                      page)
        self.assertNotIn('class="cv-top', page)

    def test_a_one_line_entry_keeps_its_grade_plain_after_a_colon_with_no_doubled_marks(self) -> None:
        # the grade was drawn bold with the heading and kept its full stop before a comma:
        # "**Diploma: Computing, Distinction, Merit in 15.**, College, 2022 - 2023" (trial, 2026-11-19)
        from jobhunt.cvstudio.model import CVDoc, Entry, Section

        look = render.personal_look("my-one", "Mine", measures.validate({"entry_form": "line", "dates": "inline"}))
        doc = CVDoc(sections=[Section("education", "education", "Education", entries=[
            Entry("ed-1", heading="Access Diploma: Computing", grade="Distinction in 30 units, Merit in 15.",
                  org="Made-up College.", start="2022", end="2023"),
            Entry("ed-2", heading="Made-up Certificate:", grade="Pass", org="Made-up School", start="2019",
                  text=": with a made-up project.", style="paragraph")])])
        page = render.html_of(render.view(doc, None, {"name": "Avery Quillon"}), look)
        self.assertIn('<div class="cv-line"><h3>Access Diploma: Computing</h3>, <span class="cv-org">Made-up '
                      'College</span>, <span class="cv-dates">2022 - 2023</span>: Distinction in 30 units, Merit '
                      "in 15.</div>", page)
        self.assertIn('<h3>Made-up Certificate</h3>, <span class="cv-org">Made-up School</span>, <span '
                      'class="cv-dates">2019</span>: Pass. <span class="cv-text">with a made-up project.</span>', page)

    def test_dates_at_the_right_of_a_one_line_entry_are_written_first(self) -> None:
        look = render.personal_look("my-one", "Mine", measures.validate({"entry_form": "line"}))
        page = render.html_of(humanities_view(), look)
        self.assertIn('<div class="cv-line"><span class="cv-dates cv-right">Sep 2023 - Present</span>'
                      "<h3>Collections volunteer</h3>, ", page)

    def test_a_stacked_entry_can_have_its_dates_in_the_line(self) -> None:
        look = render.personal_look("my-run", "Mine", measures.validate({"dates": "inline"}))
        page = render.html_of(humanities_view(), look)
        self.assertIn('<div class="cv-top cv-run"><h3>Collections volunteer</h3><span class="cv-dates">, '
                      "Sep 2023 - Present</span></div>", page)

    def test_the_contact_line_takes_the_looks_separator(self) -> None:
        look = render.personal_look("my-dot", "Mine", measures.validate({"contact_sep": "comma"}))
        page = render.html_of(humanities_view(), look)
        self.assertIn('<span class="sep">, </span>', page)
        self.assertNotIn('<span class="sep"> | </span>', page)

    def test_the_contact_lines_short_links_keep_the_looks_separator_and_link_style(self) -> None:
        profile = {"name": "Avery Quillon", "email": "avery@example.org",
                   "links": {"github": "https://www.github.com/avery-quillon"}}
        m = measures.validate({"contact_sep": "dot", "link_colour": "#0f5a6b", "link_underline": False})
        page = render.html_of(render.view(made_up_cv(people.HUMANITIES.key), None, profile),
                              render.personal_look("my-dot", "Mine", m))
        self.assertIn('<span class="sep"> \u00b7 </span><span><a href="https://www.github.com/avery-quillon"', page)
        self.assertIn(">github.com/avery-quillon</a>", page)
        self.assertIn(".cv-sec a, .cv-contact a { color: #0f5a6b; text-decoration: none; }", page)

    def test_a_built_in_look_draws_its_page_as_before(self) -> None:
        page = render.html_of(humanities_view(), "classic")
        self.assertIn('<div class="cv-top"><h3>Collections volunteer</h3><span class="cv-dates">Sep 2023 - '
                      'Present</span></div><p class="cv-org">', page)
        self.assertIn('<span class="sep"> | </span>', page)
        self.assertNotIn("personal look", page)

    def test_a_personal_looks_page_keeps_the_page_policy_and_its_own_style(self) -> None:
        look = render.personal_look("my-x", "Mine", measures.validate({"size": 9.2}))
        page = render.html_of(humanities_view(), look)
        self.assertIn(f'content="{render.POLICY}"', page)
        self.assertNotIn("<script", page)
        self.assertIn("font-size: 9.2pt", page)
        self.assertEqual(len(re.findall(r"<style>", page)), 1)
        letter = render.render_letter_html("Dear team,\n\nHello.", profile_of(people.HUMANITIES), ["Made-up Ltd"],
                                           look)
        self.assertIn(".letter-body { margin-top: 4mm; font-size: 9.2pt;", letter)


if __name__ == "__main__":
    unittest.main()
