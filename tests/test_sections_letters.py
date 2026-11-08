"""The letter reader: cover letters and personal statements are kept as samples of the person's writing voice,
never as facts, and `voice_samples` gives the newest few.

The two made-up graduates of `support.graduates` (Avery Quillon, software; Marnie Quillfeather, history) and
every name, place and file are invented.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.graduates import AVERY, GRADUATES, MARNIE
from test_ingest import home

from jobhunt.profile import store as profile_store
from jobhunt.sections import letters, pipeline, store
from jobhunt.sections.pipeline import Incoming

PRIVATE = "# Letter to my GP\n\nPlease renew my repeat prescription. Diagnosed with asthma; NHS number 943 476 5919.\n"


def add_letter(con, name: str, text: str, kind: str = "cover_letter") -> int:
    """`text` added to letters as the file `name` and read; its source's id."""
    source_id = pipeline.add_uploads(con, "letter", [Incoming(name, text.encode())], doc_kind=kind)["added"][0]
    pipeline.read(con, source_id)
    return source_id


class Letters(unittest.TestCase):
    def test_a_letter_is_read_and_kept_but_creates_no_facts(self):
        for person in GRADUATES:
            with self.subTest(person=person.name), home() as (con, vault, _):
                source_id = pipeline.add_uploads(con, "letter", [Incoming("letter.md", person.letter.encode())])
                out = pipeline.read(con, source_id["added"][0])
                got = store.get(con, source_id["added"][0])
                facts = con.execute("SELECT COUNT(*) FROM profile_evidence").fetchone()[0]
                cards = {k: profile_store.items(con, k) for k in profile_store.KINDS}
                note = (vault / got["note_rel"]).is_file()
            self.assertEqual((out["state"], out["facts"], facts), ("read", 0, 0))
            self.assertEqual({k: v for k, v in cards.items() if v}, {})
            self.assertTrue(note)

    def test_a_letter_keeps_its_title_and_date(self):
        with home() as (con, _, _):
            got = store.get(con, add_letter(con, "statement.md", MARNIE.letter, "statement"))
        self.assertEqual((got["title"], got["dated"], got["doc_kind"]), ("Personal statement", "2025-03", "statement"))

    def test_voice_samples_are_the_newest_letters_words_newest_first(self):
        with home() as (con, _, _):
            add_letter(con, "avery.md", AVERY.letter)
            add_letter(con, "statement.md", MARNIE.letter, "statement")
            both = letters.voice_samples(con, 5)
            one = letters.voice_samples(con, 1)
            none = letters.voice_samples(con, 0)
        self.assertEqual(len(both), 2)
        self.assertIn("read the hands of the 1840s", both[0], "March 2025 is newer than February 2025")
        self.assertIn("turning messy readings", both[1])
        self.assertEqual((one, none), ([both[0]], []))

    def test_a_private_letter_or_one_not_read_is_never_a_sample(self):
        with home() as (con, _, _):
            kept_out = add_letter(con, "gp.md", PRIVATE)
            pipeline.add_uploads(con, "letter", [Incoming("waiting.md", AVERY.letter.encode())])
            samples = letters.voice_samples(con, 5)
            state = store.get(con, kept_out)["state"]
        self.assertEqual((state, samples), ("kept_out", []))


class Excerpts(unittest.TestCase):
    def test_an_excerpt_is_the_letters_own_prose_cut_at_a_sentence_within_150_words(self):
        said = letters.excerpt(AVERY.letter)
        self.assertTrue(said.startswith("I would like to join Made-up Harbour Software as a graduate developer."))
        for left_out in ("# Letter", "12 February 2025", "Dear hiring team", "Yours sincerely", "Avery Quillon"):
            self.assertNotIn(left_out, said)
        long = "# Statement\n\n" + " ".join(f"Sentence {k} says a little more about the archive." for k in range(60))
        cut = letters.excerpt(long)
        self.assertLessEqual(len(cut.split()), letters.EXCERPT_WORDS)
        self.assertTrue(cut.endswith("archive."), "cut at the end of a sentence")

    def test_voice_excerpts_are_the_newest_letters_and_none_without_a_letter(self):
        with home() as (con, _, _):
            nothing = letters.voice_excerpts(con)
            add_letter(con, "avery.md", AVERY.letter)
            add_letter(con, "statement.md", MARNIE.letter, "statement")
            got = letters.voice_excerpts(con)
        self.assertEqual(nothing, [])
        self.assertEqual(len(got), letters.EXCERPTS)
        self.assertTrue(got[0].startswith("Old letters fascinate me"), "March 2025 is newer than February 2025")


if __name__ == "__main__":
    unittest.main()
