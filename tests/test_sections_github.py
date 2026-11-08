"""GitHub sources of Projects: one project link cloned and read, an account's projects as their own sources, private
projects only with a token, back-off on GitHub's limit, and reading from the last clone offline.

GitHub is made up (`support.fake_github`): its API runs on 127.0.0.1 and its projects are cloned from a temporary
folder. The person is Avery Quillon, `averyq` on the made-up GitHub; Robin Testwell is a made-up classmate.
"""
import time
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.fake_github import FakeGitHub, repo_json
from support.repos import AVERY, ROBIN
from test_ingest import home
from test_sections_repo import me

from jobhunt.core import runner
from jobhunt.ingest import web
from jobhunt.profile import store as profile_store
from jobhunt.sections import actions, github, pipeline, store

TIDE = {"README.md": "# Tide gauge\n\nReads made-up tides.\n", "pyproject.toml": '[project]\nname = "tide"\n'
        'dependencies = ["fastapi"]\n'}
TOKEN = "made-up-token-for-tests"


def link(con, url: str) -> int:
    """A project link added to Projects; its source's id."""
    return pipeline.add_link(con, "project", url, doc_kind="repo_url")["added"][0]


class RepoLink(unittest.TestCase):
    def test_a_project_link_is_cloned_partially_into_the_data_folder_and_read_with_githubs_details(self):
        with home() as (con, _, _), FakeGitHub() as gh:
            me()
            gh.project("averyq", "tide", TIDE, topics=["tides"], license={"spdx_id": "MIT"})
            source_id = link(con, "https://github.com/averyq/tide")
            out = pipeline.read(con, source_id)
            card = profile_store.items(con, "project", hidden=False)[0]
            clone = github.clone_dir("averyq", "tide")
            config = (clone / ".git" / "config").read_text()
            shallow = (clone / ".git" / "shallow").exists() or "promisor" in config
        self.assertEqual(out["state"], "read")
        self.assertTrue(str(clone).endswith("projects/averyq/tide"))
        self.assertEqual((card["fields"]["name"], card["fields"]["link"]), ("tide", "https://github.com/averyq/tide"))
        self.assertEqual(card["fields"]["role"], "sole author")
        self.assertIn("FastAPI", card["fields"]["tools"])
        self.assertTrue(shallow)

    def test_offline_a_project_is_read_from_its_last_clone(self):
        with home() as (con, _, _), FakeGitHub() as gh:
            me()
            gh.project("averyq", "tide", TIDE)
            source_id = link(con, "https://github.com/averyq/tide")
            pipeline.read(con, source_id)
            with mock.patch.object(web, "API_BASE", "http://127.0.0.1:9"), \
                    mock.patch.object(github, "CLONE_BASE", "file:///nowhere-at-all"):
                again = pipeline.read(con, source_id)
            cards = profile_store.items(con, "project", hidden=False)
        self.assertEqual(again["state"], "read")
        self.assertEqual([c["fields"]["name"] for c in cards], ["tide"])

    def test_a_private_project_is_read_only_with_a_token_which_never_lands_in_the_clone(self):
        def private(headers: dict):
            ok = headers.get("Authorization") == f"Bearer {TOKEN}"
            return (200, repo_json("averyq", "diary", private=True)) if ok else (404, {"message": "Not Found"})

        with home() as (con, _, _), FakeGitHub() as gh:
            me()
            gh.project("averyq", "diary", TIDE)
            gh.api["/repos/averyq/diary"] = private
            source_id = link(con, "https://github.com/averyq/diary")
            without = pipeline.read(con, source_id)
            error = store.get(con, source_id)["error"]
            with mock.patch.object(web, "_github_token", return_value=TOKEN):
                with_token = pipeline.read(con, source_id)
            config = (github.clone_dir("averyq", "diary") / ".git" / "config").read_text()
        self.assertEqual((without["state"], with_token["state"]), ("failed", "read"))
        self.assertIn("GitHub token", error)
        self.assertNotIn(TOKEN, config)

    def test_a_used_up_limit_that_resets_soon_is_waited_out_once(self):
        calls = []

        def limited(_headers: dict):
            calls.append(1)
            return (403, {"message": "rate limit"}) if len(calls) == 1 else (200, repo_json("averyq", "tide"))

        waited = []
        with home() as (con, _, _), FakeGitHub() as gh, mock.patch.object(github, "_sleep", waited.append):
            me()
            gh.project("averyq", "tide", TIDE)
            gh.api["/repos/averyq/tide"] = limited
            gh.headers[403] = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 5)}
            out = pipeline.read(con, link(con, "https://github.com/averyq/tide"))
        self.assertEqual(out["state"], "read")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(waited), 1)
        self.assertLessEqual(waited[0], github.BACKOFF_MAX_S)

    def test_a_link_that_is_not_a_github_project_is_refused_in_plain_words(self):
        with self.assertRaises(ValueError):
            github.parse_url("https://example.org/averyq/tide")
        with self.assertRaises(ValueError):
            github.parse_url("https://github.com/averyq")


