"""The updater: pull, sync, migrate, restart, roll back, hold, go back, refuse; against local bare repos.

Every test builds an "origin" bare repository, an owner's working clone that pushes to it, and the installed
clone the updater works on. uv, the migration and the server are fakes on `FakeWorld`; git is real.
"""
import ast
import contextlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - must come before jobhunt
from support.api import HalServer

from jobhunt.core import paths, update_routes, updates
from jobhunt.core.platform import linux as linux_platform
from jobhunt.core.platform import windows as windows_platform

GIT_TIMEOUT_S = 30
EXIT_WAIT_S = 5      # the server ends itself half a second after replying
POLL_S = 0.05
INIT = '__version__ = "{v}"\n'
NO_KEY = "# no key yet\n"


def git(cwd: Path, *args: str) -> str:
    """Runs git in `cwd` and returns its output; fails the test on an error."""
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
                          check=False)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout.strip()


class Repos:
    """origin (bare), the owner's working clone `dev`, and the installed clone `app`."""

    def __init__(self, root: Path) -> None:
        self.origin, self.dev, self.app = root / "origin.git", root / "dev", root / "app"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(root, "init", "-q", "-b", "main", str(self.dev))
        git(self.dev, "remote", "add", "origin", str(self.origin))
        self.first = self.commit({"jobhunt/__init__.py": INIT.format(v="2.0.1"),
                                  "scripts/install/allowed_signers": NO_KEY, "README.md": "Hal\n"}, "docs: first")
        git(root, "clone", "-q", str(self.origin), str(self.app))

    def commit(self, files: dict[str, str], message: str, *, key: Path | None = None) -> str:
        """Writes `files` in the owner's clone, commits (signed with `key` when given) and pushes."""
        for name, text in files.items():
            path = self.dev / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        git(self.dev, "add", "-A")
        if key is None:
            git(self.dev, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
        else:
            git(self.dev, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key.as_posix()}", "commit", "-q", "-S",
                "-m", message)
        git(self.dev, "push", "-q", "origin", "main")
        return git(self.dev, "rev-parse", "HEAD")

    def head(self) -> str:
        """The installed clone's commit."""
        return git(self.app, "rev-parse", "HEAD")


class FakeWorld(updates.World):
    """Real git; fake uv, migration and server."""

    def __init__(self, inst: updates.Install, *, fail: str | None = None, **kw) -> None:
        super().__init__(inst, **kw)
        self.fail, self.calls, self.envs = fail, [], []
        self.running: str | None = None
        self.filling = False
        self.syncs = 0

    def run(self, args, *, timeout, check=True):
        self.envs.append((list(args), self.env()))
        return super().run(args, timeout=timeout, check=check)

    def sync(self) -> None:
        self.calls.append("sync")
        self.syncs += 1
        if self.fail == "sync" and self.syncs == 1:
            raise updates.UpdateError("Hal's update stopped.", "uv sync: a package was missing")

    def migrate(self) -> None:
        self.calls.append("migrate")
        if self.fail == "migrate":
            with contextlib.closing(sqlite3.connect(paths.data_dir() / "jobs.db")) as con:
                con.execute("INSERT INTO notes VALUES ('half-migrated')")
                con.commit()
            raise updates.UpdateError("Hal's update stopped.", "migration 0018 failed")

    def stop_server(self) -> None:
        self.calls.append("stop")
        self.running = None

    def start_server(self) -> None:
        self.calls.append("start")
        self.running = git(self.inst.code_dir, "rev-parse", "HEAD")

    def running_commit(self):
        return self.running

    def apply_running(self) -> bool:
        return self.filling


