"""The Applications routes and Classic's slow routes turned into actions.

Each test serves the made-up world from a real server with a signed-in window. The browser and the form
driver are replaced by stand-ins; the routes, the rules of `apply/actions.py` and the database are real.
"""
from __future__ import annotations

import contextlib
import json
import time
import unittest
from unittest import mock
from urllib.parse import quote

from support.api import HalServer
from support.world import World, job_id

from jobhunt import browser, db, documents, record
from jobhunt.apply import driver
from jobhunt.core import naming, runner

WAIT_S = 10.0
POLL_S = 0.05
SLOW_RECORD_S = 3.0      # longer than Classic's 2 s wait, so the route answers "started"
SYNCED = {"converted": [], "added": 1, "changed": 0, "removed": 0}


def made_up_record(delay: float = 0.0):
    """The record's `sync` and `ask`, made up and as slow as asked, under the record's real action kinds."""
    def sync() -> dict:
        time.sleep(delay)
        return dict(SYNCED)

    def ask(q: str) -> dict:
        time.sleep(delay)
        return {"answer": "A made-up answer.", "sources": ["notes.md"], "note": "", "passages": []}

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(record, "sync", sync))
    stack.enter_context(mock.patch.object(record, "ask", ask))
    return stack


class Routes(unittest.TestCase):
    def setUp(self):
        self.world = World().__enter__()
        self.addCleanup(self.world.__exit__, None, None, None)
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.c = self.hal.signed_in()
        self.con = self.world.con
        for p in (mock.patch.object(documents, "render", lambda job, docs: {}),
                  mock.patch.object(driver, "act_auto", lambda con, job_id, here=False, fresh=False: "filled"),
                  mock.patch.object(browser, "close", lambda job_id: None)):
            p.start()
            self.addCleanup(p.stop)

    def url(self, key: str, rest: str = "") -> str:
        return f"/api/v2/applications/{quote(job_id(key), safe='')}{rest}"

    def start_applying(self, *keys: str) -> None:
        for key in keys:
            db.set_status(self.con, job_id(key), "applying", "chosen from the queue")
        self.con.commit()

    def put_app(self, key: str, **cols) -> None:
        """Makes or changes the application row of a made-up job."""
        self.con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                         (job_id(key), db.now()))
        for col, value in cols.items():
            self.con.execute(f"UPDATE applications SET {col} = ? WHERE job_id = ?", (value, job_id(key)))
        self.con.commit()

    def wait(self, action_id: str) -> dict:
        done = runner.wait(action_id, WAIT_S)
        self.assertEqual(done["state"], "done", done["error"])
        return done

    def test_the_board_puts_each_application_in_its_column_and_keeps_the_closed_ones(self):
        self.start_applying("sorrel-ready")
        board = self.c.get("/api/v2/applications").json()
        cols = {c["key"]: [i["id"] for i in c["items"]] for c in board["columns"]}
        self.assertEqual([c["title"] for c in board["columns"]],
                         ["Getting ready", "Form filled", "Sent", "Interview", "Offer"])
        self.assertIn(job_id("sorrel-ready"), cols["ready"])
        self.assertIn(job_id("ambleside-applied"), cols["sent"])
        self.assertEqual(set(cols["interview"]), {job_id("cobalt-interview"), job_id("heron-assessment")})
        card = next(c for c in board["columns"][0]["items"] if c["id"] == job_id("sorrel-ready"))
        self.assertEqual((card["next"], card["tone"], card["org"]), ("Check and approve documents", "info",
                                                                     "Sorrel Payments"))

    def test_previous_and_next_move_through_applications_in_progress(self):
        self.start_applying("sorrel-ready", "wexford-ready", "larchmont-grad")
        first, middle, last = (job_id(k) for k in ("sorrel-ready", "wexford-ready", "larchmont-grad"))
        nav = self.c.get(self.url("wexford-ready")).json()["nav"]
        self.assertEqual(nav, {"index": 2, "total": 3, "prev": first, "next": last})
        self.assertEqual(self.c.get(self.url("sorrel-ready")).json()["nav"]["prev"], None)
        self.assertEqual(self.c.get(self.url("larchmont-grad")).json()["nav"]["next"], None)
        # A sent application is not in progress, so it has no place in the run.
        self.assertEqual(self.c.get(self.url("ambleside-applied")).json()["nav"]["index"], 0)
        self.assertEqual(middle, nav and self.c.get(self.url("wexford-ready")).json()["id"])

    def test_an_application_can_be_opened_by_its_number(self):
        self.start_applying("sorrel-ready")
        self.put_app("sorrel-ready", stage="documents", number=41)
        page = self.c.get("/api/v2/applications/41")
        self.assertEqual(page.status, 200, page.text)
        got = page.json()
        self.assertEqual((got["id"], got["number"], got["step"]), (job_id("sorrel-ready"), 41, 1))

    def test_discard_reasons_are_kept_apart(self):
        keys = {"advert_closed": "sorrel-ready", "too_much_effort": "wexford-ready",
                "not_interested": "larchmont-grad", "problem": "tidewater-grad"}
        self.start_applying(*keys.values())
        for reason, key in keys.items():
            r = self.c.post(self.url(key, "/discard"), {"reason": reason, "words": "" if reason != "problem"
                                                        else "It needs a car"})
            self.assertEqual(r.status, 200, r.text)
        rows = {r["job_id"]: r for r in self.con.execute("SELECT * FROM applications")}
        for reason, key in keys.items():
            self.assertEqual(rows[job_id(key)]["closed_reason"], reason)
        status = dict(self.con.execute("SELECT id, status FROM jobs").fetchall())
        self.assertEqual(status[job_id("sorrel-ready")], "closed")
        self.assertEqual(status[job_id("wexford-ready")], "skipped")
        effort = self.con.execute("SELECT job_id FROM events WHERE kind = 'effort'").fetchall()
        self.assertEqual([r["job_id"] for r in effort], [job_id("wexford-ready")])
        closed = {c["id"]: c["why"] for c in self.c.get("/api/v2/applications").json()["closed"]}
        words = [closed[job_id(k)] for k in keys.values()]
        self.assertEqual(len(set(words)), 4, words)
        self.assertIn("It needs a car", closed[job_id("tidewater-grad")])
        self.assertEqual(self.c.post(self.url("cobalt-interview", "/discard"), {"reason": "bored"}).status, 400)

    def test_restore_takes_a_discarded_job_back_to_matches(self):
        self.start_applying("sorrel-ready")
        self.c.post(self.url("sorrel-ready", "/discard"), {"reason": "not_interested"})
        self.assertEqual(self.c.post(self.url("sorrel-ready", "/restore")).status, 200)
        row = self.con.execute("SELECT j.status, a.closed_reason FROM jobs j JOIN applications a ON a.job_id = j.id "
                               "WHERE j.id = ?", (job_id("sorrel-ready"),)).fetchone()
        self.assertEqual((row["status"], row["closed_reason"]), ("ready", None))

    def test_approving_the_documents_starts_the_form(self):
        self.start_applying("sorrel-ready")
        r = self.c.post(self.url("sorrel-ready", "/documents/approve"), {"letter": "A made-up letter."})
        self.assertEqual(r.status, 200, r.text)
        done = self.wait(r.json()["action_id"])
        self.assertEqual((done["kind"], done["result"]), ("apply.fill", "filled"))
        page = self.c.get(self.url("sorrel-ready")).json()
        self.assertIsNotNone(page["docs"]["approved_at"])
        self.assertEqual(page["docs"]["letter"]["text"], "A made-up letter.")
        self.assertFalse(page["first_time"])

    def test_editing_documents_after_the_form_is_filled_marks_the_form_out_of_date(self):
        self.start_applying("sorrel-ready")
        self.put_app("sorrel-ready", stage="filled", form='{"filled": {"at": "x"}, "fields": []}')
        r = self.c.request("PUT", self.url("sorrel-ready", "/documents"), {"letter": "Shorter."})
        self.assertEqual(r.json(), {"ok": True, "form_out_of_date": True})

    def test_i_sent_it_myself_moves_the_application_to_sent_with_a_follow_up(self):
        self.start_applying("sorrel-ready")
        r = self.c.post(self.url("sorrel-ready", "/sent"))
        self.assertEqual(r.json(), {"column": "sent"})
        row = self.con.execute("SELECT * FROM applications WHERE job_id = ?", (job_id("sorrel-ready"),)).fetchone()
        self.assertEqual(row["sent_how"], "you_said")
        self.assertGreater(row["follow_up_at"], row["sent_at"])
        page = self.c.get(self.url("sorrel-ready")).json()
        self.assertEqual(page["step"], 3)
        self.assertEqual(page["sent"]["timeline"][0]["t"], "Sent")

    def test_a_private_answer_never_leaves_the_form_in_the_page(self):
        canary = "PRIVATE-CANARY-7731"
        self.start_applying("sorrel-ready")
        form = {"fields": [{"hid": "a", "label": "Do you need adjustments?", "kind": "text", "value": canary,
                            "source": "private: adjustments"},
                           {"hid": "b", "label": "Full name", "kind": "text", "value": "Robin Testwell",
                            "source": "profile"}], "final": True, "filled": {"at": "x"}, "state": "ready"}
        self.put_app("sorrel-ready", stage="filled", form=json.dumps(form))
        r = self.c.get(self.url("sorrel-ready"))
        self.assertNotIn(canary, r.text)
        fields = {f["hid"]: f for f in r.json()["form"]["fields"]}
        self.assertEqual((fields["a"]["src"], fields["b"]["src"]), ("private", "profile"))

    def test_the_form_buttons_start_actions(self):
        self.start_applying("sorrel-ready")
        with mock.patch.object(driver, "act_refill", lambda con, job_id, edits: f"{len(edits)} put in"), \
                mock.patch.object(driver, "act_approve", lambda con, job_id, edits: "moved on"), \
                mock.patch.object(driver, "act_draft_rest", lambda con, job_id: "drafted"):
            for path, body, kind, result in (
                    ("/form/edits", {"edits": [{"hid": "a", "value": "x"}]}, "apply.apply_edits", "1 put in"),
                    ("/form/next", None, "apply.next_page", "moved on"),
                    ("/form/draft-rest", None, "apply.draft_rest", "drafted"),
                    ("/form/fill", {"fresh": True}, "apply.fill", "filled")):
                r = self.c.post(self.url("sorrel-ready", path), body)
                self.assertEqual(r.status, 200, r.text)
                done = self.wait(r.json()["action_id"])
                self.assertEqual((done["kind"], done["result"]), (kind, result))

    def test_classics_slow_legacy_routes_start_actions(self):
        self.start_applying("sorrel-ready")
        with made_up_record():
            synced = self.c.post("/api/record/sync")
            self.assertEqual(synced.status, 200, synced.text)
            self.assertEqual(synced.json()["last_sync"], SYNCED)
            asked = self.c.post("/api/record/ask", {"question": "Where did I volunteer?"}).json()
            self.assertEqual(asked["answer"], "A made-up answer.")
        with made_up_record(delay=SLOW_RECORD_S):
            self.assertEqual(self.c.post("/api/record/sync").json()["started"], True)
        with mock.patch.object(browser, "look", lambda job_id, shot: {"url": "https://jobs.example.org/apply"}):
            look = self.c.post(f"/api/job/{quote(job_id('sorrel-ready'), safe='')}/form-look")
            self.assertEqual(look.json()["url"], "https://jobs.example.org/apply")
        adopted = self.c.post("/api/ext/adopt", {"url": "https://jobs.example.org/sorrel/apply",
                                                 "title": "Software Engineer, Graduate - Sorrel Payments",
                                                 "text": "Sorrel Payments. Software Engineer, Graduate."})
        self.assertEqual(adopted.status, 200, adopted.text)
        runner.flush()
        kinds = {r["kind"] for r in self.con.execute("SELECT kind FROM actions")}
        self.assertLessEqual({"record.sync", "record.ask", "apply.look", "apply.match_tab"}, kinds)
        deadline = time.monotonic() + WAIT_S
        while runner.listing(self.con) and time.monotonic() < deadline:
            time.sleep(POLL_S)

    def test_a_document_is_served_under_the_name_employers_see(self):
        pdf = self.world.root / "made-up-cv.pdf"
        pdf.write_bytes(b"%PDF-1.4 made up")
        with mock.patch.object(documents, "files", lambda job: {"cv": str(pdf)}):
            r = self.c.get(f"/doc/{quote(job_id('sorrel-ready'), safe='')}/cv")
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(r.headers["Content-Disposition"], f'inline; filename="{naming.file_name("cv")}"')
        self.assertNotIn("made-up-cv", r.headers["Content-Disposition"])

    def test_a_pdf_the_reader_cannot_open_is_not_found_rather_than_an_error(self):
        import sys
        import types

        import jobhunt.cvstudio as cvstudio

        class Unreadable(Exception):
            pass

        def page_png(pdf, number, scale=2.0):
            raise Unreadable("not a PDF")

        fake = types.SimpleNamespace(Unreadable=Unreadable, page_png=page_png)
        pdf = self.world.root / "broken.pdf"
        pdf.write_bytes(b"not a pdf")
        with mock.patch.dict(sys.modules, {"jobhunt.cvstudio.pdfread": fake}), \
                mock.patch.object(cvstudio, "pdfread", fake, create=True), \
                mock.patch("jobhunt.web.server.SERVABLE", [self.world.root.resolve()], create=True):
            r = self.c.get(f"/pages?path={quote(str(pdf))}&page=1")
        self.assertEqual(r.status, 404, r.text)


