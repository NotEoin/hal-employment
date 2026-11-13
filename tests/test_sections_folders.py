"""A folder of projects on this computer: each code project or folder of work inside it becomes its own source and card,
and a folder that is one code project itself is one project.

Every folder is made up in a temporary home; the person is Avery Quillon.
"""
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import repos
from test_ingest import home
from test_sections_repo import README, me

from jobhunt.core import runner
from jobhunt.profile import store as profile_store
from jobhunt.sections import actions, folders, pipeline, store


def shelf(files) -> None:
    """Avery's made-up folder of projects: a code project, a poster series, a shelf of folders and junk."""
    repos.make(files / "Projects" / "tide-gauge", {"README.md": README, "app.py": "print(1)\n"})
    repos.write(files / "Projects" / "Harbour posters", {"brief.md": "# Harbour posters\n\nA made-up print series.\n"})
    repos.write(files / "Projects" / "Old years" / "Year 1", {"notes.md": "# Notes\n"})
    repos.write(files / "Projects" / "node_modules" / "x", {"index.js": "1"})
    repos.write(files / "Projects" / ".cache", {"a.md": "1"})
    repos.write(files / "Projects", {"todo.md": "# To do\n"})


class LocalFolder(unittest.TestCase):
    def test_each_project_inside_a_folder_becomes_its_own_source_and_card(self):
        with home() as (con, _, files):
            me()
            shelf(files)
            folder = pipeline.add_link(con, "project", str(files / "Projects"), doc_kind="local_folder")["added"][0]
            done = runner.run(actions.KIND, params={"ids": [folder]})
            children = [s for s in store.listing(con, "project") if s["parent_id"] == folder]
            cards = sorted(c["fields"]["name"] for c in profile_store.items(con, "project", hidden=False))
        self.assertEqual(done["result"]["read"], 3)
        self.assertEqual(sorted(Path(s["locator"]).name for s in children), ["Harbour posters", "tide-gauge"])
        self.assertEqual(cards, ["Harbour posters", "tide-gauge"])

    def test_a_folder_that_is_one_code_project_is_read_as_that_project(self):
        with home() as (con, _, files):
            me()
            repos.make(files / "tide-gauge", {"README.md": README, "app.py": "print(1)\n"})
            folder = pipeline.add_link(con, "project", str(files / "tide-gauge"), doc_kind="local_folder")["added"][0]
            out = pipeline.read(con, folder)
            cards = [c["fields"]["name"] for c in profile_store.items(con, "project", hidden=False)]
        self.assertEqual((out["children"], cards), ([], ["tide-gauge"]))

    def test_a_folder_that_is_gone_says_so_in_plain_words(self):
        with home() as (con, _, files):
            (files / "Gone").mkdir()
            folder = pipeline.add_link(con, "project", str(files / "Gone"), doc_kind="local_folder")["added"][0]
            (files / "Gone").rmdir()
            out = pipeline.read(con, folder)
            error = store.get(con, folder)["error"]
        self.assertEqual(out["state"], "failed")
        self.assertIn("is not a folder Hal can open now", error)

    def test_a_shelf_of_folders_junk_and_hidden_folders_are_not_projects(self):
        with home() as (_, _, files):
            shelf(files)
            got = [p.name for p in folders.projects(files / "Projects")]
        self.assertEqual(got, ["Harbour posters", "tide-gauge"])


if __name__ == "__main__":
    unittest.main()
