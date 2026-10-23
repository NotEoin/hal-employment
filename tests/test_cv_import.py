"""The one-time import of today's CV (data task `cv.import_v1`), on a made-up website `cv.json`
with made-up overrides and snapshots in the importer's folder.
"""
import json
import os
import unittest

import support  # noqa: F401 - the temporary home, before any jobhunt import
from test_cv_store import cv_home

from jobhunt import cv, db
from jobhunt.core.db import tasks
from jobhunt.cvstudio import import_v1, legacy_actions, model, store

SITE = {"_comment": ["Made-up CV for tests"], "title": "Computing graduate",
        "contact": {"location": "Exampleton", "github": "github.com/made-up", "site": "made-up.example"},
        "summary": "Graduate who builds small, careful tools.", "moreProjects": "Also: a chess clock",
        "closing": "Thanks.",
        "education": [{"qualification": "BSc (Hons) Computing", "grade": "First",
                       "meta": "Made-up University | 2022 - 2026",
                       "detail": "Dissertation on cache-friendly trees.", "pdfModulesMin": 70,
                       "modules": [{"year": "Final year", "mark": "76",
                                    "rows": [["Compilers", 81], ["Networks", 72], ["Ethics", 64]]}]}],
        "skills": [["Languages", ["Python", "C"]], ["Tools", ["Git", "Make"]]],
        "projects": [{"slug": "tree", "name": "Tree index", "stack": "C", "body": "A B-tree in 2,000 lines."},
                     {"slug": "bot", "name": "Rota bot", "stack": "Python", "body": "Plans a cafe rota."}],
        "experience": [{"role": "Lab assistant", "org": "Made-up University", "dates": "Oct 2024 - Jun 2025",
                        "body": "- Helped 40 first-years\n- Marked weekly labs"}],
        "additional": [["Interests", "Orienteering"]]}
SNAP_OLD = [{"field": "summary", "value": "Graduate who builds tools."}]
SNAP_NEW = [{"field": "summary", "value": "Graduate who builds small tools."},
            {"section": "projects", "key": "Rota bot", "field": "hidden", "value": True}]
NOW = [{"field": "summary", "value": "Graduate who builds small, careful tools that people use."},
       {"field": "pdfProjects", "value": 1},
       {"section": "projects", "key": "Tree index", "field": "name", "value": "Tree index in C"},
       {"section": "projects", "key": "Tree index", "field": "body", "value": "A B-tree in 2,000 lines, with tests."},
       {"section": "education", "key": "BSc (Hons) Computing", "field": "pdfModulesMin", "value": 75},
       {"section": "additional", "key": "Interests", "field": 1, "value": "Orienteering and chess"},
       {"field": "skills", "value": [["Languages", ["Python", "C", "SQL"]], ["Tools", ["Git"]]]}]
SNAP_TIMES = {"cv-overrides.before-2025-09-01.json": 1756717200, "cv-overrides.before-2025-10-01.json": 1759309200}


