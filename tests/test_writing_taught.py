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


CARDS = [{"kind": "project (university team project)", "name": "Ferry planner", "aliases": []}]
LIST = ("Second year modules (overall 73.3): Ferry Studies 86, River Trade 79, Software Engineering Team Project 73",)


class Joined(unittest.TestCase):
    """"I achieved 86 in <module>, where I contributed to a team project" from a CV line of every module and its
    mark, where "Team Project" was another module's name (2026-11-08)."""

    def test_a_module_and_its_mark_tied_to_work_its_own_entry_never_names_is_found(self):
        for said, tied in (("I achieved 86 in Ferry Studies, where I contributed to a team project.", "a team project"),
                           ("I scored 86 in Ferry Studies by building Ferry planner.", "Ferry planner")):
            with self.subTest(said=said):
                self.assertEqual(taught.joined(said, [], LIST, CARDS),
                                 [f"{said} (nothing in your facts ties Ferry Studies to {tied})"])

    def test_a_module_and_its_mark_alone_or_tied_as_its_own_entry_says_are_left_alone(self):
        self.assertEqual(taught.joined("I scored 86 in Ferry Studies.", [], LIST, CARDS), [])
        self.assertEqual(taught.joined("I scored 73 in Software Engineering Team Project, a team project.", [], LIST,
                                       CARDS), [])
        lines = ("Databases module, mark 66: a team project on a made-up harbour database.",)
        self.assertEqual(taught.joined("I achieved 66 in the Databases module through a team project.", MODULES,
                                       lines, CARDS), [])

    def test_the_modules_of_a_list_are_named_modules(self):
        self.assertEqual(taught.modules_listed(LIST),
                         ["Ferry Studies", "River Trade", "Software Engineering Team Project"])


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


class Tooled(unittest.TestCase):
    """"Algorithm Design & Analysis gave me the specific C++ knowledge" of a module that never names C++
    (2026-11-08)."""

    KNOWN = ("SQL", "C++", "Python")

    def test_a_module_named_with_a_tool_its_own_entry_never_names_is_found(self):
        said = "Statistics for Seafarers gave me the specific C++ knowledge this role needs."
        self.assertEqual(taught.tooled(said, MODULES, (CV, PASSAGE), list(self.KNOWN)),
                         [f"{said} (nothing in your facts ties Statistics for Seafarers to C++)"])

    def test_a_tool_the_modules_own_entry_names_is_left_alone(self):
        for said in ("In Foundations of Imaginary Data I wrote SQL queries over a harbour database.",
                     "I learnt C++ on my own, and Python at work."):
            with self.subTest(said):
                self.assertEqual(taught.tooled(said, MODULES, (CV, PASSAGE), list(self.KNOWN)), [])


if __name__ == "__main__":
    unittest.main()
