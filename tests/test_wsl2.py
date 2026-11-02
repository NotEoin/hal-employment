"""WSL2 differences, on a fake kernel release: the check, start at login, the terminal and the GPU holders."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.ai import gpu
from jobhunt.core.platform import wsl2
from jobhunt.core.platform.linux import NO_SYSTEMD, LinuxPlatform

WSL2_RELEASE = "5.15.153.1-microsoft-standard-WSL2"


class Wsl2Case(unittest.TestCase):
    def kernel(self, text: str) -> None:
        """Makes the platform check read `text` as the kernel release."""
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "osrelease").write_text(text + "\n")
        self.enterContext(mock.patch.dict(os.environ, {wsl2.OSRELEASE_ENV: str(folder / "osrelease")}))


class Detection(Wsl2Case):
    def test_a_wsl2_kernel_release_is_detected(self):
        self.kernel(WSL2_RELEASE)
        self.assertTrue(wsl2.is_wsl2())

    def test_a_plain_linux_kernel_release_is_not_wsl2(self):
        self.kernel("6.8.0-generic")
        self.assertFalse(wsl2.is_wsl2())

    def test_an_unreadable_release_is_not_wsl2(self):
        with mock.patch.dict(os.environ, {wsl2.OSRELEASE_ENV: "/nowhere/osrelease"}):
            self.assertFalse(wsl2.is_wsl2())


class Linux(Wsl2Case):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ran: list[list[str]] = []

        def run(args, **kw):
            self.ran.append(list(args))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        self.lin = LinuxPlatform(run=run, popen=mock.Mock(), which=lambda name: f"/usr/bin/{name}", home=self.home)

    def test_start_at_login_says_to_turn_systemd_on_under_wsl2_and_enables_nothing(self):
        self.kernel(WSL2_RELEASE)
        with mock.patch.object(wsl2, "SYSTEMD_RUN_DIR", self.home / "no-systemd"):
            said = self.lin.start_at_login(True)
        self.assertTrue(said.startswith(NO_SYSTEMD))
        self.assertIn("/etc/wsl.conf", said)
        self.assertEqual(self.ran, [])

    def test_start_at_login_works_under_wsl2_when_systemd_runs(self):
        self.kernel(WSL2_RELEASE)
        with mock.patch.object(wsl2, "SYSTEMD_RUN_DIR", self.home):
            said = self.lin.start_at_login(True)
        self.assertEqual(said, "Hal starts when you sign in to this computer.")
        self.assertIn(["systemctl", "--user", "enable", "hal-employment-app.service"], self.ran)

    def test_start_at_login_does_not_check_for_systemd_on_plain_linux(self):
        self.kernel("6.8.0-generic")
        with mock.patch.object(wsl2, "SYSTEMD_RUN_DIR", self.home / "no-systemd"):
            said = self.lin.start_at_login(True)
        self.assertEqual(said, "Hal starts when you sign in to this computer.")

    def test_no_terminal_under_wsl2_names_xterm_and_the_how_to(self):
        self.kernel(WSL2_RELEASE)
        lin = LinuxPlatform(run=mock.Mock(), popen=mock.Mock(), which=lambda name: None, home=self.home)
        with self.assertRaisesRegex(RuntimeError, "apt-get install -y xterm"):
            lin.open_console(["claude"], "Sign in")

    def test_no_terminal_on_plain_linux_has_no_wsl2_advice(self):
        self.kernel("6.8.0-generic")
        lin = LinuxPlatform(run=mock.Mock(), popen=mock.Mock(), which=lambda name: None, home=self.home)
        with self.assertRaises(RuntimeError) as caught:
            lin.open_console(["claude"], "Sign in")
        self.assertNotIn("WSL2", str(caught.exception))


class GpuHolders(Wsl2Case):
    def smi(self, text: str):
        done = subprocess.CompletedProcess([], 0, stdout=text, stderr="")
        return mock.patch.object(gpu.subprocess, "run", return_value=done)

    def test_a_process_nvidia_smi_cannot_name_is_not_a_holder_under_wsl2(self):
        self.kernel(WSL2_RELEASE)
        with self.smi("4242, [N/A], 900\n"):
            self.assertEqual(gpu.holders(), [])

    def test_a_named_process_is_a_holder_under_wsl2(self):
        self.kernel(WSL2_RELEASE)
        with self.smi("4242, /opt/made-up-trainer, 900\n"):
            self.assertEqual(gpu.holders(), [("made-up-trainer", 900)])

    def test_an_unnamed_process_still_counts_on_plain_linux(self):
        self.kernel("6.8.0-generic")
        with self.smi("4242, [N/A], 900\n"):
            self.assertEqual(len(gpu.holders()), 1)


if __name__ == "__main__":
    unittest.main()
