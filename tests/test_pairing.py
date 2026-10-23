"""Pairing the browser helper and its key: codes, keys, origins and refusing an old helper.

These drive the real server over HTTP the way the helper's background does, with the extension's origin
in the Origin header. The helper itself, loaded in a browser, is in tests/test_extension.py.
"""
import time
import unittest
from contextlib import closing
from datetime import timedelta
from unittest import mock

from support.api import HalServer

from jobhunt import db, extension
from jobhunt.apply import helpers
from jobhunt.core import clock, events

SHORT_POLL_S = 0.2     # long enough for a poll to be held, short enough for a quick test
HELD_WRITE_S = 5   # far under the 30 s busy timeout a held write cost before


def helper_origin() -> str:
    """The Origin header the paired helper's requests carry."""
    return f"chrome-extension://{helpers.manifest_id()}"


def pair(hal, client=None, *, browser="opera", origin=None, host=None) -> dict:
    """Asks for a code from a signed-in window and swaps it as the helper would; returns the pair reply."""
    window = client or hal.signed_in()
    code = window.post("/api/v2/helpers/pairing-code", {"browser": browser}).json()["code"]
    headers = {"Origin": origin or helper_origin()}
    if host:
        headers["Host"] = host
    reply = hal.client().post("/api/v2/helpers/pair", {"code": code, "browser": browser, "version": "1.0.0",
                                                        "protocol": 1, "extension_id": helpers.manifest_id()},
                              headers=headers)
    return {"status": reply.status, **(reply.json() if reply.status == 200 else {})}


def key_headers(paired: dict) -> dict:
    """The headers of a paired helper's requests."""
    return {"X-Hal-Key": f"{paired['helper_id']}:{paired['key']}", "Origin": helper_origin()}


class HelperTest(unittest.TestCase):
    """Each test starts with no helper in touch, and short polls."""

    def setUp(self):
        extension.reset()
        self.addCleanup(extension.reset)
        self.enterContext(mock.patch.object(extension, "POLL_WAIT", SHORT_POLL_S))