class Account(unittest.TestCase):
    def account(self, gh: FakeGitHub) -> None:
        """Avery's made-up account: two own projects, a fork she changed and a fork she did not."""
        gh.api["/users/averyq"] = (200, {"login": "averyq"})
        own = [gh.project("averyq", "tide", TIDE), gh.project("averyq", "harbour", {"README.md": "# Harbour\n"}),
               gh.project("averyq", "lib-fixed", {"README.md": "# Lib\n"}, fork=True,
                          commits=[(ROBIN, "2023-01-01T00:00:00Z", {}), (AVERY, "2023-02-01T00:00:00Z", {"f": "1"})]),
               gh.project("averyq", "lib-copy", {"README.md": "# Copy\n"}, fork=True,
                          commits=[(ROBIN, "2023-01-01T00:00:00Z", {})])]
        gh.api["/users/averyq/repos"] = (200, own)
        gh.api["/repos/averyq/lib-fixed/commits"] = (200, [{"sha": "abc"}])
        gh.api["/repos/averyq/lib-copy/commits"] = (200, [])

    def test_an_account_gives_each_of_the_persons_projects_as_its_own_source_and_card(self):
        with home() as (con, _, _), FakeGitHub() as gh:
            me()
            self.account(gh)
            account = pipeline.add_link(con, "project", "averyq", doc_kind="github_account")["added"][0]
            done = runner.run(actions.KIND, params={"ids": [account]})
            listed = store.listing(con, "project")
            cards = sorted(c["fields"]["name"] for c in profile_store.items(con, "project", hidden=False))
        self.assertEqual(done["result"]["read"], 4)
        children = [s for s in listed if s["parent_id"] == account]
        self.assertEqual(sorted(s["locator"].rsplit("/", 1)[-1] for s in children), ["harbour", "lib-fixed", "tide"])
        self.assertEqual(cards, ["harbour", "lib-fixed", "tide"])

    def test_a_project_removed_from_an_account_stays_out_when_the_account_is_read_again(self):
        with home() as (con, _, _), FakeGitHub() as gh:
            me()
            self.account(gh)
            account = pipeline.add_link(con, "project", "averyq", doc_kind="github_account")["added"][0]
            runner.run(actions.KIND, params={"ids": [account]})
            harbour = next(s for s in store.listing(con, "project") if s["locator"].endswith("/harbour"))
            pipeline.remove(con, harbour["id"])
            gone = github.clone_dir("averyq", "harbour").exists()
            again = pipeline.read(con, account)
            names = sorted(s["locator"].rsplit("/", 1)[-1] for s in store.listing(con, "project") if s["parent_id"])
        self.assertFalse(gone)
        self.assertEqual(again["children"], [])
        self.assertEqual(names, ["lib-fixed", "tide"])

    def test_removing_an_account_removes_its_projects_their_cards_and_clones(self):
        with home() as (con, _, _), FakeGitHub() as gh:
            me()
            self.account(gh)
            account = pipeline.add_link(con, "project", "averyq", doc_kind="github_account")["added"][0]
            runner.run(actions.KIND, params={"ids": [account]})
            pipeline.remove(con, account)
            left = (store.listing(con, "project"), profile_store.items(con, "project", hidden=False))
            clone = github.clone_dir("averyq", "tide").exists()
        self.assertEqual(left, ([], []))
        self.assertFalse(clone)

    def test_with_a_token_an_organisations_project_counts_only_where_the_person_has_commits(self):
        with home(), FakeGitHub() as gh, mock.patch.object(web, "_github_token", return_value=TOKEN):
            me()
            gh.api["/user"] = (200, {"login": "averyq"})
            mine = gh.project("harbour-club", "site", {"README.md": "# Site\n"})
            other = gh.project("harbour-club", "accounts", {"README.md": "# Accounts\n"})
            gh.api["/user/repos"] = (200, [gh.project("averyq", "tide", TIDE), mine, other])
            gh.api["/repos/harbour-club/site/commits"] = (200, [{"sha": "abc"}])
            gh.api["/repos/harbour-club/accounts/commits"] = (200, [])
            got = [r["name"] for r in github.account_repos("averyq")]
        self.assertEqual(got, ["tide", "site"])


if __name__ == "__main__":
    unittest.main()
