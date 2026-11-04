"""The browser helper's server side: routing between helpers, versions and reload, the browsers
setup explains, the visible helper folder, and the files a helper may upload.

The helper itself, loaded in a browser, is in tests/test_extension.py; pairing and keys in test_pairing.py.
"""
import json
import shutil
import tempfile
import threading
import time
import tomllib
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from support.api import HalServer
from test_pairing import HelperTest, helper_origin, key_headers, pair

from jobhunt import db, extension
from jobhunt.apply import helper_health, helper_mirror, helper_routes, helpers
from jobhunt.core import paths, platform, plugins
from jobhunt.web import app

ROOT = Path(__file__).resolve().parent.parent
PICTURES = ROOT / "jobhunt" / "web" / "static" / "app" / "setup" / "img" / "helper"
SUPPORTED = ("opera", "chrome", "edge", "brave", "vivaldi")
WAIT_S = 5


def poll(hal, paired, version=None):
    """One poll from a paired helper; returns its reply."""
    return hal.client().post("/api/v2/helpers/poll", {"helper_id": paired["helper_id"], "version": version,
                                                      "protocol": 1}, headers=key_headers(paired)).json()


def answer_one(hal, paired, value) -> list:
    """Polls once as this helper, answers each command it got with `value`; returns the commands."""
    got = poll(hal, paired)["commands"]
    for c in got:
        hal.client().post("/api/v2/helpers/result", {"id": c["id"], "ok": True, "value": value},
                          headers=key_headers(paired))
    return got


def start_application(con, job_id: str, session_id: str | None) -> None:
    """A made-up application row, started from the window with `session_id`."""
    with con:
        con.execute("INSERT OR IGNORE INTO jobs (id, source, title, company, url, status, first_seen, last_seen) "
                    "VALUES (?, 'test', 'Junior Archivist', 'Fernvale Records', 'https://jobs.example.org/1', "
                    "'applying', '2026-10-20', '2026-10-20')", (job_id,))
        con.execute("INSERT OR REPLACE INTO applications (job_id, stage, session_id, updated) "
                    "VALUES (?, 'form', ?, '2026-10-20')", (job_id, session_id))


def session_id_of(client) -> str:
    """The session id (the hash of the cookie) a signed-in test client holds."""
    from jobhunt.web import auth

    return auth._hash(client.jar[auth.session_cookie_name()])


