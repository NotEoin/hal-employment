"""Tests of the owner's one-time import and the preferences export.

Every old install here is made up by `support.old_repo`, in a temporary folder, with a made-up private
answers file and carer answer as canaries. The computer (port, timers, disk) is faked through
`importer.System`, including the platform's timer calls (`FakeTimers`). Secrets and private answers go to
the real stores, on their file fallback in the temporary home.
"""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import old_db, old_repo

from jobhunt import db
from jobhunt.cli import data as cli_data
from jobhunt.core import importer, paths
from jobhunt.core.settings import store

ROOT = Path(__file__).resolve().parents[1]
FAKE_NOW = "2026-10-22T09:00:00Z"
DAY = "2026-10-22"


def load_export():
    """scripts/dev/export_owner_prefs.py as a module."""
    script = ROOT / "scripts" / "dev" / "export_owner_prefs.py"
    spec = importlib.util.spec_from_file_location("export_owner_prefs", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def writable(root: Path) -> None:
    """Undoes the import's read-only lock so the temporary folder can be removed."""
    for p in [root, *root.rglob("*")]:
        if not p.is_symlink():
            with contextlib.suppress(OSError):
                p.chmod(stat.S_IMODE(p.stat().st_mode) | stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)


class Opened:
    """Records every path opened through `open` while installed."""

    def __init__(self):
        self.paths: list[str] = []
        self.real = io.open

    def __call__(self, file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            self.paths.append(str(Path(file).resolve()))
        return self.real(file, *args, **kwargs)

    @contextlib.contextmanager
    def installed(self):
        with mock.patch("builtins.open", self), mock.patch("io.open", self):
            yield self


class FakeTimers:
    """The platform's `retire_legacy_timers` and `restore_legacy_timers`, counting calls instead of touching systemd."""

    NAMES = ("jobhunt-discover.timer", "jobhunt-mail.timer", "jobhunt-digest.timer")

    def __init__(self):
        self.retired = 0
        self.restored = 0

    def retire(self) -> list[str]:
        self.retired += 1
        return list(self.NAMES)

    def restore(self) -> list[str]:
        self.restored += 1
        return list(self.NAMES)

    def call(self, name: str):
        """`importer.System.platform_call`: the fake for a timer call, None for anything else."""
        return {"retire_legacy_timers": self.retire, "restore_legacy_timers": self.restore}.get(name)


class ImportCase(unittest.TestCase):
    """A made-up old checkout, an empty temporary home and the faked computer."""

    carer = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(writable, self.root)
        self.home = self.root / "home"
        env = mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_HOME": str(self.home), "HAL_EMPLOYMENT_FAKE_NOW": FAKE_NOW})
        env.start()
        self.addCleanup(env.stop)
        store.drop_cache()
        self.addCleanup(store.drop_cache)
        self.old = old_repo.build(self.root / "old", carer=self.carer)
        self.prefs = self.home / "config" / "import" / "owner-prefs.toml"
        with contextlib.redirect_stdout(io.StringIO()):
            argv = ["--repo", str(self.old.root), "--ref", "HEAD", "--out", str(self.prefs)]
            self.assertEqual(load_export().main(argv), 0)
        self.platform = FakeTimers()
        self.lines: list[str] = []
        self.system = importer.System(port_answers=lambda port: False, new_app_answers=lambda port: False,
                                      timer_active=lambda name: False, free_bytes=lambda path: 10 ** 12,
                                      platform_call=self.platform.call, say=self.lines.append)
        self.target = self.home / "data"

    def saved_secrets(self) -> dict[str, str]:
        """Every secret the real store holds now, by name."""
        from jobhunt.core.settings import secrets

        return {name: secrets.get(name) for name, state in secrets.statuses().items() if state == "saved"}

    def run_import(self, **kw) -> importer.Report:
        opts = importer.Options(repo=self.old.root, legacy_secrets=self.old.secrets, **kw)
        return importer.run(opts, self.system)

    def setting(self, key):
        from jobhunt.core import settings

        store.drop_cache()
        return settings.get(key)

    def new_db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.target / "jobs.db")
        self.addCleanup(con.close)
        return con

    @contextlib.contextmanager
    def legacy_view(self):
        """What a hal-jobs command in another process sees: no test home, the old checkout as the code."""
        with mock.patch.object(paths, "home_override", lambda: None), \
                mock.patch.object(paths, "_guarded", lambda: None), \
                mock.patch.object(paths, "code_dir", lambda: self.old.root), \
                mock.patch.object(paths, "is_developer_checkout", lambda: True), \
                mock.patch.object(paths, "_default_data_dir", lambda: self.target), \
                mock.patch.object(paths, "config_dir", lambda: self.home / "config"):
            yield


class Copying(ImportCase):
    def test_import_copies_every_table_and_folder_and_locks_the_old_copy(self):
        before = old_db.counts(self.old.data / "jobs.db")
        profile = importer._files(self.old.data / "browser-profile")
        packs = importer._files(self.old.root / "packs")
        self.run_import()

        con = self.new_db()
        for table, n in before.items():
            if table == "answers":  # the old saved answers move to answers2, private ones left out
                after = con.execute("SELECT count(*) FROM answers2").fetchone()[0]
                self.assertEqual(after, len(old_db.ORDINARY_ANSWERS))
                continue
            after = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if table == "meta":     # migrations add their own rows
                self.assertGreaterEqual(after, n)
            else:
                self.assertEqual(after, n, table)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(importer._files(self.target / "browser-profile"), profile)
        self.assertEqual(importer._files(self.target / "legacy-packs"), packs)
        self.assertTrue((self.target / "apply" / "form-1.png").is_file())
        self.assertTrue((self.target / "signed-in.json").is_file())
        self.assertEqual((self.target / "cv" / "words.txt").read_text(), "pandas\nnumpy\n")
        cv = self.target / "import" / "cv"
        for name in ("cv-overrides.json", "cv-overrides.before-20261001T090000Z.json", "cv-built.json",
                     "cv-built-overrides.json", "cv-review.json", "cv-origin", "site-cv.json"):
            self.assertTrue((cv / name).is_file(), name)
        self.assertTrue((cv / "cv-sources" / "projects.txt").is_file())
        for name in ("boards.toml", "companies.txt", "owner-prefs.toml", "report.json"):
            self.assertTrue((self.target / "import" / name).is_file(), name)
        marker = json.loads((self.target / paths.MARKER).read_text())
        self.assertEqual(marker["kind"], "imported")
        self.assertEqual(marker["sha256"], importer._sha(self.target / "jobs.db"))
        self.assertFalse((self.target / importer.INCOMPLETE).exists())
        self.assertEqual(con.execute("SELECT count(*) FROM imports WHERE kind = 'owner'").fetchone()[0], 1)
        kinds = {r[0] for r in con.execute("SELECT kind FROM activity WHERE kind LIKE 'import.%'")}
        self.assertTrue({"import.copy", "import.verify", "import.migrate", "import.lists",
                         "import.lock", "import.timers"} <= kinds)

        self.assertFalse((self.old.root / "data").exists())
        for name in (f"data.imported-{DAY}", f"packs.imported-{DAY}", f"config.imported-{DAY}.toml"):
            locked = self.old.root / name
            self.assertTrue(locked.exists(), name)
            self.assertFalse(stat.S_IMODE(locked.stat().st_mode) & stat.S_IWUSR, name)
        self.assertTrue(self.old.secrets.with_name(f"jobhunt.imported-{DAY}").is_dir())
        with self.legacy_view():
            self.assertEqual(paths.data_dir(), self.target)

    def test_a_dry_run_changes_nothing(self):
        roots = (self.old.root, self.old.site, self.old.secrets.parent, self.old.hal)
        before = old_repo.tree_state(*roots)
        self.run_import(dry_run=True)
        self.assertEqual(old_repo.tree_state(*roots), before)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.saved_secrets(), {})
        self.assertEqual(self.platform.retired, 0)
        self.assertIn("Dry run", self.lines[-1])
        self.assertTrue(any("Moved" in line and "settings" in line for line in self.lines))

    def test_import_refuses_while_hal_or_the_old_timers_run(self):
        before = old_repo.tree_state(self.old.root)
        self.system.port_answers = lambda port: True
        with self.assertRaisesRegex(importer.ImportStopped, "Hal is running"):
            self.run_import()
        self.system.port_answers = lambda port: False
        self.system.timer_active = lambda name: name == "jobhunt-mail.timer"
        with self.assertRaisesRegex(importer.ImportStopped, "Stop the old timers first.*jobhunt-mail.timer"):
            self.run_import()
        self.assertFalse(self.target.exists())
        self.assertEqual(old_repo.tree_state(self.old.root), before)
        self.assertEqual(self.saved_secrets(), {})

    def test_a_failure_before_the_seal_leaves_the_old_data_in_use(self):
        before = old_repo.tree_state(self.old.root)
        with mock.patch.object(importer._Run, "lists_step", side_effect=importer.ImportStopped("made-up failure")), \
                self.assertRaises(importer.ImportStopped):
            self.run_import()
        self.assertFalse(self.target.exists())
        self.assertEqual(old_repo.tree_state(self.old.root), before)
        self.run_import()
        self.assertTrue((self.target / paths.MARKER).is_file())

    def test_a_second_import_is_refused_once_the_data_has_moved(self):
        self.run_import()
        (self.old.root / f"data.imported-{DAY}").rename(self.old.root / "data")
        writable(self.old.root / "data")
        with self.assertRaisesRegex(importer.ImportStopped, "already in"):
            self.run_import()

    def test_a_hal_jobs_cli_call_during_the_import_reads_the_old_data(self):
        seen = {}

        def look(step):
            with self.legacy_view():
                con = db.connect()
                try:
                    seen[step] = (paths.data_dir(), con.execute("SELECT count(*) FROM jobs").fetchone()[0])
                finally:
                    con.close()

        self.system.after_step = look
        jobs = old_db.counts(self.old.data / "jobs.db")["jobs"]
        self.run_import()
        for step in ("copy", "verify", "migrate", "settings", "identity", "lists", "hal"):
            self.assertEqual(seen[step], (self.old.data, jobs), step)
        self.assertEqual(seen["seal"], (self.target, jobs))
        self.assertEqual(seen["done"], (self.target, jobs))


