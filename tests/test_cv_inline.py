"""Bold, italic and links inside a CV line: reading the marks, the plain words, writing them back and
carrying them onto new words. Every line here is made up."""
from __future__ import annotations

import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.cvstudio.inline import Run, has_marks, html, keep_marks, plain, runs, safe_link, to_text

SITE = "https://example.org/avery"


class Pairs(unittest.TestCase):
    def test_a_pair_of_double_stars_is_bold_and_a_pair_of_single_stars_italic(self) -> None:
        self.assertEqual(runs("Led **twelve** volunteers on *Saturday* tours"),
                         [Run("Led "), Run("twelve", bold=True), Run(" volunteers on "),
                          Run("Saturday", italic=True), Run(" tours")])

    def test_three_stars_are_bold_and_italic_together(self) -> None:
        self.assertEqual(runs("***Winner***"), [Run("Winner", bold=True, italic=True)])

    def test_italic_inside_bold_and_bold_inside_italic_nest(self) -> None:
        self.assertEqual(runs("**ran *every* shift**"),
                         [Run("ran ", bold=True), Run("every", bold=True, italic=True), Run(" shift", bold=True)])
        self.assertEqual(runs("*read **all** of it*"),
                         [Run("read ", italic=True), Run("all", bold=True, italic=True), Run(" of it", italic=True)])

    def test_a_mark_next_to_punctuation_still_counts(self) -> None:
        self.assertEqual(plain("(*Hamlet*), **Lear**."), "(Hamlet), Lear.")
        self.assertEqual(runs("*Hamlet*'s ghost")[0], Run("Hamlet", italic=True))

    def test_a_pair_never_spans_a_line_break(self) -> None:
        self.assertEqual(plain("one *star\nand* another"), "one *star\nand* another")


class LoneMarks(unittest.TestCase):
    def test_stars_that_touch_a_letter_or_digit_outside_stay_as_typed(self) -> None:
        for text in ("C*", "A* in Maths and A* in Physics", "A*A*A", "D*D*D*", "5* rating", "2*3*4",
                     "a * b * c", "Rated *****.", "(*) required", "*nix and *BSD", "*Subject to approval",
                     "**unclosed", "** spaced**"):
            with self.subTest(text=text):
                self.assertEqual(runs(text), [Run(text)])
                self.assertEqual(plain(text), text)
                self.assertFalse(has_marks(text))

    def test_underscores_never_mark_anything(self) -> None:
        for text in ("snake_case", "_italic_", "__init__", "my_var_name"):
            with self.subTest(text=text):
                self.assertEqual(plain(text), text)

    def test_an_escaped_star_or_bracket_is_text(self) -> None:
        self.assertEqual(runs(r"\*Note\* and \[1\]"), [Run("*Note* and [1]")])


class Links(unittest.TestCase):
    def test_a_link_gives_its_words_and_keeps_its_address(self) -> None:
        self.assertEqual(runs(f"See [my portfolio]({SITE})."),
                         [Run("See "), Run("my portfolio", link=SITE), Run(".")])
        self.assertEqual(plain(f"See [my portfolio]({SITE})."), "See my portfolio.")

    def test_brackets_and_parentheses_inside_a_link_are_kept(self) -> None:
        url = "https://example.org/wiki/Avery_(writer)"
        self.assertEqual(runs(f"[C++ [advanced]]({url})"), [Run("C++ [advanced]", link=url)])

    def test_only_web_and_email_addresses_make_links(self) -> None:
        self.assertEqual(runs("[mail me](mailto:avery@example.org)"),
                         [Run("mail me", link="mailto:avery@example.org")])
        for text in ("[x](javascript:alert(1))", "[x](example.org)", "[x](ftp://example.org)", "[x]( )",
                     "[](https://example.org)", "[x](https://example.org/a b)"):
            with self.subTest(text=text):
                self.assertEqual(plain(text), text)
        self.assertEqual(safe_link("JavaScript:alert(1)"), "")
        self.assertEqual(safe_link(" HTTPS://example.org "), "HTTPS://example.org")

    def test_bold_words_inside_a_link_and_a_bold_link(self) -> None:
        self.assertEqual(runs(f"**[site]({SITE})**"), [Run("site", bold=True, link=SITE)])
        self.assertEqual(runs(f"[**site**]({SITE})"), [Run("site", bold=True, link=SITE)])


class Writing(unittest.TestCase):
    def test_written_marks_read_back_the_same(self) -> None:
        for text in ("Led **twelve** volunteers", "***Winner***", "**ran *every* shift**", "*read **all** of it*",
                     f"See [my **portfolio**]({SITE}).", "C* and A*A*A", "plain words"):
            with self.subTest(text=text):
                self.assertEqual(to_text(runs(text)), text)

    def test_spaces_at_the_edge_of_a_bold_stretch_move_outside_it(self) -> None:
        self.assertEqual(to_text([Run("Led "), Run("twelve ", bold=True), Run("volunteers")]),
                         "Led **twelve** volunteers")

    def test_a_star_that_would_become_a_mark_is_escaped(self) -> None:
        out = to_text([Run("*Note*")])
        self.assertEqual(out, r"\*Note\*")
        self.assertEqual(runs(out), [Run("*Note*")])

    def test_a_style_change_inside_a_word_is_dropped_not_stored_as_stars(self) -> None:
        self.assertEqual(to_text([Run("Py"), Run("thon", bold=True)]), "Python")


class KeepMarks(unittest.TestCase):
    def test_marks_whose_words_are_still_there_are_kept_and_the_rest_dropped(self) -> None:
        old = f"Built **Python** tools for *Made-up Rail* at [the depot]({SITE})"
        self.assertEqual(keep_marks(old, "Wrote Python tools for the team at the depot"),
                         f"Wrote **Python** tools for the team at [the depot]({SITE})")

    def test_a_word_inside_a_longer_word_is_not_marked(self) -> None:
        self.assertEqual(keep_marks("Taught **Java** classes", "Taught JavaScript classes"),
                         "Taught JavaScript classes")

    def test_new_words_never_carry_a_broken_pair(self) -> None:
        out = keep_marks("**Led** the team", "*Led the team")
        self.assertEqual(runs(out), [Run("*"), Run("Led", bold=True), Run(" the team")])
        self.assertEqual(plain(out), "*Led the team")
        self.assertEqual(runs(keep_marks("plain", "*Note*")), [Run("*Note*")])


class Html(unittest.TestCase):
    def test_words_are_escaped_and_marks_become_tags(self) -> None:
        self.assertEqual(html("**<script>** & *it*"),
                         "<strong>&lt;script&gt;</strong> &amp; <em>it</em>")

    def test_a_link_is_an_anchor_with_its_address_escaped(self) -> None:
        out = html('[site](https://example.org/?a="1"&b=2)')
        self.assertIn('href="https://example.org/?a=&quot;1&quot;&amp;b=2"', out)
        self.assertIn('rel="noopener noreferrer"', out)

    def test_a_script_address_never_becomes_a_link(self) -> None:
        self.assertNotIn("<a ", html("[x](javascript:alert(1))"))


if __name__ == "__main__":
    unittest.main()