class Routing(HelperTest):
    def test_two_helpers_never_take_each_others_commands(self):
        with HalServer() as hal, closing(db.connect()) as con:
            desk, laptop = hal.signed_in(), hal.signed_in(remote=True, remote_host="desk-01")
            mine, theirs = pair(hal, desk, browser="opera"), pair(hal, laptop, browser="chrome")
            poll(hal, mine), poll(hal, theirs)   # both in touch
            start_application(con, "test:desk", session_id_of(desk))
            start_application(con, "test:laptop", session_id_of(laptop))

            asked = {}
            def ask(job):
                asked[job] = extension._ask("show", timeout=WAIT_S, job=job)

            threads = [threading.Thread(target=ask, args=(j,)) for j in ("test:desk", "test:laptop")]
            for t in threads:
                t.start()
            got_desk = answer_one(hal, mine, {"shown": "desk"})
            got_laptop = answer_one(hal, theirs, {"shown": "laptop"})
            for t in threads:
                t.join(WAIT_S)
            self.assertEqual([c["args"]["job"] for c in got_desk], ["test:desk"])
            self.assertEqual([c["args"]["job"] for c in got_laptop], ["test:laptop"])
            self.assertEqual(asked, {"test:desk": {"shown": "desk"}, "test:laptop": {"shown": "laptop"}})
            kept = dict(con.execute("SELECT job_id, helper_id FROM applications").fetchall())
            self.assertEqual(kept, {"test:desk": mine["helper_id"], "test:laptop": theirs["helper_id"]})

    def test_a_helper_cannot_answer_another_helpers_command(self):
        with HalServer() as hal, closing(db.connect()) as con:
            window = hal.signed_in()
            mine, other = pair(hal, window), pair(hal, hal.signed_in())
            poll(hal, mine), poll(hal, other)
            start_application(con, "test:mine", session_id_of(window))
            out = {}
            t = threading.Thread(target=lambda: out.setdefault("v", self._ask_or_error("test:mine")))
            t.start()
            command = poll(hal, mine)["commands"][0]
            hal.client().post("/api/v2/helpers/result", {"id": command["id"], "ok": True, "value": {"shown": "forged"}},
                              headers=key_headers(other))
            hal.client().post("/api/v2/helpers/result", {"id": command["id"], "ok": True, "value": {"shown": "real"}},
                              headers=key_headers(mine))
            t.join(WAIT_S)
            self.assertEqual(out["v"], {"shown": "real"})

    def test_a_command_whose_ask_timed_out_never_reaches_the_helper(self):
        # a timed-out ask left its command queued, so the next real one waited behind it
        with HalServer() as hal, closing(db.connect()) as con:
            window = hal.signed_in()
            mine = pair(hal, window)
            poll(hal, mine)
            start_application(con, "test:late", session_id_of(window))
            with self.assertRaises(RuntimeError):
                extension._ask("show", timeout=0.2, job="test:late")
            out = {}
            t = threading.Thread(target=lambda: out.setdefault("v", self._ask_or_error("test:late")))
            t.start()
            got, deadline = [], time.monotonic() + WAIT_S
            while not got and time.monotonic() < deadline:
                got = answer_one(hal, mine, {"shown": "fresh"})
            t.join(WAIT_S)
            self.assertEqual(len(got), 1)
            self.assertEqual(out["v"], {"shown": "fresh"})

    def test_pairing_works_when_the_app_window_is_another_browser(self):
        with HalServer() as hal, closing(db.connect()) as con:
            # the window is Edge (no helper); the helper lives in Opera, which never signed in
            edge_window = hal.signed_in()
            opera = pair(hal, edge_window, browser="opera")
            stranger = pair(hal, hal.signed_in(), browser="brave")
            poll(hal, stranger)
            poll(hal, opera)   # most recently seen, but chosen for being this window's helper, not for that
            poll(hal, stranger)
            start_application(con, "test:edge", session_id_of(edge_window))
            t = threading.Thread(target=lambda: extension._ask("show", timeout=WAIT_S, job="test:edge"), daemon=True)
            t.start()
            self.assertEqual([c["args"]["job"] for c in answer_one(hal, opera, {"shown": True})], ["test:edge"])
            t.join(WAIT_S)
            listed = edge_window.get("/api/v2/helpers").json()
            self.assertEqual({h["browser"]: h["this_browser"] for h in listed}, {"opera": True, "brave": False})

    def test_with_no_helper_in_touch_hal_says_where_to_open_it(self):
        with HalServer():
            with self.assertRaises(RuntimeError) as said:
                extension._ask("show", timeout=1, job="test:none")
            self.assertIn(extension.NOT_CONNECTED, str(said.exception))

    @staticmethod
    def _ask_or_error(job):
        try:
            return extension._ask("show", timeout=WAIT_S, job=job)
        except RuntimeError as e:
            return str(e)


