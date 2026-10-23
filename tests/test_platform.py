"""The platform layer: browsers per OS, the app window, start at login and the old timers, all on fakes.

No test here starts a browser, reads the real registry or touches a real systemd unit: each platform gets a
fake command runner, `which`, registry and home folder.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_runner import fresh_home

from jobhunt.cli import system
from jobhunt.core import platform, settings
from jobhunt.core.platform.linux import LinuxPlatform
from jobhunt.core.platform.windows import WindowsPlatform


class Commands:
    """A fake `subprocess.run` and `Popen` that remember every command and answer from a table."""

    def __init__(self, answers: dict[tuple[str, ...], str] | None = None):
        self.answers = answers or {}
        self.ran: list[list[str]] = []
        self.started: list[list[str]] = []

    def run(self, args, **kw):
        self.ran.append(list(args))
        out = self.answers.get(tuple(args), "")
        return subprocess.CompletedProcess(args, 0 if out else 1, stdout=out, stderr="")

    def popen(self, args, **kw):
        self.started.append(list(args))
        return mock.Mock(pid=4242)


WIN_ENV = {"LOCALAPPDATA": r"C:\Users\robin\AppData\Local", "ProgramFiles": r"C:\Program Files",
           "ProgramFiles(x86)": r"C:\Program Files (x86)", "APPDATA": r"C:\Users\robin\AppData\Roaming"}
OPERA = r"C:\Users\robin\AppData\Local\Programs\Opera\opera.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BRAVE = r"C:\Users\robin\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"
VIVALDI = r"C:\Users\robin\AppData\Local\Vivaldi\Application\vivaldi.exe"
FIREFOX = r"C:\Program Files\Mozilla Firefox\firefox.exe"
APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"


def windows(files: set[str], registry: dict[tuple[str, str], str], cmds: Commands | None = None) -> WindowsPlatform:
    cmds = cmds or Commands()
    return WindowsPlatform(reg=lambda hive, key: registry.get((hive, key)), exists=lambda p: p in files,
                           env=WIN_ENV, run=cmds.run, popen=cmds.popen)


class WindowsBrowsers(unittest.TestCase):
    def test_browsers_are_found_from_app_paths_and_install_folders(self):
        registry = {("HKLM", rf"{APP_PATHS}\chrome.exe"): f'"{CHROME}"',
                    ("HKCU", rf"{APP_PATHS}\vivaldi.exe"): VIVALDI}
        files = {OPERA, EDGE, CHROME, BRAVE, VIVALDI, FIREFOX}
        found = windows(files, registry).browsers()
        self.assertEqual([(b.id, str(b.exe)) for b in found],
                         [("opera", OPERA), ("chrome", CHROME), ("edge", EDGE), ("brave", BRAVE),
                          ("vivaldi", VIVALDI), ("firefox", FIREFOX)])
        self.assertEqual({b.id: b.extensions_url for b in found}["opera"], "opera://extensions")
        firefox = found[-1]
        self.assertFalse(firefox.supported)
        self.assertEqual(platform.label(firefox), "Firefox (not supported yet)")
        self.assertTrue(all(b.supported for b in found[:-1]))

    def test_an_app_paths_entry_whose_program_is_gone_is_ignored(self):
        registry = {("HKCU", rf"{APP_PATHS}\opera.exe"): r"C:\Old\opera.exe"}
        self.assertEqual(windows({OPERA}, registry).browsers()[0].exe, Path(OPERA))

    def test_the_app_window_is_edge_on_windows(self):
        cmds = Commands()
        win = windows({OPERA, EDGE}, {}, cmds)
        win.open_app_window("http://127.0.0.1:24321/open?t=made-up")
        self.assertEqual(cmds.started, [[EDGE, "--app=http://127.0.0.1:24321/open?t=made-up"]])

    def test_a_new_browser_window_is_opened_without_an_address(self):
        # Edge dropped edge://extensions from its command line and showed a new tab
        cmds = Commands()
        win = windows({OPERA, EDGE}, {}, cmds)
        win.open_browser_window(win.browsers()[0])
        self.assertEqual(cmds.started, [[OPERA, "--new-window"]])

    def test_children_run_without_a_console_window(self):
        cmds = Commands()
        seen = {}

        def run(args, **kw):
            seen.update(kw)
            return cmds.run(args, **kw)
        win = WindowsPlatform(reg=lambda h, k: None, exists=lambda p: False, env=WIN_ENV, run=run, popen=cmds.popen)
        win.run_hidden(["cmd.exe", "/c", "echo"])
        self.assertEqual(seen["creationflags"], 0x08000000)
        self.assertEqual(win.retire_legacy_timers(), [])


class LinuxBrowsers(unittest.TestCase):
    def linux(self, on_path: dict[str, str], cmds: Commands | None = None, home: Path | None = None):
        cmds = cmds or Commands()
        return LinuxPlatform(run=cmds.run, popen=cmds.popen, which=on_path.get, home=home or Path("/nowhere"))

    def test_snap_and_flatpak_browsers_are_marked(self):
        cmds = Commands({("flatpak", "list", "--app", "--columns=application"):
                         "com.opera.Opera\norg.gnome.Calculator\ncom.brave.Browser\n"})
        on_path = {"chromium": "/snap/bin/chromium", "google-chrome": "/usr/bin/google-chrome",
                   "firefox": "/usr/bin/firefox", "flatpak": "/usr/bin/flatpak"}
        found = {b.id: b for b in self.linux(on_path, cmds).browsers()}
        self.assertEqual(sorted(found), ["brave", "chrome", "chromium", "firefox", "opera"])
        self.assertTrue(found["chromium"].sandboxed)
        self.assertFalse(found["chrome"].sandboxed)
        self.assertTrue(found["opera"].sandboxed)
        self.assertEqual(found["opera"].argv("https://example.org"),
                         [str(Path("/usr/bin/flatpak")), "run", "com.opera.Opera", "https://example.org"])
        self.assertFalse(found["firefox"].supported)

    def test_the_app_window_is_the_first_chromium_browser_else_a_tab(self):
        cmds = Commands()
        lin = self.linux({"firefox": "/usr/bin/firefox", "vivaldi-stable": "/usr/bin/vivaldi-stable"}, cmds)
        lin.open_app_window("http://127.0.0.1:24321/open?t=made-up")
        self.assertEqual(cmds.started, [[str(Path("/usr/bin/vivaldi-stable")), "--app=http://127.0.0.1:24321/open?t=made-up"]])
        with mock.patch("webbrowser.open") as tab:
            self.linux({"firefox": "/usr/bin/firefox"}).open_app_window("http://127.0.0.1:24321/")
        tab.assert_called_once_with("http://127.0.0.1:24321/")


class StartAtLogin(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cmds = Commands({("systemctl", "--user", "is-enabled", platform.APP_UNIT): "enabled\n"})
        self.lin = LinuxPlatform(run=self.cmds.run, popen=self.cmds.popen, which=lambda name: f"/usr/bin/{name}",
                                 home=self.home)
        self.units = self.home / ".config" / "systemd" / "user"

    def test_start_at_login_is_left_to_hals_own_unit_when_it_exists(self):
        self.units.mkdir(parents=True)
        (self.units / platform.HAL_UNIT).write_text("[Service]\n")
        self.assertEqual(self.lin.service(), "hal-unit")
        self.assertEqual(self.lin.start_at_login(True), "Managed by your Hal setup")
        self.assertEqual(self.lin.start_at_login(False), "Managed by your Hal setup")
        self.assertTrue(self.lin.start_at_login_state())
        self.assertEqual(self.cmds.ran, [])
        self.assertFalse((self.units / platform.APP_UNIT).exists())

    def test_a_user_install_gets_its_own_unit(self):
        self.assertEqual(self.lin.start_at_login(True), "Hal starts when you sign in to this computer.")
        unit = (self.units / platform.APP_UNIT).read_text()
        self.assertIn("-m jobhunt serve --background", unit)
        self.assertIn(["systemctl", "--user", "enable", platform.APP_UNIT], self.cmds.ran)
        self.assertEqual(self.lin.service(), "app-unit")
        self.assertTrue(self.lin.start_at_login_state())
        self.lin.start_at_login(False)
        self.assertIn(["systemctl", "--user", "disable", platform.APP_UNIT], self.cmds.ran)

    def test_restart_goes_through_the_unit_without_waiting(self):
        self.units.mkdir(parents=True)
        (self.units / platform.HAL_UNIT).write_text("[Service]\n")
        self.lin.restart_server()
        self.assertEqual(self.cmds.ran[-1], ["systemctl", "--user", "--no-block", "restart", platform.HAL_UNIT])


    def test_the_setting_is_applied_at_once_but_never_from_a_test_home(self):
        from jobhunt.core import schedule_routes

        plat = mock.Mock(**{"start_at_login.return_value": "Hal starts when you sign in to this computer."})
        with mock.patch.object(platform, "current", return_value=plat):
            self.assertIsNone(schedule_routes.apply_start_at_login("schedule.start_at_login", False, True))
            plat.start_at_login.assert_not_called()
            with mock.patch("jobhunt.core.paths.home_override", return_value=None):
                words = schedule_routes.apply_start_at_login("schedule.start_at_login", False, True)
        self.assertEqual(words, "Hal starts when you sign in to this computer.")
        plat.start_at_login.assert_called_once_with(True)


class OldTimers(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.units = self.home / ".config" / "systemd" / "user"
        self.units.mkdir(parents=True)
        for timer in platform.LEGACY_TIMERS:
            (self.units / timer).write_text("[Timer]\n")
            (self.units / timer.replace(".timer", ".service")).write_text("[Service]\n")
        self.cmds = Commands({("systemctl", "--user", "is-enabled", t): "enabled\n" for t in platform.LEGACY_TIMERS})
        self.lin = LinuxPlatform(run=self.cmds.run, popen=self.cmds.popen, which=lambda name: f"/usr/bin/{name}",
                                 home=self.home)

    def test_the_old_systemd_timers_are_retired_once(self):
        self.assertEqual(self.lin.retire_legacy_timers(dry_run=True), list(platform.LEGACY_TIMERS))
        self.assertFalse([c for c in self.cmds.ran if "disable" in c], "a dry run changes nothing")
        self.assertEqual(self.lin.retire_legacy_timers(), list(platform.LEGACY_TIMERS))
        disabled = [c for c in self.cmds.ran if "disable" in c]
        self.assertEqual(disabled, [["systemctl", "--user", "disable", "--now", t] for t in platform.LEGACY_TIMERS])
        retired = self.units / platform.RETIRED_DIR
        self.assertEqual(sorted(p.name for p in retired.iterdir()),
                         sorted([*platform.LEGACY_TIMERS, *(t.replace(".timer", ".service")
                                                            for t in platform.LEGACY_TIMERS)]))
        self.cmds.answers.clear()                       # systemd no longer knows them
        self.assertEqual(self.lin.retire_legacy_timers(), [])
        self.assertEqual(len([c for c in self.cmds.ran if "disable" in c]), 3, "the second call does nothing")

    def test_undo_puts_the_timers_back_and_starts_them(self):
        self.lin.retire_legacy_timers()
        self.assertEqual(self.lin.restore_legacy_timers(), list(platform.LEGACY_TIMERS))
        self.assertTrue(all((self.units / t).exists() for t in platform.LEGACY_TIMERS))
        self.assertIn(["systemctl", "--user", "enable", "--now", "jobhunt-mail.timer"], self.cmds.ran)

    def test_enabled_timers_are_read_with_a_time_limit(self):
        seen = []

        def run(args, **kw):
            seen.append(kw.get("timeout"))
            return self.cmds.run(args, **kw)
        lin = LinuxPlatform(run=run, popen=self.cmds.popen, which=lambda n: f"/usr/bin/{n}", home=self.home)
        self.assertEqual(lin.legacy_timers_enabled(), list(platform.LEGACY_TIMERS))
        self.assertEqual(set(seen), {platform.TOOL_TIMEOUT_S})

    def test_the_module_calls_the_importer_uses_return_the_names(self):
        with mock.patch.object(platform, "current", return_value=self.lin):
            self.assertEqual(platform.retire_legacy_timers(), list(platform.LEGACY_TIMERS))
            self.assertEqual(platform.restore_legacy_timers(), list(platform.LEGACY_TIMERS))


class Open(unittest.TestCase):
    def setUp(self):
        self.enterContext(fresh_home())
        self.plat = mock.Mock()
        self.enterContext(mock.patch.object(platform, "current", return_value=self.plat))
        self.enterContext(mock.patch.object(system, "START_POLL_S", 0))
        self.args = mock.Mock(port=24321, next=None, print=False)

    def test_jobhunt_open_starts_hal_and_opens_one_window(self):
        answers = iter([False, False, True])
        with mock.patch("jobhunt.web.app.answering", side_effect=lambda port: next(answers)):
            system.cmd_open(self.args)
        argv = self.plat.spawn_detached.call_args.args[0]
        self.assertEqual(argv[1:], ["-m", "jobhunt", "serve", "--background", "--port", "24321"])
        self.assertEqual(self.plat.spawn_detached.call_args.kwargs, {"unit": system.SERVE_UNIT})
        self.plat.open_app_window.assert_called_once()
        self.assertIn("http://127.0.0.1:24321/open?t=", self.plat.open_app_window.call_args.args[0])

    def test_open_leaves_a_running_hal_alone_and_opens_the_app_even_in_developer_mode(self):
        settings.set("advanced.developer_mode", True)
        with mock.patch("jobhunt.web.app.answering", return_value=True), \
                mock.patch("jobhunt.web.auth.mint_open_url", return_value="http://127.0.0.1:24321/open?t=x") as mint:
            system.cmd_open(self.args)
        self.plat.spawn_detached.assert_not_called()
        self.assertIsNone(mint.call_args.kwargs["next"])
        self.plat.open_app_window.assert_called_once_with("http://127.0.0.1:24321/open?t=x")


if __name__ == "__main__":
    unittest.main()
