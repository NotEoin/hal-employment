"""Applying in Hal's own browser, against the practice site in tests/site: reaching the form, filling it,
waiting for approval, discarding, choosing what to apply for, and forms already open.

Moved by class from tests/test_apply.py; the test bodies are unchanged.
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from support import legacy
from support.legacy import PROSE, Practice

from jobhunt import autofill, browser, db, documents
from jobhunt.web import server

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


class Applying(Practice):
    """Forms filled in Hal's own browser."""

    @classmethod
    def tearDownClass(cls):
        try:
            browser.BROWSER.call(lambda b: b._ctx and b._ctx.close())
        except Exception:  # noqa: BLE001 - the browser never started
            pass
        super().tearDownClass()

    def page_text(self, job_id: str) -> str:
        return browser.BROWSER.call(lambda b: b.tab(job_id).locator("body").inner_text())

    def test_reads_each_question_with_its_own_label_and_options(self):
        job_id = self.job("read", "form.html")
        found = browser.open_form(job_id, f"{self.base}/form.html")
        by_label = {f["label"]: f for f in found["fields"]}
        self.assertEqual(by_label["Do you have the right to work in the UK?"]["options"], ["Yes", "No"])
        self.assertEqual(by_label["Will you need visa sponsorship?"]["kind"], "radio")
        self.assertEqual(by_label["Which university did you attend?"]["options"][1], "University")
        self.assertEqual(by_label["Resume/CV"]["kind"], "file")
        self.assertTrue(by_label["First name"]["required"])
        self.assertNotIn("Search", by_label)
        self.assertEqual(by_label["Date of birth"]["section"], "2. Questions")
        self.assertTrue(found["next"] and not found["submit"])

    def test_fills_the_form_waits_for_approval_then_submits(self):
        job_id = self.job("full", "advert.html")
        said = server.act_auto(self.con, job_id)
        # Page one cannot be passed: nothing Hal knows answers the date of birth.
        form = self.form(job_id)
        self.assertEqual((form["state"], form["step"], form["final"]), ("needs", 1, False))
        self.assertIn("1 required question", said)
        self.assertEqual(self.field(job_id, "Which university")["value"], "University")
        self.assertEqual(self.field(job_id, "Notice period")["value"], "0")
        self.assertEqual([f["label"] for f in form["fields"] if f.get("error")], [])

        dob = self.field(job_id, "Date of birth")
        server.act_approve(self.con, job_id, [{"hid": dob["hid"], "value": "01/02/2003", "edited": True}])
        # The last page is filled in and held: the essay is there to read, and nothing has gone.
        form = self.form(job_id)
        self.assertEqual((form["state"], form["step"], form["final"]), ("ready", 2, True))
        self.assertTrue(self.field(job_id, "Why do you want")["written"])
        self.assertEqual(self.status(job_id), "applying")
        self.assertNotIn("Thank you for applying", self.page_text(job_id))
        self.assertEqual(form["done"][0]["step"], 1)

        sent = browser.BROWSER.call(lambda b: b.tab(job_id).evaluate("() => window.halSent || null"))
        self.assertIsNone(sent)
        self.assertEqual(server.act_approve(self.con, job_id, []), "application submitted")
        self.assertEqual(self.status(job_id), "applied")
        # What he typed himself is kept for the next form that asks it.
        kept = self.con.execute("SELECT answer FROM answers WHERE question = 'date of birth'").fetchone()
        self.assertEqual(db.json_value(kept["answer"]), "01/02/2003")

    def test_enters_the_emailed_code_and_reuses_an_earlier_answer(self):
        job_id = self.job("code", "form.html?code=1")
        self.con.execute("INSERT INTO answers VALUES ('date of birth', 'Date of birth', '\"01/02/2003\"', ?)", (db.now(),))
        self.con.commit()
        server.act_auto(self.con, job_id)
        form = self.form(job_id)
        self.assertEqual((form["state"], form["step"]), ("ready", 2))
        self.assertEqual(form["done"][0]["fields"][-1]["source"], "your earlier answer")
        self.assertEqual(server.act_approve(self.con, job_id, []), "application submitted")
        self.assertEqual(self.status(job_id), "applied")

    def test_what_the_form_received(self):
        job_id = self.job("sent", "form.html")
        self.con.execute("INSERT INTO answers VALUES ('date of birth', 'Date of birth', '\"01/02/2003\"', ?)", (db.now(),))
        self.con.commit()
        server.act_auto(self.con, job_id)
        # Keeps the tab open after sending, to read back what the form was given.
        closing, browser.close = browser.close, lambda job_id: None
        try:
            server.act_approve(self.con, job_id, [])
        finally:
            browser.close = closing
        sent = browser.BROWSER.call(lambda b: b.tab(job_id).evaluate("() => window.halSent"))
        self.assertEqual(sent, {"first": "Ada", "uni": "University", "rtw": "y", "sponsor": False,
                                "news": False, "why": PROSE, "cv": "Ada-Lovelace-CV.pdf", "heard": "Company website",
                                "dob": "01/02/2003", "referee": ""})

    def test_a_posting_taken_down_is_marked_closed(self):
        job_id = self.job("closed", "closed.html")
        self.assertIn("taken down", server.act_auto(self.con, job_id))
        self.assertEqual(self.status(job_id), "closed")

    def test_follows_the_control_the_model_picks_when_no_apply_button_is_recognised(self):
        job_id = self.job("portal", "portal.html")
        asked = []
        autofill.choose_control = lambda job, found: (asked.append([c["name"] for c in found["controls"]])
                                                      or "Register your interest in this vacancy")
        try:
            server.act_auto(self.con, job_id)
        finally:
            autofill.choose_control = lambda job, found: ""
        self.assertIn("Tell a friend", asked[0])
        self.assertGreater(len(self.form(job_id)["fields"]), 8)

    def test_an_approval_after_the_window_closed_refills_and_sends_nothing(self):
        job_id = self.job("lost", "form.html")
        server.act_auto(self.con, job_id)
        dob = self.field(job_id, "Date of birth")
        browser.BROWSER.call(lambda b: b.tab(job_id).close())
        with self.assertRaisesRegex(RuntimeError, "sent nothing"):
            server.act_approve(self.con, job_id, [{"hid": dob["hid"], "value": "01/02/2003", "edited": True}])
        # His answer survived the refill, which let the form reach its last page; it is still unsent.
        form = self.form(job_id)
        self.assertEqual((form["state"], form["step"]), ("ready", 2))
        self.assertEqual(self.status(job_id), "applying")

    def test_starts_again_when_the_window_was_closed(self):
        job_id = self.job("gone", "form.html")
        browser.open_form(job_id, f"{self.base}/form.html")
        browser.BROWSER.call(lambda b: b.tab(job_id).close())
        server.act_auto(self.con, job_id, here=True)
        self.assertEqual(self.form(job_id)["state"], "needs")