class Versions(HelperTest):
    def test_an_older_helper_is_asked_to_reload_once_and_comes_back_newer(self):
        with HalServer() as hal:
            paired = pair(hal)
            first = poll(hal, paired, version="0.9.0")
            self.assertEqual((first["reload"], first["wanted_version"]), (True, helpers.wanted_version()))
            after = poll(hal, paired, version=helpers.wanted_version())
            self.assertFalse(after["reload"])
            with closing(db.connect()) as con:
                self.assertEqual(helpers.row(con, paired["helper_id"])["version"], helpers.wanted_version())
            self.assertTrue(helper_health.helper_check().ok)

    def test_a_helper_still_older_after_reloading_is_named_on_is_hal_working(self):
        with HalServer() as hal:
            paired = pair(hal)
            self.assertTrue(poll(hal, paired, version="0.9.0")["reload"])
            again = poll(hal, paired, version="0.9.0")
            self.assertFalse(again["reload"])   # asked once only, so it never reloads in a loop
            check = helper_health.helper_check()
            self.assertEqual((check.level, check.text), ("warn", helper_health.OLDER))
            laptop = pair(hal, hal.signed_in(remote=True, remote_host="desk-01"))
            with closing(db.connect()) as con, con:
                con.execute("UPDATE helpers SET revoked = 'x' WHERE id = ?", (paired["helper_id"],))
            poll(hal, laptop, version="0.9.0"), poll(hal, laptop, version="0.9.0")
            self.assertIn("Run the launcher again", helper_health.helper_check().text)

    def test_versions_compare_as_numbers(self):
        self.assertTrue(helpers.older("0.5", "1.0.0"))
        self.assertTrue(helpers.older("1.0.9", "1.0.10"))
        self.assertFalse(helpers.older("1.0", "1.0.0"))
        self.assertTrue(helpers.older(None, "1.0.0"))

    def test_the_version_hal_wants_is_the_manifests(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(helpers.wanted_version(), manifest["version"])
        self.assertEqual(manifest["version"], "1.0.9")
        self.assertIn("never presses Submit", manifest["description"])
        self.assertEqual(manifest["content_scripts"][0]["js"], ["pair.js"])
        self.assertEqual(set(manifest["content_scripts"][0]["matches"]), {"http://127.0.0.1/*", "http://localhost/*"})
        # the id the key gives is fixed, so every computer's copy is the same helper
        self.assertRegex(helpers.manifest_id(), r"^[a-p]{32}$")


class Browsers(unittest.TestCase):
    def setUp(self):
        with (ROOT / "jobhunt" / "apply" / "browsers.toml").open("rb") as f:
            self.table = tomllib.load(f)["browsers"]

    def test_each_supported_browser_has_its_extensions_page_and_steps(self):
        self.assertEqual([k for k, v in self.table.items() if v["supported"]], list(SUPPORTED))
        pages = {k: v["extensions_url"] for k, v in self.table.items() if v["supported"]}
        self.assertEqual(pages, {"opera": "opera://extensions", "chrome": "chrome://extensions",
                                 "edge": "edge://extensions", "brave": "brave://extensions",
                                 "vivaldi": "vivaldi://extensions"})
        for key in SUPPORTED:
            entry = self.table[key]
            self.assertEqual(len(entry["steps"]), 3, key)
            self.assertIn('"Developer mode"', entry["steps"][1]["text"])
            self.assertIn('"Load unpacked"', entry["steps"][2]["text"])
            for step in entry["steps"]:
                picture = PICTURES / step["picture"]
                self.assertTrue(picture.is_file(), picture)
                self.assertIn(entry["extensions_url"].split("://")[0], picture.read_text(encoding="utf-8"))
        self.assertIn("top right", self.table["opera"]["steps"][1]["text"])
        self.assertIn("left", self.table["edge"]["steps"][1]["text"])

    def test_firefox_is_listed_as_not_supported_yet(self):
        firefox = self.table["firefox"]
        self.assertFalse(firefox["supported"])
        self.assertIn("does not work in Firefox yet", firefox["why"])

    def test_the_setup_step_lists_the_browsers_found_and_the_helper_folder(self):
        opera = platform.found("opera", Path("C:/Users/test/AppData/Local/Programs/Opera/opera.exe"))
        fake = mock.Mock(browsers=lambda: [opera])
        with HalServer() as hal, mock.patch("jobhunt.core.platform.current", return_value=fake):
            out = hal.signed_in().get("/api/v2/helpers/browsers").json()
            found = {b["id"]: b["found"] for b in out["browsers"]}
            self.assertEqual(found, {"opera": True, "chrome": False, "edge": False, "brave": False,
                                     "vivaldi": False, "firefox": False})
            self.assertEqual(out["folder"], str(paths.helper_dir()))
            self.assertTrue(out["browsers"][0]["steps"][0]["picture"].startswith("/app/setup/img/helper/"))
            picture = hal.client().get(out["browsers"][0]["steps"][1]["picture"])
            self.assertEqual(picture.status, 200)

    def test_opening_a_browsers_pages_goes_through_the_platform_layer(self):
        opera = platform.found("opera", Path("C:/Users/test/AppData/Local/Programs/Opera/opera.exe"))
        opened = []
        fake = mock.Mock(browsers=lambda: [opera], open_in_browser=lambda b, url: opened.append((b.id, url)),
                         open_browser_window=lambda b: opened.append((b.id, "a new window")))
        with HalServer() as hal, mock.patch("jobhunt.core.platform.current", return_value=fake), \
                mock.patch("jobhunt.web.auth.server_port", return_value=hal.port):   # the port Hal is set to use
            window = hal.signed_in()
            # a browser drops opera://extensions given on its command line and shows a new
            # tab, so Hal opens a window and the page pastes the address it gets back
            page = window.post("/api/v2/helpers/open-extensions-page", {"browser": "opera"})
            self.assertEqual((page.status, page.json()["address"]), (200, "opera://extensions"))
            code = window.post("/api/v2/helpers/pairing-code", {"browser": "opera"}).json()["code"]
            self.assertEqual(window.post("/api/v2/helpers/open-pair-page", {"browser": "opera", "code": code}).status,
                             200)
            self.assertEqual(opened, [("opera", "a new window"),
                                      ("opera", f"http://127.0.0.1:{hal.port}/pair#code={code}")])
            missing = window.post("/api/v2/helpers/open-extensions-page", {"browser": "vivaldi"})
            self.assertEqual(missing.status, 409)
            self.assertIn("vivaldi://extensions", missing.json()["error"]["message"])
            firefox = window.post("/api/v2/helpers/open-extensions-page", {"browser": "firefox"})
            self.assertIn("does not work in Firefox yet", firefox.json()["error"]["message"])

    def test_a_window_from_another_computer_cannot_open_this_computers_browser(self):
        fake = mock.Mock(browsers=list, open_in_browser=mock.Mock())
        with HalServer() as hal, mock.patch("jobhunt.core.platform.current", return_value=fake):
            laptop = hal.signed_in(remote=True, remote_host="desk-01")
            refused = laptop.post("/api/v2/helpers/open-extensions-page", {"browser": "opera"})
            self.assertEqual((refused.status, refused.json()["error"]["code"]), (409, "remote"))
            self.assertIn("desk-01", refused.json()["error"]["message"])
            fake.open_in_browser.assert_not_called()


class HelperFolder(unittest.TestCase):
    def test_the_visible_helper_folder_is_refreshed_at_every_start(self):
        app.load_routes()
        self.assertIn("jobhunt.apply.helper_mirror.refresh_at_start", [name for name, _ in plugins._start])
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp) / "code" / "extension", Path(tmp) / "Hal Employment" / "Browser helper"
            shutil.copytree(ROOT / "extension", src)
            first = helper_mirror.refresh(src, dest)
            self.assertIn("manifest.json", first["written"])
            self.assertEqual(sorted(p.name for p in dest.iterdir()), sorted(p.name for p in src.iterdir()))
            self.assertEqual(helper_mirror.refresh(src, dest), {"folder": str(dest), "written": [], "removed": []})

            # git updated the helper: a changed file, a new one, and one that went
            (src / "background.js").write_text("// a newer helper\n", encoding="utf-8")
            (src / "panel.js").write_text("// new\n", encoding="utf-8")
            (src / "page.js").unlink()
            (dest / "notes-of-mine.txt").write_text("stale\n", encoding="utf-8")
            second = helper_mirror.refresh(src, dest)
            self.assertEqual(sorted(second["written"]), ["background.js", "panel.js"])
            self.assertEqual(sorted(second["removed"]), ["notes-of-mine.txt", "page.js"])
            self.assertEqual((dest / "background.js").read_text(encoding="utf-8"), "// a newer helper\n")

    def test_the_folder_refresh_fails_loudly_when_the_helper_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            helper_mirror.refresh(Path(tmp) / "nothing", Path(tmp) / "dest")

    def test_the_helper_folder_is_outside_hidden_folders(self):
        with mock.patch.object(paths, "_guarded", return_value=None), \
                mock.patch.object(paths, "_is_windows", return_value=True), \
                mock.patch.dict("os.environ", {"USERPROFILE": str(Path("C:/Users/test"))}):
            self.assertEqual(paths.helper_dir(), Path("C:/Users/test") / "Hal Employment" / "Browser helper")