class Providers(unittest.TestCase):
    """What the Applications area adds to Home and to Ctrl K."""

    def setUp(self):
        self.world = World().__enter__()
        self.addCleanup(self.world.__exit__, None, None, None)
        self.con = self.world.con

    def test_home_lists_the_applications_that_need_you_most_urgent_first(self):
        from jobhunt.apply import home

        for key in ("sorrel-ready", "wexford-ready"):
            db.set_status(self.con, job_id(key), "applying", "chosen from the queue")
        form = {"fields": [{"hid": "a", "label": "Why us?", "kind": "textarea", "value": "", "required": True}],
                "final": False, "filled": {"at": "x"}, "state": "needs"}
        self.con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'filled', ?)",
                         (job_id("wexford-ready"), db.now()))
        self.con.execute("UPDATE applications SET stage = 'filled', form = ? WHERE job_id = ?",
                         (json.dumps(form), job_id("wexford-ready")))
        self.con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'submitted', ?)",
                         (job_id("ambleside-applied"), db.now()))
        self.con.execute("UPDATE applications SET follow_up_at = '2000-01-01T00:00:00Z' WHERE job_id = ?",
                         (job_id("ambleside-applied"),))
        self.con.commit()
        items = home.needs(self.con)
        by_job = {i["act"]["route"]: i for i in items}
        check = by_job["app-" + quote(job_id("wexford-ready"), safe="")]
        self.assertEqual(check["title"].split(":")[0], "1 answer needs you")
        self.assertIn("Approve documents", by_job["app-" + quote(job_id("sorrel-ready"), safe="")]["title"])
        self.assertIn("Follow up", by_job["app-" + quote(job_id("ambleside-applied"), safe="")]["title"])
        self.assertEqual([i["urgency"] for i in items], sorted((i["urgency"] for i in items), reverse=True))
        self.assertEqual(items[0], check)

    def test_search_finds_an_application_by_employer_and_leaves_out_other_jobs(self):
        from jobhunt.apply import search

        db.set_status(self.con, job_id("sorrel-ready"), "applying", "chosen from the queue")
        self.con.commit()
        found = search.search(self.con, "sorrel")
        self.assertEqual([r["route"] for r in found], ["app-" + quote(job_id("sorrel-ready"), safe="")])
        self.assertEqual(found[0]["kind"], "Application")
        self.assertEqual(search.search(self.con, "wexford"), [])


class LegacyPaths(unittest.TestCase):
    """Regression: `web/server.py` imported `BASE_CV` once, so the base CV preview kept the first home's folder."""

    def test_the_base_cv_folder_follows_the_home(self):
        from pathlib import Path

        from jobhunt.core import paths
        from jobhunt.web import server

        with mock.patch.object(paths, "cache_dir", lambda: Path("/made-up/cache")):
            self.assertEqual(server.BASE_CV, Path("/made-up/cache/base-cv"))


if __name__ == "__main__":
    unittest.main()
