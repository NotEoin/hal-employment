"""Project folders: a folder of the person's own work with a README is one project, however many notes it holds.

Every test builds a made-up workspace in its own temporary home; the AI is the fake one, which names a project
after each file it reads, as a real read named one after each ticket and write-up (2026-11-08).
"""
import json
import re
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import fake_ai
from test_ingest import NOTE_REPLY, OWNER, home, put, rows, run

from jobhunt.core import settings
from jobhunt.ingest import group, projects
from jobhunt.profile import store

WORKSPACE = {
    "Lantern workspace/README.md": "# Lantern counter\n\nA workspace for a tool that counts harbour lanterns.",
    "Lantern workspace/01 plan.md": "# Plan\n\nFirst count the lanterns, then log the beams.",
    "Lantern workspace/tickets/LCT-001 Tide clock.md": "# LCT-001 Tide clock\n\nAdd a tide clock to the log.",
    "Lantern workspace/tickets/LCT-002 Wick sensor.md": "# LCT-002 Wick sensor\n\nRead the wick sensor.",
    "Lantern workspace/tickets/LCT-003 Beam log.md": "# LCT-003 Beam log\n\nKeep a log of each beam.",
    "Lantern workspace/writeups/Writeup A.md": "# Writeup A\n\nWhat the beam log showed in May.",
}
TOOLS = {"README": "Python", "01 plan": "SQLite", "LCT-001 Tide clock": "Rust", "LCT-002 Wick sensor": "Arduino",
         "LCT-003 Beam log": "Pandas", "Writeup A": "LaTeX"}


def names_a_project(req) -> str:
    """A made-up AI reply that calls each file a report and names a project after it, as a real read did."""
    rel = re.search(r"^File: (.+)$", req.user, re.M).group(1)
    stem = rel.rsplit("/", 1)[-1].removesuffix(".md")
    doc_type = "cv" if stem == "CV" else "report"
    name = {"CV": "Beacon app", "Harbour note": "Harbour bot"}.get(stem, stem)
    facts = [{"kind": "project", "fields": {"name": name, "tools": [TOOLS.get(stem, "Lua")],
                                            "summary": f"All about {stem}."}, "field": "name", "quote": ""},
             {"kind": "skill", "fields": {"name": TOOLS.get(stem, "Lua")}, "field": "name", "quote": ""}]
    return json.dumps({**NOTE_REPLY, "doc_type": doc_type, "category": "Coursework", "title": stem, "role": "own",
                       "facts": facts})


def project_cards(con) -> dict[str, dict]:
    """The project cards by name: {name: shaped item}."""
    return {i["fields"]["name"]: i for i in store.items(con, "project")}