class Discarding(unittest.TestCase):
    """An application already started can be dropped, for a job that turns out to have closed."""

    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "jobs.db"
        self.con = db.connect(path)
        self.addCleanup(self.con.close)
        # Hal's background actions open the database themselves.
        connect = db.connect
        db.connect = lambda p=path: connect(p)
        self.addCleanup(setattr, db, "connect", connect)
        self.closed = []
        saved = browser.close
        browser.close = self.closed.append
        self.addCleanup(setattr, browser, "close", saved)
        for n, status in (("1", "applying"), ("2", "ready")):
            db.upsert(self.con, {"id": f"greenhouse:{n}", "source": "greenhouse", "company": "Monzo",
                                 "title": f"Graduate Engineer {n}", "location": "London", "url": f"https://example.org/{n}",
                                 "description": "", "salary": "", "posted_at": None, "board_size": None})
            db.set_status(self.con, f"greenhouse:{n}", status)
        self.con.commit()

    def discard(self, job_id: str, **body):
        return server.Handler.job_action(None, self.con, job_id, "discard", body)

    def test_a_closed_job_leaves_the_applications_in_progress_and_stops_its_watch(self):
        self.discard("greenhouse:1", closed=True, reason="The job has closed")
        jobs = {j["id"]: j for j in server.applications(self.con)}
        self.assertEqual(jobs["greenhouse:1"]["status"], "closed")
        self.assertIn("discarded mid-application: The job has closed",
                      self.con.execute("SELECT detail FROM events WHERE job_id = 'greenhouse:1' ORDER BY id DESC").fetchone()[0])
        saved = browser.peek
        browser.peek = lambda job_id: self.fail("a discarded application's tab was looked at")
        self.addCleanup(setattr, browser, "peek", saved)
        self.assertFalse(server._watch_step(self.con, "greenhouse:1", {}))

    def test_another_reason_skips_the_application(self):
        self.discard("greenhouse:1", reason="Not eligible")
        self.assertEqual(server._job(self.con, "greenhouse:1")["status"], "skipped")
        self.assertIn("greenhouse:1", [j["id"] for j in server.applications(self.con)])

    def test_a_job_discarded_from_the_queue_is_not_listed_as_an_application(self):
        self.discard("greenhouse:2", reason="Too senior")
        self.assertEqual(server._job(self.con, "greenhouse:2")["status"], "skipped")
        self.assertNotIn("greenhouse:2", [j["id"] for j in server.applications(self.con)])

    def test_an_application_sent_back_to_the_queue_and_discarded_there_is_not_listed(self):
        server.Handler.job_action(None, self.con, "greenhouse:1", "restore", {})
        self.discard("greenhouse:1", reason="Too senior")
        self.assertNotIn("greenhouse:1", [j["id"] for j in server.applications(self.con)])

    def slow_action(self, job_id: str, name: str = "applying", replace: bool = False):
        """Starts an action that stands in for a slow page load; returns its entry and the event that ends the load."""
        reached, release = threading.Event(), threading.Event()

        def act(con, job_id):
            reached.set()
            release.wait(10)
            server._say(job_id, "filling in the form")
            server._set_app(con, job_id, stage="filled", form={"state": "ready", "message": "", "fields": []})
            return "filled"

        server.run_action(job_id, name, act, job_id, replace=replace)
        self.addCleanup(release.set)
        self.addCleanup(server.ACTIONS.pop, job_id, None)
        self.assertTrue(reached.wait(5))
        return server.ACTIONS[job_id], release

    def finished(self, entry: dict) -> str:
        deadline = time.monotonic() + 5
        while entry["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        return entry["state"]

    def test_an_application_hal_is_working_on_is_discarded_at_once_and_hal_stops(self):
        # Discarding used to be refused until Hal finished, which on an expired advert could take minutes.
        entry, release = self.slow_action("greenhouse:1")
        began = time.monotonic()
        self.discard("greenhouse:1", closed=True, reason="The job has closed")
        self.assertLess(time.monotonic() - began, 1)
        self.assertEqual(server._job(self.con, "greenhouse:1")["status"], "closed")
        self.assertNotIn("greenhouse:1", server.ACTIONS)
        release.set()
        self.assertEqual(self.finished(entry), "stopped")
        self.assertIsNone(server._app(self.con, "greenhouse:1")["stage"])
        server.POOL.submit(lambda: None).result(5)
        self.assertEqual(self.closed, ["greenhouse:1"])

    def test_starting_again_takes_over_from_hal_and_the_first_run_writes_nothing(self):
        first, release = self.slow_action("greenhouse:1")
        server.run_action("greenhouse:1", "applying", lambda con, job_id: "fresh start", "greenhouse:1", replace=True)
        second = server.ACTIONS["greenhouse:1"]
        self.assertEqual(self.finished(second), "done")
        release.set()
        self.assertEqual(self.finished(first), "stopped")
        self.assertIs(server.ACTIONS["greenhouse:1"], second)
        self.assertIsNone(server._app(self.con, "greenhouse:1")["stage"])

    def test_marking_applied_while_hal_works_stops_hal(self):
        entry, release = self.slow_action("greenhouse:1")
        saved = server.tasks.after_submit
        server.tasks.after_submit = lambda con, job: None
        self.addCleanup(setattr, server.tasks, "after_submit", saved)
        server.Handler.job_action(None, self.con, "greenhouse:1", "applied", {"how": "submitted by hand"})
        release.set()
        self.assertEqual(self.finished(entry), "stopped")
        self.assertEqual(server._job(self.con, "greenhouse:1")["status"], "applied")
        self.assertEqual(server._app(self.con, "greenhouse:1")["stage"], "submitted")

    def test_too_much_effort_is_remembered_for_the_company_and_the_site(self):
        for n, company in (("3", "Monzo"), ("4", "Starling")):
            db.upsert(self.con, {"id": f"greenhouse:{n}", "source": "greenhouse", "company": company,
                                 "title": "Platform Engineer", "location": "London", "url": f"https://jobs.example.org/{n}",
                                 "description": "", "salary": "", "posted_at": None, "board_size": None})
            db.set_status(self.con, f"greenhouse:{n}", "ready")
        self.discard("greenhouse:1", reason="Too much effort", effort=True)
        notes = server.effort_notes(self.con, server._job(self.con, "greenhouse:3"))
        self.assertEqual(len(notes), 1)
        self.assertIn("to Monzo as too much effort", notes[0])
        self.assertIn("on example.org as too much effort", server.effort_notes(self.con, server._job(self.con, "greenhouse:4"))[0])
        self.assertTrue(next(j for j in server.applications(self.con) if j["id"] == "greenhouse:1")["effort"])
        # A job board found tiresome once says nothing about the forms of the other jobs it lists.
        self.con.execute("UPDATE events SET detail = 'adzuna.co.uk' WHERE kind = 'effort'")
        self.con.execute("UPDATE jobs SET url = 'https://www.adzuna.co.uk/jobs/land/ad/4' WHERE id = 'greenhouse:4'")
        self.assertEqual(server.effort_notes(self.con, server._job(self.con, "greenhouse:4")), [])
        # Reopened, it is no longer held against the company.
        db.set_status(self.con, "greenhouse:1", "applying", "reopened after discarding")
        self.assertEqual(server.effort_notes(self.con, server._job(self.con, "greenhouse:3")), [])

    def test_a_site_is_named_without_the_employer_part(self):
        self.assertEqual(server._site("https://acme.wd3.myworkdayjobs.com/en-GB/careers/job/1"), "myworkdayjobs.com")
        self.assertEqual(server._site("https://careers.acme.co.uk/jobs/2"), "acme.co.uk")


class Choosing(unittest.TestCase):
    """Applications start with their documents, and are worked through one at a time."""

    def setUp(self):
        path = Path(tempfile.mkdtemp()) / "jobs.db"
        self.con = db.connect(path)
        self.addCleanup(self.con.close)
        for n, score in (("1", 90), ("2", 80), ("3", 70)):
            db.upsert(self.con, {"id": f"greenhouse:{n}", "source": "greenhouse", "company": f"Firm {n}",
                                 "title": "Graduate Engineer", "location": "London", "url": f"https://example.org/{n}",
                                 "description": "", "salary": "", "posted_at": None, "board_size": None})
            self.con.execute("UPDATE jobs SET score = ? WHERE id = ?", (score, f"greenhouse:{n}"))
            db.set_status(self.con, f"greenhouse:{n}", "ready")
        self.con.commit()
        saved = server.db.connect, server.act_auto, server.run_action
        def restore():
            server.db.connect, server.act_auto, server.run_action = saved
        self.addCleanup(restore)
        connect = saved[0]
        server.db.connect = lambda p=path: connect(p)
        self.started = []
        server.run_action = lambda job_id, name, fn, *args, replace=False: self.started.append((job_id, fn)) or {}

    def post(self, route: str, body: dict):
        handler = server.Handler.__new__(server.Handler)
        return handler.api_post(route, body)

    def test_applying_to_all_opens_no_form_and_orders_them_best_first(self):
        # Every form was opened at once, so tabs behind others were never filled.
        started = self.post("queue/apply", {"count": 3})["started"]
        self.assertEqual(started, ["greenhouse:1", "greenhouse:2", "greenhouse:3"])
        self.assertEqual(self.started, [])
        self.assertEqual(server.in_progress_around(self.con, "greenhouse:2"),
                         {"place": 2, "count": 3, "prev": "greenhouse:1", "next": "greenhouse:3"})
        self.assertEqual(server.in_progress_around(self.con, "greenhouse:1")["prev"], None)

    def test_hal_fills_the_form_only_once_the_documents_are_approved(self):
        saved = server.documents.save, server.documents.load
        def restore():
            server.documents.save, server.documents.load = saved
        self.addCleanup(restore)
        stored = {}
        server.documents.load = lambda con, job: dict(stored)
        server.documents.save = lambda con, job, docs: stored.update(docs)
        self.post("job/greenhouse:1/apply", {})
        self.assertEqual(server._job(self.con, "greenhouse:1")["status"], "applying")
        self.assertEqual(self.started, [])
        self.post("job/greenhouse:1/docs-approve", {"docs": {"letter": "Dear Firm 1"}})
        self.assertEqual(self.started, [("greenhouse:1", server.act_auto)])
        self.assertEqual(stored["letter"], "Dear Firm 1")
        self.assertTrue(stored["approved"])


class OpenForms(unittest.TestCase):
    """A form already open in the job's tab is never closed by Hal looking for one."""

    def setUp(self):
        self.con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(self.con.close)
        db.upsert(self.con, {"id": "workable:1", "source": "workable", "company": "Acme", "title": "Engineer",
                             "location": "London", "url": "https://jobs.workable.com/view/1", "description": "",
                             "salary": "", "posted_at": None, "board_size": None})
        db.set_status(self.con, "workable:1", "applying")
        self.con.commit()
        names = ("peek", "read_here", "open_form", "press_control")
        saved = {n: getattr(browser, n) for n in names} | {"render": documents.render, "load": documents.load}
        def restore():
            for n in names:
                setattr(browser, n, saved[n])
            documents.render, documents.load = saved["render"], saved["load"]
        self.addCleanup(restore)
        documents.render = lambda job, docs: {}
        documents.load = lambda con, job: {"letter": "", "answers": []}
        self.calls = []
        empty = {"fields": [], "needs": "", "url": "https://jobs.workable.com/view/1", "title": "", "site": "workable.com",
                 "controls": [{"name": "Apply now", "inNav": False}], "sent": False, "closed": False, "applied": False,
                 "blocked": False, "captcha": False, "next": False}
        browser.read_here = lambda job_id: self.calls.append("read") or dict(empty)
        browser.open_form = lambda job_id, url: self.calls.append("open") or dict(empty)
        browser.press_control = lambda job_id, name: self.fail(f"Hal pressed {name!r} on the applicant's page")

    def test_filling_the_page_you_are_on_never_presses_anything_to_find_a_form(self):
        # A Workable form whose fields Hal missed was closed by Hal pressing "Apply now" to look for it.
        browser.peek = lambda job_id: {"form": False}
        with self.assertRaisesRegex(RuntimeError, "cannot see a form"):
            server.act_auto(self.con, "workable:1", here=True)
        self.assertEqual(self.calls, ["read"])

    def test_fill_in_the_form_uses_the_form_open_in_the_jobs_tab_rather_than_reopening_the_advert(self):
        browser.peek = lambda job_id: {"form": True}
        with self.assertRaisesRegex(RuntimeError, "cannot see a form"):
            server.act_auto(self.con, "workable:1")
        self.assertEqual(self.calls, ["read"])

    def test_starting_over_opens_the_advert_even_with_a_form_open(self):
        browser.peek = lambda job_id: self.fail("starting over looked for an open form")
        saved = server.autofill.choose_control
        server.autofill.choose_control = lambda job, found: ""
        self.addCleanup(setattr, server.autofill, "choose_control", saved)
        with self.assertRaises(RuntimeError):
            server.act_auto(self.con, "workable:1", fresh=True)
        self.assertEqual(self.calls[0], "open")


if __name__ == "__main__":
    unittest.main()