class Undo(ImportCase):
    def test_undo_restores_the_old_layout(self):
        self.run_import(retire_timers=True)
        self.assertEqual(self.platform.retired, 1)
        importer.undo(system=self.system)
        self.assertEqual(self.platform.restored, 1)
        for name in ("data", "packs", "config.toml"):
            path = self.old.root / name
            self.assertTrue(path.exists(), name)
            self.assertTrue(stat.S_IMODE(path.stat().st_mode) & stat.S_IWUSR, name)
        self.assertTrue((self.old.data / "jobs.db").is_file())
        self.assertTrue(self.old.secrets.is_dir())
        self.assertFalse((self.target / paths.MARKER).exists())
        with self.legacy_view():
            self.assertEqual(paths.data_dir(), self.old.data)

    def test_undo_after_changes_needs_force_and_says_what_is_left_behind(self):
        self.run_import()
        con = self.new_db()
        with con:
            con.execute("INSERT INTO meta (k, v) VALUES ('made_up_change', '1')")
        con.close()
        with self.assertRaisesRegex(importer.ImportStopped, "left behind"):
            importer.undo(system=self.system)
        importer.undo(force=True, system=self.system)
        self.assertTrue(any("left behind" in line for line in self.lines))
        self.assertTrue((self.old.data / "jobs.db").is_file())


