"""The Applications routes, a document under the name employers see, and a tab the helper hands over.

Each test serves the made-up world from a real server with a signed-in window. The browser and the form
driver are replaced by stand-ins; the routes, the rules of `apply/actions.py` and the database are real.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock
from urllib.parse import quote

from support.api import HalServer
from support.world import World, job_id

from jobhunt import browser, db, documents
from jobhunt.apply import actions, driver, helpers, queries
from jobhunt.core import naming, paths, runner

WAIT_S = 10.0


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
        with mock.patch.object(queries, "_has_documents", lambda job: True):   # Hal has made its documents
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

    def test_discarding_skips_copies_in_the_same_place_only(self):
        # discarding the Newcastle copies of an agency's advert also took its London listing out of Matches
        here, there = "lever:copy-here", "lever:copy-there"
        for copy, place in ((here, "London"), (there, "Manchester")):
            self.con.execute("INSERT INTO jobs (id, source, company, title, location, url, description, status, "
                             "first_seen, last_seen) SELECT ?, source, company, title, ?, url, description, 'new', "
                             "first_seen, last_seen FROM jobs WHERE id = ?", (copy, place, job_id("sorrel-ready")))
        self.con.commit()
        self.start_applying("sorrel-ready")
        self.assertEqual(self.c.post(self.url("sorrel-ready", "/discard"), {"reason": "not_interested"}).status, 200)
        status = dict(self.con.execute("SELECT id, status FROM jobs").fetchall())
        self.assertEqual((status[here], status[there]), ("skipped", "new"))

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

    def test_an_approved_application_is_on_the_form_step_before_the_form_opens(self):
        # testers: approving went back to the board, and the application stayed on Documents until the page loaded
        self.start_applying("sorrel-ready")
        with mock.patch.object(actions, "start", lambda *a, **k: "held"):
            r = self.c.post(self.url("sorrel-ready", "/documents/approve"), {})
        self.assertEqual(r.status, 200, r.text)
        page = self.c.get(self.url("sorrel-ready")).json()
        self.assertEqual((page["step"], page["column"], page["form"]), (2, "form", None))

    def test_change_the_documents_takes_an_approved_application_back_to_approve_again(self):
        # testers asked for a way back from the form to edit and approve the documents again (2026-11-04)
        self.start_applying("sorrel-ready")
        with mock.patch.object(actions, "start", lambda *a, **k: "held"):
            self.c.post(self.url("sorrel-ready", "/documents/approve"), {"letter": "A made-up letter."})
            self.assertEqual(self.c.post(self.url("sorrel-ready", "/documents/reopen")).status, 200)
            page = self.c.get(self.url("sorrel-ready")).json()
            self.assertEqual((page["step"], page["docs"]["state"], page["docs"]["approved_at"]), (1, "ready", None))
            self.assertEqual(page["docs"]["letter"]["text"], "A made-up letter.")
            self.c.post(self.url("sorrel-ready", "/documents/approve"), {"letter": "A changed letter."})
        self.assertEqual(self.c.get(self.url("sorrel-ready")).json()["step"], 2)
        self.assertEqual(self.c.post(self.url("ambleside-applied", "/documents/reopen")).status, 400)

    def test_a_form_hal_stopped_on_says_why_rather_than_that_hal_is_filling_it(self):
        # testers: a failed run left "Hal is filling it in" on the page for a day (2026-11-04)
        self.start_applying("sorrel-ready")
        self.put_app("sorrel-ready", stage="form", form='{"state": "working", "fields": [], "url": ""}')
        self.con.execute("""INSERT INTO actions (id, kind, subject, job_id, state, error, created)
                            VALUES ('made-up-run', 'apply.fill', ?, ?, 'error', 'the made-up site would not load',
                                    ?)""", (job_id("sorrel-ready"), job_id("sorrel-ready"), db.now()))
        self.con.commit()
        page = self.c.get(self.url("sorrel-ready")).json()
        self.assertEqual((page["form"]["state"], page["steps"][1]["note"]), ("stopped", "Hal stopped"))
        self.assertIn("the made-up site would not load", page["form"]["words"])
        board = self.c.get("/api/v2/applications").json()
        card = next(i for c in board["columns"] for i in c["items"] if i["id"] == job_id("sorrel-ready"))
        self.assertEqual(card["tone"], "warn")

    def test_fill_in_from_this_page_reads_the_tab_without_opening_the_advert(self):
        self.start_applying("sorrel-ready")
        asked = []
        def act_auto(con, job_id, here, fresh):
            asked.append((here, fresh))
            return "ok"

        with mock.patch.object(driver, "act_auto", act_auto):
            r = self.c.post(self.url("sorrel-ready", "/form/fill"), {"here": True})
            self.wait(r.json()["action_id"])
        self.assertEqual(asked, [(True, False)])
        # a button the person pressed never waits behind queued jobs (testers, 2026-11-04)
        priority = self.con.execute("SELECT priority FROM actions WHERE id = ?", (r.json()["action_id"],)).fetchone()
        self.assertEqual(priority[0], 0)

    def test_documents_a_restart_stopped_are_shown_and_made_again_together(self):
        # testers: after a restart, queued jobs sat under Getting ready saying "Check and approve documents" with
        # no documents to check, and nothing started them again (2026-11-04)
        self.start_applying("sorrel-ready", "wexford-ready")
        self.con.execute("""INSERT INTO actions (id, kind, subject, job_id, state, created)
                            VALUES ('made-up-prepare', 'docs.prepare', ?, ?, 'interrupted', ?)""",
                         (job_id("sorrel-ready"), job_id("sorrel-ready"), db.now()))
        self.con.commit()
        with mock.patch.object(queries, "_has_documents", lambda job: job["id"] != job_id("sorrel-ready")):
            board = self.c.get("/api/v2/applications").json()
            cards = {i["id"]: i for c in board["columns"] for i in c["items"]}
            stalled = cards[job_id("sorrel-ready")]
            self.assertEqual((stalled["next"], stalled["tone"], stalled["remakeable"]),
                             ("Documents not made: make them again", "warn", True))
            self.assertFalse(cards[job_id("wexford-ready")]["remakeable"])
            started = []
            with mock.patch.object(actions, "start", lambda kind, job, **k: started.append((kind, job)) or "made"):
                r = self.c.post("/api/v2/applications/remake-missing",
                                {"ids": [job_id("sorrel-ready"), job_id("wexford-ready")]})
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(started, [("docs.prepare", job_id("sorrel-ready"))])
        self.assertEqual(r.json()["skipped"], [job_id("wexford-ready")])

    def test_approve_all_starts_every_ready_form_and_leaves_the_rest(self):
        # testers asked to queue several jobs, as the older tool did, then apply to them together
        self.start_applying("sorrel-ready", "wexford-ready")
        ready = {job_id("sorrel-ready"), job_id("wexford-ready")}
        with mock.patch.object(queries, "_has_documents", lambda job: job["id"] in ready):
            board = self.c.get("/api/v2/applications").json()
            shown = {i["id"]: i["approvable"] for c in board["columns"] for i in c["items"]}
            self.assertTrue(shown[job_id("sorrel-ready")])
            self.assertFalse(shown[job_id("ambleside-applied")])
            r = self.c.post("/api/v2/applications/approve-ready",
                            {"ids": [job_id("sorrel-ready"), job_id("wexford-ready"), job_id("ambleside-applied")]})
        self.assertEqual(r.status, 200, r.text)
        out = r.json()
        self.assertEqual(set(out["started"]), ready)
        self.assertEqual(out["skipped"], [job_id("ambleside-applied")])
        for action_id in out["started"].values():
            self.assertEqual(self.wait(action_id)["kind"], "apply.fill")
        self.assertEqual(self.c.post("/api/v2/applications/approve-ready", {"ids": []}).status, 400)

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

    def test_the_form_names_its_site_and_application_system_for_a_report(self):
        self.start_applying("sorrel-ready")
        form = {"url": "https://apply.workable.com/sorrel-made-up/j/AB12/apply/", "final": False,
                "fields": [{"hid": "b", "label": "Full name", "kind": "text", "value": "Robin Testwell",
                            "source": "profile"}], "filled": {"at": "x"}, "state": "needs"}
        self.put_app("sorrel-ready", stage="form", form=json.dumps(form))
        page = self.c.get(self.url("sorrel-ready")).json()["form"]
        self.assertEqual((page["site"], page["platform"]), ("workable.com", "Workable"))

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

    def test_a_tab_the_paired_helper_hands_over_is_matched_as_an_action(self):
        origin = {"Origin": f"chrome-extension://{helpers.manifest_id()}"}
        code = self.c.post("/api/v2/helpers/pairing-code", {"browser": "opera"}).json()["code"]
        paired = self.hal.client().post("/api/v2/helpers/pair", {
            "code": code, "browser": "opera", "version": helpers.wanted_version(), "protocol": helpers.PROTOCOL,
            "extension_id": helpers.manifest_id()}, headers=origin).json()
        key = {**origin, "X-Hal-Key": f"{paired['helper_id']}:{paired['key']}"}
        adopted = self.hal.client().post("/api/v2/helpers/adopt", {"tab": {
            "url": "https://jobs.example.org/sorrel/apply", "title": "Software Engineer, Graduate - Sorrel Payments",
            "text": "Sorrel Payments. Software Engineer, Graduate."}}, headers=key)
        self.assertEqual(adopted.status, 200, adopted.text)
        self.assertEqual(adopted.json()["job_id"], job_id("sorrel-ready"))
        runner.flush()
        kinds = {r["kind"] for r in self.con.execute("SELECT kind FROM actions")}
        self.assertIn("apply.match_tab", kinds)

    def test_a_document_is_served_under_the_name_employers_see(self):
        pdf = self.world.root / "made-up-cv.pdf"
        pdf.write_bytes(b"%PDF-1.4 made up")
        with mock.patch.object(documents, "files", lambda job: {"cv": str(pdf)}):
            r = self.c.get(f"/doc/{quote(job_id('sorrel-ready'), safe='')}/cv")
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(r.headers["Content-Disposition"], f'inline; filename="{naming.file_name("cv")}"')
        self.assertNotIn("made-up-cv", r.headers["Content-Disposition"])

    def test_files_are_served_from_the_application_folders_only(self):
        inside = paths.applications_dir() / "made-up-application" / "letter.pdf"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_bytes(b"%PDF-1.4 made up")
        self.addCleanup(inside.unlink)
        outside = self.world.root / "made-up-notes.txt"
        outside.write_text("made up", encoding="utf-8")
        found = self.c.get(f"/files?path={quote(str(inside))}")
        self.assertEqual((found.status, found.headers["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(self.c.get(f"/files?path={quote(str(outside))}").status, 404)

    def test_a_file_address_survives_plus_hash_and_ampersand_in_the_path(self):
        # regression: `_file_url` left the path unencoded, so "+", "#" and "&" cut it short
        odd = paths.applications_dir() / "made-up-application" / "cv a+b #1 & c.pdf"
        odd.parent.mkdir(parents=True, exist_ok=True)
        odd.write_bytes(b"%PDF-1.4 made up")
        self.addCleanup(odd.unlink)
        found = self.c.get(queries._file_url(str(odd)))
        self.assertEqual((found.status, found.headers["Content-Type"]), (200, "application/pdf"))
        self.assertIsNone(queries._file_url(None))


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
        self.assertEqual([i["urgency"] for i in items], sorted(i["urgency"] for i in items))
        self.assertEqual(items[0], check)

    def test_the_form_step_says_one_answer_needs_you_in_the_singular(self):
        # the step tab said "1 answer need you" while the board card said "1 answer needs you"
        from jobhunt.apply import queries

        form = {"fields": [{"hid": "a", "label": "Why us?", "kind": "textarea", "value": "", "required": True}],
                "final": False, "filled": {"at": "x"}, "state": "needs"}
        _, steps = queries._steps(self.con, job_id("wexford-ready"), "form", {"form": form}, None)
        self.assertEqual(steps[1]["note"], "1 answer needs you")

    def test_search_finds_an_application_by_employer_and_leaves_out_other_jobs(self):
        from jobhunt.apply import search

        db.set_status(self.con, job_id("sorrel-ready"), "applying", "chosen from the queue")
        self.con.commit()
        found = search.search(self.con, "sorrel")
        self.assertEqual([r["route"] for r in found], ["app-" + quote(job_id("sorrel-ready"), safe="")])
        self.assertEqual(found[0]["kind"], "Application")
        self.assertEqual(search.search(self.con, "wexford"), [])


if __name__ == "__main__":
    unittest.main()
