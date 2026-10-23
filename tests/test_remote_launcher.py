"""The laptop launchers: filled in for this desktop, safe to run, and working end to end.

The end-to-end tests run the real Linux launcher against a real Hal server in a temporary home. A fake
`ssh` stands in for OpenSSH: it forwards the tunnel's port on 127.0.0.1 and runs the desktop's commands in a
made-up desktop home whose `~/.local/bin/hal-employment-cli` is the shim. A fake `chromium` opens the link
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
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer

from jobhunt.remote import launcher

HOST, USER = "deskbox", "sam"
RUN_WAIT_S = 60          # the launcher's own waits add up to well under this
POLL_S = 0.1
CRLF = b"\r\n"

FAKE_SSH = r'''#!{python}
"""A stand-in for OpenSSH: forwards -L on 127.0.0.1, or runs the command in the made-up desktop home."""
import os, socket, subprocess, sys, threading

args = sys.argv[1:]
with open(os.environ["FAKE_SSH_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\n")
forward = control = None
i = 0
while i < len(args) and args[i].startswith("-"):
    if args[i] in ("-o", "-L", "-O", "-p", "-i"):
        if args[i] == "-L":
            forward = args[i + 1]
        if args[i] == "-O":
            control = args[i + 1]
        i += 2
    else:
        i += 1
rest = args[i:]
if control:
    sys.exit(0)
if forward:
    lhost, lport, rhost, rport = forward.split(":")
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
env = dict(os.environ, HOME=os.environ["FAKE_SSH_DESKTOP_HOME"])
sys.exit(subprocess.run(["bash", "-c", " ".join(rest[1:])], env=env).returncode)
'''

FAKE_BROWSER = r'''#!{python}
"""A stand-in for a Chromium browser: opens the --app link and records what Hal answered."""
import http.client, json, os, sys
from urllib.parse import urlparse

url = urlparse(next(a for a in sys.argv[1:] if a.startswith("--app="))[len("--app="):])
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
            command = launcher.hal_command(home)
            self.assertNotIn("hal-employment ", command + " ")
            self.assertTrue(command.endswith(" -m jobhunt"), command)
            (bin_dir / "hal-employment-cli").write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual(launcher.hal_command(home), "~/.local/bin/hal-employment-cli")
        for os_name in ("linux", "windows"):
            text = launcher.render(os_name, host=HOST, user=USER, port=8737).decode("ascii")
            self.assertNotRegex(text, r"\.local/bin/hal-employment(?!-cli)")

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
        _script(fakes / "chromium", FAKE_BROWSER.format(python=sys.executable))
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
            return json.loads(self.browser_log.read_text(encoding="utf-8"))
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
        for option in ("StrictHostKeyChecking=accept-new", "ExitOnForwardFailure=yes", "ServerAliveInterval=15"):
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
