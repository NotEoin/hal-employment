"""Tidying the record: a backup, then what earlier reads filed held to today's rules.

The record is filled by a real ingest, then made to look as an older Hal left it (no `role`, an
`about_owner` answer, People and Places notes, a lecturer's email in the profile). Every person, file and
email is made up.
"""
import json
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from support.fake_ai import fake_ai
from test_ingest import NOTE_REPLY, OWNER, REPLY, home, put, rows, run

from jobhunt.core import settings
from jobhunt.ingest import actions as ingest_actions
from jobhunt.ingest import engine, frontmatter
from jobhunt.profile import store

SLIDES = "# Rivers\n\nDr Ama Pell, ama.pell@example.org. Meanders and oxbow lakes."
REPORT = "# Tide gauges\n\nA report on tide gauges along the made-up bay."


def make_old(vault, note_rel, **fields):
    """Rewrites a note's frontmatter as an older Hal wrote it: no role, an `about_owner` answer."""
    path = vault / note_rel
    fm, body = frontmatter.parse(path.read_text(encoding="utf-8"))
    fm.pop("role", None)
    fm.update(fields)
    path.write_text(frontmatter.dump(fm) + body, encoding="utf-8")


def entity(vault, folder, name, *, hal=True):
    """An entity note in `folder`, Hal's own or one the person wrote."""
    path = vault / folder / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "---\ntype: entity\n---\n" if hal else ""
    path.write_text(f"{head}# {name}\n", encoding="utf-8")
    return path


