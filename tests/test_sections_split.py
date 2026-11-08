"""Monorepos: "Split into projects by folder" makes each part of one code project its own project, with the dates and
share of commits of that folder only.

The monorepo is made up in a temporary folder; Avery Quillon made the web part and Robin Testwell the API.
"""
import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import repos
from support.repos import AVERY, ROBIN
from test_ingest import home
from test_sections_repo import me

from jobhunt.core import runner
from jobhunt.profile import store as profile_store
from jobhunt.sections import actions, pipeline, split, store

ROOT = {"README.md": "# Harbour suite\n", "package.json": '{"name": "harbour-suite"}'}


def monorepo(files):
    """A made-up monorepo: `web/` by Avery in 2024, `packages/api/` by Robin in 2023, and a docs folder."""
    return repos.make(files / "harbour-suite", ROOT, [
        (ROBIN, "2023-03-01T10:00:00Z", {"packages/api/package.json": '{"name": "api", "dependencies": '
                                                                      '{"express": "4"}}',
                                         "packages/api/index.js": "1\n"}),
        (AVERY, "2024-05-01T10:00:00Z", {"web/package.json": '{"name": "web", "dependencies": {"react": "18"}}',
                                         "web/app.jsx": "1\n"}),
        (AVERY, "2024-06-01T10:00:00Z", {"docs/notes.md": "# Notes\n"})])


class Split(unittest.TestCase):
    def test_the_parts_of_a_monorepo_are_its_folders_with_their_own_manifests(self):
        with home() as (_, _, files):
            root = monorepo(files)
            got = [p.relative_to(root).as_posix() for p in split.parts(root)]
        self.assertEqual(got, ["packages/api", "web"])

    def test_each_part_becomes_its_own_project_with_that_folders_commits(self):
        with home() as (con, _, files):
            me()
            folder = monorepo(files)
            source_id = pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0]
            pipeline.read(con, source_id)
            can = store.get(con, source_id)["can_split"]
            new = split.split(con, source_id)
            runner.run(actions.KIND, params={"ids": new})
            cards = {c["fields"]["name"]: c["fields"] for c in profile_store.items(con, "project", hidden=False)}
            parts = [store.get(con, i) for i in new]
        self.assertTrue(can)
        self.assertEqual(set(cards), {"harbour-suite", "web"})      # none of the API's commits are hers
        self.assertEqual((cards["web"]["role"], cards["web"]["period"]), ("sole author", "2024-05"))
        self.assertEqual([p["not_yours"] for p in parts], [True, False])
        self.assertIn("React", cards["web"]["tools"])
        self.assertNotIn("Express", cards["web"]["tools"])
        self.assertTrue(all(p["parent_id"] == source_id and not p["can_split"] for p in parts))

    def test_splitting_again_adds_nothing_twice(self):
        with home() as (con, _, files):
            me()
            source_id = pipeline.add_link(con, "project", str(monorepo(files)), doc_kind="repo")["added"][0]
            pipeline.read(con, source_id)
            first = split.split(con, source_id)
            again = split.split(con, source_id)
        self.assertEqual((len(first), again), (2, []))

    def test_a_project_with_no_parts_or_that_is_not_code_cannot_be_split(self):
        with home() as (con, _, files):
            me()
            folder = repos.make(files / "tide", {"README.md": "# Tide\n", "app.py": "1\n"})
            source_id = pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0]
            manual = pipeline.add_link(con, "project", "Harbour clean-up day", doc_kind="manual")["added"][0]
            with self.assertRaises(ValueError):
                split.split(con, source_id)
            with self.assertRaises(ValueError):
                split.split(con, manual)


if __name__ == "__main__":
    unittest.main()
