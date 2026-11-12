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

    def test_commands_dependencies_stats_docs_and_cut_fragments_are_not_about_the_work(self):
        # each is shaped like a line a real card's quotes held (2026-11-08); the tools here are made up
        for line in ("quill tune --auto --write # pin the models and save the settings",
                     "quill serve -p 8080",
                     "$ make install",
                     "Depends on: ferrylib>=25.5, tidepdf>=4.30, gaugekit==1.2",
                     "It needs ferrylib>=25.5 and tidepdf>=4.30 to run.",
                     "Languages: Python (16 files), Markdown (2 files)",
                     "Python (16 files), Markdown (2 files), Lua (3 files)",
                     "tool that goes online, and every tool call passes the same policy.",
                     "The terminal UI tour covers every key and menu.",
                     "The repository contains the source code, tests, setup scripts, and media files",
                     "See the user guide for every setting and what it does."):
            self.assertFalse(quotes.about_work(line), line)

    def test_sentences_about_what_the_project_does_are_kept(self):
        for line in ("Quill drafts each letter from the facts the person checked.",
                     "The scanner reads document files and keeps their headings.",
                     "Every tool call passes the same policy before it runs.",
                     "iOS builds run on every push to the main branch.",
                     "A 40-test suite checks every answer against its note."):
            self.assertTrue(quotes.about_work(line), line)

    def test_quotes_with_a_result_or_a_number_come_first(self):
        said = ["It reads phone signals.", "A 113-test suite covers it.", "It cut start-up time in half."]
        self.assertEqual(quotes.ranked(said), ["A 113-test suite covers it.", "It cut start-up time in half.",
                                               "It reads phone signals."])

    def test_after_a_result_a_quote_saying_how_comes_first(self):
        said = ["It answers offline.", "It answers questions using a local model.", "It cut costs by half."]
        self.assertEqual(quotes.ranked(said), ["It cut costs by half.", "It answers questions using a local model.",
                                               "It answers offline."])

    def test_a_hard_wrapped_paragraph_gives_whole_sentences_and_no_tail(self):
        # a README wrapped at 80 columns gave "tool that goes online, and every ..." as a quote (2026-11-08)
        body = ("Quill keeps a policy for each\ntool that goes online, and every tool call passes the same\n"
                "policy before it runs. It logs each call it refused\n\n"
                "```bash\nquill tune --auto --write # pin the models\n```\n\n"
                "- A 40-test suite checks every answer\n  against its note.\n")
        self.assertEqual(quotes.sentences(body), [
            "Quill keeps a policy for each tool that goes online, and every tool call passes the same policy "
            "before it runs.",
            "A 40-test suite checks every answer against its note."])

    def test_a_sentence_cut_off_where_the_note_stops_being_read_is_dropped(self):
        head, read = "Quill answers questions over a folder of notes.\n", "Quill keeps every answer it gave"
        rows = "| x | y |\n" * ((quotes.NOTE_CHARS - len(head) - len(read)) // len("| x | y |\n"))
        last = read + " in a local log for the person to read."
        body = head + rows + last
        self.assertIn("Quill keeps every answer it", body[:quotes.NOTE_CHARS], "the cut falls inside the last line")
        self.assertEqual(quotes.sentences(body), ["Quill answers questions over a folder of notes."])

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