class Pairing(HelperTest):
    def test_pairing_gives_one_helper_a_key_and_the_tick_follows_its_first_poll(self):
        with HalServer() as hal:
            window = hal.signed_in()
            paired = pair(hal, window)
            self.assertEqual(paired["status"], 200)
            self.assertTrue(paired["helper_id"] and len(paired["key"]) > 30)
            with closing(db.connect()) as con:
                row = helpers.row(con, paired["helper_id"])
                self.assertNotEqual(row["key_hash"], paired["key"])   # only the hash is kept
                self.assertEqual((row["browser"], row["version"], row["protocol"]), ("opera", "1.0.0", 1))
            listed = window.get("/api/v2/helpers").json()
            self.assertEqual([(h["id"], h["connected"], h["this_browser"]) for h in listed],
                             [(paired["helper_id"], False, True)])

            sub = events.subscribe()
            self.addCleanup(sub.close)
            polled = hal.client().post("/api/v2/helpers/poll", {"helper_id": paired["helper_id"], "version": "1.0.0",
                                                                 "protocol": 1}, headers=key_headers(paired))
            self.assertEqual(polled.status, 200)
            self.assertEqual(polled.json()["commands"], [])
            ticks = []
            while (ev := sub.get(timeout=0.5)) is not None:
                if ev.type == "helper":
                    ticks.append(ev.data)
            self.assertEqual(ticks, [{"id": paired["helper_id"], "connected": True}])
            self.assertTrue(window.get("/api/v2/helpers").json()[0]["connected"])

            # a second poll is not a new connection, so the step does not tick again
            hal.client().post("/api/v2/helpers/poll", {"version": "1.0.0", "protocol": 1}, headers=key_headers(paired))
            again = [ev for ev in iter(lambda: sub.get(timeout=0.3), None) if ev.type == "helper"]
            self.assertEqual(again, [])

    def test_a_pairing_code_works_once_and_runs_out(self):
        with HalServer() as hal:
            window = hal.signed_in()
            code = window.post("/api/v2/helpers/pairing-code", {"browser": "chrome"}).json()
            self.assertEqual(code["expires_in"], 120)
            self.assertTrue(code["pair_url"].endswith(f"/pair#code={code['code']}"))
            body = {"code": code["code"], "version": "1.0.0", "protocol": 1}
            first = hal.client().post("/api/v2/helpers/pair", body, headers={"Origin": helper_origin()})
            again = hal.client().post("/api/v2/helpers/pair", body, headers={"Origin": helper_origin()})
            self.assertEqual((first.status, again.status), (200, 403))
            self.assertIn("not valid any more", again.json()["error"]["message"])

            late = window.post("/api/v2/helpers/pairing-code", {"browser": "chrome"}).json()["code"]
            later = clock.iso(clock.now_utc() + timedelta(seconds=helpers.CODE_S + 1))
            with mock.patch.dict("os.environ", {clock.ENV_FAKE_NOW: later}):
                expired = hal.client().post("/api/v2/helpers/pair", {**body, "code": late},
                                            headers={"Origin": helper_origin()})
            self.assertEqual(expired.status, 403)
            self.assertEqual(hal.client().post("/api/v2/helpers/pair", {**body, "code": "made-up"},
                                               headers={"Origin": helper_origin()}).status, 403)

    def test_only_a_signed_in_window_can_ask_for_a_pairing_code(self):
        with HalServer() as hal:
            self.assertEqual(hal.client().post("/api/v2/helpers/pairing-code", {"browser": "opera"}).status, 401)

    def test_a_web_page_cannot_pair_even_with_a_code(self):
        with HalServer() as hal:
            refused = pair(hal, origin="http://evil.example")
            self.assertEqual(refused["status"], 403)
            other_extension = pair(hal, origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop")
            self.assertEqual(other_extension["status"], 403)

    def test_a_helper_from_another_protocol_is_told_to_load_the_new_one(self):
        with HalServer() as hal:
            code = hal.signed_in().post("/api/v2/helpers/pairing-code", {}).json()["code"]
            reply = hal.client().post("/api/v2/helpers/pair", {"code": code, "protocol": 2},
                                      headers={"Origin": helper_origin()})
            self.assertEqual(reply.status, 409)
            self.assertIn("Load the helper from Hal's helper folder again", reply.json()["error"]["message"])

    def test_the_helper_talks_to_the_address_it_paired_from(self):
        with HalServer() as hal:
            # a laptop's tunnel may use another local port, and the page may be opened as localhost
            tunnel = f"localhost:{hal.port + 1}"
            paired = pair(hal, host=tunnel)
            self.assertEqual(paired["base"], f"http://{tunnel}")
            direct = pair(hal)
            self.assertEqual(direct["base"], f"http://127.0.0.1:{hal.port}")

    def test_a_helper_paired_from_a_remote_window_is_marked_remote(self):
        with HalServer() as hal:
            laptop = hal.signed_in(remote=True, remote_host="desk-01")
            paired = pair(hal, laptop)
            with closing(db.connect()) as con:
                self.assertEqual(helpers.row(con, paired["helper_id"])["remote"], 1)
            self.assertTrue(laptop.get("/api/v2/helpers").json()[0]["remote"])


class Keys(HelperTest):
    def test_helper_requests_without_the_key_are_refused(self):
        with HalServer() as hal:
            paired = pair(hal)
            body = {"version": "1.0.0", "protocol": 1}
            c = hal.client()
            self.assertEqual(c.post("/api/v2/helpers/poll", body, headers={"Origin": helper_origin()}).status, 401)
            wrong = {"X-Hal-Key": f"{paired['helper_id']}:not-the-key", "Origin": helper_origin()}
            self.assertEqual(c.post("/api/v2/helpers/poll", body, headers=wrong).status, 401)
            page = {**key_headers(paired), "Origin": "http://evil.example"}
            self.assertEqual(c.post("/api/v2/helpers/poll", body, headers=page).status, 403)
            self.assertEqual(c.post("/api/v2/helpers/result", {"id": "x", "ok": True}).status, 401)
            self.assertEqual(c.get("/api/v2/helpers/file?job_id=x&kind=cv").status, 401)
            self.assertEqual(c.post("/api/v2/helpers/poll", body, headers=key_headers(paired)).status, 200)

    def test_a_forgotten_helper_is_refused_at_once(self):
        with HalServer() as hal:
            window = hal.signed_in()
            paired = pair(hal, window)
            self.assertEqual(window.request("DELETE", f"/api/v2/helpers/{paired['helper_id']}").status, 200)
            polled = hal.client().post("/api/v2/helpers/poll", {"version": "1.0.0", "protocol": 1},
                                       headers=key_headers(paired))
            self.assertEqual(polled.status, 401)
            self.assertEqual(window.get("/api/v2/helpers").json(), [])
            self.assertEqual(window.request("DELETE", "/api/v2/helpers/nobody").status, 404)

    def test_a_helper_key_opens_no_window_routes(self):
        with HalServer() as hal:
            paired = pair(hal)
            self.assertEqual(hal.client().get("/api/v2/helpers", headers=key_headers(paired)).status, 401)
            self.assertEqual(hal.client().get("/api/v2/session", headers=key_headers(paired)).status, 401)


class HeldWrite(HelperTest):
    """Regression: the form driver held a write while it waited on the helper, so the helper's poll waited the
    whole 30 s busy timeout to record itself, failed, and the command meant for it never arrived."""

    def test_a_poll_is_answered_while_another_write_holds_the_database(self):
        with HalServer() as hal:
            paired = pair(hal)
            with closing(db.connect()) as holder:
                holder.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                reply = hal.client().post("/api/v2/helpers/poll", {"version": "1.0.0", "protocol": 1},
                                          headers=key_headers(paired))
                waited = time.monotonic() - started
                holder.rollback()
        self.assertEqual(reply.status, 200, reply.text)
        self.assertLess(waited, SHORT_POLL_S + HELD_WRITE_S)

    def test_the_carrier_is_kept_later_while_another_write_holds_the_database(self):
        with HalServer(), closing(db.connect()) as con:
            con.execute("INSERT INTO jobs (id, source, company, title, first_seen, last_seen) "
                        "VALUES ('made-up:1', 'test', 'Made-up Ltd', 'Archivist', '2026-10-01', '2026-10-01')")
            con.execute("INSERT INTO applications (job_id, stage, updated) VALUES ('made-up:1', 'form', '2026-10-01')")
            con.commit()
            with closing(db.connect()) as holder:
                holder.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                self.assertFalse(helpers.carry(con, "made-up:1", "helper-1"))
                self.assertLess(time.monotonic() - started, HELD_WRITE_S)
                holder.rollback()
            self.assertTrue(helpers.carry(con, "made-up:1", "helper-1"))


class OldHelper(HelperTest):
    """A helper from before pairing (0.4 or 0.5) asks at /api/ext/* with `X-Hal: 1` and no key."""

    def test_an_old_helper_is_refused_and_told_to_load_the_new_one(self):
        with HalServer() as hal:
            for who, client in (("no session", hal.client()), ("signed in", hal.signed_in())):
                with self.subTest(who):
                    poll = client.post("/api/ext/poll", {}, headers={"Origin": helper_origin()})
                    self.assertEqual(poll.status, 410)
                    self.assertIn("load the new one", poll.text)
                    # plain text, so the old helper waits before it asks again
                    self.assertTrue(poll.headers["Content-Type"].startswith("text/plain"))
                    adopt = client.post("/api/ext/adopt", {"url": "https://jobs.example.org/sorrel/apply"},
                                        headers={"Origin": helper_origin()})
                    self.assertEqual(adopt.status, 410)
                    self.assertIn("too old", adopt.json()["error"])
            self.assertFalse(extension.connected())
            self.assertEqual(hal.signed_in().get("/api/v2/helpers").json(), [])


if __name__ == "__main__":
    unittest.main()
