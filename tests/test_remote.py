"""Windows opened from another computer, against a real server in a temporary home.

A "remote" window is one signed in through an `open-url --remote` link, as the laptop launcher does. The
browser helper on the laptop is paired from that window and polls over HTTP; desktop pop-ups go to a
fake platform. Everyone and everything here is made up.
"""
from __future__ import annotations

import contextlib
import http.client
import ipaddress
import os
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from support.world import World, job_id
from test_helpers import answer_one, session_id_of
from test_helpers import poll as helper_poll
from test_pairing import HelperTest, pair

from jobhunt import browser, db, notify, record
from jobhunt.core import events, paths, settings
from jobhunt.evidence import routes as record_routes
from jobhunt.remote import instructions, launcher, sessions
from jobhunt.web import app, sse

WAIT_S = 10.0
POLL_S = 0.05
DESK = "deskbox"


def _until(check, what: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(POLL_S)


class EventStream:
    """One open /api/v2/events stream of a client, read line by line in a thread."""

    def __init__(self, port: int, cookies: dict[str, str]) -> None:
        self.con = http.client.HTTPConnection("127.0.0.1", port, timeout=WAIT_S)
        # our own socket, because the connection lets go of its socket once a reply with no length starts
        self.sock = self.con.sock = socket.create_connection(("127.0.0.1", port), timeout=WAIT_S)
        self.con.request("GET", "/api/v2/events", headers={
            "Host": f"127.0.0.1:{port}", "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())})
        self.reply = self.con.getresponse()
        self.lines: list[str] = []
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            for raw in self.reply:
                self.lines.append(raw.decode("utf-8").rstrip("\n"))
        except (OSError, ValueError):
            pass  # the test closed the stream

    def close(self) -> None:
        # shutdown first: the reading thread holds the reply's buffer, so closing it here would wait for that
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        self.thread.join(WAIT_S)
        with contextlib.suppress(OSError):
            self.reply.close()
        self.con.close()
        self.sock.close()


class Remote(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.here = self.hal.signed_in()
        self.away = self.hal.signed_in(remote=True, remote_host=DESK)

    def session_id(self) -> str:
        with contextlib.closing(db.connect()) as con:
            return con.execute("SELECT id_hash FROM sessions WHERE remote = 1 ORDER BY created DESC").fetchone()[0]

    def test_remote_sessions_can_be_signed_out(self):
        other = self.hal.signed_in(remote=True, remote_host=DESK)
        listed = self.away.get("/api/v2/remote/sessions").json()
        self.assertEqual(len(listed), 2, "only windows from another computer are listed")
        self.assertEqual([s["this_one"] for s in listed].count(True), 1)
        self.assertTrue(all(s["host"] == DESK for s in listed))
        mine = next(s for s in listed if s["this_one"])
        theirs = next(s for s in listed if not s["this_one"])
        self.assertEqual(self.here.request("DELETE", f"/api/v2/remote/sessions/{theirs['id']}").status, 200)
        self.assertEqual(other.get("/api/v2/session").status, 401, "the signed-out window is signed out")
        self.assertEqual(self.away.get("/api/v2/session").status, 200, "the other window is not")
        self.assertEqual([s["id"] for s in self.here.get("/api/v2/remote/sessions").json()], [mine["id"]])
        self.assertEqual(self.here.request("DELETE", f"/api/v2/remote/sessions/{theirs['id']}").status, 404)
        self.assertEqual(self.here.request("DELETE", "/api/v2/remote/sessions/not-a-window").status, 404)

    def test_a_window_on_this_computer_cannot_be_signed_out_from_the_list(self):
        with contextlib.closing(db.connect()) as con:
            local = con.execute("SELECT id_hash FROM sessions WHERE remote = 0").fetchone()[0]
        self.assertEqual(self.away.request("DELETE", f"/api/v2/remote/sessions/{local}").status, 404)
        self.assertEqual(self.here.get("/api/v2/session").status, 200)

    def test_pop_ups_go_to_the_browser_you_are_using(self):
        popups = mock.Mock()
        self.enterContext(mock.patch("jobhunt.core.platform.current", return_value=popups))
        self.enterContext(mock.patch.object(notify, "quiet_now", return_value=False))
        settings.set("notify.phone", False)
        stream = EventStream(self.hal.port, self.away.jar)
        self.addCleanup(stream.close)
        sid = self.session_id()
        _until(lambda: sse.open_count(sid) > 0, "the remote window's event stream")
        with contextlib.closing(db.connect()) as con:
            self.assertTrue(sessions.beat(con))
        self.assertTrue(sessions.window_open(), "other processes can tell from the presence file")
        notify.notify("Interview: Northgate Institute", "Invitation to interview")
        popups.notify.assert_not_called()
        _until(lambda: "event: notify" in stream.lines, "the notify event in the remote window")
        data = stream.lines[stream.lines.index("event: notify") + 1]
        self.assertIn("Interview: Northgate Institute", data)
        stream.close()

        def gone() -> bool:
            # the server learns a page has gone only when a write to it fails
            events.publish("toast", text="Still here?")
            return sse.open_count(sid) == 0

        _until(gone, "the stream to close")
        with contextlib.closing(db.connect()) as con:
            self.assertFalse(sessions.beat(con))
        self.assertFalse(sessions.window_open())
        notify.notify("A new match", "Library Assistant at Elmside Museum")
        popups.notify.assert_called_once_with("A new match", "Library Assistant at Elmside Museum", urgent=False)

    def test_an_old_presence_file_never_holds_pop_ups_back(self):
        file = paths.state_dir() / sessions.PRESENCE_FILE
        file.write_text('{"until": "2026-01-01T00:00:00Z"}\n', encoding="utf-8")
        self.assertFalse(sessions.window_open())
        file.write_text("half a file", encoding="utf-8")
        self.assertFalse(sessions.window_open())

    def test_hal_still_listens_only_on_127_0_0_1(self):
        self.assertEqual(app.BIND, "127.0.0.1")
        self.assertEqual(self.hal.httpd.server_address[0], "127.0.0.1")
        self.assertEqual(self.away.get("/healthz").status, 200)
        lan = _lan_address()
        if lan is not None:
            with self.assertRaises(OSError, msg="nothing answers on the network address"):
                socket.create_connection((lan, self.hal.port), timeout=1).close()
        # a page served from the network name is refused even through a tunnel
        self.assertEqual(self.away.get("/api/v2/session", headers={"Host": f"{DESK}:{self.hal.port}"}).status, 403)
        code = Path(app.__file__).resolve().parents[1]
        for path in code.rglob("*.py"):
            self.assertNotIn("0.0.0.0", path.read_text(encoding="utf-8"), f"{path} binds to every address")

    def test_the_steps_and_launchers_are_offered_in_settings(self):
        with mock.patch.object(instructions, "ssh_server", return_value={"installed": False, "running": False}):
            how = self.here.get("/api/v2/remote/instructions").json()
        self.assertEqual(how["ssh_server"], {"installed": False, "running": False})
        self.assertEqual(how["port"], int(os.environ.get("HAL_EMPLOYMENT_PORT") or 8737))
        login = f"{launcher.this_user()}@{launcher.this_host()}"
        commands = [s["command"] for s in how["steps"] if s["command"]]
        self.assertIn("sudo apt install openssh-server", commands)
        self.assertTrue(any(c == f"ssh-copy-id {login}" for c in commands))
        self.assertTrue(any("remote-launcher --os windows" in c for c in commands))
        self.assertEqual([s["n"] for s in how["steps"]], list(range(1, len(how["steps"]) + 1)))
        # the desktop is Linux: a Windows runner's checkout (D:/a/...) cannot be called over SSH, and
        # test_remote_launcher covers which command the launcher calls (CI windows-2025, 2026-10-23)
        with mock.patch.object(launcher, "this_host", return_value=DESK), \
                mock.patch.object(launcher, "this_user", return_value="sam"), \
                mock.patch.object(launcher, "hal_command", return_value=launcher.SHIM):
            sh = self.away.get("/api/v2/remote/launcher?os=linux")
            cmd = self.away.get("/api/v2/remote/launcher?os=windows")
        self.assertEqual(sh.status, 200)
        self.assertIn('attachment; filename="hal-remote.sh"', sh.headers["Content-Disposition"])
        self.assertIn(b'HOST="sam@deskbox"', sh.body)
        self.assertIn(b'set "HOST=sam@deskbox"\r\n', cmd.body)
        self.assertEqual(self.here.get("/api/v2/remote/launcher?os=amiga").status, 400)

    def test_the_ssh_server_is_reported_from_systemd(self):
        done = mock.Mock(stdout="active\n")
        run = mock.Mock(return_value=done)
        with mock.patch.object(paths, "platform_name", return_value="linux"):
            state = instructions.ssh_server(run=run, exists=lambda p: p.name == "sshd")
        self.assertEqual(state, {"installed": True, "running": True})
        self.assertEqual(run.call_args.args[0], ["systemctl", "is-active", "ssh"])
        with mock.patch.object(paths, "platform_name", return_value="linux"), \
                self.assertLogs("jobhunt.remote.instructions", "WARNING"):
            state = instructions.ssh_server(run=mock.Mock(side_effect=OSError("no systemd")), exists=lambda p: False)
        self.assertEqual(state["running"], False)


def _lan_address() -> str | None:
    """This computer's address on its network, when it has one (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            address = s.getsockname()[0]
    except OSError:
        return None
    return None if ipaddress.ip_address(address).is_loopback else address


class DesktopActions(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.enterContext(mock.patch.object(record, "VAULT", Path(paths.record_default()), create=True))
        self.addCleanup(record._CACHE.clear)
        folder = record_routes.make_default()
        (folder / "Certificates" / "First Aid.md").write_text("# First Aid\n\nCompleted 5 March 2025.\n",
                                                             encoding="utf-8")
        record.sync()
        self.away = self.hal.signed_in(remote=True, remote_host=DESK)

    def test_desktop_actions_become_downloads_in_a_remote_session(self):
        caps = self.away.get("/api/v2/session").json()["caps"]
        self.assertFalse(any(caps.values()), caps)
        refused = self.away.post("/api/v2/record/open-folder")
        self.assertEqual(refused.status, 409)
        self.assertEqual(refused.json()["error"]["code"], "remote")
        self.assertEqual(refused.json()["error"]["message"], f"Do this on {DESK}, the computer Hal runs on.")
        files = self.away.get("/api/v2/record/files").json()
        one = next(f for f in files if f["name"] == "First Aid.md")
        got = self.away.get(f"/api/v2/record/file/{one['file_id']}")
        self.assertEqual(got.status, 200)
        self.assertIn("attachment", got.headers["Content-Disposition"])
        self.assertIn(b"Completed 5 March 2025.", got.body)


class ShowTheForm(HelperTest):
    def setUp(self):
        super().setUp()
        self.world = self.enterContext(World())
        self.hal = self.enterContext(HalServer())
        self.job = job_id("sorrel-ready")
        self.enterContext(mock.patch.object(browser, "show", side_effect=AssertionError("the desktop's window")))
        self.away = self.hal.signed_in(remote=True, remote_host=DESK)
        with contextlib.closing(db.connect()) as con:
            db.set_status(con, self.job, "applying", "chosen from the queue")
            # started from the laptop's window, so the laptop's helper carries it
            con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'form', ?)",
                        (self.job, db.now()))
            con.execute("UPDATE applications SET session_id = ? WHERE job_id = ?", (session_id_of(self.away), self.job))
            con.commit()
        self.path = f"/api/v2/applications/{quote(self.job, safe='')}/form/show"

    def test_show_me_the_form_asks_the_laptops_helper_in_a_remote_session(self):
        laptop = pair(self.hal, self.away, browser="chrome")
        self.assertEqual(laptop["status"], 200)
        helper_poll(self.hal, laptop)   # in touch
        asked = []

        def laptop_helper():
            deadline = time.monotonic() + WAIT_S
            while not asked and time.monotonic() < deadline:
                asked.extend(answer_one(self.hal, laptop, {"shown": True}))

        helper = threading.Thread(target=laptop_helper, daemon=True)
        helper.start()
        shown = self.away.post(self.path)
        helper.join(WAIT_S)
        self.assertEqual(shown.status, 200, shown.text)
        self.assertEqual([c["cmd"] for c in asked], ["show"])
        self.assertEqual(asked[0]["args"]["job"], self.job)

    def test_show_me_the_form_says_what_to_do_when_no_helper_is_connected(self):
        refused = self.away.post(self.path)
        self.assertEqual(refused.status, 409)
        self.assertEqual(refused.json()["error"]["code"], "remote")
        self.assertIn("Open Hal in the browser that has the helper", refused.json()["error"]["message"])
