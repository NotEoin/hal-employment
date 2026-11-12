"""The person's voice from their own writing: the voice card measures it, the exemplars show it, and both
reach the letter prompt only while "Match my own writing" is on.

The samples are made up. The record is a temporary folder of made-up notes; nothing asks an AI.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import people
from test_pack import as_applicant

from jobhunt import db, pack, record
from jobhunt.core import settings
from jobhunt.writing import edits, voice

LETTER = ("During my placement at a made-up county records office I sorted parish papers by date. I did not "
          "rush the work. It took six weeks, and the index I wrote is still used by visitors. During the "
          "summer I also guided walks for a made-up heritage trust. I wrote the route notes myself and I "
          "organised the volunteers' rota. I am glad of what I learned there, and I would bring the same care "
          "to your collections. The organised habits stayed with me.")
ESSAY = ("This essay asks how a made-up port town recorded its river trade. The answer is in the harbour "
         "books, which were organised by season rather than by year. During the war the books stopped. The "
         "gap matters, because the trade did not stop with them.")


def sample(kind: str, text: str) -> voice.Sample:
    return voice.Sample(kind, text, "made-up")


class Card(unittest.TestCase):
    def test_the_card_measures_sentence_length_contractions_spelling_and_openings(self):
        found = voice.card([sample("letter", LETTER), sample("writing", ESSAY)])
        self.assertTrue(8 <= found["sentence_words"] <= 16, found)
        self.assertFalse(found["contractions"])
        self.assertEqual(found["spelling"], "British")
        self.assertIn("During", found["openers"])
        self.assertIn("organised", found["favourites"])
        lines = voice.card_lines(found)
        self.assertIn("No contractions: writes I am, it is, do not.", lines)
        self.assertIn("British spelling.", lines)

    def test_contractions_are_seen_when_the_person_uses_them(self):
        casual = " ".join(["I'm glad it's done and I didn't rush it, so it's still used today."] * 12)
        self.assertTrue(voice.card([sample("answer", casual)])["contractions"])

    def test_too_little_writing_makes_no_card(self):
        self.assertEqual(voice.card([sample("letter", "A short made-up line.")]), {})
        self.assertEqual(voice.card_lines({}), [])

    def test_exemplars_are_two_or_three_of_the_closest_kind_cut_to_the_budget(self):
        long_answer = " ".join(["word"] * 300)
        found = [sample("cv", "Catalogued 400 made-up letters"), sample("answer", long_answer),
                 sample("letter", LETTER), sample("letter", ESSAY), sample("writing", ESSAY),
                 sample("letter", "A third made-up letter paragraph.")]
        chosen = voice.exemplars("letter", found)
        self.assertLessEqual(len(chosen), voice.EXEMPLARS_MAX)
        self.assertTrue(chosen[0].startswith("During my placement"), "a letter comes first for a letter")
        self.assertTrue(all(len(c.split()) <= voice.EXEMPLAR_WORDS + 1 for c in chosen))
        self.assertLessEqual(sum(len(c) for c in chosen), voice.BUDGET_CHARS)
        self.assertNotIn("Catalogued 400 made-up letters", chosen, "a CV line is not a letter's voice")

    def test_the_persons_own_words_are_theirs_to_keep(self):
        self.assertIn("vibrant", voice.own_words([sample("letter", "A vibrant made-up market town.")]))


class Sources(unittest.TestCase):
    def test_letters_statements_and_cvs_in_the_record_are_read_and_a_private_folder_never(self):
        with tempfile.TemporaryDirectory(prefix="hal-voice-") as tmp, as_applicant(people.HUMANITIES):
            root = Path(tmp)
            (root / "Letters").mkdir()
            (root / "Letters" / "Cover letter for a made-up trust.md").write_text(LETTER, encoding="utf-8")
            (root / "Sources").mkdir()
            (root / "Sources" / "Harbour books.md").write_text(f"---\ndoc_type: essay\n---\n{ESSAY}", encoding="utf-8")
            (root / "Private").mkdir()
            (root / "Private" / "Cover letter draft.md").write_text("A made-up private letter.", encoding="utf-8")
            (root / "Notes on a made-up film.md").write_text("Not writing of the person's own kind.", encoding="utf-8")
            with mock.patch.object(record, "VAULT", root):
                found = voice.record_samples()
        self.assertEqual(sorted((s.kind, s.source) for s in found),
                         [("letter", "Letters/Cover letter for a made-up trust.md"),
                          ("writing", "Sources/Harbour books.md")])
        self.assertNotIn("---", found[0].text + found[1].text, "the frontmatter is not the person's writing")

    def test_the_card_works_with_no_record_at_all(self):
        with as_applicant(people.HUMANITIES) as a:
            self.assertEqual(voice.voice_for(a.con)["lines"], [])

    def test_your_edits_and_rewritten_answers_are_samples_and_a_private_answer_never(self):
        with as_applicant(people.HUMANITIES) as a:
            edits.record(a.con, "letter", "job:1", "Hal's made-up draft.", "My own made-up final.")
            for label, answer in (("Why heritage?", "Old maps made me curious early on."),
                                  ("Do you consider yourself to have a disability?", "A made-up private line.")):
                a.con.execute("INSERT INTO answers2 (question, label, answer, scope, source, updated) "
                              "VALUES (?, ?, ?, 'never', 'you', ?)", (label, label, json.dumps(answer), db.now()))
            a.con.commit()
            texts = [s.text for s in voice.samples(a.con)]
        self.assertEqual(texts[:2], ["My own made-up final.", "Old maps made me curious early on."])
        self.assertNotIn("A made-up private line.", texts)


class Prompt(unittest.TestCase):
    def test_the_letter_prompt_holds_the_card_and_the_exemplars_while_voice_is_on(self):
        with as_applicant(people.HUMANITIES) as a:
            for n in range(3):
                edits.record(a.con, "letter", f"job:{n}", f"Hal's made-up draft {n}.", LETTER)
            on = pack.prompt("letter")
            settings.set("cvdocs.voice", False, by="you")
            off = pack.prompt("letter")
        self.assertIn("How the applicant writes, measured from their own writing", on)
        self.assertIn("No contractions", on)
        self.assertIn("never copy their facts", on)
        self.assertIn("During my placement", on)
        self.assertNotIn("How the applicant writes", off)
        self.assertNotIn("During my placement", off)


if __name__ == "__main__":
    unittest.main()