class UpdaterCase(unittest.TestCase):
    """A temporary home with a jobs.db, the three repositories and a user install record."""

    def setUp(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="hal-upd-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        empty = tmp / "gitconfig"
        empty.write_text("", encoding="utf-8")
        env = {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Rowan Ellery",
               "GIT_AUTHOR_EMAIL": "rowan@example.invalid", "GIT_COMMITTER_NAME": "Rowan Ellery",
               "GIT_COMMITTER_EMAIL": "rowan@example.invalid"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        home = tmp / "home"
        stack = contextlib.ExitStack()
        stack.enter_context(paths.set_override(home))
        self.addCleanup(stack.close)
        paths.ensure()
        with contextlib.closing(sqlite3.connect(paths.data_dir() / "jobs.db")) as con:
            con.execute("CREATE TABLE notes (text TEXT)")
            con.execute("INSERT INTO notes VALUES ('before the update')")
            con.commit()
        self.repos = Repos(tmp)
        self.inst = updates.Install(kind="user", code=str(self.repos.app), git="git", uv="uv", extras=["browser"])
        updates.write_install(self.inst)

    def world(self, **kw) -> FakeWorld:
        return FakeWorld(updates.read_install(), **kw)

    @staticmethod
    def notes() -> list[str]:
        with contextlib.closing(sqlite3.connect(paths.data_dir() / "jobs.db")) as con:
            return [r[0] for r in con.execute("SELECT text FROM notes ORDER BY rowid")]


class Updating(UpdaterCase):
    def test_an_update_pulls_syncs_migrates_and_restarts(self):
        new = self.repos.commit({"jobhunt/__init__.py": INIT.format(v="2.0.2")}, "cv: a clearer heading")
        w = self.world()
        result = updates.update(w)
        self.assertEqual(result["state"], "updated")
        self.assertEqual(self.repos.head(), new)
        self.assertEqual(w.calls, ["stop", "sync", "migrate", "start"])
        self.assertEqual(result["words"], "Updated Hal from 2.0.1 to 2.0.2")
        self.assertEqual(result["whats_new"], ["cv: a clearer heading"])
        self.assertFalse(result["recorded"])
        backups = list(paths.backups_dir().glob("jobs-*Z-before-update-*.db"))
        self.assertEqual(len(backups), 1)
        self.assertIn(self.repos.first[:7], backups[0].name)
        self.assertTrue(updates.can_go_back())

    def test_nothing_new_says_hal_is_up_to_date(self):
        w = self.world()
        self.assertEqual(updates.update(w)["words"], "Hal is up to date.")
        self.assertEqual(w.calls, [])

    def test_a_failed_sync_goes_back_to_the_previous_commit_and_says_so(self):
        new = self.repos.commit({"README.md": "Hal 2\n"}, "docs: new words")
        w = self.world(fail="sync")
        result = updates.update(w)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["words"],
                         "The update did not work, so Hal went back to the version you had. Nothing was lost.")
        self.assertIn("a package was missing", result["details"])
        self.assertEqual(self.repos.head(), self.repos.first)
        self.assertEqual(w.calls, ["stop", "sync", "sync", "start"])
        self.assertEqual(result["held"], new)
        self.assertEqual(w.running, self.repos.first)

    def test_a_failed_migration_restores_the_backup(self):
        self.repos.commit({"README.md": "Hal 2\n"}, "core: new records")
        result = updates.update(self.world(fail="migrate"))
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.notes(), ["before the update"])
        self.assertEqual(self.repos.head(), self.repos.first)

    def test_a_failed_commit_is_held_and_not_offered_again(self):
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: new words")
        updates.update(self.world(fail="sync"))
        w = self.world()
        found = updates.check(w)
        self.assertFalse(found["available"])
        self.assertEqual(found["words"], "Hal is up to date.")
        self.assertEqual(updates.update(w)["state"], "up_to_date")
        self.assertEqual(self.repos.head(), self.repos.first)
        newer = self.repos.commit({"README.md": "Hal 3\n"}, "docs: fixed words")
        self.assertTrue(updates.check(self.world())["available"])
        self.assertEqual(updates.update(self.world())["state"], "updated")
        self.assertEqual(self.repos.head(), newer)

    def test_an_update_waits_while_hal_fills_a_form(self):
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: new words")
        w = self.world()
        w.filling = True
        result = updates.update(w)
        self.assertEqual(result["state"], "waiting")
        self.assertEqual(result["words"], "Hal is filling in a form. It will update when it has finished.")
        self.assertTrue(updates.waiting())
        self.assertEqual(w.calls, [])
        self.assertEqual(self.repos.head(), self.repos.first)
        w.filling = False
        self.assertEqual(updates.update(w)["state"], "updated")
        self.assertFalse(updates.waiting())

    def test_go_back_restores_the_backup_and_the_previous_commit(self):
        self.repos.commit({"jobhunt/__init__.py": INIT.format(v="2.0.2")}, "apply: steadier forms")
        updates.update(self.world())
        with contextlib.closing(sqlite3.connect(paths.data_dir() / "jobs.db")) as con:
            con.execute("INSERT INTO notes VALUES ('an application made after the update')")
            con.commit()
        w = self.world()
        result = updates.go_back(w)
        self.assertEqual(result["state"], "went_back")
        self.assertEqual(result["words"], "Went back to Hal 2.0.1")
        self.assertEqual(self.repos.head(), self.repos.first)
        self.assertEqual(self.notes(), ["before the update"])
        self.assertEqual(w.calls, ["stop", "sync", "start"])
        self.assertFalse(updates.can_go_back())

    def test_go_back_needs_an_update_first(self):
        self.assertEqual(updates.go_back(self.world())["words"], "There is no earlier version to go back to.")

    def test_local_changes_stop_an_update_in_plain_words(self):
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: new words")
        (self.repos.app / "README.md").write_text("changed by hand\n", encoding="utf-8")
        w = self.world()
        result = updates.update(w)
        self.assertEqual(result["words"], "Hal's files were changed by hand, so it cannot update by itself. "
                                          "Ask the person who set Hal up.")
        self.assertEqual(w.calls, [])

    def test_a_file_the_person_added_does_not_stop_an_update(self):
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: new words")
        (self.repos.app / "my-notes.txt").write_text("mine\n", encoding="utf-8")
        self.assertEqual(updates.update(self.world())["state"], "updated")
        self.assertTrue((self.repos.app / "my-notes.txt").exists())

    def test_an_update_that_is_not_a_fast_forward_changes_nothing(self):
        (self.repos.app / "extra.txt").write_text("x\n", encoding="utf-8")
        git(self.repos.app, "add", "extra.txt")
        git(self.repos.app, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "local")
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: new words")
        w = self.world()
        self.assertEqual(updates.update(w)["words"], "This update needs the person who set Hal up.")
        self.assertEqual(w.calls, [])

    def test_background_fetches_never_prompt_for_a_sign_in(self):
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: new words")
        quiet = self.world(interactive=False)
        updates.check(quiet)
        fetches = [env for args, env in quiet.envs if "fetch" in args]
        self.assertTrue(fetches)
        for env in fetches:
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(env["GCM_INTERACTIVE"], "never")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_TERMINAL_PROMPT", None)
            os.environ.pop("GCM_INTERACTIVE", None)
            pressed = self.world(interactive=True)
            updates.check(pressed)
            env = next(env for args, env in pressed.envs if "fetch" in args)
            self.assertNotIn("GCM_INTERACTIVE", env)
            self.assertNotIn("GIT_TERMINAL_PROMPT", env)

    def test_a_missing_repository_is_said_in_plain_words(self):
        w = self.world()
        failed = subprocess.CompletedProcess([], 128, "", "remote: Repository not found.\nfatal: repository "
                                                          "'https://github.com/a/b.git/' not found")
        with mock.patch.object(w, "git", return_value=failed):
            with self.assertRaises(updates.UpdateError) as e:
                updates.fetch(w)
        self.assertIn("no longer has access to Hal's files", e.exception.words)
        refused = subprocess.CompletedProcess([], 128, "", "fatal: Authentication failed for 'https://github.com/'")
        with mock.patch.object(w, "git", return_value=refused):
            with self.assertRaises(updates.UpdateError) as e:
                updates.fetch(w)
        self.assertEqual(e.exception.words, "Hal cannot reach its updates on GitHub. Sign in to GitHub again.")


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is needed to make SSH commit signatures")
class Signatures(UpdaterCase):
    def key(self, name: str) -> tuple[Path, str]:
        """A new ed25519 key and its allowed-signers line."""
        folder = Path(tempfile.mkdtemp(prefix="hal-key-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = folder / name
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)], check=True,
                       timeout=GIT_TIMEOUT_S, capture_output=True)
        public = (folder / f"{name}.pub").read_text(encoding="utf-8").strip()
        return path, f'rowan@example.invalid namespaces="git" {public}\n'

    def owner_key_installed(self) -> Path:
        key, line = self.key("owner")
        self.repos.commit({"scripts/install/allowed_signers": NO_KEY + line}, "build: the owner's signing key", key=key)
        self.assertEqual(updates.update(self.world())["state"], "updated")
        return key

    def test_an_update_with_a_commit_not_signed_by_the_owner_is_refused(self):
        self.owner_key_installed()
        installed = self.repos.head()
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: an unsigned change")
        w = self.world()
        result = updates.update(w)
        self.assertEqual(result["words"], "This update was not made by the person who set Hal up, so Hal did "
                                          "not install it. Ask them.")
        self.assertEqual(self.repos.head(), installed)
        self.assertEqual(w.calls, [])

    def test_a_push_that_changes_allowed_signers_cannot_bring_its_own_key(self):
        self.owner_key_installed()
        installed = self.repos.head()
        other, line = self.key("stranger")
        self.repos.commit({"scripts/install/allowed_signers": NO_KEY + line}, "build: someone else's key", key=other)
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: signed by the stranger", key=other)
        self.assertEqual(updates.update(self.world())["state"], "not_owner")
        self.assertEqual(self.repos.head(), installed)

    def test_every_commit_signed_by_the_owner_installs(self):
        key = self.owner_key_installed()
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: one", key=key)
        new = self.repos.commit({"README.md": "Hal 3\n"}, "docs: two", key=key)
        result = updates.update(self.world())
        self.assertEqual(result["state"], "updated")
        self.assertTrue(result["signed"])
        self.assertEqual(self.repos.head(), new)
        self.assertEqual(updates.signing(self.world())["words"],
                         "Every update is checked: it must be signed by the person who set Hal up.")

    def test_once_a_key_is_committed_every_later_commit_in_the_same_update_must_verify(self):
        # an install with no key yet would otherwise take the key commit and an unsigned one after it
        key, line = self.key("owner")
        installed = self.repos.head()
        self.repos.commit({"scripts/install/allowed_signers": NO_KEY + line}, "build: the owner's signing key", key=key)
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: an unsigned change after the key")
        self.assertEqual(updates.update(self.world())["state"], "not_owner")
        self.assertEqual(self.repos.head(), installed)

    def test_signed_commits_after_a_new_key_install_in_the_same_update(self):
        key, line = self.key("owner")
        self.repos.commit({"scripts/install/allowed_signers": NO_KEY + line}, "build: the owner's signing key", key=key)
        new = self.repos.commit({"README.md": "Hal 2\n"}, "docs: signed after the key", key=key)
        result = updates.update(self.world())
        self.assertEqual(result["state"], "updated")
        self.assertFalse(result["signed"])       # the key commit itself had nothing to be checked against
        self.assertEqual(self.repos.head(), new)

    def test_the_daily_check_does_not_offer_a_commit_not_signed_by_the_owner(self):
        self.owner_key_installed()
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: an unsigned change")
        with self.assertRaises(updates.UpdateError) as e:
            updates.check_and_keep(self.world())
        self.assertEqual(e.exception.state, "not_owner")
        self.assertFalse(updates.last_check()["available"])

    def test_updates_install_unchecked_while_allowed_signers_holds_no_key(self):
        self.repos.commit({"README.md": "Hal 2\n"}, "docs: unsigned while signing is not set up")
        w = self.world()
        self.assertEqual(updates.signing(w)["words"],
                         "Updates are not yet checked for the owner's signature: they have not set up signing.")
        result = updates.update(w)
        self.assertEqual(result["state"], "updated")
        self.assertFalse(result["signed"])


