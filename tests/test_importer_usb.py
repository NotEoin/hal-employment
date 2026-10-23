"""A USB stick install, copied in and retired, with a made-up stick folder and fakes for Windows."""
import contextlib
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - must come before jobhunt
from support import old_db
from support.actions import wait_for_actions

from jobhunt import db
from jobhunt.core import importer, importer_usb, paths, runner, settings
from jobhunt.core.settings import secrets, store

PRIVATE = '[applicant]\nadjustments = "Prefer not to say"\n'   # made up; copied as bytes, never read by Hal
CONFIG = """[applicant]
name = "Morgan Vale"
right_to_work = "Yes"
sponsorship = "No"

[search]
radius_miles = "15 miles"
salary_floor = "22,000"

[claude]
parallel = 2

[old_feature]
colour = "teal"
"""
JOBS_MD = "Run the made-up jobs command.\n"


class FakeWindows:
    """The parts of Windows the import touches, in folders under the test's own home."""

    def __init__(self, root: Path, stick: Path, *, desktop_is_old: bool = True) -> None:
        self.root, self.stick = root, stick
        self.stopped: list[Path] = []
        self.path = f"C:\\Tools;{stick}\\bin"
        self.desktop_is_old = desktop_is_old
        for name in ("startup", "desktop"):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / "startup" / importer_usb.STARTUP_LINK).write_bytes(b"lnk")
        (root / "desktop" / importer_usb.DESKTOP_LINK).write_bytes(b"lnk")

    def system(self) -> importer_usb.System:
        return importer_usb.System(stop_old=self.stopped.append, folder=lambda name: self.root / name,
                                   link_points_into=lambda link, folder: self.desktop_is_old,
                                   user_path=lambda: self.path, set_user_path=self._set_path,
                                   home=lambda: self.root / "user", today=lambda: "2026-10-23")

    def _set_path(self, value: str) -> None:
        self.path = value


def make_stick(folder: Path) -> Path:
    """A made-up stick install with every kind of file the import copies."""
    data = folder / "data"
    old_db.build(data / "jobs.db", "U")
    (data / "private.toml").write_text(PRIVATE, encoding="utf-8")
    (data / "apply" / "sorrel").mkdir(parents=True)
    (data / "apply" / "sorrel" / "form.json").write_text("{}", encoding="utf-8")
    (data / "cv-built.json").write_text('{"name": "Morgan Vale"}', encoding="utf-8")
    (folder / "cv-sources").mkdir()
    (folder / "cv-sources" / "cv.txt").write_text("Morgan Vale, History BA", encoding="utf-8")
    (folder / "packs" / "harbour").mkdir(parents=True)
    (folder / "packs" / "harbour" / "pack.md").write_text("# Harbour Museum", encoding="utf-8")
    (folder / "config.toml").write_text(CONFIG, encoding="utf-8")
    (data / "secrets").mkdir()
    (data / "secrets" / "reed").write_text("made-up-reed-key\n", encoding="utf-8")
    (data / "schedule.json").write_text(json.dumps({"discover": {"last_run": "2026-10-20T08:00:00+00:00"}}),
                                        encoding="utf-8")
    (folder / "hal-commands").mkdir()
    (folder / "hal-commands" / "jobs.md").write_text(JOBS_MD, encoding="utf-8")
    return folder


