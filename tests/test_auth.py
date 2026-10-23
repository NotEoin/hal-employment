"""Who may use Hal's server: open links, the session cookie, the write header, origins and hosts,
the local key, and the rule that Hal listens on 127.0.0.1 only.
"""
import socket
import sys
import unittest
from datetime import timedelta
from unittest import mock
from urllib.parse import urlparse

from support.api import HalServer

from jobhunt import db
from jobhunt.core import clock
from jobhunt.web import app, auth


class OpenLinks(unittest.TestCase):
    """A window signs in by opening a one-time link."""

    def test_an_open_link_works_once(self):
        with HalServer() as hal:
            url = urlparse(hal.open_url(next="/#jobs"))
            first = hal.client().get(f"{url.path}?{url.query}")
            self.assertEqual(first.status, 302)
            self.assertEqual(first.headers["Location"], "/#jobs")
            again = hal.client().get(f"{url.path}?{url.query}")
            self.assertEqual(again.status, 410)
            self.assertIn(app.LINK_RAN_OUT, again.text)
            self.assertNotIn("Set-Cookie", again.headers)

    def test_an_expired_link_says_it_ran_out(self):
        with HalServer() as hal:
            url = urlparse(hal.open_url(remote=True, remote_host="desktop"))
            later = clock.iso(clock.now_utc() + timedelta(seconds=auth.OPEN_TOKEN_S + 1))
            with mock.patch.dict("os.environ", {clock.ENV_FAKE_NOW: later}):
                reply = hal.client().get(f"{url.path}?{url.query}")
            self.assertEqual(reply.status, 410)
            self.assertIn(app.LINK_RAN_OUT_REMOTE, reply.text)

    def test_next_must_be_a_path_on_this_server(self):
        with HalServer() as hal:
            url = urlparse(hal.open_url(next="//elsewhere.example/steal"))
            reply = hal.client().get(f"{url.path}?{url.query}")
            self.assertEqual(reply.headers["Location"], "/#setup", "a new install's own start page, the setup guide")

    def test_the_token_itself_is_never_stored(self):
        with HalServer() as hal:
            token = urlparse(hal.open_url()).query.split("t=")[1].split("&")[0]
            con = db.connect()
            try:
                rows = [tuple(r) for r in con.execute("SELECT * FROM open_tokens")]
            finally:
                con.close()
            self.assertTrue(rows)
            self.assertNotIn(token, repr(rows))

    def test_the_cookie_is_httponly_strict_and_named_for_this_install(self):
        with HalServer() as hal:
            url = urlparse(hal.open_url())
            reply = hal.client().get(f"{url.path}?{url.query}")
            cookies = reply.cookies()
            name = f"hal_s_{auth.install_id().replace('-', '')[:8]}"
            self.assertIn(name, cookies)
            for raw in cookies.values():
                self.assertIn("HttpOnly", raw)
                self.assertIn("SameSite=Strict", raw)
                self.assertIn("Path=/", raw)
            self.assertIn(f"hal_b_{name[6:]}", cookies)
            self.assertIn(f"Max-Age={auth.SESSION_DAYS * 86400}", cookies[name])


class Access(unittest.TestCase):
    """What each kind of request needs."""

    def test_api_needs_a_session_and_posts_need_the_header(self):
        with HalServer() as hal:
            self.assertEqual(hal.client().get("/api/v2/session").status, 401)
            self.assertEqual(hal.client().get("/files?path=made-up.pdf").status, 401)
            signed = hal.signed_in()
            self.assertEqual(signed.get("/api/v2/session").status, 200)
            self.assertEqual(signed.get("/files?path=made-up.pdf").status, 404)
            self.assertEqual(signed.post("/api/v2/session/end", csrf=False).status, 403)
            self.assertEqual(signed.post("/api/v2/session/end").status, 200)
            self.assertEqual(signed.get("/api/v2/session").status, 401)

    def test_the_local_key_opens_the_api_without_a_window(self):
        with HalServer() as hal:
            key = {"Authorization": f"Bearer {auth.local_api_key()}"}
            self.assertEqual(hal.client().get("/api/v2/version", headers=key).status, 200)
            self.assertEqual(hal.client().get("/api/v2/version", headers={"Authorization": "Bearer wrong"}).status,
                             401)
            if sys.platform != "win32":   # Windows keeps no Unix modes; the profile folder's ACL guards the key
                self.assertEqual((hal.home / "config" / auth.KEY_FILE).stat().st_mode & 0o777, auth.SECRET_MODE)

    def test_foreign_origins_and_hosts_are_refused(self):
        with HalServer() as hal:
            signed = hal.signed_in()
            for host in ("evil.example", f"evil.example:{hal.port}", f"192.168.1.20:{hal.port}"):
                with self.subTest(host=host):
                    self.assertEqual(signed.get("/api/v2/session", headers={"Host": host}).status, 403)
                    self.assertEqual(signed.get("/healthz", headers={"Host": host}).status, 403)
            evil = {"Origin": "http://evil.example"}
            self.assertEqual(signed.post("/api/v2/session/end", headers=evil).status, 403)
            self.assertEqual(signed.post("/api/ext/poll", {}, headers=evil).status, 403)
            own = {"Origin": f"http://127.0.0.1:{hal.port}"}
            self.assertEqual(signed.post("/api/v2/session/end", headers=own).status, 200)

    def test_any_local_port_is_accepted_for_a_tunnel(self):
        with HalServer() as hal:
            signed = hal.signed_in()
            for host in ("127.0.0.1:18737", "localhost:9999", "localhost"):
                with self.subTest(host=host):
                    self.assertEqual(signed.get("/api/v2/session", headers={"Host": host}).status, 200)
            tunnel = {"Host": "localhost:18737", "Origin": "http://localhost:18737"}
            self.assertEqual(signed.post("/api/v2/session/end", headers=tunnel).status, 200)

    def test_a_remote_link_makes_a_remote_session(self):
        with HalServer() as hal:
            signed = hal.signed_in(remote=True, remote_host="desktop")
            body = signed.get("/api/v2/session").json()
            self.assertTrue(body["remote"])
            self.assertEqual(body["remote_host"], "desktop")
            self.assertFalse(body["caps"]["open_folder"])

    def test_hal_listens_only_on_127_0_0_1(self):
        self.assertEqual(app.BIND, "127.0.0.1")
        with HalServer() as hal:
            self.assertEqual(hal.httpd.server_address[0], "127.0.0.1")
            others = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
            others -= {"127.0.0.1"}
            if sys.platform.startswith("linux"):
                others.add("127.0.0.2")  # Linux answers the whole 127/8 block, so this proves the exact bind
            for address in others:
                with self.subTest(address=address), self.assertRaises(OSError):
                    socket.create_connection((address, hal.port), timeout=2).close()


if __name__ == "__main__":
    unittest.main()