class Settings(ImportCase):
    def test_config_values_become_settings_and_the_old_push_topic_is_left_behind(self):
        self.run_import()
        self.assertEqual(self.setting("notify.alert_score"), 70)
        self.assertEqual(self.setting("scoring.prepare_ahead"), 5)
        self.assertEqual(self.setting("scoring.min_show"), 55)
        self.assertEqual(self.setting("applying.mode"), "helper")
        self.assertEqual(self.setting("applying.window"), "this")
        self.assertEqual(self.setting("addon.hal_root"), str(self.old.hal))
        self.assertEqual(self.setting("ai.gpu_ignore"), ["made-up-voice"])
        self.assertEqual(self.setting("you.salary_answer"), "Around 26,000 pounds")
        self.assertEqual(self.setting("ai.model.checker"), "made-up-main:7b")
        self.assertEqual(self.setting("ai.model.quick"), "made-up-fast:3b")
        self.assertEqual(self.setting("ai.provider"), "local")
        self.assertTrue(self.setting("ai.local_only"))
        self.assertFalse(self.setting("addon.respect_off"))
        self.assertEqual(self.setting("goal.kinds"), importer.KINDS)
        self.assertEqual(self.setting("goal.min_pay"), 24000)
        self.assertEqual(self.setting("goal.places"), ["Bristol", "Leeds"])
        self.assertIn("Bristol: clifton, bedminster, portishead, avon and somerset", self.setting("goal.place_aliases"))
        self.assertEqual(self.setting("goal.training_ok_places"), ["Bristol"])
        self.assertEqual(self.setting("goal.training_providers"), ["Quickskill Academy"])
        self.assertEqual(self.setting("goal.avoid_employers")[:2],
                         ["# Employers Sam will not work for. One name per line.", "# Made-up arms makers"])
        self.assertIn("Ironfold Defence", self.setting("goal.avoid_employers"))
        self.assertIn("python = 3", self.setting("scoring.skills"))
        self.assertEqual(self.setting("email.address"), "sam.example@example.org")

        from jobhunt.core.settings import private

        self.assertNotIn("carer", private.read_table())    # the canary is not one of the store's choices
        saved = self.saved_secrets()
        topic = saved["notify.ntfy_topic"]
        self.assertRegex(topic, r"^hal-[a-z0-9]{24}$")
        self.assertEqual(saved["email.app_password"], old_repo.SECRETS["gmail-app-password"])
        self.assertEqual(saved["sites.reed_key"], old_repo.SECRETS["reed"])
        for path in [*self.target.rglob("*"), *(self.home / "config").rglob("*")]:
            if path.is_file():
                self.assertNotIn(old_repo.OLD_TOPIC.encode(), path.read_bytes(), path)
        self.assertTrue(any("signed_in_apply" in line for line in self.lines))

    def test_every_old_profile_fact_has_a_setting_after_import(self):
        self.run_import()
        tree = ast.parse((self.old.root / "jobhunt" / "autofill.py").read_text())
        profile = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "profile")
        keys = {k.value for n in ast.walk(profile) if isinstance(n, ast.Dict) for k in n.keys}
        expected = {
            "first_name": ("you.name", "Sam Example"), "last_name": ("you.name", "Sam Example"),
            "full_name": ("you.name", "Sam Example"), "preferred_name": ("you.preferred_name", "Sam"),
            "email": ("you.email", "sam.example@example.org"), "phone": ("you.phone", "07700 900123"),
            "linkedin": ("you.links.linkedin", "https://www.linkedin.com/in/sam-example"),
            "github": ("you.links.github", "https://github.com/sam-example"),
            "website": ("you.links.website", "https://sam-example.example.org"),
            "city": ("you.city", "Bristol"), "country": ("you.country", "United Kingdom"),
            "location": ("you.location_line", "Bristol, United Kingdom"),
            "right_to_work": ("you.right_to_work", "uk_no_visa"),
            "sponsorship": ("you.right_to_work", "uk_no_visa"),
            "work_status": ("you.work_status", "British or Irish citizen"),
            "nationality": ("you.nationality", "British"),
            "notice": ("you.notice", "None, available immediately"), "start": ("you.start", "now"),
            "university": ("you.education.university", "University of Nowhere"),
            "degree": ("you.education.degree", "BA (Hons) Made-up Studies"),
            "grade": ("you.education.grade", "Upper second-class honours"), "grad_year": ("goal.study_end", "2026"),
            "office": ("you.office", True), "relocate": ("you.relocate", True),
            "diversity": ("you.diversity", "prefer_not"),
        }
        self.assertEqual(keys, set(expected))
        for key in keys:
            self.assertIn(key, importer.YOU_MAP, key)
            setting, value = expected[key]
            self.assertEqual(self.setting(setting), value, key)

    def test_a_profile_fact_with_no_setting_stops_the_import_with_its_name(self):
        self.prefs.write_text(self.prefs.read_text() + 'shoe_size = "made up"\n')
        with self.assertRaisesRegex(importer.ImportStopped, "shoe_size"):
            self.run_import()
        self.assertFalse(self.target.exists())


