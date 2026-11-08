"""The projects refresh: each project source checked cheaply, only the changed ones read again, a "What changed"
note, the person's edits kept, and going back to the read before.

Projects, pages and GitHub are made up in temporary folders and on 127.0.0.1. The person is Avery Quillon.
"""
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import repos
from support.fake_github import FakeGitHub
from support.fake_site import FakeSite
from support.repos import AVERY
from test_ingest import home
from test_sections_repo import README, me

from jobhunt.core import runner, settings
from jobhunt.ingest import web
from jobhunt.profile import store as profile_store
from jobhunt.sections import actions, pipeline, refresh, revisions, schedule, store

PYPROJECT = '[project]\nname = "tide"\ndependencies = ["fastapi"]\n'


def code(files, name: str = "tide") -> tuple:
    """A made-up code project folder with one commit; (folder, its pyproject path)."""
    folder = repos.make(files / name, {"README.md": README, "pyproject.toml": PYPROJECT, "app.py": "print(1)\n"})
    return folder, folder / "pyproject.toml"


def commit(folder, more: dict, date: str = "2024-09-01T10:00:00Z") -> None:
    """One more made-up commit by Avery."""
    repos.write(folder, more)
    repos.git(folder, "add", "-A", author=AVERY, date=date)
    repos.git(folder, "commit", "-q", "-m", "more", author=AVERY, date=date)


def folder_source(con, folder) -> int:
    """A code project folder added to Projects and read once; its source's id."""
    source_id = pipeline.add_link(con, "project", str(folder), doc_kind="repo")["added"][0]
    pipeline.read(con, source_id)
    return source_id


class Revisions(unittest.TestCase):
    def test_a_new_read_says_what_changed_and_can_go_back_to_the_one_before(self):
        with home() as (con, _, files):
            me()
            folder, _ = code(files)
            source_id = folder_source(con, folder)
            first = store.get(con, source_id)
            commit(folder, {"pyproject.toml": PYPROJECT.replace('"fastapi"', '"fastapi", "django"')})
            pipeline.read(con, source_id)
            second = store.get(con, source_id)
            tools_then = profile_store.items(con, "project", hidden=False)[0]["fields"]["tools"]
            revisions.go_back(con, source_id)
            back = store.get(con, source_id)
            tools_back = profile_store.items(con, "project", hidden=False)[0]["fields"]["tools"]
        self.assertEqual((first["what_changed"], first["can_go_back"]), ("", False))
        self.assertEqual(second["what_changed"], "Tools added: Django. 1 new commit. Last changed 2024-09-01.")
        self.assertTrue(second["can_go_back"])
        self.assertIn("Django", tools_then)
        self.assertNotIn("Django", tools_back)
        self.assertFalse(back["can_go_back"])
        self.assertTrue(back["what_changed"].startswith("Went back to the read of "))

    def test_going_back_with_no_earlier_read_says_so(self):
        with home() as (con, _, files):
            me()
            source_id = folder_source(con, code(files)[0])
            with self.assertRaises(LookupError):
                revisions.go_back(con, source_id)

    def test_what_changed_names_tools_removed_and_says_nothing_on_a_first_read(self):
        self.assertEqual(revisions.words({}, {"tools": ["React"]}), "")
        self.assertEqual(revisions.words({"tools": ["React", "Vue"], "commits": 3}, {"tools": ["React"], "commits": 3}),
                         "Tools removed: Vue.")


class Checks(unittest.TestCase):
    def test_an_unchanged_folder_is_not_read_again_and_a_new_commit_is(self):
        with home() as (con, _, files):
            me()
            folder, _ = code(files)
            source_id = folder_source(con, folder)
            unchanged = refresh.changed(con)
            commit(folder, {"b.py": "2\n"})
            after = refresh.changed(con)
        self.assertEqual((unchanged, after), ([], [source_id]))

    def test_a_folder_with_no_history_is_checked_by_its_newest_file(self):
        with home() as (con, _, files):
            me()
            repos.write(files / "copy", {"README.md": "# Copy\n", "pyproject.toml": PYPROJECT})
            source_id = folder_source(con, files / "copy")
            unchanged = refresh.changed(con)
            repos.write(files / "copy", {"notes.md": "# New\n"})
            after = refresh.changed(con)
        self.assertEqual((unchanged, after), ([], [source_id]))

    def test_a_github_project_is_read_again_only_when_it_was_pushed_to(self):
        with home() as (con, _, _), FakeGitHub() as gh:
            me()
            data = gh.project("averyq", "tide", {"README.md": README})
            source_id = pipeline.add_link(con, "project", "https://github.com/averyq/tide", doc_kind="repo_url")[
                "added"][0]
            pipeline.read(con, source_id)
            unchanged = refresh.changed(con)
            gh.api["/repos/averyq/tide"] = (200, {**data, "pushed_at": "2024-07-01T10:00:00Z"})
            after = refresh.changed(con)
            with mock.patch.object(web, "API_BASE", "http://127.0.0.1:9"):
                offline = refresh.changed(con)
        self.assertEqual((unchanged, after, offline), ([], [source_id], []))

    def test_a_page_is_read_again_only_when_its_words_changed(self):
        with home() as (con, _, _), FakeSite() as site:
            url = site.saved("/work/harbour-posters/", "harbour-posters.html")
            source_id = pipeline.add_link(con, "project", url, doc_kind="web_page")["added"][0]
            pipeline.read(con, source_id)
            unchanged = refresh.changed(con)
            site.page("/work/harbour-posters/", "<html><body><h1>Harbour posters</h1><p>Reprinted.</p></body></html>")
            after = refresh.changed(con)
        self.assertEqual((unchanged, after), ([], [source_id]))

    def test_a_project_added_by_hand_is_read_once_and_never_checked_again(self):
        with home() as (con, _, _):
            source_id = pipeline.add_link(con, "project", "Harbour clean-up day", doc_kind="manual")["added"][0]
            waiting = refresh.changed(con)
            pipeline.read(con, source_id)
            after = refresh.changed(con)
        self.assertEqual((waiting, after), ([source_id], []))


class RefreshAction(unittest.TestCase):
    def test_the_refresh_reads_only_what_changed_and_keeps_the_persons_edits(self):
        with home() as (con, _, files):
            me()
            folder, _ = code(files)
            still, _ = code(files, "harbour")
            source_id, other = folder_source(con, folder), folder_source(con, still)
            card = next(c for c in profile_store.items(con, "project") if c["fields"]["name"] == "tide")
            profile_store.edit(con, card["id"], {**card["fields"], "summary": "My own words about tides."})
            commit(folder, {"pyproject.toml": PYPROJECT.replace('"fastapi"', '"fastapi", "django"')})
            done = runner.run(actions.REFRESH)
            card = profile_store.item(con, card["id"])
            revisions_now = (store.get(con, source_id)["revision"], store.get(con, other)["revision"])
        self.assertEqual(done["result"]["read"], 1)
        self.assertEqual(revisions_now, (2, 1))
        self.assertEqual(card["fields"]["summary"], "My own words about tides.")

    def test_the_refresh_runs_every_six_hours_by_default_and_never_when_off(self):
        now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
        with home():
            default = schedule.run_after(now)
            settings.set("schedule.projects_every", 0)
            off = schedule.run_after(now)
        self.assertEqual(default, now + timedelta(hours=6))
        self.assertIsNone(off)


if __name__ == "__main__":
    unittest.main()
