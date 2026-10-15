"""Tests of the employment record: documents turned into notes, notes indexed and searched.

Each test builds its own vault in a temporary folder, and the embedding model is
replaced by a bag-of-words stand-in, so nothing here needs Ollama or reads the
real record. Hal's retrieval library must be at config.toml's `hal_root`.

Run with: .venv/bin/python -m unittest discover tests
"""
import re
import tempfile
import unittest
import zlib
from pathlib import Path

from jobhunt import record
from jobhunt.llm import HAL_ROOT

DIM = 512  # wide enough that the few words in a test rarely share a slot


def fake_embed(texts: list[str], kind: str) -> list[list[float]]:
    """One slot per word, so passages sharing a query's words score highest."""
    out = []
    for text in texts:
        vec = [0.0] * DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vec[zlib.crc32(word.encode()) % DIM] += 1.0
        out.append(vec)
    return out


@unittest.skipUnless((HAL_ROOT / "hal_rag.py").exists(), "Hal's retrieval library is not installed")
class Record(unittest.TestCase):
    def setUp(self):
        saved = record.VAULT, record._embed
        def restore():
            record.VAULT, record._embed = saved
            record._CACHE.clear()
        self.addCleanup(restore)
        self.vault = Path(tempfile.mkdtemp()) / "Employment-Record"
        self.vault.mkdir()
        record.VAULT, record._embed = self.vault, fake_embed
        record._CACHE.clear()

    def note(self, rel: str, text: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_dropped_document_becomes_a_note_that_keeps_its_headings(self):
        self.note("Awards/prize.html", "<html><body><h1>Prizes</h1><h2>Dean's list</h2>"
                                       "<p>Named on the School of Computing dean's list for 2025.</p></body></html>")
        out = record.sync()
        self.assertEqual(out["converted"], ["Awards/prize (HTML).md"])
        text = (self.vault / "Awards/prize (HTML).md").read_text()
        self.assertIn('source: "[[prize.html]]"', text)
        self.assertIn("## Dean's list", text)
        hit = record.search("dean's list 2025", min_score=0)[0]
        self.assertEqual((hit["path"], hit["heading"]), ("Awards/prize (HTML).md", "Dean's list"))
        # A second sync leaves the note alone, since the document has not changed.
        self.assertEqual(record.sync()["converted"], [])

    def test_each_heading_keeps_only_its_own_text(self):
        """One year's module marks never appear in a passage filed under another year's heading."""
        self.note("Education/BSc.md", "# BSc\n\n## Second year\n\n" + "Databases 70, Networks 68. " * 20
                  + "\n\n## Final year\n\nCryptography 80, Compilers 75, Distributed systems 72.\n")
        record.sync()
        hits = record.search("cryptography mark", k=5, min_score=0)
        self.assertEqual(hits[0]["heading"], "Final year")
        self.assertNotIn("Databases", hits[0]["text"])
        self.assertFalse([h for h in hits if h["heading"] == "Second year" and "Cryptography" in h["text"]])

    def test_a_changed_note_is_read_again_and_a_deleted_one_dropped(self):
        self.note("Notes/a.md", "# A\n\nWorked the late bar shift at the Old George Inn on weekends.\n")
        gone = self.note("Notes/b.md", "# B\n\nCaptained the university chess team for two seasons.\n")
        self.assertEqual(record.sync()["changed"], 2)
        self.note("Notes/a.md", "# A\n\nTrained three new bar staff at the Old George Inn.\n")
        gone.unlink()
        out = record.sync()
        self.assertEqual((out["changed"], out["removed"], out["notes"]), (1, 1, 1))
        self.assertIn("Trained three", record.search("bar staff", min_score=0)[0]["text"])
        self.assertFalse(record.search("chess team", min_score=0.5))

    def test_an_index_note_is_not_searched(self):
        self.note("Start Here.md", "---\ntype: index\n---\n# Start here\n\nDrop certificates into Certificates.\n")
        self.note("Certificates/CITI.md", "# CITI\n\nHuman-subjects research training, completed 5 March 2026.\n")
        record.sync()
        self.assertEqual({h["path"] for h in record.search("certificates", k=5, min_score=0)}, {"Certificates/CITI.md"})

    def test_context_names_each_passage_and_is_empty_when_the_record_cannot_be_read(self):
        self.note("Certificates/CITI.md", "# CITI\n\nHuman-subjects research training, completed 5 March 2026.\n")
        record.sync()
        found = record.context(["human-subjects research training completed"])
        self.assertTrue(found.startswith("[Certificates/CITI.md > CITI]\nHuman-subjects"))
        def down(texts, kind):
            raise record.RecordUnavailable("the embedding model did not answer")
        record._embed = down
        self.assertEqual(record.context(["research training"]), "")

    def test_a_missing_vault_is_reported_not_created(self):
        record.VAULT = self.vault.parent / "nowhere"
        with self.assertRaisesRegex(record.RecordUnavailable, "no employment record"):
            record.sync()
        self.assertEqual((record.status()["exists"], record.status()["notes"]), (False, 0))
        self.assertFalse(record.VAULT.exists())


if __name__ == "__main__":
    unittest.main()