class Website(ImportCase):
    def test_the_website_cv_is_copied_once_and_never_read_again(self):
        cv_json = (self.old.site / "src" / "data" / "cv.json").resolve()
        with Opened().installed() as opened:
            self.run_import()
        self.assertEqual(opened.paths.count(str(cv_json)), 1)
        self.assertEqual((self.target / "import" / "cv" / "site-cv.json").read_bytes(), cv_json.read_bytes())
        con = self.new_db()
        values = " ".join(r[0] for r in con.execute("SELECT value FROM settings"))
        self.assertNotIn(str(self.old.site), values)
        self.old.site.chmod(0)
        self.addCleanup(self.old.site.chmod, 0o700)
        self.assertEqual(self.setting("you.name"), "Sam Example")
        self.assertTrue((self.target / "import" / "cv" / "site-cv.json").is_file())


class Private(ImportCase):
    carer = False

    def test_private_answers_are_copied_byte_for_byte_and_never_logged(self):
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(root.removeHandler, handler)
        self.run_import()

        copy = self.target / "private.toml"
        self.assertEqual(copy.read_bytes(), (self.old.root / f"data.imported-{DAY}" / "private.toml").read_bytes())
        if sys.platform != "win32":   # Windows keeps no Unix modes; the account folder's ACL guards it
            self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        canary = old_repo.PRIVATE_CANARY
        self.assertNotIn(canary, logs.getvalue())
        self.assertNotIn(canary, "\n".join(self.lines))
        for path in [*self.target.rglob("*"), *(self.home / "config").rglob("*")]:
            if path.is_file() and path != copy and not path.name.endswith(".db") and path.suffix != ".db":
                self.assertNotIn(canary.encode(), path.read_bytes(), path)
        con = self.new_db()
        for table in ("activity", "imports", "settings", "settings_history", "answers2"):
            text = json.dumps([list(r) for r in con.execute(f"SELECT * FROM {table}")])
            self.assertNotIn(canary, text, table)
        from jobhunt.core.settings import private

        self.assertNotIn("carer", private.read_table())    # only the copied file's answers, nothing saved on top