def lay_out(folder, site=SITE, overrides=NOW, snapshots=True) -> None:
    """Writes the importer's copy of the old CV files into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "site-cv.json").write_text(json.dumps(site), encoding="utf-8")
    (folder / "cv-overrides.json").write_text(json.dumps(overrides), encoding="utf-8")
    if snapshots:
        for (name, when), snap in zip(SNAP_TIMES.items(), (SNAP_OLD, SNAP_NEW), strict=True):
            (folder / name).write_text(json.dumps(snap), encoding="utf-8")
            os.utime(folder / name, (when, when))


class OwnerImport(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(cv_home())
        lay_out(import_v1.folder())
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def test_todays_cv_imports_with_nothing_lost(self):
        out = import_v1.import_into(self.con)
        self.assertEqual(out["not_found"], [])
        self.assertEqual(len(out["versions"]), 4)
        versions = store.versions()
        self.assertEqual([v["what"] for v in versions][:3], [
            "With the changes you made in Hal", "Your saved copy from 2025-10-01", "Your saved copy from 2025-09-01"])
        self.assertTrue(versions[-1]["what"].startswith("Your CV from your website, imported "))
        self.assertEqual((versions[-1]["by"], versions[-1]["baseline"]), ("baseline", True))
        self.assertTrue(versions[0]["current"])
        # Hal's copy is what the old code made of the website CV and its overrides, field for field.
        old = import_v1.printable(import_v1.apply_overrides(SITE, NOW))
        self.assertEqual(import_v1.printable(model.to_v1(store.current()[1], {"contact": SITE["contact"]})), old)
        self.assertEqual(cv.load()["projects"][0]["body"], "A B-tree in 2,000 lines, with tests.")
        self.assertEqual(cv.load()["education"][0]["pdfModulesMin"], 75)
        self.assertEqual(model.not_found(import_v1.printable(SITE), store.get(versions[-1]["id"])), [])
        d = store.doc()
        self.assertEqual((d["type_id"], d["look_id"], d["route"], d["send"]),
                         ("technical_grad", "classic", "improve", "hal_copy"))
        self.assertEqual(store.current()[1].extras["from"], "site-cv.json+overrides")

    def test_a_renamed_entry_keeps_its_edits(self):
        import_v1.import_into(self.con)
        first = store.get(store.versions()[-1]["id"])
        now = store.current()[1]
        old_id = first.sections[2].entries[0].id
        renamed = next(e for e in now.sections[2].entries if e.id == old_id)
        self.assertEqual((renamed.heading, renamed.text), ("Tree index in C", "A B-tree in 2,000 lines, with tests."))
        kinds = {(c["kind"], c["target"].rsplit("/", 1)[-1]) for c in model.diff(first, now)
                 if c["target"].startswith(f"s:projects/e:{old_id}")}
        self.assertEqual(kinds, {("Reworded", "heading"), ("Reworded", "text")})
        # Renamed again in the classic screens, it is still the same entry, edits and all.
        mine = cv.for_editing()
        mine["projects"][0]["name"] = "Tree index, a B-tree in C"
        cv.save_base(mine)
        again = store.current()[1].sections[2].entries[0]
        self.assertEqual((again.id, again.heading, again.text),
                         (old_id, "Tree index, a B-tree in C", "A B-tree in 2,000 lines, with tests."))

    def test_the_hidden_project_of_a_snapshot_is_kept_hidden_in_that_version(self):
        import_v1.import_into(self.con)
        snap = store.get(store.versions()[1]["id"])
        self.assertTrue(snap.sections[2].entries[1].hidden)
        self.assertNotIn("Rota bot", [p["name"] for p in model.to_v1(snap, {})["projects"] if not p.get("hidden")])

    def test_running_it_again_does_nothing(self):
        import_v1.import_into(self.con)
        again = import_v1.import_into(self.con)
        self.assertEqual((again["versions"], again["skipped"]), ([], "Your CV was imported before."))
        self.assertEqual(len(store.versions()), 4)

    def test_the_store_imports_on_first_use_so_nothing_starts_from_a_blank_cv(self):
        self.assertEqual(cv.load()["title"], "Computing graduate")
        self.assertEqual(len(store.versions()), 4)

    def test_the_data_task_is_registered_and_runs_once(self):
        self.assertIn(import_v1.NAME, tasks.TASKS)
        self.assertIs(tasks.TASKS[import_v1.NAME].fn, import_v1.run)
        self.assertTrue(legacy_actions.add_source)
        done = tasks.run_now(import_v1.NAME)
        self.assertEqual(done["state"], "done")
        self.assertEqual(len(store.versions()), 4)
        self.assertEqual(tasks.state(self.con, import_v1.NAME), "done")


class FriendImport(unittest.TestCase):
    def test_the_cv_from_the_old_hal_becomes_version_1(self):
        self.enterContext(cv_home())
        folder = import_v1.folder()
        folder.mkdir(parents=True)
        built = {k: v for k, v in SITE.items() if k != "_comment"}
        (folder / "cv-built.json").write_text(json.dumps({"cv": built, "flags": []}), encoding="utf-8")
        (folder / "cv-built-overrides.json").write_text(json.dumps([{"field": "title", "value": "History graduate"}]))
        (folder / "cv-origin").write_text("built\n")
        con = db.connect()
        self.addCleanup(con.close)
        out = import_v1.import_into(con)
        self.assertEqual(out["not_found"], [])
        self.assertEqual([v["what"] for v in store.versions()],
                         ["With the changes you made in Hal", "Your CV from your old Hal"])
        self.assertEqual(store.doc()["type_id"], "humanities_grad")
        self.assertEqual(store.current()[1].headline, "History graduate")

    def test_with_nothing_to_import_the_task_says_so(self):
        self.enterContext(cv_home())
        con = db.connect()
        self.addCleanup(con.close)
        out = import_v1.import_into(con)
        self.assertEqual(import_v1.summary(out), "There was no CV from before to import.")


if __name__ == "__main__":
    unittest.main()
