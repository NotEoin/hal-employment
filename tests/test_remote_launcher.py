"""The laptop launchers: filled in for this desktop, safe to run, and working end to end.

The end-to-end tests run the real Linux launcher against a real Hal server in a temporary home. A fake
`ssh` stands in for OpenSSH: it forwards the tunnel's port on 127.0.0.1 and runs the desktop's commands in a
made-up desktop home whose `~/.local/bin/hal-employment-cli` is the shim. A fake `xdg-open` opens the link
the launcher got and records what Hal answered. No real SSH, browser or account is used.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer

from jobhunt.core import paths
from jobhunt.remote import launcher

HOST, USER = "deskbox", "sam"
RUN_WAIT_S = 60          # the launcher's own waits add up to well under this
POLL_S = 0.1
STAYS_OPEN_S = 1.0       # long enough for a launcher that is about to close to have closed
CRLF = b"\r\n"

FAKE_SSH = r'''#!{python}
"""A stand-in for OpenSSH: forwards -L on 127.0.0.1, or runs the command in the made-up desktop home.

Like OpenSSH, a call that finds a live control master hands its -L to the master and exits at once, and
`-O check` and `-O exit` act on that master. The control socket is a file holding the master's pid.
"""
import atexit, os, signal, socket, subprocess, sys, threading, time

args = sys.argv[1:]
SERVE = args[:1] == ["--serve"]  # a forward handed to the master: lives until the master ends
if not SERVE:
    with open(os.environ["FAKE_SSH_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\n")
forward = control = None
opts = {{}}
i = 0
while i < len(args) and args[i].startswith("-"):
    if args[i] in ("-o", "-L", "-O", "-p", "-i"):
        if args[i] == "-L":
            forward = args[i + 1]
        if args[i] == "-O":
            control = args[i + 1]
        if args[i] == "-o":
            key, _, value = args[i + 1].partition("=")
            opts.setdefault(key, value)  # ssh keeps the first value given
        i += 2
    else:
        i += 1
rest = args[i:]
ctl = opts.get("ControlPath", "").replace("%C", "fake") or None


def master_pid():
    try:
        pid = int(open(ctl).read())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, TypeError):
        return None


def serve(spec, master=None):
    if master:
        def watch():
            while True:
                try:
                    os.kill(master, 0)
                except OSError:
                    os._exit(0)
                time.sleep(0.1)
        threading.Thread(target=watch, daemon=True).start()
    lhost, lport, rhost, rport = spec.split(":")
    srv = socket.socket()
    try:
        srv.bind((lhost, int(lport)))
    except OSError:
        sys.exit(255)
    srv.listen(16)

    def pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    while True:
        c, _ = srv.accept()
        u = socket.create_connection((rhost, int(rport)))
        threading.Thread(target=pipe, args=(c, u), daemon=True).start()
        threading.Thread(target=pipe, args=(u, c), daemon=True).start()


if SERVE:
    serve(args[1], int(args[2]))
if control:
    pid = master_pid() if ctl else None
    if control == "exit" and pid:
        os.kill(pid, signal.SIGTERM)
    sys.exit(0 if pid else 255)
mode = opts.get("ControlMaster", "no")
if ctl and master_pid() and mode != "yes":
    if forward:  # the master takes the forward over; this client is done
        subprocess.Popen([sys.executable, __file__, "--serve", forward, str(master_pid())])
        sys.exit(0)
elif ctl and mode in ("yes", "auto"):
    persist = opts.get("ControlPersist", "no")
    if forward or persist == "no":
        with open(ctl, "w") as f:
            f.write(str(os.getpid()))
        atexit.register(lambda: os.path.exists(ctl) and os.remove(ctl))
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    else:  # ControlPersist: a background master outlives this call
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        with open(ctl, "w") as f:
            f.write(str(holder.pid))
if forward:
    serve(forward)
env = dict(os.environ, HOME=os.environ["FAKE_SSH_DESKTOP_HOME"])
sys.exit(subprocess.run(["bash", "-c", " ".join(rest[1:])], env=env).returncode)
'''

FAKE_BROWSER = r'''#!{python}
"""A stand-in for xdg-open and the default browser: opens the link and records what Hal answered."""
import http.client, json, os, sys
from urllib.parse import urlparse

url = urlparse(sys.argv[-1])
con = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
con.request("GET", url.path + "?" + url.query, headers={{"Host": url.netloc}})
r = con.getresponse(); r.read()
cookies = "; ".join(c.split(";", 1)[0] for c in r.headers.get_all("Set-Cookie") or [])
con = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
con.request("GET", "/api/v2/session", headers={{"Host": url.netloc, "Cookie": cookies}})
s = con.getresponse()
out = {{"url": url.geturl(), "open": r.status, "session": s.status, "body": json.loads(s.read() or b"{{}}")}}
with open(os.environ["FAKE_BROWSER_LOG"] + ".tmp", "w", encoding="utf-8") as f:
    json.dump(out, f)
os.replace(os.environ["FAKE_BROWSER_LOG"] + ".tmp", os.environ["FAKE_BROWSER_LOG"])
'''

SHIM = '#!/bin/sh\nexec "{python}" -m jobhunt "$@"\n'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


class Filled(unittest.TestCase):
    def test_the_launchers_are_filled_in_with_host_user_and_port(self):
        for os_name in ("linux", "mac"):
            text = launcher.render(os_name, host=HOST, user=USER, port=24680).decode("ascii")
            self.assertIn('HOST="sam@deskbox"; PORT=24680', text)
            self.assertIn("HAL='~/.local/bin/hal-employment-cli'", text)
            self.assertIn("StrictHostKeyChecking=accept-new", text)
            self.assertIn("ExitOnForwardFailure=yes", text)
            self.assertIn('-L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${PORT}"', text)
            self.assertNotIn("USER@DESKTOP", text)
            self.assertNotIn("\r", text)
        data = launcher.render("windows", host=HOST, user=USER, port=24680)
        self.assertNotIn(b"\n", data.replace(CRLF, b""), "every line of the .cmd ends in CRLF")
        text = data.decode("ascii")
        for line in ('set "HOST=sam@deskbox"', 'set "PORT=24680"', 'set "LPORT=24680"',
                     'set "HAL=~/.local/bin/hal-employment-cli"'):
            self.assertIn(line + "\r\n", text)
        lines = text.split("\r\n")
        for line in lines:
            if "%URL%" in line and not line.startswith("rem "):
                self.assertRegex(line, r'"[^"]*%URL%[^"]*"', f"%URL% is quoted: {line}")
            if "msedge" in line and not line.startswith("rem "):
                self.assertTrue(line.startswith('start "" msedge'), f"msedge runs only through start: {line}")
        self.assertTrue(any(re.match(r'for /f "usebackq delims=" %%u in \(`%SSH% .*open-url --remote', line)
                            for line in lines), "the link is captured with for /f")
        self.assertIn('start "Hal tunnel" /min %SSH%', text)

    def test_the_launchers_refuse_names_that_could_run_commands(self):
        for host, user in (("desk;rm -rf ~", USER), (HOST, "sam$(id)"), ("", USER), (HOST, "a b")):
            with self.subTest(host=host, user=user), self.assertRaises(ValueError):
                launcher.render("linux", host=host, user=user, port=8737)
        with self.assertRaises(ValueError):
            launcher.render("amiga", host=HOST, user=USER, port=8737)

    def test_the_launcher_calls_hal_employment_cli_not_hals_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bin_dir = home / ".local" / "bin"
            bin_dir.mkdir(parents=True)
            # Hal's own command on the owner's desktop: it ignores its arguments and opens a browser tab
            (bin_dir / "hal-employment").write_text("#!/bin/sh\nxdg-open http://127.0.0.1:8737/\n", encoding="utf-8")
            # the desktop is Linux; a Windows runner's checkout (D:/a/...) is refused for its colon
            with mock.patch.object(launcher.paths, "code_dir", return_value=Path("/home/sam/hal-employment")):
                command = launcher.hal_command(home)
            self.assertNotIn("hal-employment ", command + " ")
            self.assertTrue(command.endswith(" -m jobhunt"), command)
            (bin_dir / "hal-employment-cli").write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual(launcher.hal_command(home), "~/.local/bin/hal-employment-cli")
        for os_name in ("linux", "windows"):
            text = launcher.render(os_name, host=HOST, user=USER, port=8737).decode("ascii")
            self.assertNotRegex(text, r"\.local/bin/hal-employment(?!-cli)")

    def test_a_windows_code_folder_with_a_drive_letter_is_called_through_its_venv(self):
        # regression: CI 2026-10-23, D:\a\hal-employment\hal-employment was refused as "a space or symbol"
        # and the launcher pointed at .venv/bin/python, which Windows does not have
        runner_folder = PureWindowsPath(r"D:\a\hal-employment\hal-employment")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(paths, "platform_name", return_value="windows"), \
                mock.patch.object(paths, "code_dir", return_value=runner_folder):
            command = launcher.hal_command(Path(tmp))
            self.assertEqual(command, "D:/a/hal-employment/hal-employment/.venv/Scripts/python.exe -m jobhunt")
        for os_name in ("linux", "windows"):
            self.assertIn(command.encode(), launcher.render(os_name, host=HOST, user=USER, port=8737, hal=command))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(paths, "platform_name", return_value="windows"), \
                mock.patch.object(paths, "code_dir", return_value=PureWindowsPath(r"C:\Users\Sam Smith\hal")), \
                self.assertRaises(ValueError):
            launcher.hal_command(Path(tmp))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(paths, "platform_name", return_value="linux"), \
                mock.patch.object(paths, "code_dir", return_value=PurePosixPath("/home/sam/hal-employment")):
            self.assertEqual(launcher.hal_command(Path(tmp)), "/home/sam/hal-employment/.venv/bin/python -m jobhunt")
        for hal in ("/home/sam/a:b -m jobhunt", "D:/hal & calc -m jobhunt"):
            with self.subTest(hal=hal), self.assertRaises(ValueError):
                launcher.render("windows", host=HOST, user=USER, port=8737, hal=hal)

    @unittest.skipIf(os.name == "nt", "bash -n checks the Linux and Mac launcher, which runs only there")
    def test_the_shell_launcher_is_valid_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hal-remote.sh"
            path.write_bytes(launcher.render("linux", host=HOST, user=USER, port=8737))
            done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(done.returncode, 0, done.stderr)


@unittest.skipIf(os.name == "nt", "the shell launcher runs on Linux and Mac laptops; Windows has the .cmd")
@unittest.skipUnless(all(shutil.which(t) for t in ("bash", "curl", "tar")), "needs bash, curl and tar")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="hal-laptop-")))
        self.laptop, desktop, fakes = tmp / "laptop", tmp / "desktop", tmp / "bin"
        self.laptop.mkdir()
        _script(desktop / ".local" / "bin" / "hal-employment-cli", SHIM.format(python=sys.executable))
        _script(fakes / "ssh", FAKE_SSH.format(python=sys.executable))
        _script(fakes / "xdg-open", FAKE_BROWSER.format(python=sys.executable))
        self.ssh_log, self.browser_log = tmp / "ssh.log", tmp / "browser.json"
        self.script = self.laptop / "hal-remote.sh"
        self.script.write_bytes(launcher.render("linux", host=HOST, user=USER, port=self.hal.port))
        self.script.chmod(0o755)
        self.env = {"PATH": f"{fakes}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(self.laptop),
                    "FAKE_SSH_LOG": str(self.ssh_log), "FAKE_SSH_DESKTOP_HOME": str(desktop),
                    "FAKE_BROWSER_LOG": str(self.browser_log), "HAL_EMPLOYMENT_HOME": str(self.hal.home),
                    "HAL_EMPLOYMENT_PORT": str(self.hal.port), "HAL_TEST_MODE": "1"}

    def run_launcher(self, local_port: int) -> dict:
        """Runs the launcher until the fake browser has opened Hal, then closes its window; what the browser saw."""
        proc = subprocess.Popen([str(self.script)], env={**self.env, "HAL_LOCAL_PORT": str(local_port)},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + RUN_WAIT_S
            while not self.browser_log.exists():
                if proc.poll() is not None:
                    out, err = proc.communicate(timeout=RUN_WAIT_S)
                    self.fail(f"the launcher ended early ({proc.returncode}): {out!r} {err!r}")
                if time.monotonic() > deadline:
                    self.fail("the fake browser was never opened")
                time.sleep(POLL_S)
            seen = json.loads(self.browser_log.read_text(encoding="utf-8"))
            # regression: the tunnel joined the helper call's control master and exited at once, so the
            # window closed as soon as Hal opened and took the tunnel with it
            time.sleep(STAYS_OPEN_S)
            self.assertIsNone(proc.poll(), "the launcher's window closed while Hal was open")
            port = urlport(seen["url"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=RUN_WAIT_S) as r:
                self.assertEqual(r.status, 200)
            return seen
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            proc.communicate(timeout=RUN_WAIT_S)

    def test_the_launcher_copies_the_helper_and_opens_a_remote_session(self):
        local = _free_port()
        seen = self.run_launcher(local)
        self.assertTrue(seen["url"].startswith(f"http://127.0.0.1:{local}/open?t="), seen["url"])
        self.assertEqual(seen["open"], 302)
        self.assertEqual(seen["session"], 200)
        self.assertTrue(seen["body"]["remote"])
        self.assertEqual(seen["body"]["remote_host"], socket.gethostname())
        self.assertFalse(seen["body"]["caps"]["open_folder"])
        helper = self.laptop / "Hal Employment" / "Browser helper"
        self.assertTrue((helper / "manifest.json").is_file())
        self.assertFalse((helper.parent / "Browser helper.new").exists())
        calls = self.ssh_log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("~/.local/bin/hal-employment-cli helper-files --tar" in c for c in calls))
        self.assertTrue(any(f"open-url --remote --local-port {local}" in c for c in calls))
        tunnel = [c for c in calls if " -N " in f" {c} "]
        self.assertEqual(len(tunnel), 1)
        self.assertIn(f"-L 127.0.0.1:{local}:127.0.0.1:{self.hal.port}", tunnel[0])
        for option in ("StrictHostKeyChecking=accept-new", "ExitOnForwardFailure=yes", "ServerAliveInterval=15",
                       "ControlMaster=yes", "ControlPersist=no"):
            self.assertIn(option, tunnel[0])
        self.assertTrue(all(c.rstrip().endswith(f"{USER}@{HOST}") or f"{USER}@{HOST} " in c for c in calls))

    def test_a_tunnel_on_another_local_port_works(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            taken = busy.getsockname()[1]
            seen = self.run_launcher(taken)
        self.assertNotEqual(urlport(seen["url"]), taken)
        self.assertGreater(urlport(seen["url"]), taken)
        self.assertEqual((seen["open"], seen["session"]), (302, 200))
        self.assertTrue(seen["body"]["remote"])

    def test_install_adds_a_menu_entry_that_opens_in_a_terminal(self):
        done = subprocess.run([str(self.script), "--install"], env=self.env, capture_output=True, text=True,
                              timeout=RUN_WAIT_S)
        self.assertEqual(done.returncode, 0, done.stderr)
        entry = (self.laptop / ".local" / "share" / "applications" / "hal-employment-desktop.desktop").read_text(
            encoding="utf-8")
        self.assertIn("Name=Hal Employment (desktop)\n", entry)
        self.assertIn("Terminal=true\n", entry)
        self.assertIn(f'Exec="{self.script}"', entry)


def urlport(url: str) -> int:
    """The port of a link."""
    return int(re.match(r"http://127\.0\.0\.1:(\d+)/", url).group(1))