class Export(ImportCase):
    def test_the_export_never_reads_private_answers(self):
        private = (self.old.data / "private.toml").resolve()
        private.chmod(0)
        self.addCleanup(private.chmod, 0o600)
        out = self.root / "prefs.toml"
        with Opened().installed() as opened, contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(load_export().main(["--repo", str(self.old.root), "--ref", "HEAD", "--out", str(out)]), 0)
        self.assertNotIn(str(private), opened.paths)
        text = out.read_text()
        for word in (old_repo.PRIVATE_CANARY, old_repo.CARER_CANARY, "carer", "disability", "adjustments"):
            self.assertNotIn(word, text)
        self.assertNotIn("Sam", printed.getvalue())
        if sys.platform != "win32":   # Windows keeps no Unix modes; the account folder's ACL guards it
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)

    def test_the_export_reads_the_old_code_from_git_not_the_working_tree(self):
        (self.old.root / "jobhunt" / "gmail.py").write_text('ADDRESS = "changed.later@example.org"\n')
        out = self.root / "prefs.toml"
        with contextlib.redirect_stdout(io.StringIO()):
            load_export().main(["--repo", str(self.old.root), "--ref", "HEAD", "--out", str(out)])
        self.assertIn("sam.example@example.org", out.read_text())


class Timers(ImportCase):
    def test_the_timers_are_kept_unless_asked_to_retire_them(self):
        self.run_import()
        self.assertEqual(self.platform.retired, 0)
        self.assertTrue(any(line.endswith("systemctl --user start jobhunt-discover.timer jobhunt-mail.timer "
                                          "jobhunt-digest.timer") for line in self.lines))

    def test_retiring_the_timers_is_asked_of_the_platform_once(self):
        self.run_import(retire_timers=True)
        self.assertEqual(self.platform.retired, 1)


