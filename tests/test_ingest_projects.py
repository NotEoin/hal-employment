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
from jobhunt.profile import duplicates, merge, store

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


def naming(projects: dict[str, list[str]]):
    """A made-up AI reply that gives each file named in `projects` (by its name without ".md") a project fact
    for each name listed, and every other file one project named after it, as `names_a_project` does."""
    def reply(req) -> str:
        rel = re.search(r"^File: (.+)$", req.user, re.M).group(1)
        stem = rel.rsplit("/", 1)[-1].removesuffix(".md")
        facts = [{"kind": "project", "fields": {"name": n, "summary": f"{n}, as {stem} tells it."}, "field": "name",
                  "quote": ""} for n in projects.get(stem, [stem])]
        return json.dumps({**NOTE_REPLY, "doc_type": "report", "category": "Coursework", "title": stem,
                           "role": "own", "facts": facts})
    return reply


# The projects a made-up portfolio's dossier describes, each with the made-up repository it lives in, named
# in other words, as a real person's were (2026-11-08).
REPOS = {"LIDAR Autonomous Navigation": "Lidar-Autonavigation-System", "Voice Interface": "parrot-voice",
         "Network Packet Sniffer": "wifi-sniffer", "Drone Autopilot": "px-autopilot-tuner",
         "Desktop Companion": "hatch-desktop", "Archivist Bot": "chat-archivist",
         "Lecture Downloader": "slide-grabber", "Notebook Package": "nbtools-kit"}
THESIS = "Tidal Energy Forecasting"


def shown_projects(con) -> dict[str, dict]:
    """The project cards shown on Your record, by name."""
    return {i["fields"]["name"]: i for i in store.items(con, "project", hidden=False)}


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

    def test_a_document_listing_three_or_more_projects_keeps_each_projects_own_card(self):
        listed = ["Tide clock", "Wick sensor", "Beam log", "Harbour lights survey"]
        portfolio = {"Portfolio/README.md": "# Portfolio\n\nMy portfolio.",
                     "Portfolio/Project dossier.md": "# Project dossier\n\nEvery project I have made.",
                     "Portfolio/Next steps.md": "# Next steps\n\nPolish the portfolio."}
        with home() as (con, _vault, files), fake_ai({"ingest_doc": naming({"Project dossier": listed})}):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            for rel, text in portfolio.items():
                put(files, rel, text)
            run(con, [files / "Portfolio"], origins={str(files / "Portfolio"): "mine"})
            cards = project_cards(con)
        self.assertEqual(sorted(cards), sorted([*listed, "Portfolio"]),
                         "a dossier lists projects, as a CV does; a note about the folder's own work is its card")

    def test_a_note_naming_a_repositorys_project_in_other_words_keeps_its_own_name(self):
        repo = {"Lidar-Autonavigation-System/pyproject.toml": "[project]\nname = \"lidar-autonav\"\n",
                "Lidar-Autonavigation-System/README.md": "# Lidar autonavigation\n\nA made-up rover."}
        notes = {"Sensor bench/README.md": "# Sensor bench\n\nMy bench for testing sensors.",
                 "Sensor bench/Rover note.md": "# Rover note\n\nThe rover's scanner needs a new mount.",
                 "Sensor bench/Mount plan.md": "# Mount plan\n\nPrint a new bracket."}
        reply = naming({"Rover note": ["LIDAR Autonomous Navigation"]})
        with home() as (con, _vault, files), fake_ai({"ingest_doc": reply}):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            for rel, text in {**repo, **notes}.items():
                put(files, rel, text)
            run(con, [files / "Lidar-Autonavigation-System"], batch="b0",
                origins={str(files / "Lidar-Autonavigation-System"): "mine"})
            run(con, [files / "Sensor bench"], origins={str(files / "Sensor bench"): "mine"})
            cards = project_cards(con)
        self.assertIn("LIDAR Autonomous Navigation", cards, "an acronym both names share ties it to the repository")
        self.assertEqual(sorted(e["source_rel"].rsplit("/", 1)[-1] for e in cards["Sensor bench"]["evidence"]),
                         ["Mount plan.md", "README.md"], "a plan with no other project's name is the bench's")

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


