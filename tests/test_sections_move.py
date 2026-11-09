"""Move my record to sections: an older Hal's record (notes under `Sources/` and facts naming them) proposed note by
note, moved only as accepted, with the person's cards kept as they were, and undone in one go.

The older record is made-up Marnie Quillfeather's (history), written as an older Hal wrote one: a note per file
with its frontmatter, and facts whose evidence names the note and no source. Every name and file is invented.
"""
import json
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from support.fake_ai import fake_ai
from test_ingest import home, put
from test_sections_sort import ended

from jobhunt import db, record
from jobhunt.core import paths
from jobhunt.ingest import frontmatter
from jobhunt.profile import store as profile_store
from jobhunt.sections import move, pipeline, sorts

CV = ("# Marnie Quillfeather\n\nCurriculum vitae\n\n## Work experience\n\nVisitor guide, Fenwick Lantern Museum "
      "Trust, 2023 to 2024.\n\n## Education\n\nBA History, University of Nowhere Fen, 2021 to 2024.\n")
ESSAY = ("# The parish registers of Harrowgate Vale\n\nAn essay on baptisms, 1750 to 1850, using archival research.\n"
         + "It reads the made-up registers year by year and weighs what they say against the vestry minutes. " * 12)
NOTES = "# Seminar 3\n\nReading secretary hand: palaeography for the parish registers.\n"
OTHERS = "# Sam Pike\n\nCurriculum vitae of a classmate.\n"


def old_note(vault: Path, rel: str, *, doc_type: str, role: str, category: str, source: str, text: str) -> str:
    """A note as an older Hal wrote one; its path inside the record."""
    fm = {"type": "source", "category": category, "doc_type": doc_type, "title": Path(rel).stem, "role": role,
          "source": source, "hal": "ingested"}
    put(vault, rel, frontmatter.dump(fm) + f"# {Path(rel).stem}\n\n## Original\n\n`{source}`\n\n## Text\n\n{text}")
    return rel


def fact(con, kind: str, fields: dict, rel: str, doc_type: str, quote: str = "", role: str = "own") -> int:
    """A fact an older Hal filed from the note at `rel`; its card's id."""
    return profile_store.add_found(con, kind, fields, {"source_rel": rel, "doc_type": doc_type, "quote": quote,
                                                       "role": role})


def old_record(con, vault: Path, files: Path) -> dict:
    """Marnie's record as an older Hal left it: notes, facts, one card confirmed and one edited; the card ids."""
    cv_file = put(files, "Old laptop/Marnie Quillfeather CV.md", CV)
    essay_file = put(files, "Old laptop/BA History/HIS2040 Archival Methods/Parish registers essay.md", ESSAY)
    cv = old_note(vault, "Sources/CVs and letters/Marnie Quillfeather CV.md", doc_type="cv", role="own",
                  category="CVs and letters", source=str(cv_file), text=CV)
    essay = old_note(vault, "Sources/Coursework/Parish registers essay.md", doc_type="essay", role="own",
                     category="Coursework", source=str(essay_file), text=ESSAY)
    notes = old_note(vault, "Sources/Study notes/Seminar 3.md", doc_type="notes", role="taught",
                     category="Study notes", source=str(files / "Old laptop/BA History/HIS2040/Seminar 3.md"),
                     text=NOTES)
    others = old_note(vault, "Sources/CVs and letters/Sam Pike CV.md", doc_type="cv", role="others",
                      category="CVs and letters", source=str(files / "gone.md"), text=OTHERS)
    ids = {"job": fact(con, "job", {"title": "Visitor guide", "employer": "Fenwick Lantern Museum Trust",
                                    "start": "2023", "end": "2024"}, cv, "cv", "Visitor guide"),
           "education": fact(con, "education", {"qualification": "BA", "subject": "History",
                                                "institution": "University of Nowhere Fen", "start": "2021",
                                                "end": "2024"}, cv, "cv", "BA History"),
           "archival": fact(con, "skill", {"name": "Archival research"}, essay, "essay", "archival research"),
           "palaeography": fact(con, "skill", {"name": "Palaeography"}, notes, "notes", "palaeography",
                                role="taught"),
           "classmate": fact(con, "skill", {"name": "Classmate's skill"}, others, "cv", "", role="others")}
    profile_store.confirm(con, ids["job"])
    profile_store.edit(con, ids["education"], {"qualification": "BA", "subject": "History and Heritage",
                                               "institution": "University of Nowhere Fen"})
    return ids


def accept_in_thread(con, sort_id: int, section: str | None = None) -> None:
    """What the accept action does for a move, in this thread."""
    sorts.accept(con, sort_id, section)
    states = move.before_accept(con, sort_id)
    for source_id in sorts.add_accepted(con, sort_id):
        pipeline.read(con, source_id)
    move.after_accept(con, sort_id, states)


def state(con, item_id: int) -> str:
    """A card's state now."""
    return con.execute("SELECT state FROM profile_items WHERE id = ?", (item_id,)).fetchone()[0]


def old_evidence(con) -> int:
    """Evidence that names an older Hal's note and no source."""
    return con.execute("SELECT count(*) FROM profile_evidence WHERE source_id IS NULL").fetchone()[0]