class Commands(ImportCase):
    def test_migrate_lists_pending_updates_and_a_dry_run_changes_nothing(self):
        old_db.build(self.home / "data" / "jobs.db", "L")
        args = mock.Mock(dry_run=True)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli_data.cmd_migrate(args)
        self.assertIn("0001 ", out.getvalue())
        self.assertIn("Nothing was changed", out.getvalue())
        con = sqlite3.connect(self.home / "data" / "jobs.db")
        self.addCleanup(con.close)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 0)
        args.dry_run = False
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli_data.cmd_migrate(args)
        self.assertIn("Updated your records", out.getvalue())

    def test_a_stopped_import_exits_with_the_reason(self):
        self.system.port_answers = lambda port: True
        args = mock.Mock(from_usb_install=None, undo=False, from_repo=self.old.root, dry_run=False,
                         retire_timers=False, force=False)
        with mock.patch.object(importer, "System", lambda say: self.system), \
                self.assertRaisesRegex(SystemExit, "Stopped: Hal is running"):
            cli_data.cmd_import(args)


class RealStores(ImportCase):
    """The import on the real stores, checked end to end.

    Regression for two breaks: the private store refuses an old carer answer that is not one of
    one of its choices, which stopped the whole import; and saving a secret writes a history row through
    `db.connect()`, which before the seal opened and migrated the old database in place, then left the new
    folder non-empty so the seal failed.
    """

    def test_the_import_runs_on_the_real_stores_and_never_opens_the_old_database(self):
        old_db_file = self.old.data / "jobs.db"
        before = hashlib.sha256(old_db_file.read_bytes()).hexdigest()
        self.run_import()
        self.assertTrue((self.target / paths.MARKER).is_file())
        moved = self.old.root / f"data.imported-{DAY}" / "jobs.db"
        self.assertEqual(hashlib.sha256(moved.read_bytes()).hexdigest(), before)
        from jobhunt.core.settings import private, secrets

        self.assertEqual(secrets.get("email.app_password"), old_repo.SECRETS["gmail-app-password"])
        self.assertRegex(secrets.get("notify.ntfy_topic"), r"^hal-[a-z0-9]{24}$")
        self.assertNotIn("carer", private.read_table())
        self.assertTrue(any("answer it again in Settings" in line for line in self.lines))
        self.assertFalse(any(old_repo.CARER_CANARY in line for line in self.lines))
        importer.undo(system=self.system)    # the sealed hash covers the secrets' rows, so no --force


if __name__ == "__main__":
    unittest.main()
