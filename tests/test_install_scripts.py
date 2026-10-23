"""The install and update scripts: ASCII, line endings, pinned downloads, the `.cmd` rule.

cmd.exe re-reads a batch file from disk by byte offset after each command, so a `.cmd` that git replaces
while it runs would carry on in the middle of the new text. Every `.cmd` in the clone is held to one of the
two allowed shapes.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import support  # noqa: F401 - must come before jobhunt

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts" / "install"
WINDOWS = INSTALL / "install-hal-windows.cmd"
LINUX = INSTALL / "install-hal-linux.sh"
UPDATE_CMD = ROOT / "scripts" / "update-hal.cmd"
UPDATE_SH = ROOT / "scripts" / "update-hal.sh"
# a template the laptop fills in and runs outside any clone, so git never replaces it mid-run
LAPTOP_LAUNCHER = ROOT / "scripts" / "remote" / "hal-remote.cmd"
URL = re.compile(r"https://github\.com/[^\s'\"]+/releases/download/[^\s'\"]+")
SHA = re.compile(r"^(|[0-9a-f]{64})$")
BATCH_QUIET = re.compile(r'^(@echo off|rem( .*)?|set "[A-Z_]+=.*"|)$', re.I)
COPY_SELF = re.compile(r'^if not "%~1"=="--copy" \(copy /y "%~f0" "%HAL_COPY%" >nul & '
                       r'"%HAL_COPY%" --copy .* & exit /b\)$')
PARSE_TIMEOUT_S = 60


def pins_module():
    """scripts/install/pins.py, loaded by path."""
    spec = importlib.util.spec_from_file_location("hal_install_pins", INSTALL / "pins.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def batch_lines(path: Path) -> list[str]:
    """The batch part of a `.cmd` (before `#PS1#`), without line endings."""
    text = path.read_bytes().decode("ascii").split("\r\n")
    return text[:text.index("#PS1#")] if "#PS1#" in text else text


def powershell_part(path: Path) -> str:
    """The PowerShell after `#PS1#`, as the install file's first line splits it."""
    return re.split(r"(?m)^#PS1#\r?$", path.read_bytes().decode("ascii"), maxsplit=1)[1]


def linux_bash() -> str | None:
    """A bash that can parse the Linux scripts, or None.

    On Windows, `bash` on PATH is often System32's launcher for WSL, which fails with no message on stderr
    when no Linux is installed (windows-2025 runners, 2026-10-23); Git for Windows' own bash is used there.
    """
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if not git:
        return None
    for top in Path(git).resolve().parents[:3]:
        for bash in (top / "bin" / "bash.exe", top / "usr" / "bin" / "bash.exe"):
            if bash.is_file():
                return str(bash)
    return None


def cmd_files() -> list[Path]:
    """Every `.cmd` in the clone outside the virtual environment and caches."""
    return sorted(p for p in ROOT.rglob("*.cmd") if not ({".venv", ".git", ".tmp-home", "node_modules"} & set(p.parts)))