class Tidy(unittest.TestCase):
    def test_tidying_backs_up_then_holds_old_notes_to_todays_rules(self):
        with home() as (con, vault, files):
            settings.set("you.name", OWNER)
            with fake_ai({}):
                put(files, "GEO101/Lecture 3 Rivers.md", SLIDES)
                put(files, "Vault/settings.md", "# Settings\n\nTheme: dark.")
                run(con, [files])
            got = rows(con)
            slides = got["Files/GEO101/Lecture 3 Rivers.md"]["note"]
            tool = got["Files/Vault/settings.md"]
            con.execute("UPDATE ingest_items SET rel = 'Files/Vault/.obsidian/settings.md' WHERE id = ?",
                        (tool["id"],))
            con.commit()
            make_old(vault, slides, about_owner="false", doc_type="slides")
            linked = (vault / slides).read_text(encoding="utf-8") + (
                "\nAsk [[People/ama.pell|ama.pell]]@example.org.\n")
            (vault / slides).write_text(linked, encoding="utf-8")
            store.add_found(con, "basic", {"key": "email", "value": "ama.pell@example.org"},
                            {"source_rel": slides, "doc_type": "notes", "quote": "ama.pell@example.org"})
            stray = vault / "Sources" / "Other" / "Gone.md"
            stray.parent.mkdir(parents=True, exist_ok=True)
            stray.write_text("---\ntype: source\nhal: ingested\n---\n# Gone\n", encoding="utf-8")
            store.add_found(con, "skill", {"name": "Surveying"}, {"source_rel": "Sources/Other/Gone.md",
                                                                  "doc_type": "notes"})
            lecturer = entity(vault, "People", "Ama Pell")
            room = entity(vault, "Places", "Room 4.005")
            mine = entity(vault, "People", "My mentor", hal=False)
            with fake_ai({}):
                out = run(con, [], batch="t", tidy=True)
            text = (vault / slides).read_text(encoding="utf-8")
            kinds = [r[0] for r in con.execute("SELECT kind FROM profile_items")]
            from jobhunt.core import backup
            made = sorted(p.suffix for p in (backup.database_file(con).parent / "backups").iterdir())
            tool_row = con.execute("SELECT state, note FROM ingest_items WHERE id = ?", (tool["id"],)).fetchone()
            gone = [p.name for p in (stray, lecturer, room) if p.exists()]
            kept = mine.exists()
        self.assertIn(".gz", made)
        self.assertIn(".db", made)
        self.assertEqual(frontmatter.parse(text)[0]["role"], "taught")
        self.assertNotIn("ama.pell@example.org", text)
        self.assertEqual(kinds, [], "the lecturer's email and the deleted note's skill are gone")
        self.assertEqual(gone, [])
        self.assertTrue(kept, "a note the person wrote is never touched")
        self.assertEqual((tool_row["state"], tool_row["note"]), ("skipped", None))
        self.assertGreaterEqual(out["tidied"]["notes_deleted"], 1)

    def test_tidying_asks_the_ai_again_about_a_document_only_it_can_judge(self):
        with home() as (con, vault, files):
            settings.set("you.name", OWNER)
            mine = {**REPLY, "doc_type": "report", "category": "Coursework", "role": "own",
                    "facts": [{"kind": "project", "fields": {"name": "Tide gauges"}, "field": "name",
                               "quote": "Tide gauges"}]}
            with fake_ai({"ingest_doc": json.dumps(mine)}):
                put(files, "Tide report.md", f"{REPORT}\n\nBy {OWNER}.")
                run(con, [files])
            note = rows(con)["Files/Tide report.md"]["note"]
            make_old(vault, note, about_owner="unsure")
            with fake_ai({"ingest_doc": json.dumps({**mine, "role": "others"})}) as backend:
                run(con, [], batch="t", tidy=True)
            row = rows(con, "t")["Files/Tide report.md"]
            projects = con.execute("SELECT count(*) FROM profile_items").fetchone()[0]
            self.assertFalse((vault / note).exists())
        self.assertEqual(len(backend.requests("ingest_doc")), 1)
        self.assertEqual((row["state"], projects), ("not_mine", 0))

    def test_a_typed_note_is_judged_by_rules_and_never_sent_to_the_ai(self):
        with home() as (con, _vault, files):
            settings.set("you.name", OWNER)
            with fake_ai({}):
                for n in (1, 2, 3):
                    put(files, f"GEO101/week {n} notes.md", f"# Week {n}\n\nMeanders, week {n}.")
                run(con, [files])
            with fake_ai({"ingest_doc": json.dumps(NOTE_REPLY)}) as backend:
                run(con, [], batch="t", tidy=True)
            roles_given = {r["role"] for r in rows(con).values() if r["kind"] == "file"}
        self.assertEqual(backend.requests("ingest_doc"), [])
        self.assertEqual(roles_given, {"taught"})

    def test_older_copies_of_a_project_keep_only_the_newest_note(self):
        with home() as (con, vault, files):
            settings.set("you.name", OWNER)
            with fake_ai({}), mock.patch.object(engine.versions, "settle", return_value=0):
                for name, day in (("tide-gauge", 1), ("tide-gauge (2)", 9)):
                    repo = files / name
                    put(repo, "README.md", "# Tide gauge\n\nReads a made-up sensor.")
                    put(repo, "main.py", "print('tide')\n")
                    put(repo, "gauge.py", "LEVEL = 1\n")
                    put(repo, "sensor.py", "PIN = 4\n")
                    put(repo, "pyproject.toml", "[project]\nname = 'tide-gauge'\n")
                    put(repo, ".git/logs/HEAD", f"0 1 {OWNER} <a@example.org> {1700000000 + day * 86400} +0000\tc\n")
                run(con, [files])
            notes_before = {r["note"] for r in rows(con).values() if r["kind"] == "repo"}
            self.assertEqual(len(notes_before), 2)
            with fake_ai({}):
                run(con, [], batch="t", tidy=True)
            kept = [n for n in notes_before if (vault / n).exists()]
            states = sorted(r["state"] for r in con.execute("SELECT state FROM ingest_items WHERE kind = 'repo'"))
            text = (vault / kept[0]).read_text(encoding="utf-8") if kept else ""
        self.assertEqual(len(kept), 1)
        self.assertIn("## Earlier versions", text)
        self.assertIn("older_version", states)


class Route(unittest.TestCase):
    def test_tidy_my_record_starts_a_tidy(self):
        with HalServer() as hal, mock.patch.object(ingest_actions, "queue", return_value="a1") as queue:
            started = hal.signed_in().post("/api/v2/record/ingest/tidy", {})
        self.assertEqual(started.json(), {"action_id": "a1"})
        self.assertTrue(queue.call_args.kwargs["tidy"])


if __name__ == "__main__":
    unittest.main()