class ProjectFolders(unittest.TestCase):
    def read(self, con, files, extra=None, origin="mine"):
        for rel, text in {**WORKSPACE, **(extra or {})}.items():
            put(files, rel, text)
        settings.set("you.name", OWNER)
        settings.set("record.read_all", True)
        return run(con, [files / "Lantern workspace"], origins={str(files / "Lantern workspace"): origin})

    def test_a_folder_of_own_work_with_a_readme_is_one_project_card_with_every_notes_tools(self):
        with home() as (con, vault, files), fake_ai({"ingest_doc": names_a_project}):
            self.read(con, files)
            cards = project_cards(con)
            got = rows(con)
            kinds = {r["kind"] for r in got.values()}
            ticket = (vault / got["Lantern workspace/tickets/LCT-001 Tide clock.md"]["note"]).read_text(
                encoding="utf-8")
            unit = got["Lantern workspace"]
            skills = {i["fields"]["name"] for i in store.items(con, "skill")}
        self.assertEqual(list(cards), ["Lantern counter"], "one card, named from the README's heading")
        card = cards["Lantern counter"]
        self.assertEqual(sorted(card["fields"]["tools"]), sorted(TOOLS.values()))
        self.assertEqual(card["fields"]["summary"], "All about README.", "only the README says what it is")
        self.assertEqual(len({e["source_rel"] for e in card["evidence"]}), len(WORKSPACE))
        self.assertNotIn("course", kinds, "its sub-folders are no course units")
        self.assertEqual((unit["kind"], unit["state"]), ("project", "done"))
        self.assertIn("## Part of", ticket)
        self.assertIn("Lantern counter", ticket)
        self.assertIn("Rust", skills, "notes inside still give skills")

    def test_a_cv_inside_and_a_note_about_another_projects_repository_keep_their_own_cards(self):
        extra = {"Lantern workspace/CV.md": "# Avery Quillon\n\nCurriculum vitae\n\n## Experience\n\nBuilt things.\n\n"
                                            "## Education\n\nBA Made-up Studies.",
                 "Lantern workspace/Harbour note.md": "# Harbour note\n\nThe harbour bot needs a new wick.",
                 "Harbour bot/pyproject.toml": "[project]\nname = \"harbour-bot\"\n",
                 "Harbour bot/README.md": "# Harbour bot\n\nA made-up bot."}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": names_a_project}):
            for rel in ("Harbour bot/pyproject.toml", "Harbour bot/README.md"):
                put(files, rel, extra.pop(rel))
            settings.set("record.read_all", True)
            run(con, [files / "Harbour bot"], batch="b0", origins={str(files / "Harbour bot"): "mine"})
            self.read(con, files, extra)
            cards = project_cards(con)
        self.assertIn("Beacon app", cards, "a CV lists projects wherever it is kept")
        self.assertTrue(any("Harbour note" in e["source_rel"] for e in cards["Harbour bot"]["evidence"]),
                        "a note about a project with its own unit speaks for that one")
        self.assertIn("Lantern counter", cards)

    def test_a_module_folder_of_course_material_still_holds_two_coursework_projects(self):
        module = {"GEO101/README.md": "# GEO101\n\nModule handbook.",
                  "GEO101/Week 1 notes.md": "# Week 1\n\nRivers.", "GEO101/Week 2 notes.md": "# Week 2\n\nTides.",
                  "GEO101/Week 3 notes.md": "# Week 3\n\nDeltas.",
                  "GEO101/Tide coursework.md": "# Tide coursework\n\nMy tide model, by Avery Quillon.",
                  "GEO101/Delta coursework.md": "# Delta coursework\n\nMy delta model, by Avery Quillon."}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": names_a_project}):
            settings.set("you.name", OWNER)
            for rel, text in module.items():
                put(files, rel, text)
            run(con, [files], origins={str(files): "course"})
            got = rows(con)
            cards = project_cards(con)
        self.assertEqual(got["Files/GEO101"]["kind"], "course", "course material is never one project")
        self.assertIn("Tide coursework", cards)
        self.assertIn("Delta coursework", cards)

    def test_a_folder_holding_two_projects_is_a_collection_and_each_inside_is_one(self):
        with home() as (con, _vault, files), fake_ai({}):
            for rel in ("Projects/README.md", "Projects/Tide/README.md", "Projects/Tide/notes.md",
                        "Projects/Wick/README.md", "Projects/Wick/notes.md"):
                put(files, rel, f"# {rel.split('/')[-2]}\n\nA made-up project.")
            run(con, [files], origins={str(files): "mine"})
            found = sorted(r["rel"] for r in rows(con).values() if r["kind"] == "project")
        self.assertEqual(found, ["Files/Projects/Tide", "Files/Projects/Wick"])

    def test_a_readme_with_no_heading_names_the_project_after_its_folder(self):
        with home() as (_con, _vault, files):
            put(files, "Wick sensor/README.md", "Notes on the wick sensor.")
            self.assertEqual(projects.name_of(str(files / "Wick sensor")), "Wick sensor")
            self.assertEqual(group.projects({files / "a", files / "a" / "b"}, set()), {files / "a"})


class Tidy(unittest.TestCase):
    def test_tidying_folds_the_cards_an_older_read_made_of_a_workspaces_notes_into_one(self):
        with home() as (con, _vault, files):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            for rel, text in WORKSPACE.items():
                put(files, rel, text)
            with fake_ai({"ingest_doc": names_a_project}), mock.patch.object(group, "projects", return_value=set()):
                run(con, [files], origins={str(files): "mine"})     # as an older Hal read it
            before = project_cards(con)
            store.confirm(con, before["Writeup A"]["id"])
            with fake_ai({}):
                run(con, [], batch="t", tidy=True)
            cards = {i["fields"]["name"]: i for i in store.items(con, "project", hidden=False)}
            hidden = {i["fields"]["name"]: i for i in store.items(con, "project") if i["state"] == "hidden"}
            courses = [r for r in rows(con, "b1").values() if r["kind"] == "course"]
            unit = rows(con, "t").get("Files/Lantern workspace")
        self.assertGreater(len(before), 2, "the older read made a card of each note")
        self.assertEqual(sorted(cards), ["Lantern counter", "Writeup A"], "a card the person checked stays")
        self.assertEqual(sorted(cards["Lantern counter"]["fields"]["tools"]),
                         sorted(t for s, t in TOOLS.items() if s != "Writeup A"))
        tide = hidden["LCT-001 Tide clock"]
        self.assertEqual((tide["merged_into"], tide["merged_why"], tide["tier_why"]),
                         (cards["Lantern counter"]["id"], "Part of Lantern counter", "Part of Lantern counter"))
        self.assertTrue(courses and all(r["state"] == "skipped" for r in courses), "its course units go")
        self.assertEqual((unit["kind"], unit["state"]), ("project", "done"))


if __name__ == "__main__":
    unittest.main()