class SignIn(UpdaterCase):
    @unittest.skipIf(os.name == "nt", "Windows signs in through Git Credential Manager, not a terminal")
    def test_sign_in_again_opens_the_github_sign_in_in_a_window_on_linux(self):
        from jobhunt.core import runner, update_actions  # noqa: F401 - registers the kinds

        tools = self.repos.app.parent / "tools"
        tools.mkdir()
        (tools / "gh").write_text("#!/bin/sh\n", encoding="utf-8")
        updates.write_install(updates.Install(**{**updates.asdict(self.inst), "tools": str(tools)}))
        fake = mock.Mock()
        with mock.patch("jobhunt.core.platform.current", return_value=fake):
            done = runner.run("updates.sign_in")
        self.assertEqual(done["state"], "done")
        args, title = fake.open_console.call_args[0]
        self.assertEqual(args[:3], [str(tools / "gh"), "auth", "login"])
        self.assertEqual(title, "Sign in to GitHub")


class Spawning(unittest.TestCase):
    INST = updates.Install(kind="user", code=str(Path("/opt/hal/app")), uv=str(Path("/opt/hal/tools/uv")),
                           python_dir=str(Path("/opt/hal/tools/python")), uv_cache=str(Path("/opt/hal/tools/c")))

    def test_the_in_app_updater_runs_outside_the_servers_unit(self):
        started = []

        def popen(args, **kw):
            started.append((args, kw))
            return mock.Mock(pid=4242)

        lin = linux_platform.LinuxPlatform(popen=popen, which=lambda name: f"/usr/bin/{name}")
        lin.spawn_detached(update_routes.updater_argv(self.INST, system="linux"), unit=update_routes.UPDATE_UNIT)
        args, _ = started[-1]
        self.assertEqual(args[:5], ["systemd-run", "--user", "--unit=hal-employment-update", "--collect", "--quiet"])
        tail = args[args.index("env"):]
        self.assertIn(f"UV_PYTHON_INSTALL_DIR={self.INST.python_dir}", tail)
        run = tail[tail.index(self.INST.uv):]
        self.assertEqual(run[:5], [self.INST.uv, "run", "--no-project", "--python", "3.13"])
        self.assertEqual(Path(run[5]), Path(self.INST.code) / "scripts" / "updater.py")

        win = windows_platform.WindowsPlatform(popen=popen, env={})
        win.spawn_detached(update_routes.updater_argv(self.INST, system="windows"), unit=update_routes.UPDATE_UNIT)
        args, kw = started[-1]
        self.assertEqual(args[:5], [self.INST.uv, "run", "--no-project", "--python", "3.13"])
        flags = kw["creationflags"]
        for flag in (windows_platform.DETACHED_PROCESS, windows_platform.CREATE_NEW_PROCESS_GROUP,
                     windows_platform.CREATE_BREAKAWAY_FROM_JOB):
            self.assertTrue(flags & flag)

    def test_ending_a_stuck_server_on_windows_spares_the_updater_it_started(self):
        """Regression: `taskkill /T` on the server also ended the updater, its child (Windows 11 VM, 2026-10-23)."""
        with mock.patch.object(updates.subprocess, "run") as run, mock.patch.object(updates.os, "name", "nt"):
            updates._end(4242)
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["taskkill", "/PID", "4242"])
        self.assertNotIn("/T", args)

    def test_the_updater_uses_only_the_standard_library(self):
        import sys

        source = Path(updates.__file__).read_text(encoding="utf-8")
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    self.assertEqual([a.name for a in node.names], ["paths"])
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                self.assertIn(name, allowed)


