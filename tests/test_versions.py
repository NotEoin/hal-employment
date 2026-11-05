"""Copies of one code project: only the newest is read, and a newer copy found later rewrites its note.

Every repository here is made up in a temporary folder, with a hand-written `.git/config` and
`.git/logs/HEAD`; the people and projects are invented. The AI is the fake one.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_ai import fake_ai
from test_ingest import OWNER, home, put, rows, run

from jobhunt.core import settings
from jobhunt.ingest import engine, items, versions
from jobhunt.profile import store

DAY = 86400
T0 = 1_700_000_000           # a made-up commit time (2023-11-14); later copies are days after it
REMOTE = "https://example.org/example-user/Lidar-Autonavigation.git"
SOURCES = ("lidar.py", "planner.py", "maps/grid.py", "maps/scan.py", "tests/test_planner.py")


def project(folder: Path, *, remote: str | None = REMOTE, commit: int | None = None, readme: str = "",
            files=SOURCES, file_time: int | None = None) -> Path:
    """A made-up copy of a project at `folder`: a README naming the owner, its source files, and a git
    folder with `remote` and a last commit at `commit` when given."""
    put(folder, "README.md", readme or f"# Lidar autonavigation\n\nBy {OWNER}. Drives a robot by lidar.")
    for rel in files:
        put(folder, rel, f"# {rel}\nVALUE = 1\n")
    if remote is not None or commit is not None:
        put(folder, ".git/HEAD", "ref: refs/heads/main\n")
    if remote is not None:
        put(folder, ".git/config", f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {remote}\n'
                                   '\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    if commit is not None:
        put(folder, ".git/logs/HEAD", f"{'0' * 40} {'1' * 40} {OWNER} <avery@example.org> {commit - DAY} +0000"
                                      f"\tcommit (initial): start\n{'1' * 40} {'2' * 40} {OWNER} "
                                      f"<avery@example.org> {commit} +0100\tcommit: more\n")
    if file_time is not None:
        for here, _dirs, names in os.walk(folder):
            for name in names:
                os.utime(Path(here) / name, (file_time, file_time))
    return folder


def code_notes(vault: Path) -> list[str]:
    """The notes of code projects in the record, without the folder's index."""
    return sorted(p.relative_to(vault).as_posix() for p in (vault / "Sources" / "Code").glob("*.md")
                  if p.name != "Index.md")


def reply(summary: str, level: str) -> str:
    """The fake AI's reading of a project: a project fact with `summary` and a Python skill at `level`."""
    return json.dumps({
        "doc_type": "repository", "category": "Code", "title": "Lidar autonavigation", "period": "2024",
        "summary": summary, "tags": ["robotics", "lidar", "python", "navigation", "mapping", "planning",
                                     "sensors", "autonomy", "code", "github", "2024", "testing"],
        "entities": {"organisations": [], "places": [], "people": [], "projects": [], "courses": [],
                     "skills": []},
        "about_owner": True,
        "facts": [{"kind": "project", "fields": {"name": "Lidar autonavigation", "summary": summary},
                   "field": "name", "quote": "Lidar autonavigation"},
                  {"kind": "skill", "fields": {"name": "Python", "level": level}, "field": "name",
                   "quote": "lidar.py"}]})


