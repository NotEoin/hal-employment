"""UK and US spelling: the other spelling is found with its fix, and right words, names and code are left alone.

The sentences are made up for the tests. The module reads nothing but its arguments (and the setting for
`chosen`), so these tests need no AI and no database.
"""
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.core import settings
from jobhunt.writing import spelling


def fixes(text: str, code: str = spelling.UK) -> list[tuple[str, str]]:
    return [(f.word, f.fix) for f in spelling.find(text, code)]


class AmericanSpellingInUkText(unittest.TestCase):
    def test_ize_and_ization_words_are_found_with_their_ise_form(self):
        self.assertEqual(fixes("Organized a reorganization; realizing it, we prioritized and summarizes."),
                         [("Organized", "Organised"), ("reorganization", "reorganisation"),
                          ("realizing", "realising"), ("prioritized", "prioritised"), ("summarizes", "summarises")])

    def test_words_that_end_in_ize_in_uk_english_too_are_kept(self):
        self.assertEqual(fixes("Size, prize, seize, capsize, resize, downsized, citizen, prized, seized."), [])

    def test_yze_words_are_found(self):
        self.assertEqual(fixes("Analyzed data and paralyzing doubt."),
                         [("Analyzed", "Analysed"), ("paralyzing", "paralysing")])

    def test_or_words_are_found_but_their_shared_forms_are_kept(self):
        self.assertEqual(fixes("My favorite color, good behavior and the neighborhood labor; honorable work."),
                         [("favorite", "favourite"), ("color", "colour"), ("behavior", "behaviour"),
                          ("neighborhood", "neighbourhood"), ("labor", "labour"), ("honorable", "honourable")])
        self.assertEqual(fixes("A humorous, laborious, vigorous and honorary humorist."), [])

    def test_er_words_are_found_but_a_meter_is_kept(self):
        self.assertEqual(fixes("The center of the theater, centered on fiber, ten kilometers."),
                         [("center", "centre"), ("theater", "theatre"), ("centered", "centred"),
                          ("fiber", "fibre"), ("kilometers", "kilometres")])
        self.assertEqual(fixes("Read the gas meter and set the parameter and diameter."), [])

    def test_ense_words_are_found_and_license_only_as_a_noun(self):
        self.assertEqual(fixes("Defense and offense. A full driving license."),
                         [("Defense", "Defence"), ("offense", "offence"), ("license", "licence")])
        self.assertEqual(fixes("We license the software and licensed it."), [])

    def test_doubled_l_words_are_found(self):
        self.assertEqual(fixes("Traveled, modeling, labeled, canceled, counselor, fulfill, enrollment, jewelry."),
                         [("Traveled", "Travelled"), ("modeling", "modelling"), ("labeled", "labelled"),
                          ("canceled", "cancelled"), ("counselor", "counsellor"), ("fulfill", "fulfil"),
                          ("enrollment", "enrolment"), ("jewelry", "jewellery")])

    def test_single_words_are_found(self):
        self.assertEqual(fixes("Gray mold, skeptical of aluminum, a catalog and a math tutor."),
                         [("Gray", "Grey"), ("mold", "mould"), ("skeptical", "sceptical"),
                          ("aluminum", "aluminium"), ("catalog", "catalogue"), ("math", "maths")])

    def test_dialog_is_kept_when_it_names_part_of_a_screen(self):
        self.assertEqual(fixes("Built the dialog box. A dialog with users."), [("dialog", "dialogue")])

    def test_words_right_in_uk_english_are_never_flagged(self):
        sentence = ("I wrote a computer program, formatted the disk, built an analog circuit, checked the "
                    "practice, read the meter and advised on exercise and expertise.")
        self.assertEqual(fixes(sentence), [])

    def test_names_code_quotes_and_addresses_are_left_alone(self):
        sentence = ('Worked at the Kennedy Center with NASA. Set `color_map`, colorPicker, style.color and '
                    'color_scheme. He said "color me impressed". See https://example.org/colors or '
                    'color@example.org.')
        self.assertEqual(fixes(sentence), [])

    def test_a_capitalised_word_at_the_start_of_a_sentence_or_bullet_is_found(self):
        self.assertEqual(fixes("Done. Organized it.\n- Color-coded the files\n1. Analyzed it"),
                         [("Organized", "Organised"), ("Color", "Colour"), ("Analyzed", "Analysed")])


class BritishSpellingInUsText(unittest.TestCase):
    def test_uk_forms_are_found_with_their_us_fix(self):
        self.assertEqual(fixes("Organised the colour catalogue, travelled to the centre; the programme.",
                               spelling.US),
                         [("Organised", "Organized"), ("colour", "color"), ("catalogue", "catalog"),
                          ("travelled", "traveled"), ("centre", "center"), ("programme", "program")])

    def test_ise_words_us_english_shares_are_kept(self):
        self.assertEqual(fixes("Advise on exercise, expertise and the enterprise; the analyses and emphasis.",
                               spelling.US), [])

    def test_cancellation_is_us_english_too(self):
        self.assertEqual(fixes("A cancellation.", spelling.US), [])


class Respelling(unittest.TestCase):
    def test_respell_changes_only_the_other_spelling_and_keeps_capitals(self):
        self.assertEqual(spelling.respell("Optimized the Kennedy Center's color program.", spelling.UK),
                         "Optimised the Kennedy Center's colour program.")
        self.assertEqual(spelling.respell("Organised the colour scheme.", spelling.US),
                         "Organized the color scheme.")

    def test_an_unset_or_unknown_spelling_is_uk_english(self):
        for value in ("", None, "fr", "EN_gb"):
            self.assertEqual(spelling.code_of(value), spelling.UK)
        self.assertEqual(spelling.code_of("en_US"), spelling.US)

    def test_chosen_follows_the_setting(self):
        with mock.patch.object(settings, "get", {"cvdocs.spelling": "en-US"}.get):
            self.assertEqual(spelling.chosen(), spelling.US)
        with mock.patch.object(settings, "get", {"cvdocs.spelling": ""}.get):
            self.assertEqual(spelling.chosen(), spelling.UK)


if __name__ == "__main__":
    unittest.main()
