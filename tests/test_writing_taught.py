"""Claims about what a module taught need a source that names the module and what it covered.

The modules, CV lines and passages are made up.
"""
import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import taught

MODULES = ["Foundations of Imaginary Data (IMAG1001)", "Statistics for Seafarers", "Databases"]
CV = "Year 1 modules (overall 68): Foundations of Imaginary Data 70, Statistics for Seafarers 65, Databases 66"
PASSAGE = ("[Sources/Writing/Data reflection.md > Week 3]\nIn Foundations of Imaginary Data I wrote SQL queries "
           "over a made-up harbour database and tagged sailors' logs with language processing.")


class Taught(unittest.TestCase):
    def test_what_a_module_covered_with_no_source_is_unsupported(self):
        # "the Foundations of Data Science module, which covered core statistical methods", while its notes
        # covered databases, SQL and language processing (2026-11-07)
        said = "I took the Foundations of Imaginary Data module, which covered core statistical methods."
        self.assertEqual(taught.unsupported(said, MODULES, (CV, PASSAGE)),
                         [f"{said} (nothing in your facts says what Foundations of Imaginary Data covered)"])

    def test_a_source_naming_the_module_and_what_it_covered_supports_it(self):
        for said in ("The Foundations of Imaginary Data module covered SQL queries.",
                     "IMAG1001 introduced language processing.",
                     "Statistics for Seafarers taught me statistical tests."):
            with self.subTest(said):
                sources = (CV, PASSAGE, "Statistics for Seafarers: statistical tests on made-up tide tables.")
                self.assertEqual(taught.unsupported(said, MODULES, sources), [])

    def test_another_modules_words_never_support_this_one(self):
        said = "The Databases module covered statistics in depth."
        self.assertEqual(len(taught.unsupported(said, MODULES, (CV,))), 1)

    def test_a_module_code_alone_is_a_named_module(self):
        self.assertEqual(len(taught.unsupported("IMAG2042 taught me harbour law.", MODULES, (CV,))), 1)

    def test_a_plain_word_or_a_far_verb_says_nothing_of_a_module(self):
        for said in ("I designed databases, which included tide tables.",
                     "In Foundations of Imaginary Data I met friends, and my volunteering later included first aid.",
                     "The Foundations of Imaginary Data module covered the basics."):
            with self.subTest(said):
                self.assertEqual(taught.unsupported(said, MODULES, (CV,)), [])


if __name__ == "__main__":
    unittest.main()