class Names(unittest.TestCase):
    def test_copy_marks_are_taken_off_a_projects_name(self):
        for name in ("Lidar-Autonavigation-System", "Lidar-Autonavigation-System (2)",
                     "Lidar-Autonavigation-System-v3", "Lidar_Autonavigation_System_v3_fixed",
                     "Lidar-Autonavigation-System-v3-final", "Copy of Lidar Autonavigation System",
                     "lidar-autonavigation-system-main", "Lidar-Autonavigation-System-2024-05-01",
                     "Lidar Autonavigation System - Copy", "lidar-autonavigation-system.zip"):
            self.assertEqual(versions.base_name(name), "lidar autonavigation system", name)
        self.assertEqual(versions.base_name("lidar refactored"), "lidar")
        self.assertEqual(versions.base_name("2048"), "2048", "a name that is all marks is kept")

    def test_a_remote_is_one_project_whether_written_as_https_or_ssh(self):
        forms = ("https://example.org/Example-User/Lidar.git", "git@example.org:example-user/lidar.git",
                 "ssh://git@example.org/example-user/lidar", "https://example.org/example-user/lidar/")
        self.assertEqual({versions.normal_remote(f) for f in forms}, {"example.org/example-user/lidar"})

    def test_the_newest_copy_is_the_latest_commit_even_when_its_files_look_older(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = project(Path(tmp) / "a", commit=T0, file_time=T0 + 30 * DAY)   # copied: fresh file times
            new = project(Path(tmp) / "b", commit=T0 + 10 * DAY, file_time=T0)
            loose = project(Path(tmp) / "c", remote=None, file_time=T0 + 5 * DAY)
            self.assertEqual(versions.newest([old, new, loose]), new)
            self.assertEqual(versions.newest([old, loose]), loose, "a copy without history goes by its files")
            self.assertEqual(versions.last_commit(loose), None)


class Copies(unittest.TestCase):
    def test_only_the_newest_copy_of_a_project_is_read_and_names_the_others(self):
        with home() as (con, vault, files), fake_ai({}):
            project(files / "Lidar-Autonavigation-System", commit=T0)
            project(files / "Lidar-Autonavigation-System (2)", remote=None, file_time=T0 - DAY)
            project(files / "Lidar-Autonavigation-System-v3-final", commit=T0 + 20 * DAY)
            project(files / "old" / "Lidar-Autonavigation-System", commit=T0 - 10 * DAY)
            project(files / "lidar refactored", remote=None, file_time=T0 + DAY)
            run(con, [files])
            found = {rel: (r["state"], r["note"]) for rel, r in rows(con).items()}
            newest = found["Files/Lidar-Autonavigation-System-v3-final"]
            self.assertEqual(newest[0], "done")
            self.assertEqual(sorted(s for s, _ in found.values()), ["done"] + ["older_version"] * 4)
            self.assertEqual(code_notes(vault), [newest[1]], "one note, not five")
            note = (vault / newest[1]).read_text(encoding="utf-8")
            earlier = note.split(versions.EARLIER, 1)[1].split("## Original", 1)[0]
            for rel in ("Lidar-Autonavigation-System", "Lidar-Autonavigation-System (2)", "lidar refactored",
                        "old/Lidar-Autonavigation-System"):
                self.assertIn(f"- `{files / rel}`\n", earlier)
            self.assertNotIn("v3-final`", earlier)
            older = rows(con)["Files/lidar refactored"]
            self.assertEqual(older["dup_of"], rows(con)["Files/Lidar-Autonavigation-System-v3-final"]["id"])

    def test_unrelated_folders_with_the_same_name_stay_separate(self):
        with home() as (con, _vault, files), fake_ai({}):
            project(files / "a" / "website", remote=None, files=("blog.py", "feed.py", "posts/one.md", "posts/two.md"))
            project(files / "b" / "website", remote=None, files=("shop.py", "cart.py", "items/a.js", "items/b.js"))
            project(files / "c" / "lidar", remote="https://example.org/a/lidar.git", commit=T0)
            project(files / "d" / "lidar", remote="https://example.org/b/lidar.git", commit=T0 + DAY)
            run(con, [files])
            self.assertEqual({r["state"] for r in rows(con).values()}, {"done"})
            self.assertEqual(len({r["note"] for r in rows(con).values()}), 4)

    def test_a_copy_inside_a_zip_counts_as_a_copy(self):
        with home() as (con, vault, files), tempfile.TemporaryDirectory() as tmp, fake_ai({}):
            import shutil

            zipped = project(Path(tmp) / "Lidar-Autonavigation-System-v3", commit=T0 + 5 * DAY)
            shutil.make_archive(str(files / "lidar-backup"), "zip", tmp)
            project(files / "Lidar-Autonavigation-System", commit=T0)
            run(con, [files])
            found = rows(con)
            self.assertEqual(found["Files/lidar-backup.zip/Lidar-Autonavigation-System-v3"]["state"], "done")
            self.assertEqual(found["Files/Lidar-Autonavigation-System"]["state"], "older_version")
            self.assertTrue(zipped.is_dir())
            note = (vault / found["Files/lidar-backup.zip/Lidar-Autonavigation-System-v3"]["note"]).read_text("utf-8")
            self.assertIn(f"- `{files / 'Lidar-Autonavigation-System'}`", note)


class LaterCopies(unittest.TestCase):
    def test_a_newer_copy_found_later_rewrites_the_note_in_place_and_replaces_its_facts(self):
        with home() as (con, vault, files), tempfile.TemporaryDirectory() as later:
            settings.set("you.name", OWNER)
            first = project(files / "Lidar-Autonavigation-System", commit=T0)
            with fake_ai({"ingest_doc": reply("Drives a robot by lidar.", "Intermediate")}):
                run(con, [files])
            note_rel = rows(con)["Files/Lidar-Autonavigation-System"]["note"]
            (vault / "Notes.md").write_text(f"See [[{note_rel[:-3]}]].", encoding="utf-8")
            proj = store.items(con, "project")[0]
            skill = store.items(con, "skill")[0]
            store.confirm(con, proj["id"])
            store.edit(con, skill["id"], {"level": "Expert"})

            second = project(Path(later) / "Lidar-Autonavigation-System-v4",
                             readme=f"# Lidar autonavigation\n\nBy {OWNER}. Now maps rooms too.", commit=T0 + DAY)
            with fake_ai({"ingest_doc": reply("Drives a robot and maps rooms.", "Advanced")}):
                run(con, [later], batch="b2")
            again = rows(con, "b2")[f"{Path(later).name}/Lidar-Autonavigation-System-v4"]
            self.assertEqual((again["state"], again["note"]), ("done", note_rel), "the same note, in place")
            self.assertEqual(code_notes(vault), [note_rel])
            note = (vault / note_rel).read_text(encoding="utf-8")
            self.assertIn("Now maps rooms too.", note)
            self.assertIn(f"source: {second}", note)
            self.assertIn(f"{versions.EARLIER}\n\n- `{first}`\n", note)
            self.assertEqual(rows(con)["Files/Lidar-Autonavigation-System"]["state"], "older_version")

            said = [json.loads(r["said"]) for r in con.execute(
                "SELECT said FROM profile_evidence WHERE source_rel = ? ORDER BY id", (note_rel,))]
            self.assertEqual(said, [{"name": "Lidar autonavigation", "summary": "Drives a robot and maps rooms."},
                                    {"name": "Python", "level": "Advanced"}], "the old copy's facts are gone")
            proj, skill = store.item(con, proj["id"]), store.item(con, skill["id"])
            self.assertEqual((proj["state"], proj["fields"]["summary"]), ("hal", "Drives a robot and maps rooms."),
                             "a checked fact the newer copy changed is to be checked again")
            self.assertEqual((skill["state"], skill["fields"]["level"]), ("edited", "Expert"))

    def test_a_newer_copy_is_matched_to_one_read_from_a_zip_since_removed(self):
        with home() as (con, vault, files), tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as later, fake_ai({}):
            import shutil

            project(Path(tmp) / "Lidar-Autonavigation-System", commit=T0)
            shutil.make_archive(str(files / "lidar"), "zip", tmp)
            run(con, [files])
            note_rel = rows(con)["Files/lidar.zip/Lidar-Autonavigation-System"]["note"]
            project(Path(later) / "Lidar-Autonavigation-System-v2", commit=T0 + DAY)
            run(con, [later], batch="b2")
            self.assertEqual(rows(con, "b2")[f"{Path(later).name}/Lidar-Autonavigation-System-v2"]["note"],
                             note_rel)
            self.assertEqual(code_notes(vault), [note_rel])
            self.assertIn("- `Files/lidar.zip/Lidar-Autonavigation-System`",
                          (vault / note_rel).read_text(encoding="utf-8"))

    def test_the_same_folder_read_again_unchanged_is_not_read_twice(self):
        with home() as (con, vault, files), fake_ai({}):
            project(files / "Lidar-Autonavigation-System", commit=T0)
            run(con, [files])
            note_rel = rows(con)["Files/Lidar-Autonavigation-System"]["note"]
            before = (vault / note_rel).read_text(encoding="utf-8")
            run(con, [files], batch="b2")
            row = rows(con, "b2")["Files/Lidar-Autonavigation-System"]
            self.assertEqual((row["state"], row["note"]), ("duplicate", note_rel))
            self.assertEqual((vault / note_rel).read_text(encoding="utf-8"), before)
            self.assertNotIn(versions.EARLIER, before)

    def test_a_confirmed_fact_the_newer_copy_says_the_same_stays_confirmed(self):
        with home() as (con, _vault, files), tempfile.TemporaryDirectory() as later:
            settings.set("you.name", OWNER)
            project(files / "Lidar-Autonavigation-System", commit=T0)
            with fake_ai({"ingest_doc": reply("Drives a robot by lidar.", "Intermediate")}):
                run(con, [files])
            proj = store.items(con, "project")[0]
            store.confirm(con, proj["id"])
            project(Path(later) / "Lidar-Autonavigation-System-v4", commit=T0 + DAY)
            with fake_ai({"ingest_doc": reply("Drives a robot by lidar.", "Intermediate")}):
                run(con, [later], batch="b2")
            self.assertEqual(store.item(con, proj["id"])["state"], "confirmed")
            self.assertIn("older_version", items.FINAL)
            self.assertIn(versions.EARLIER, engine.KEPT_SECTIONS)


if __name__ == "__main__":
    unittest.main()
