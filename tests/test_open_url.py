"""`open-url`: one line on stdout, a remote session, and Hal started first when it is not running.

Servers here are real Hal servers in temporary homes, or a tiny stand-in that answers /healthz when a test
only needs "something answers". Systemd is faked: a `systemctl` on PATH that reports a unit whose start
condition is unmet (as Hal's own unit is while Hal is switched off), and a `systemd-run` that records the
transient unit it was asked for and starts the stand-in.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import Client, HalServer

from jobhunt.cli import remote as cli_remote
from jobhunt.core import paths, platform
from jobhunt.core.platform.linux import LinuxPlatform
from jobhunt.remote import service
from jobhunt.web import app

RUN_S = 60
LINK = re.compile(r"^http://127\.0\.0\.1:(\d+)/open\?t=[A-Za-z0-9_-]+$")

FAKE_SYSTEMCTL = r'''#!{python}
"""systemctl --user: `start` succeeds and changes nothing (start condition unmet); `is-active` says inactive."""
import os, subprocess, sys
with open(os.environ["FAKE_SYSTEMD_LOG"], "a", encoding="utf-8") as log:
    log.write("systemctl " + " ".join(sys.argv[1:]) + "\n")
if "is-active" in sys.argv:
    print("active" if os.environ.get("FAKE_UNIT_STARTS") == "1" else "inactive")
    sys.exit(0 if os.environ.get("FAKE_UNIT_STARTS") == "1" else 3)
if "start" in sys.argv and os.environ.get("FAKE_UNIT_STARTS") == "1":
    subprocess.Popen([sys.executable, os.environ["FAKE_HEALTHZ"], os.environ["FAKE_UNIT_PORT"],
                      os.environ["FAKE_PIDS"]], start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
sys.exit(0)
'''

FAKE_SYSTEMD_RUN = r'''#!{python}
"""systemd-run --user: records the unit and starts the /healthz stand-in on the --port it was given."""
import os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["FAKE_SYSTEMD_LOG"], "a", encoding="utf-8") as log:
    log.write("systemd-run " + " ".join(args) + "\n")
port = args[args.index("--port") + 1]
subprocess.Popen([sys.executable, os.environ["FAKE_HEALTHZ"], port, os.environ["FAKE_PIDS"]], start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''

HEALTHZ = r'''"""Answers /healthz like Hal does, on 127.0.0.1:<port>, until killed."""
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
with open(sys.argv[2], "a", encoding="utf-8") as f:
    f.write(f"{os.getpid()}\n")

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hal_env(home: Path, port: int) -> dict:
    return {**os.environ, "HAL_EMPLOYMENT_HOME": str(home), "HAL_EMPLOYMENT_PORT": str(port), "HAL_TEST_MODE": "1"}


def _open_url(*extra: str) -> tuple[str, str, int]:
    """Runs the command in this process; (stdout, stderr, exit code)."""
    parser = argparse.ArgumentParser()
    cli_remote.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["open-url", *extra])
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args.fn(args)
        except SystemExit as e:
            code = e.code
    return out.getvalue(), err.getvalue(), code


class Link(unittest.TestCase):
    def test_open_url_prints_one_line_and_marks_the_session_remote(self):
        with HalServer() as hal:
            done = subprocess.run([sys.executable, "-m", "jobhunt", "open-url", "--remote", "--local-port", "23999",
                                   "--host-label", "deskbox"], env=_hal_env(hal.home, hal.port),
                                  capture_output=True, text=True, timeout=RUN_S)
            self.assertEqual(done.returncode, 0, done.stderr)
            lines = done.stdout.splitlines()
            self.assertEqual(len(lines), 1, done.stdout)
            self.assertEqual(done.stdout, lines[0] + "\n")
            match = LINK.match(lines[0])
            self.assertIsNotNone(match, lines[0])
            self.assertEqual(match.group(1), "23999", "the link carries the other computer's local port")
            url = urlparse(lines[0])
            c = hal.client()
            self.assertEqual(c.get(f"{url.path}?{url.query}").status, 302)
            me = c.get("/api/v2/session").json()
            self.assertTrue(me["remote"])
            self.assertEqual(me["remote_host"], "deskbox")
            self.assertEqual(me["caps"], {"open_folder": False, "window_mode": False, "sign_in_here": False,
                                          "start_at_login": False})
            self.assertEqual(c.get(f"{url.path}?{url.query}").status, 410, "the link works once")

    def test_a_link_without_remote_opens_a_window_on_this_computer(self):
        with HalServer() as hal, mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_PORT": str(hal.port)}):
            out, _, _ = _open_url()
            c = hal.client()
            url = urlparse(out.strip())
            self.assertEqual(c.get(f"{url.path}?{url.query}").status, 302)
            self.assertFalse(c.get("/api/v2/session").json()["remote"])


class FakePlatform:
    """A platform whose detached child is a real Hal server, run in a thread in the same home."""

    def __init__(self, name: str = "windows", kind: str = "none") -> None:
        self.name, self.kind, self.spawned, self.httpd = name, kind, [], None

    def service(self) -> str:
        return self.kind

    def spawn_detached(self, args: list[str], *, unit: str | None = None) -> int:
        self.spawned.append((args, unit))
        self.httpd = app.make_server(int(args[args.index("--port") + 1]))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return 0


class Starting(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="hal-open-url-")))
        self.enterContext(paths.set_override(self.home))
        paths.ensure()
        self.port = _free_port()
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_PORT": str(self.port)}))

    def test_open_url_starts_hal_when_it_is_not_running(self):
        fake = FakePlatform()
        self.assertFalse(app.answering(self.port))
        with mock.patch.object(platform, "current", return_value=fake):
            out, _, _ = _open_url("--remote", "--local-port", "24001")
        try:
            self.assertEqual(len(fake.spawned), 1)
            argv, unit = fake.spawned[0]
            self.assertEqual(argv[1:5], ["-m", "jobhunt", "serve", "--background"])
            self.assertEqual(unit, service.TRANSIENT_UNIT)
            self.assertRegex(out, r"^http://127\.0\.0\.1:24001/open\?t=\S+\n$")
            c = Client(self.port)
            url = urlparse(out.strip())
            self.assertEqual(c.get(f"{url.path}?{url.query}").status, 302)
            self.assertTrue(c.get("/api/v2/session").json()["remote"])
        finally:
            fake.httpd.stop()

    def test_open_url_says_why_and_exits_1_when_hal_cannot_start(self):
        fake = FakePlatform()
        fake.spawn_detached = lambda args, unit=None: 0
        with mock.patch.object(platform, "current", return_value=fake), \
                mock.patch.object(service, "START_WAIT_S", 0.5):
            out, err, code = _open_url("--remote")
        self.assertEqual((out, code), ("", 1))
        self.assertIn("Hal Employment did not start on this computer.", err)


@unittest.skipIf(os.name == "nt", "systemd units are Linux only")
class Systemd(unittest.TestCase):
    """Hal's own unit on the owner's desktop, with fake systemctl and systemd-run on PATH."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="hal-systemd-")))
        self.enterContext(paths.set_override(self.home / "hal"))
        paths.ensure()
        self.port = _free_port()
        fakes = self.home / "bin"
        fakes.mkdir()
        for name, text in (("systemctl", FAKE_SYSTEMCTL), ("systemd-run", FAKE_SYSTEMD_RUN)):
            (fakes / name).write_text(text.format(python=sys.executable), encoding="utf-8")
            (fakes / name).chmod(0o755)
        (self.home / "healthz.py").write_text(HEALTHZ, encoding="utf-8")
        self.log, self.pids = self.home / "systemd.log", self.home / "pids"
        self.enterContext(mock.patch.dict(os.environ, {
            "PATH": f"{fakes}{os.pathsep}{os.environ.get('PATH', '')}", "HAL_EMPLOYMENT_PORT": str(self.port),
            "FAKE_SYSTEMD_LOG": str(self.log), "FAKE_HEALTHZ": str(self.home / "healthz.py"),
            "FAKE_PIDS": str(self.pids), "FAKE_UNIT_PORT": str(self.port)}))
        self.addCleanup(self._kill)
        units = self.home / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / platform.HAL_UNIT).write_text("[Unit]\nConditionPathExists=!%h/.local/state/hal/off\n",
                                               encoding="utf-8")
        self.plat = LinuxPlatform(home=self.home)

    def _kill(self):
        for pid in (self.pids.read_text().split() if self.pids.exists() else []):
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(pid), signal.SIGTERM)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_open_url_starts_a_server_of_its_own_when_hals_unit_will_not_start(self):
        with mock.patch.object(platform, "current", return_value=self.plat):
            out, _, code = _open_url("--remote", "--local-port", "24002")
        self.assertEqual(code, 0)
        calls = self.calls()
        self.assertIn(f"systemctl --user start {platform.HAL_UNIT}", calls)
        self.assertIn(f"systemctl --user is-active {platform.HAL_UNIT}", calls)
        runs = [c for c in calls if c.startswith("systemd-run ")]
        self.assertEqual(len(runs), 1, calls)
        self.assertIn(f"--user --unit={service.TRANSIENT_UNIT} --collect", runs[0])
        self.assertIn("-m jobhunt serve --background", runs[0])
        self.assertNotIn("Condition", runs[0])
        self.assertRegex(out, r"^http://127\.0\.0\.1:24002/open\?t=\S+\n$")

    def test_open_url_uses_hals_own_unit_when_it_starts(self):
        with mock.patch.dict(os.environ, {"FAKE_UNIT_STARTS": "1"}), \
                mock.patch.object(platform, "current", return_value=self.plat):
            out, _, _ = _open_url("--remote")
        calls = self.calls()
        self.assertIn(f"systemctl --user start {platform.HAL_UNIT}", calls)
        self.assertFalse([c for c in calls if c.startswith("systemd-run ")], calls)
        self.assertRegex(out, rf"^http://127\.0\.0\.1:{self.port}/open\?t=\S+\n$")