class Shapes(unittest.TestCase):
    def test_the_bootstrap_files_are_ascii_crlf_and_pin_their_downloads(self):
        for path in (*cmd_files(), *ROOT.joinpath("scripts").rglob("*.ps1")):
            data = path.read_bytes()
            data.decode("ascii")
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), f"{path.name} has a line not ended by CRLF")
        for path in (LINUX, UPDATE_SH):
            data = path.read_bytes()
            data.decode("ascii")
            self.assertNotIn(b"\r", data, f"{path.name} must use LF")
        pins = tomllib.loads((INSTALL / "pins.toml").read_text(encoding="utf-8"))
        urls = {}
        for tool, systems in pins.items():
            for system, pin in systems.items():
                self.assertIn(pin["version"], pin["url"], f"{tool} {system} is not pinned by version")
                self.assertRegex(pin["sha256"], SHA, f"{tool} {system} has a malformed SHA-256")
                urls[pin["url"]] = pin
        self.assertEqual(pins_module().check(), [])
        for path in (WINDOWS, LINUX):
            text = path.read_bytes().decode("ascii")
            found = URL.findall(text)
            self.assertTrue(found)
            for url in found:
                self.assertIn(url, urls, f"{path.name} downloads {url}, which pins.toml does not pin")
        windows = powershell_part(WINDOWS)
        self.assertIn("Get-FileHash -Algorithm SHA256", windows)
        self.assertIn("it has no checksum for", windows)
        self.assertIn('sha256sum "$4"', LINUX.read_text(encoding="ascii"))

    def test_the_windows_install_file_is_one_file_with_its_powershell_after_ps1(self):
        lines = batch_lines(WINDOWS)
        self.assertEqual(lines[0], "@echo off")
        runs = [line for line in lines if not BATCH_QUIET.match(line)]
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].startswith("powershell -NoProfile -ExecutionPolicy Bypass -Command "))
        self.assertTrue(runs[0].endswith("& pause & exit /b"))
        self.assertIn("$env:HAL_SELF", runs[0])

    def test_every_cmd_in_the_clone_survives_being_replaced_while_it_runs(self):
        files = cmd_files()
        self.assertIn(UPDATE_CMD, files)
        self.assertIn(WINDOWS, files)
        for path in (p for p in files if p != LAPTOP_LAUNCHER):
            lines = batch_lines(path)
            runs = [line for line in lines if not BATCH_QUIET.match(line)]
            with self.subTest(cmd=path.name):
                if path == UPDATE_CMD:
                    # it runs while git changes the clone: its first command copies it to %TEMP% and runs that
                    self.assertRegex(runs[0], COPY_SELF)
                    self.assertIn('set "HAL_COPY=%TEMP%\\hal-update-%RANDOM%.cmd"', lines)
                    continue
                # any other: one command line, last, so there is nothing after it to resume into; one that
                # pauses keeps its pause and its exit /b on that same line
                self.assertEqual(len(runs), 1, f"{path.name} runs more than one line")
                self.assertEqual(lines[-1] if lines[-1] else lines[-2], runs[0])
                if "pause" in runs[0]:
                    self.assertRegex(runs[0], r"(& pause|pause >nul) & exit /b\)?$")

    def test_a_busy_log_file_never_stops_the_install(self):
        # Add-Content failed with "being used by another process" and printed a red error
        text = powershell_part(WINDOWS)
        body = text[text.index("function Write-Log"):text.index("function Say")]
        self.assertIn("-ErrorAction Stop; return } catch { Start-Sleep -Milliseconds $LogWaitMs }", body)
        self.assertIn("for ($try = 0; $try -lt $LogTries; $try++)", body)

    def test_the_install_window_is_named_for_hal(self):
        # the console's title was C:\\WINDOWS\\system32\\cmd.exe
        self.assertIn("$Host.UI.RawUI.WindowTitle = 'Installing Hal Employment'", powershell_part(WINDOWS))

    def test_the_windows_install_calls_only_the_bundled_git(self):
        text = powershell_part(WINDOWS)
        self.assertIn("$Git = Join-Path $GitDir 'cmd\\git.exe'", text)
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            self.assertNotRegex(code, r"(^|[\s&(])git(\.exe)?\s", f"a bare git call: {line.strip()}")
        self.assertNotRegex(text, r"\$env:PATH\s*=|setx\s", "the install must not change PATH")

    def test_the_install_files_name_the_public_repository(self):
        url = "https://github.com/NotEoin/hal-employment.git"
        self.assertIn(f'set "HAL_REPO={url}"', batch_lines(WINDOWS))
        self.assertIn(f'HAL_REPO="${{HAL_REPO:-{url}}}"', LINUX.read_text(encoding="ascii"))
        self.assertEqual(tomllib.loads((INSTALL / "repo.toml").read_text(encoding="utf-8"))["slug"],
                         "NotEoin/hal-employment")
        self.assertNotIn("OWNER/REPO", WINDOWS.read_text(encoding="ascii") + LINUX.read_text(encoding="ascii"))

    def test_the_windows_steps_are_counted_and_the_older_hal_step_is_optional(self):
        text = powershell_part(WINDOWS)
        self.assertIn("$Total = 6 + [int]$HasOld", text)
        self.assertRegex(text, r"if \(\$HasOld\) \{\r?\n  Say-Step 'Copying")
        self.assertNotIn("Looking for an older Hal", text)

    def test_both_install_files_end_by_opening_the_setup_guide(self):
        self.assertIn("-m jobhunt open --next /#setup'", powershell_part(WINDOWS))
        self.assertIn('-m jobhunt open --next "/#setup"', LINUX.read_text(encoding="ascii"))

    def test_the_update_script_reads_the_uv_folders_from_the_install_record(self):
        last = next(line for line in batch_lines(UPDATE_CMD) if line.startswith("powershell"))
        for part in ("install.json", "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR", "run --no-project --python 3.13",
                     "updater.py"):
            self.assertIn(part, last)
        self.assertTrue(last.endswith("pause >nul & exit /b"))

    def test_the_allowed_signers_file_ships_without_a_key(self):
        text = (INSTALL / "allowed_signers").read_text(encoding="ascii")
        self.assertTrue(all(not line.strip() or line.lstrip().startswith("#") for line in text.splitlines()))

    @unittest.skipUnless(linux_bash(), "bash checks the Linux scripts (Git for Windows' bash on Windows)")
    def test_the_shell_scripts_parse(self):
        for path in (LINUX, UPDATE_SH):
            done = subprocess.run([linux_bash(), "-n", path.as_posix()], capture_output=True, text=True,
                                  timeout=PARSE_TIMEOUT_S, check=False)
            self.assertEqual(done.returncode, 0, f"{path.name}: {done.stdout}{done.stderr}")

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 5.1 is only on Windows (the Windows CI runs this)")
    def test_the_powershell_part_parses(self):
        folder = Path(tempfile.mkdtemp(prefix="hal-ps1-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        script = folder / "install.ps1"
        script.write_text(powershell_part(WINDOWS), encoding="ascii")
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:HAL_PS1, "
                 "[ref]$null, [ref]$e); if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }")
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", check],
                              env={**os.environ, "HAL_PS1": str(script)}, capture_output=True, text=True,
                              timeout=PARSE_TIMEOUT_S, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


GH_SIGNED_OUT = """#!/bin/sh
echo "gh $*" >> "$FAKE_LOG"
case "$*" in "auth status"*) exit 1 ;; esac
exit 0
"""
FAKE_GIT = """#!/bin/sh
echo "git $*" >> "$FAKE_LOG"
case "$*" in
  *clone*)
    for last; do :; done
    made() { mkdir -p "$last/.git" "$last/.venv/bin"; printf '#!/bin/sh\\nexit 0\\n' > "$last/.venv/bin/python"
             chmod +x "$last/.venv/bin/python"; exit 0; }
    case "$*" in *credential.https://github.com.helper=*) made ;; esac
    case "$FAKE_ANON" in
      open) made ;;
      unreachable) echo "fatal: unable to access: Could not resolve host: github.com" >&2; exit 128 ;;
      *) echo "remote: Repository not found." >&2; echo "fatal: Authentication failed" >&2; exit 128 ;;
    esac ;;
esac
exit 0
"""


@unittest.skipUnless(linux_bash() and os.name != "nt", "the Linux install file runs under bash with fake tools")
class LinuxSignIn(unittest.TestCase):
    """The Linux install file signs in to GitHub only when the repository cannot be read without it."""

    def run_install(self, anonymous: str) -> tuple[int, str, str]:
        """Runs the install file with fake git, gh, uv and curl; returns (exit code, output, tool call log)."""
        home = Path(tempfile.mkdtemp(prefix="hal-install-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        fake = home / "bin"
        tools = home / ".local" / "opt" / "hal-employment" / "tools"
        for folder in (fake, tools):
            folder.mkdir(parents=True)
        calls = home / "calls.log"
        scripts = {fake / "git": FAKE_GIT, fake / "curl": "#!/bin/sh\nexit 0\n",
                   tools / "uv": "#!/bin/sh\nexit 0\n", tools / "gh": GH_SIGNED_OUT}
        for path, body in scripts.items():
            path.write_text(body, encoding="ascii")
            path.chmod(0o755)
        env = {"HOME": str(home), "XDG_DATA_HOME": str(home / "data"), "PATH": f"{fake}:/usr/bin:/bin",
               "FAKE_LOG": str(calls), "FAKE_ANON": anonymous}
        done = subprocess.run([linux_bash(), LINUX.as_posix()], env=env, capture_output=True, text=True,
                              timeout=PARSE_TIMEOUT_S, check=False)
        return done.returncode, done.stdout, calls.read_text(encoding="ascii") if calls.exists() else ""

    def test_a_readable_repository_is_cloned_without_signing_in(self):
        code, out, calls = self.run_install("open")
        self.assertEqual(code, 0, out)
        self.assertNotIn("gh ", calls)
        self.assertNotIn("sign in", out)

    def test_a_repository_that_needs_a_login_triggers_the_github_sign_in(self):
        code, out, calls = self.run_install("login")
        self.assertEqual(code, 0, out)
        self.assertIn("gh auth login", calls)
        self.assertIn("sign in", out)

    def test_an_unreachable_github_stops_without_signing_in(self):
        code, out, calls = self.run_install("unreachable")
        self.assertEqual(code, 1)
        self.assertNotIn("gh ", calls)
        self.assertIn("could not reach GitHub", out)


if __name__ == "__main__":
    unittest.main()