class Files(HelperTest):
    def test_a_helper_gets_only_that_applications_documents(self):
        with HalServer() as hal, closing(db.connect()) as con:
            paired = pair(hal)
            start_application(con, "test:files", None)
            cv = hal.home / "documents" / "cv.pdf"
            cv.parent.mkdir(parents=True, exist_ok=True)
            cv.write_bytes(b"%PDF-1.4 made-up")
            other = hal.home / "config" / "local-api-key"
            with mock.patch("jobhunt.documents.files", return_value={"cv": str(cv)}):
                where = "/api/v2/helpers/file?job_id=test%3Afiles"
                by_kind = hal.client().get(f"{where}&kind=cv", headers=key_headers(paired))
                by_path = hal.client().get(f"{where}&path={quote(str(cv))}", headers=key_headers(paired))
                outside = hal.client().get(f"{where}&path={quote(str(other))}", headers=key_headers(paired))
            self.assertEqual((by_kind.status, by_kind.body), (200, b"%PDF-1.4 made-up"))
            self.assertEqual(by_path.status, 200)
            self.assertEqual(outside.status, 404)
            self.assertEqual(hal.client().get("/api/v2/helpers/file?job_id=nope&kind=cv",
                                              headers=key_headers(paired)).status, 404)


class Routes(unittest.TestCase):
    def test_the_helper_routes_need_the_auth_the_spec_gives_them(self):
        app.load_routes()
        from jobhunt.web import router

        auths = {(r.method, r.pattern): (r.auth, r.local_only) for r in router.ROUTES.values()
                 if r.pattern.startswith(helper_routes.PREFIX)}
        p = helper_routes.PREFIX
        self.assertEqual(auths, {
            ("GET", p): ("session", False), ("GET", p + "/browsers"): ("session", False),
            ("POST", p + "/open-extensions-page"): ("session", True), ("POST", p + "/pairing-code"): ("session", False),
            ("POST", p + "/open-pair-page"): ("session", True), ("POST", p + "/pair"): ("pairing", False),
            ("POST", p + "/poll"): ("helper", False), ("POST", p + "/result"): ("helper", False),
            ("POST", p + "/adopt"): ("helper", False), ("POST", p + "/fill-empty"): ("helper", False),
            ("GET", p + "/file"): ("helper", False), ("DELETE", p + "/{helper_id}"): ("session", False)})
        self.assertTrue(helper_origin().startswith("chrome-extension://"))


if __name__ == "__main__":
    unittest.main()