class Routes(unittest.TestCase):
    def test_developer_mode_shows_status_only(self):
        with HalServer() as hal:
            c = hal.signed_in()
            r = c.get("/api/v2/updates")
            self.assertEqual(r.status, 200)
            self.assertTrue(r.json()["developer"])
            self.assertEqual(r.json()["blocked_by"], "You run Hal from your own folder. Update it with git.")
            r = c.post("/api/v2/updates/apply")
            self.assertEqual(r.status, 409)
            self.assertEqual(r.json()["error"]["message"], "You run Hal from your own folder. Update it with git.")
            self.assertEqual(c.post("/api/v2/updates/rollback", {"confirm": True}).status, 409)

    def test_the_server_stops_for_the_updater_only_with_the_local_key(self):
        from jobhunt.web import auth

        with HalServer() as hal, mock.patch.object(update_routes, "_exit") as end:
            self.assertEqual(hal.client().post("/api/v2/system/shutdown").status, 401)
            key = {"Authorization": f"Bearer {auth.local_api_key()}"}
            r = hal.client().post("/api/v2/system/shutdown", headers=key)
            self.assertEqual(r.status, 200)
            deadline = time.monotonic() + EXIT_WAIT_S
            while not end.called and time.monotonic() < deadline:
                time.sleep(POLL_S)
            end.assert_called_once_with(0)

    def test_the_updater_can_stop_a_new_install_that_never_made_its_key(self):
        """Regression: nothing made the local key on a new install, so the updater's stop got a 401 (VM, 2026-10-23)."""
        with HalServer() as hal, mock.patch.object(update_routes, "_exit") as end, \
                mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_PORT": str(hal.port)}):
            update_routes.at_start()
            code, _ = updates.World(updates.read_install())._request("POST", "/api/v2/system/shutdown")
            self.assertEqual(code, 200)
            deadline = time.monotonic() + EXIT_WAIT_S
            while not end.called and time.monotonic() < deadline:
                time.sleep(POLL_S)
            end.assert_called_once_with(0)

    def test_the_version_names_the_commit_and_the_helper_it_wants(self):
        with HalServer() as hal:
            r = hal.signed_in().get("/api/v2/version")
            self.assertEqual(r.status, 200)
            self.assertEqual(r.json()["protocol"], 1)
            self.assertIn("app", r.json())

    def test_the_update_result_becomes_an_activity_row_after_the_restart(self):
        with HalServer():
            updates.write_status({"state": "updated", "words": "Updated Hal from 2.0.1 to 2.0.2",
                                  "whats_new": ["cv: a clearer heading"], "recorded": False})
            row = update_routes.absorb_result()
            self.assertIsNotNone(row)
            self.assertTrue(updates.status()["recorded"])
            self.assertIsNone(update_routes.absorb_result())
            from jobhunt import db

            with contextlib.closing(db.connect()) as con:
                text, detail = con.execute("SELECT text, detail FROM activity WHERE id = ?", (row,)).fetchone()
            self.assertEqual(text, "Updated Hal from 2.0.1 to 2.0.2")
            self.assertIn("cv: a clearer heading", detail)

    def test_a_failed_update_holds_its_commit_in_settings(self):
        with HalServer():
            updates.write_status({"state": "failed", "words": updates.WENT_BACK, "held": "abc1234def",
                                  "recorded": False})
            update_routes.absorb_result()
            from jobhunt.core import settings

            self.assertEqual(settings.get("updates.held_commit"), "abc1234def")


if __name__ == "__main__":
    unittest.main()
