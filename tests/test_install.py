"""`python -m jobhunt install`: shortcuts, the shim, units, the record, removal."""
import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - must come before jobhunt

from jobhunt.cli import install
from jobhunt.core import paths, updates


class FakePlatform:
    """Records what the install asks of the computer."""

    def __init__(self, root: Path, service: str = "none") -> None:
        self.root, self._service = root, service
        self.links: list[tuple[Path, str, str]] = []
        self.icons: dict[str, str] = {}
        self.login: list[bool] = []
        self.started = 0
        self.removed_unit = 0

    def special_folder(self, name: str) -> Path:
        return self.root / name

    def make_shortcut(self, link: Path, target: str, arguments: str = "", **kw) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_bytes(b"lnk")
        self.links.append((link, target, arguments))
        self.icons[link.name] = kw.get("icon", "")

    def start_at_login(self, on: bool) -> str:
        self.login.append(on)
        return "Hal starts when you sign in." if on else "Hal no longer starts when you sign in."

    def service(self) -> str:
        return self._service

    def start_app_unit(self) -> bool:
        self.started += 1
        return True

    def remove_app_unit(self) -> None:
        self.removed_unit += 1


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="hal-install-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(paths.set_override(self.root / "data-home"))
        paths.ensure()
        self.code = self.root / "Hal Employment" / "app"
        self.code.mkdir(parents=True)
        self.home = self.root / "person"
        self.lines: list[str] = []

    def installer(self, system: str, **kw) -> install.Installer:
        self.platform = kw.pop("platform", None) or FakePlatform(self.root / "folders")
        self.registry: dict[str, dict | None] = {}
        return install.Installer(code=self.code, home=self.home, system=system, platform=self.platform,
                                 say=self.lines.append, reg_write=self.registry.__setitem__,
                                 spawn=lambda args: None, **kw)


class Windows(InstallCase):
    def test_the_windows_install_always_uses_the_bundled_git_and_records_it(self):
        with mock.patch("shutil.which", return_value=str(Path("/usr/bin/git"))):
            self.installer("windows").windows([])
        inst = updates.read_install()
        self.assertEqual(inst.kind, "user")
        self.assertEqual(Path(inst.git), self.root / "Hal Employment" / "tools" / "git" / "cmd" / "git.exe")
        self.assertEqual(Path(inst.uv), self.root / "Hal Employment" / "tools" / "uv" / "uv.exe")
        self.assertEqual(Path(inst.code), self.code)
        self.assertFalse(inst.developer)

    def test_the_shortcuts_open_hal_update_it_and_start_it_at_sign_in(self):
        self.installer("windows").windows([])
        made = {(link.parent.name, link.name): (Path(target).name, args) for link, target, args in self.platform.links}
        self.assertEqual(made[("start_menu", "Hal Employment.lnk")], ("pythonw.exe", "-m jobhunt open"))
        self.assertEqual(made[("desktop", "Hal Employment.lnk")], ("pythonw.exe", "-m jobhunt open"))
        self.assertEqual(made[("start_menu", "Update Hal Employment.lnk")], ("update-hal.cmd", ""))
        self.assertEqual(self.platform.login, [True])
        entry = self.registry[install.UNINSTALL_KEY]
        self.assertTrue(entry["UninstallString"].endswith("-m jobhunt install --remove"))

    def test_the_shortcuts_show_hals_eye_not_the_python_logo(self):
        # Windows 11 install test (2026-10-23): with no icon set, the desktop shortcut showed pythonw.exe's logo
        self.installer("windows").windows([])
        ico = self.root / "Hal Employment" / "tools" / install.ICON_FILE
        self.assertEqual(self.platform.icons, {"Hal Employment.lnk": f"{ico},0",
                                               "Update Hal Employment.lnk": f"{ico},0"})
        self.assertEqual(self.registry[install.UNINSTALL_KEY]["DisplayIcon"], str(ico))
        data = ico.read_bytes()
        self.assertEqual(data[:6], b"\0\0\1\0\4\0", "an icon file with four pictures")
        self.assertEqual(data.count(b"\x89PNG\r\n\x1a\n"), 4)

    def test_remove_takes_away_the_shortcuts_and_keeps_the_data_by_default(self):
        inst = self.installer("windows")
        inst.windows([])
        removed = []
        inst._spawn = removed.append
        inst.remove(keep_data=True)
        self.assertFalse(any(link.exists() for link, _, _ in self.platform.links))
        self.assertEqual(self.platform.login, [True, False])
        self.assertIsNone(self.registry[install.UNINSTALL_KEY])
        self.assertTrue(paths.data_dir().exists())
        self.assertIn(str(self.code), removed[0][-1])


class Linux(InstallCase):
    @unittest.skipIf(os.name == "nt", "the shim and Hal's own command are Linux files; symlinks need admin on Windows")
    def test_the_install_never_replaces_hals_own_command(self):
        ecosystem = self.root / "hal-ecosystem" / "extensions" / "hal-jobs" / "bin"
        ecosystem.mkdir(parents=True)
        (ecosystem / "hal-employment").write_text("#!/bin/sh\nxdg-open http://127.0.0.1:8737/\n", encoding="utf-8")
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "hal-employment").symlink_to(ecosystem / "hal-employment")
        inst = self.installer("linux")
        inst.linux_files()
        self.assertTrue((bin_dir / "hal-employment").is_symlink())
        self.assertEqual(os.readlink(bin_dir / "hal-employment"), str(ecosystem / "hal-employment"))
        shim = (bin_dir / "hal-employment-cli").read_text(encoding="utf-8")
        self.assertIn(install.MARK, shim)
        self.assertIn(f'exec "{self.code / ".venv" / "bin" / "python"}" -m jobhunt "$@"', shim)
        # a shim pointing into hal-ecosystem, or a file someone else made, is never written over
        (bin_dir / "hal-employment-cli").unlink()
        (bin_dir / "hal-employment-cli").symlink_to(ecosystem / "hal-employment")
        with self.assertRaises(install.InstallRefused):
            inst.linux_files()
        (bin_dir / "hal-employment-cli").unlink()
        (bin_dir / "hal-employment-cli").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        with self.assertRaises(install.InstallRefused):
            inst.linux_files()
        self.assertEqual((bin_dir / "hal-employment-cli").read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")

    def test_a_linux_install_makes_and_starts_its_unit(self):
        inst = self.installer("linux")
        inst.linux(["browser", "documents"])
        self.assertEqual(self.platform.login, [True])
        self.assertEqual(self.platform.started, 1)
        self.assertTrue(inst.desktop_path().is_file())
        self.assertEqual(updates.read_install().extras, ["browser", "documents"])

    def test_no_unit_is_made_when_hals_own_unit_runs_the_server(self):
        inst = self.installer("linux", platform=FakePlatform(self.root / "f", service="hal-unit"))
        inst.linux([])
        self.assertEqual(self.platform.login, [])
        self.assertEqual(self.platform.started, 0)

    def test_the_developer_checkout_gets_only_the_command_and_the_menu_entry(self):
        inst = self.installer("linux")
        inst.linux_files()
        self.assertTrue(inst.shim_path().is_file())
        self.assertEqual(self.platform.login, [])
        self.assertTrue(updates.read_install().developer)

    def test_remove_on_a_developer_checkout_keeps_the_code(self):
        inst = self.installer("linux")
        inst.linux_files()
        inst.remove(keep_data=True)
        self.assertTrue(self.code.exists())
        self.assertFalse(inst.shim_path().exists())
        self.assertEqual(self.platform.removed_unit, 1)


if __name__ == "__main__":
    unittest.main()
