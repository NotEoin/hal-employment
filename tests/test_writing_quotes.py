"""Quotes a paragraph may rest on: sentences about the work, those with a result or a number first.

A real card's quotes held a table row, a credit line and a rating line, while its own repository's test suite
was missing (2026-11-08). The lines below are made up in the same shapes.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401 - sets a temporary home before jobhunt is imported

from jobhunt.writing import quotes


class AboutWork(unittest.TestCase):
    def test_table_rows_links_credits_ratings_and_headings_are_not_about_the_work(self):
        for line in ("ferry-sniffer | Yes | https://example.org/avery/ferry-sniffer",
                     "https://example.org/avery/ferry-sniffer",
                     "Built by [@avery](https://example.org/avery)",
                     "Tide gauge - merit 5/5 * Lua, SQLite*",
                     "## How it works", "Features:", "Lua, SQLite"):
            self.assertFalse(quotes.about_work(line), line)

    def test_a_sentence_about_the_work_is_kept_short_or_long(self):
        for line in ("A 113-test suite checks every parser rule before release.", "Tide gauge logs river levels",
                     "Quill answers offline"):
            self.assertTrue(quotes.about_work(line), line)

    def test_quotes_with_a_result_or_a_number_come_first(self):
        said = ["It reads phone signals.", "A 113-test suite covers it.", "It cut start-up time in half."]
        self.assertEqual(quotes.ranked(said), ["A 113-test suite covers it.", "It cut start-up time in half.",
                                               "It reads phone signals."])

    def test_a_readmes_sentences_come_out_without_markup_or_junk(self):
        body = ("# Ferry sniffer\n\n| Tool | Used |\n|---|---|\n| Lua | Yes |\n\n"
                "Ferry sniffer listens for probe requests from nearby phones. It fingerprints each device by its "
                "**information elements**.\n\n- A 113-test suite runs on every push.\n\nBuilt by [@avery](x)\n")
        self.assertEqual(quotes.sentences(body), [
            "Ferry sniffer listens for probe requests from nearby phones.",
            "It fingerprints each device by its information elements.",
            "A 113-test suite runs on every push."])


if __name__ == "__main__":
    unittest.main()