TICKETS = 100


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

    def older_rules(self, con) -> None:
        """What the rules of an older Hal did to a record (2026-11-08): every project the dossier names
        folded into the portfolio's card, that card merged into the dissertation's because both had the
        dossier, and one repository's card merged into another's."""
        cards = project_cards(con)
        dossier = [e for e in con.execute("SELECT id, item_id FROM profile_evidence WHERE source_rel LIKE ?",
                                          ("%Project dossier%",)).fetchall()]
        for e in dossier:
            store.refile(con, e["id"], {"name": "Portfolio"})
        portfolio = project_cards(con)["Portfolio"]["id"]
        for e in dossier:
            if not con.execute("SELECT 1 FROM profile_evidence WHERE item_id = ?", (e["item_id"],)).fetchone():
                store.fold_away(con, e["item_id"], portfolio, "Part of Portfolio")
        store.merge_into(con, portfolio, cards[THESIS]["id"], duplicates.MERGED + THESIS)
        store.merge_into(con, cards["slide-grabber"]["id"], cards["nbtools-kit"]["id"],
                         duplicates.MERGED + "nbtools-kit")

    def test_tidying_takes_apart_a_portfolio_whose_dossier_folded_every_project_into_one_card(self):
        files_in = {"Portfolio/README.md": "# Portfolio\n\nWhere I keep my portfolio.",
                    "Portfolio/Project dossier.md": "# Project dossier\n\nEvery project I have made.",
                    "Dissertation/Final dissertation.md": "# Final dissertation\n\nForecasting tidal energy."}
        for name, repo in REPOS.items():
            files_in[f"{repo}/pyproject.toml"] = f"[project]\nname = \"{repo}\"\n"
            files_in[f"{repo}/README.md"] = f"# {repo}\n\nMy {name.lower()}."
        reply = naming({"Project dossier": [*REPOS, THESIS], "README": ["Portfolio"], "Final dissertation": [THESIS]})
        with home() as (con, _vault, files):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            for rel, text in files_in.items():
                put(files, rel, text)
            with fake_ai({"ingest_doc": reply}), mock.patch.object(group, "projects", return_value=set()):
                run(con, [files], origins={str(files): "mine"})
            self.older_rules(con)
            before = shown_projects(con)
            with fake_ai({}):
                run(con, [], batch="t", tidy=True)
                duplicates.refresh(con)
            after = shown_projects(con)
            portfolio = after.get("Portfolio", {"evidence": []})
        self.assertNotIn("Voice Interface", before, "the older rules hid the dossier's projects")
        self.assertLessEqual(set(REPOS) | {THESIS, "Portfolio", "slide-grabber"}, set(after),
                             "each project is a card again, and the portfolio is not the dissertation")
        self.assertNotIn("Project dossier.md", [e["source_rel"].rsplit("/", 1)[-1] for e in portfolio["evidence"]])

    def test_tidying_still_folds_a_research_folder_of_a_hundred_tickets_into_one_project(self):
        tickets = {f"Probe study/tickets/PRB-{n:03d} Step {n}.md": f"# PRB-{n:03d} Step {n}\n\nCalibrate step {n}."
                   for n in range(1, TICKETS + 1)}
        with home() as (con, _vault, files):
            settings.set("you.name", OWNER)
            settings.set("record.read_all", True)
            for rel, text in {"Probe study/README.md": "# Probe study\n\nMy research on a salinity probe.",
                              "CV.md": "# Avery Quillon\n\nCurriculum vitae.", **tickets}.items():
                put(files, rel, text)
            with fake_ai({"ingest_doc": names_a_project}), mock.patch.object(group, "projects", return_value=set()):
                run(con, [files], origins={str(files): "mine"})
            before = project_cards(con)
            with fake_ai({}):
                run(con, [], batch="t", tidy=True)
                duplicates.refresh(con)
            after = shown_projects(con)
        self.assertGreater(len(before), TICKETS, "the older read made a card of each ticket")
        self.assertEqual(sorted(after), ["Beacon app", "Probe study"], "the CV's project stays; the tickets fold")

    def test_tidying_takes_apart_two_projects_an_older_read_merged_by_the_persons_site(self):
        # CV lines ending "Write-ups at <the person's site>" gave a flagship project the site's front page as its
        # link, so it merged into the portfolio site's card, which took its tools (2026-11-08)
        said = {"Tidecaster": "PyTorch", "Portfolio": "Hugo"}

        def cv(req) -> str:
            facts = [{"kind": "project", "fields": {"name": n, "tools": [t], "link": "https://example.org"},
                      "field": "name", "quote": f"{n}. Write-ups at example.org"} for n, t in said.items()]
            return json.dumps({**NOTE_REPLY, "doc_type": "cv", "category": "CV", "title": "CV", "role": "own",
                               "facts": facts})
        with home() as (con, _vault, files):
            settings.set("you.name", OWNER)
            put(files, "CV.md", "# Avery Quillon\n\nCurriculum vitae.")
            older = mock.patch.object(merge, "project_link", merge._url_key)     # the rule before (2026-11-08)
            with fake_ai({"ingest_doc": cv}), older:
                run(con, [files], origins={str(files): "mine"})
            before = shown_projects(con)
            with fake_ai({}):
                run(con, [], batch="t", tidy=True)
            after = shown_projects(con)
        self.assertEqual(len(before), 1, "the older rule merged the two")
        self.assertEqual({n: c["fields"]["tools"] for n, c in after.items()}, {n: [t] for n, t in said.items()})


if __name__ == "__main__":
    unittest.main()