class MoveARecord(unittest.TestCase):
    def test_each_older_note_is_proposed_by_what_its_frontmatter_says_and_nothing_changes_yet(self):
        with home() as (con, vault, files), fake_ai():
            ids = old_record(con, vault, files)
            self.assertTrue(move.can_move(con))
            before = old_evidence(con)
            sort_id = sorts.create(con, "move", [], "older record")
            sorts.run(con, sort_id)
            got = {r["note_rel"]: r for state_ in ("proposed", "left_out")
                   for r in sorts.rows(con, sort_id, state=state_)["rows"]}
            cv = got["Sources/CVs and letters/Marnie Quillfeather CV.md"]
            self.assertEqual((cv["section"], cv["doc_kind"], cv["name"]), ("cv", "cv", "Marnie Quillfeather CV.md"))
            essay = got["Sources/Coursework/Parish registers essay.md"]
            self.assertEqual((essay["section"], essay["doc_kind"], essay["target_id"]),
                             ("coursework", "essay", ids["education"]))
            notes = got["Sources/Study notes/Seminar 3.md"]
            self.assertEqual((notes["section"], notes["target_id"]), ("lecture", ids["education"]))
            self.assertEqual(got["Sources/CVs and letters/Sam Pike CV.md"]["state"], "left_out",
                             "someone else's note is never moved")
            self.assertEqual(old_evidence(con), before)
            self.assertEqual(con.execute("SELECT count(*) FROM record_sources").fetchone()[0], 0)

    def test_a_move_keeps_the_persons_cards_sets_aside_the_rest_and_one_undo_puts_it_all_back(self):
        with home() as (con, vault, files), fake_ai():
            ids = old_record(con, vault, files)
            before = old_evidence(con)
            fields_before = {k: con.execute("SELECT fields FROM profile_items WHERE id = ?", (i,)).fetchone()[0]
                             for k, i in ids.items()}
            sort_id = sorts.create(con, "move", [], "older record")
            sorts.run(con, sort_id)
            sorts.tick(con, sort_id, False, "lecture")
            accept_in_thread(con, sort_id)
            sections = sorted(r[0] for r in con.execute("SELECT section FROM record_sources"))
            self.assertEqual(sections, ["coursework", "cv"])
            self.assertEqual(con.execute("SELECT count(*) FROM profile_evidence WHERE source_id IS NULL AND "
                                         "source_rel LIKE 'Sources/CVs and letters/Marnie%'").fetchone()[0], 0,
                             "the moved CV's older evidence is set aside")
            self.assertEqual((state(con, ids["job"]), state(con, ids["education"])), ("confirmed", "edited"))
            self.assertEqual(state(con, ids["palaeography"]), "hal", "a note not yet accepted keeps its facts")
            set_aside = move.finish(con, sort_id)
            self.assertGreater(set_aside, 0)
            self.assertEqual(state(con, ids["palaeography"]), "hidden",
                             "a fact only an unaccepted note gave is set aside, not deleted")
            self.assertTrue(con.execute("SELECT 1 FROM record_sort_kept_out WHERE what = 'evidence'").fetchone())
            self.assertFalse(move.can_move(con))
            with self.assertRaisesRegex(ValueError, "Undo the move first"):
                sorts.remove(con, sort_id)
            out = move.undo(con, sort_id)
            self.assertEqual(out["removed"], 2)
            self.assertEqual(con.execute("SELECT count(*) FROM record_sources").fetchone()[0], 0)
            self.assertEqual(old_evidence(con), before, "every piece of older evidence is back")
            self.assertEqual({k: state(con, i) for k, i in ids.items()},
                             {"job": "confirmed", "education": "edited", "archival": "hal", "palaeography": "hal",
                              "classmate": "hal"})
            self.assertEqual(json.loads(con.execute("SELECT fields FROM profile_items WHERE id = ?",
                                                    (ids["education"],)).fetchone()[0])["subject"],
                             "History and Heritage", "the person's edit survives the move and its undo")
            self.assertEqual(con.execute("SELECT fields FROM profile_items WHERE id = ?",
                                         (ids["palaeography"],)).fetchone()[0], fields_before["palaeography"])
            self.assertTrue((vault / "Sources/Study notes/Seminar 3.md").exists(), "the older notes stay")
            self.assertTrue(move.can_move(con), "the record can be moved again")
            with self.assertRaisesRegex(ValueError, "undone already"):
                move.undo(con, sort_id)


class MoveRoutes(unittest.TestCase):
    def test_the_page_offers_the_move_and_runs_it_finishes_it_and_undoes_it(self):
        hal = self.enterContext(HalServer())
        vault = Path(paths.record_default())
        vault.mkdir(parents=True, exist_ok=True)
        self.enterContext(mock.patch.object(record, "VAULT", vault, create=True))
        self.addCleanup(record._CACHE.clear)
        self.enterContext(fake_ai())
        with closing(db.connect()) as con:
            old_record(con, vault, hal.home / "Files")
        c = hal.signed_in()
        self.assertTrue(c.get("/api/v2/record/sort").json()["can_move"])
        started = c.post("/api/v2/record/move", {})
        self.assertEqual(started.status, 200, started.text)
        self.assertEqual(ended(started.json()["action_id"])["state"], "done")
        sort_id = started.json()["sort"]["id"]
        accepted = c.post(f"/api/v2/record/sort/{sort_id}/accept", {"section": "cv"}).json()
        self.assertEqual(ended(accepted["action_id"])["state"], "done")
        self.assertTrue(c.get(f"/api/v2/record/sort/{sort_id}").json()["can_undo"])
        self.assertEqual(c.post(f"/api/v2/record/sort/{sort_id}/finish", {}).status, 200)
        self.assertEqual(c.request("DELETE", f"/api/v2/record/sort/{sort_id}").status, 400)
        undone = c.post(f"/api/v2/record/sort/{sort_id}/undo", {})
        self.assertEqual((undone.status, undone.json()["removed"]), (200, 1))
        self.assertEqual(c.get(f"/api/v2/record/sort/{sort_id}").json()["state"], "undone")
        self.assertEqual(c.get("/api/v2/record/sources").json(), [])


if __name__ == "__main__":
    unittest.main()