class StickImport(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="hal-usb-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(paths.set_override(self.root / "home"))
        stack.callback(wait_for_actions)
        store.drop_cache()
        stack.callback(store.drop_cache)
        paths.ensure()
        self.stick = make_stick(self.root / "HalEmployment")
        self.win = FakeWindows(self.root / "win", self.stick)
        commands = self.root / "win" / "user" / ".claude" / "commands"
        commands.mkdir(parents=True)
        (commands / "jobs.md").write_text(JOBS_MD, encoding="utf-8")

    def test_the_stick_install_is_imported_and_retired(self):
        jobs = old_db.counts(self.stick / "data" / "jobs.db")["jobs"]
        private_sha = hashlib.sha256((self.stick / "data" / "private.toml").read_bytes()).hexdigest()
        report = importer_usb.run(self.stick, system=self.win.system())
        with contextlib.closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM jobs").fetchone()[0], jobs)
            last = con.execute("SELECT last_run FROM schedule_state WHERE job = 'find.discover'").fetchone()[0]
            done = con.execute("SELECT text FROM activity WHERE kind = 'setup.import_usb'").fetchone()[0]
        self.assertEqual(last, "2026-10-20T08:00:00+00:00")
        self.assertEqual(done, "Copied your jobs, applications and settings from your old Hal. Nothing was deleted.")
        self.assertEqual(settings.get("you.name"), "Morgan Vale")
        self.assertEqual(settings.get("goal.max_distance"), 15)
        self.assertEqual(settings.get("goal.min_pay"), 22000)
        self.assertEqual(settings.get("you.right_to_work"), "uk_no_visa")
        self.assertEqual(settings.get("ai.provider"), "claude_cli")
        self.assertEqual(settings.get("scoring.preset"), "humanities_grad")
        self.assertEqual(hashlib.sha256(paths.private_path().read_bytes()).hexdigest(), private_sha)
        self.assertEqual(secrets.get("sites.reed_key"), "made-up-reed-key")
        data = paths.data_dir()
        self.assertTrue((data / "apply" / "sorrel" / "form.json").is_file())
        self.assertTrue((data / "import" / "cv" / "cv-built.json").is_file())
        self.assertTrue((data / "import" / "cv" / "cv-sources" / "cv.txt").is_file())
        self.assertTrue((paths.legacy_packs_dir() / "harbour" / "pack.md").is_file())
        # retired: the old Hal is stopped, its shortcuts and PATH entry are gone, the folder is kept, renamed
        self.assertEqual(self.win.stopped, [self.stick])
        self.assertFalse((self.root / "win" / "startup" / importer_usb.STARTUP_LINK).exists())
        self.assertFalse((self.root / "win" / "desktop" / importer_usb.DESKTOP_LINK).exists())
        self.assertEqual(self.win.path, "C:\\Tools")
        self.assertFalse((self.root / "win" / "user" / ".claude" / "commands" / "jobs.md").exists())
        self.assertFalse(self.stick.exists())
        kept = self.root / "HalEmployment.old-2026-10-23"
        self.assertTrue((kept / "data" / "jobs.db").is_file())
        self.assertFalse((kept / "data" / "secrets" / "reed").exists())
        self.assertIn("settings", [s["step"] for s in report.steps])

    def test_a_setting_this_hal_does_not_use_is_left_out_not_fatal(self):
        values, skipped = importer_usb.settings_from({"old_feature": {"colour": "teal"},
                                                      "search": {"radius_miles": 10}})
        self.assertIn("old_feature.colour", skipped)
        self.assertEqual(values["goal.max_distance"], 10)

    def test_the_new_installs_own_desktop_shortcut_is_kept(self):
        self.win.desktop_is_old = False
        importer_usb.run(self.stick, system=self.win.system())
        self.assertTrue((self.root / "win" / "desktop" / importer_usb.DESKTOP_LINK).exists())

    def test_a_dry_run_changes_nothing(self):
        report = importer_usb.run(self.stick, dry_run=True, system=self.win.system())
        self.assertIn("Nothing was changed", report.steps[-1]["line"])
        self.assertTrue(self.stick.exists())
        self.assertEqual(self.win.stopped, [])
        self.assertFalse(paths.private_path().exists())

    def test_a_hal_that_already_has_jobs_is_never_overwritten(self):
        importer_usb.run(self.stick, system=self.win.system())
        again = make_stick(self.root / "HalEmployment")
        with self.assertRaises(importer.ImportStopped) as e:
            importer_usb.run(again, system=self.win.system())
        self.assertIn("already has jobs", str(e.exception))
        self.assertTrue(again.exists())

    def test_setup_copies_the_old_hal_as_an_action(self):
        from support.api import HalServer

        with HalServer() as hal:
            stick = make_stick(hal.home / "HalEmployment")
            (stick / "hal-commands" / "jobs.md").unlink()
            c = hal.signed_in()
            self.assertEqual(c.post("/api/v2/setup/import-usb", {"path": str(hal.home / "none")}).status, 400)
            r = c.post("/api/v2/setup/import-usb", {"path": str(stick)})
            self.assertEqual(r.status, 200)
            done = runner.wait(r.json()["action_id"], timeout=60)
            self.assertEqual(done["state"], "done", done["error"])
            # the window that pressed the button is still signed in, and the jobs arrived
            self.assertEqual(c.get("/api/v2/session").status, 200)
            with contextlib.closing(db.connect()) as con:
                self.assertEqual(con.execute("SELECT count(*) FROM jobs").fetchone()[0],
                                 old_db.counts(next(hal.home.glob("HalEmployment.old-*")) / "data" / "jobs.db")["jobs"])
            self.assertTrue((hal.home / "HalEmployment.old-" f"{importer_usb.clock.local_day().isoformat()}").exists())

    def test_a_folder_with_no_old_hal_stops_with_words(self):
        with self.assertRaises(importer.ImportStopped):
            importer_usb.run(self.root / "nothing-here", system=self.win.system())


if __name__ == "__main__":
    unittest.main()
