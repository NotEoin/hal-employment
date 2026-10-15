"""End-to-end tests of applying, against the practice site in tests/site.

The site has an advert with a cookie banner, a two-page form built from the
widgets real application forms use (hidden radios, a Yes/No button pair, a
searchable dropdown, a question that appears only for one answer, an upload,
an emailed security code) and a confirmation page. Nothing leaves the machine.

Run with: .venv/bin/python -m unittest discover tests
"""
import functools
import json
import http.server
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"

from jobhunt import (adopt, autofill, browser, cv, cvbuild, cvscore, db, documents, extension, gmail,  # noqa: E402
                     record, salary, tasks)
from jobhunt.web import server  # noqa: E402

# An employment record that does not exist, so no test reads the real one or calls Ollama.
record.VAULT = Path(tempfile.mkdtemp()) / "no-record"
_SAVED = salary.itjobswatch, salary.adverts, autofill.private, tasks.private


def setUpModule():
    """No salary research from the web or the real job database, and none of the applicant's private answers."""
    salary.itjobswatch = lambda phrase, london: None
    salary.adverts = lambda con, job, name: []
    autofill.private = tasks.private = lambda: {}


def tearDownModule():
    salary.itjobswatch, salary.adverts, autofill.private, tasks.private = _SAVED

SITE = Path(__file__).parent / "site"
EXTENSION = Path(__file__).resolve().parent.parent / "extension"
PROFILE = {
    "first_name": "Ada", "last_name": "Lovelace", "full_name": "Ada Lovelace", "preferred_name": "Ada",
    "email": "ada@example.org", "phone": "07000 000000", "city": "London", "country": "United Kingdom",
    "location": "London, United Kingdom", "right_to_work": "Yes", "sponsorship": "No",
    "notice": "None, available immediately", "start": "Immediately", "salary": "Open",
    "university": "University", "degree": "Degree",
    "grade": "Grade", "grad_year": "Year", "office": "Yes", "relocate": "Yes",
    "diversity": "Prefer not to say", "nationality": "British and Irish",
}
PROSE = ("I want to work here because the practice forms are a good test of careful engineering, "
         "and careful engineering is what I enjoy most.")


def fake_model(job, fields, docs, con=None, problems=None):
    """Stands in for the local model: writes prose for the essay and leaves the date of birth unanswered."""
    answered = 0
    for f in fields:
        if f.get("value") in (None, "", []) and not f.get("skip") and f["kind"] == "textarea":
            f.update(value=PROSE, source="hal", note="")
            answered += 1
    autofill.mark_written(fields)
    return answered, None


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: D102 - keep the test output to the results
        pass

    def do_GET(self):
        """/slow is the practice form, six seconds late, as a busy employer's site can be."""
        if self.path.startswith("/slow"):
            time.sleep(6)
            self.path = "/form.html"
        super().do_GET()


class Practice(unittest.TestCase):
    """Serves the practice site and stands in for the applicant's profile, documents, model and inbox."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        handler = functools.partial(Quiet, directory=str(SITE))
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cv = tmp / "Ada-Lovelace-CV.pdf"
        cv.write_bytes(b"%PDF-1.4\n%%EOF\n")
        browser.PROFILE, browser.SHOTS, browser.SIGNED_IN = tmp / "profile", tmp / "shots", tmp / "signed-in.json"
        cls.saved = (autofill.profile, autofill.ask_model, autofill.choose_control, documents.render,
                     documents.files, documents.load, gmail.security_code, server.browser.search, db.connect)
        autofill.profile = lambda: dict(PROFILE)
        autofill.ask_model = fake_model
        autofill.choose_control = lambda job, found: ""
        documents.render = lambda job, docs: {}
        documents.files = lambda job: {"cv": str(cv)}
        documents.load = lambda con, job: {"letter": "", "answers": []}
        gmail.security_code = lambda since: "A1b2C3d4"
        server.browser.search = lambda queries, pause=0: ([[]], False)
        # Whatever opens the database itself, such as the web helper, opens the test's copy.
        connect = db.connect
        db.connect = lambda path=tmp / "jobs.db": connect(path)
        cls.con = db.connect()

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        (autofill.profile, autofill.ask_model, autofill.choose_control, documents.render, documents.files,
         documents.load, gmail.security_code, server.browser.search, db.connect) = cls.saved
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def job(self, name: str, page: str):
        job_id = f"test:{name}"
        db.upsert(self.con, {"id": job_id, "source": "test", "company": "Practice Ltd", "title": "Junior Engineer",
                             "location": "London", "url": f"{self.base}/{page}", "description": "A practice job.",
                             "salary": "", "posted_at": None, "board_size": None})
        db.set_status(self.con, job_id, "ready")
        self.con.execute("DELETE FROM answers")
        self.con.commit()
        server.ACTIONS[job_id] = {"name": "test", "state": "running", "message": "", "at": db.now()}
        return job_id

    def form(self, job_id: str) -> dict:
        return server._app(self.con, job_id)["form"]

    def status(self, job_id: str) -> str:
        return self.con.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]

    def field(self, job_id: str, label: str) -> dict:
        return next(f for f in self.form(job_id)["fields"] if f["label"].startswith(label))


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


class InYourBrowser(Practice):
    """Forms filled through the extension, in a browser that loads it as the applicant's Brave does."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        super().setUpClass()
        tmp = Path(cls.tmp.name)
        server.SERVABLE, cls.servable = (*server.SERVABLE, tmp.resolve()), server.SERVABLE
        cls.helper = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.helper.serve_forever, daemon=True).start()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "extension"
        cls.pw = sync_playwright().start()
        cls.ctx = cls.pw.chromium.launch_persistent_context(
            str(tmp / "brave"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"])
        worker = cls.ctx.service_workers[0] if cls.ctx.service_workers else cls.ctx.wait_for_event("serviceworker")
        worker.evaluate("url => chrome.storage.local.set({hal: url})", f"http://127.0.0.1:{cls.helper.server_address[1]}")
        cls.worker = worker
        for _ in range(60):
            if extension.connected():
                break
            cls.ctx.pages[0].wait_for_timeout(500)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.close()
        cls.pw.stop()
        cls.helper.shutdown()
        cls.helper.server_close()
        server.SERVABLE = cls.servable
        os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"
        super().tearDownClass()

    def tab(self, address: str):
        # Playwright hears of tabs the extension opened only while it is waiting on something.
        self.ctx.pages[0].wait_for_timeout(300)
        return next(t for t in reversed(self.ctx.pages) if address in t.url)

    def test_a_slow_page_in_one_tab_does_not_hold_up_another(self):
        """2026-10-15 10:25: with six applications open, one timed out queued behind another tab's page load."""
        quick, slow = self.job("ext-quick", "form.html"), self.job("ext-slow", "form.html")
        extension._ask("open", timeout=60, job=quick, url=f"{self.base}/form.html")
        opening = threading.Thread(target=extension._ask, args=("open",),
                                   kwargs={"timeout": 60, "job": slow, "url": f"{self.base}/slow"})
        opening.start()
        time.sleep(0.5)
        started = time.time()
        extension._ask("read", timeout=4, job=quick, patterns={**extension.PATTERNS, "banners": None, "options": False})
        self.assertLess(time.time() - started, 4)
        opening.join()

    def test_applications_open_together_in_a_window_of_their_own(self):
        """apply_window = "separate": job tabs share one window, apart from the one the applicant works in."""
        for name in ("ext-window-1", "ext-window-2"):
            server.act_auto(self.con, self.job(name, f"form.html?{name}"))
        self.ctx.pages[0].wait_for_timeout(300)
        tabs = self.worker.evaluate("() => chrome.tabs.query({}).then(ts => ts.map(t => [t.url, t.windowId]))")
        own = self.worker.evaluate("() => chrome.storage.session.get('halWindow').then(s => s.halWindow)")
        # Earlier tests may have opened the window already; either way both jobs are in it, and it is not the only one.
        self.assertEqual({w for url, w in tabs if "ext-window" in url}, {own})
        self.assertGreater(len({w for _, w in tabs}), 1)

    def test_fills_the_first_page_and_leaves_what_it_cannot_answer(self):
        job_id = self.job("ext-fill", "form.html")
        said = server.act_auto(self.con, job_id)
        self.assertIn("1 required question", said)
        page = self.tab("form.html")
        self.assertEqual(page.input_value("#fn"), "Ada")
        self.assertEqual(page.inner_text("#uni-shown"), "University")
        self.assertTrue(page.is_checked("#rtw-y"))
        self.assertEqual(page.input_value("#heard"), "Company website")
        self.assertEqual(page.input_value("#notice"), "0")
        self.assertEqual(page.input_value("#dob"), "")

    def test_marks_the_page_then_follows_the_applicant_through_to_submitting(self):
        job_id = self.job("ext-journey", "form.html")
        server.act_auto(self.con, job_id)
        page = self.tab("form.html")
        marks = page.evaluate("""() => Object.fromEntries(['#fn', '#dob', '#notice'].map(
            s => [s, document.querySelector(s).closest('[data-hal-mark]')?.getAttribute('data-hal-mark') || null]))""")
        self.assertEqual(marks, {"#fn": "ok", "#dob": "answer", "#notice": "ok"})
        self.assertIn("1 to answer", page.inner_text("#hal-panel .counts"))
        self.assertIn("Date of birth", page.inner_text("#hal-panel ul"))

        # The applicant answers what Hal could not, changes one of its answers, and moves on.
        seen = {}
        page.fill("#dob", "01/02/2003")
        page.fill("#ln", "Byron")
        self.assertEqual(page.evaluate("() => document.querySelector('#dob').closest('[data-hal-mark]').dataset.halMark"),
                         "edited")
        self.assertTrue(server._watch_step(self.con, job_id, seen))
        page.click("#next")
        self.assertTrue(server._watch_step(self.con, job_id, seen))
        form = self.form(job_id)
        self.assertEqual((form["step"], form["state"]), (2, "ready"))
        self.assertEqual(page.input_value("#why"), PROSE)
        self.assertEqual(page.evaluate("() => document.querySelector('#why').closest('[data-hal-mark]').dataset.halMark"),
                         "check")
        # The first page's upload, hidden with the rest of that page, is not outlined.
        self.assertIsNone(page.evaluate("() => document.querySelector('#app').getAttribute('data-hal-mark')"))

        # Hal never submits; once the applicant does, the site's thanks marks the job applied.
        self.assertEqual(self.status(job_id), "applying")
        page.click("#send")
        self.assertFalse(server._watch_step(self.con, job_id, seen))
        self.assertEqual(self.status(job_id), "applied")
        kept = {r["question"]: db.json_value(r["answer"]) for r in self.con.execute("SELECT * FROM answers")}
        self.assertEqual((kept["date of birth"], kept["last name"]), ("01/02/2003", "Byron"))


    def test_fills_a_tab_the_applicant_opened_and_handed_over(self):
        job_id = self.job("ext-adopt", "advert.html?jobId=7")
        server.ACTIONS.pop(job_id)
        mine = self.ctx.new_page()
        mine.goto(f"{self.base}/form.html?jobId=7")
        worker = self.ctx.service_workers[0]
        worker.evaluate("""async () => {
            const [tab] = await chrome.tabs.query({ url: '*://127.0.0.1/form.html?jobId=7' });
            await handTab(tab);
        }""")
        for _ in range(120):
            if server.ACTIONS.get(job_id, {}).get("state") not in (None, "running"):
                break
            mine.wait_for_timeout(500)
        self.assertEqual(server.ACTIONS[job_id]["state"], "done", server.ACTIONS[job_id]["message"])
        self.assertEqual(mine.input_value("#fn"), "Ada")
        self.assertIn("1 to answer", mine.inner_text("#hal-panel .counts"))


class Adopting(unittest.TestCase):
    """Matching a tab the applicant opened to a known job, or making one from the page."""

    def setUp(self):
        self.con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(self.con.close)
        self.saved = adopt.gpu.busy
        adopt.gpu.busy = lambda: "no model in the tests"
        self.addCleanup(setattr, adopt.gpu, "busy", self.saved)
        db.upsert(self.con, {"id": "greenhouse:1", "source": "greenhouse", "company": "Monzo", "title": "Graduate Backend Engineer",
                             "location": "London", "url": "https://job-boards.greenhouse.io/monzo/jobs/123",
                             "description": "", "salary": "", "posted_at": None, "board_size": None})
        db.set_status(self.con, "greenhouse:1", "ready")

    def page(self, url, **more):
        return {"url": url, "title": "", "heading": "", "site": "", "text": "", **more}

    def test_the_form_under_an_advert_is_that_advert_s_job(self):
        job, made = adopt.adopt(self.con, self.page("https://job-boards.greenhouse.io/monzo/jobs/123/apply?utm=x"))
        self.assertEqual((job["id"], made), ("greenhouse:1", False))

    def test_a_job_is_known_by_its_employer_and_title_on_another_site(self):
        job, _ = adopt.adopt(self.con, self.page("https://monzo.wd3.myworkdayjobs.com/x/apply",
                                                 title="Graduate Backend Engineer - Monzo Careers"))
        self.assertEqual(job["id"], "greenhouse:1")

    def test_an_unknown_application_becomes_a_job_named_from_its_page(self):
        job, made = adopt.adopt(self.con, self.page("https://jobs.lever.co/acme-robotics/abc/apply",
                                                    heading="Software Engineer, Perception", text="We build robots."))
        self.assertTrue(made)
        self.assertEqual((job["company"], job["title"], job["status"]),
                         ("Acme Robotics", "Software Engineer, Perception", "applying"))
        again, made = adopt.adopt(self.con, self.page("https://jobs.lever.co/acme-robotics/abc/apply"))
        self.assertEqual((again["id"], made), (job["id"], False))


class Discarding(unittest.TestCase):
    """An application already started can be dropped, for a job that turns out to have closed."""

    def setUp(self):
        self.con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(self.con.close)
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

    def test_an_application_hal_is_working_on_is_not_discarded(self):
        server.ACTIONS["greenhouse:1"] = {"name": "applying", "state": "running", "message": "", "at": db.now()}
        self.addCleanup(server.ACTIONS.pop, "greenhouse:1")
        with self.assertRaisesRegex(RuntimeError, "still working"):
            self.discard("greenhouse:1", closed=True)
        self.assertEqual(server._job(self.con, "greenhouse:1")["status"], "applying")


class BaseCv(unittest.TestCase):
    """Hal's base CV: the website's, with the applicant's changes laid over it."""

    SITE = {"title": "Graduate", "summary": "Builds things.", "moreProjects": "", "closing": "",
            "education": [{"qualification": "BSc", "meta": "City", "detail": "New dissertation wording.",
                           "modules": [{"year": "Final", "rows": [["Security", 80]]}]}],
            "projects": [{"name": "Hal", "stack": "Python", "body": "An assistant."},
                         {"name": "Hatch", "stack": "C#", "body": "A mood tracker."}],
            "experience": [], "skills": [["Languages", ["Python", "C++"]]], "additional": [["Interests", "Running"]]}

    def setUp(self):
        self.site = json.loads(json.dumps(self.SITE))
        saved = cv.site, cv.OVERRIDES, cv.ORIGIN
        def restore():
            cv.site, cv.OVERRIDES, cv.ORIGIN = saved
        self.addCleanup(restore)
        cv.site = lambda: json.loads(json.dumps(self.site))
        tmp = Path(tempfile.mkdtemp())
        cv.OVERRIDES, cv.ORIGIN = tmp / "cv-overrides.json", tmp / "cv-origin"

    def test_an_edit_reaches_hals_cv_and_survives_the_website_changing(self):
        mine = cv.for_editing()
        mine["education"][0]["detail"] = "Old dissertation wording."
        mine["projects"][1]["hidden"] = True
        mine["skills"][0][1] = ["Python"]
        self.assertEqual(len(cv.save_base(mine)), 3)
        # The website later adds a project and rewords its summary; Hal's CV takes both, and keeps its own changes.
        self.site["projects"].insert(0, {"name": "Lidar", "stack": "C++", "body": "A car."})
        self.site["summary"] = "Builds careful things."
        base = cv.load()
        self.assertEqual(base["education"][0]["detail"], "Old dissertation wording.")
        self.assertEqual(base["education"][0]["modules"], self.SITE["education"][0]["modules"])
        self.assertEqual([p["name"] for p in base["projects"]], ["Lidar", "Hal"])
        self.assertEqual((base["skills"][0][1], base["summary"]), (["Python"], "Builds careful things."))
        self.assertNotIn("_key", base["projects"][0])

    def test_skill_groups_can_be_rearranged(self):
        mine = cv.for_editing()
        mine["skills"] = [["Languages", ["Python"]], ["Tools", ["Git"]]]
        cv.save_base(mine)
        self.site["skills"].append(["Web", ["React"]])
        self.assertEqual(cv.load()["skills"], [["Languages", ["Python"]], ["Tools", ["Git"]]])

    def test_a_renamed_entry_is_still_matched_to_the_websites(self):
        mine = cv.for_editing()
        mine["projects"][0]["name"] = "Hal, a local assistant"
        cv.save_base(mine)
        self.assertEqual([p["name"] for p in cv.load()["projects"]], ["Hal, a local assistant", "Hatch"])

    def test_an_application_keeps_its_own_edit_of_the_cv(self):
        job = {"title": "Engineer", "company": "Acme", "location": "", "salary": "", "description": "Python"}
        self.assertEqual(documents.current_cv(job, {})["education"][0]["detail"], "New dissertation wording.")
        mine = {"cv": {**cv.load(), "summary": "For Acme."}}
        self.assertEqual(documents.current_cv(job, mine)["summary"], "For Acme.")


class BuildingCv(unittest.TestCase):
    """A CV written from the applicant's material, in the shape the website's PDF builder takes."""

    FACTS = {"education": [{"qualification": "Degree", "institution": "University",
                            "dates": "2023 - 2026", "grade": "First", "points": ["Dissertation on a research topic"],
                            "modules": [{"year": "Final year", "mark": 75,
                                         "rows": [["Cryptography", 80], ["Compilers", "n/a"], ["Security", 79]]}]}],
             "projects": [{"name": "Hal", "stack": "Python", "points": ["Local assistant"]}]}
    DRAFT = {"title": "Graduate", "summary": "A recent graduate.",
             "education": [{"qualification": "Degree", "grade": "Grade",
                            "meta": "University | 2023 - 2026", "detail": "**Dissertation:** a research topic."}],
             "projects": [{"name": "Hal - local assistant", "stack": "Python", "body": "**Hal:** answers from notes."},
                          {"name": "Hatch", "stack": "C#", "body": "A mood tracker."}],
             "skills": [["Languages", ["Python", ""]]], "experience": [{"role": "Tutor", "org": "", "dates": "", "body": "x"}],
             "additional": [["Interests", "Running"]], "moreProjects": "", "closing": ""}

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        saved = (cvbuild.SOURCES, cvbuild.PREVIEW, cv.BUILT, cvbuild.gpu.busy, cvbuild.llm.chat_json)
        def restore():
            cvbuild.SOURCES, cvbuild.PREVIEW, cv.BUILT, cvbuild.gpu.busy, cvbuild.llm.chat_json = saved
        self.addCleanup(restore)
        cvbuild.SOURCES, cvbuild.PREVIEW, cv.BUILT = tmp / "sources", tmp / "preview", tmp / "cv-built.json"
        cvbuild.gpu.busy = lambda: None
        self.asked = []

        def model(system, user, **kw):
            self.asked.append(system)
            if system is cvbuild.EXTRACT:
                return self.FACTS
            if system is cvbuild.COMPOSE:
                return self.DRAFT
            return {"unsupported": ["tutoring experience"]}
        cvbuild.llm.chat_json = model

    def test_material_is_kept_as_plain_text(self):
        name = cvbuild.add("Dissertation summary", "I studied a research topic.")
        self.assertEqual(name, "dissertation-summary")
        self.assertEqual(cvbuild.add("notes.md", data=b"More notes"), "notes")
        self.assertEqual([x["name"] for x in cvbuild.sources()], ["notes", "dissertation-summary"])
        cvbuild.remove("notes")
        self.assertEqual(len(cvbuild.sources()), 1)

    def test_builds_a_cv_the_pdf_builder_accepts_and_lists_what_to_check(self):
        cvbuild.add("transcript", "Cryptography 80, Security 79")
        out = cvbuild.build({"website": False, "projects": 1, "modules_min": 0})
        built = out["cv"]
        # Marks come from the material as it stands; a mark that is not a number is dropped.
        self.assertEqual(built["education"][0]["modules"][0]["rows"], [["Cryptography", 80], ["Security", 79]])
        self.assertEqual([p["slug"] for p in built["projects"]], ["hal-local-assistant"])
        self.assertEqual((built["experience"], built["skills"]), ([], [["Languages", ["Python"]]]))
        self.assertEqual(built["closing"], "References available on request.")
        self.assertNotIn("—", built["education"][0]["detail"])
        self.assertIn("a number no source states: 12", out["flags"])
        self.assertIn("not backed by your material: tutoring experience", out["flags"])
        self.assertTrue((cvbuild.PREVIEW / "Firstname-Lastname-CV.pdf").exists())
        self.assertEqual(json.loads(cv.BUILT.read_text())["cv"], built)

    def test_nothing_is_built_without_material(self):
        with self.assertRaisesRegex(RuntimeError, "add some material"):
            cvbuild.build({"website": False})


class Scoring(unittest.TestCase):
    """Hal's CV layout read back as a parser reads it, and the CV graded by rule."""

    def cv(self, body: str) -> dict:
        base = cv.printable(cv.site())
        base["projects"] = [{**base["projects"][0], "body": body}]
        base["experience"] = []
        return base

    def render(self, data: dict) -> Path:
        return cv.render(data, Path(tempfile.mkdtemp()))

    def test_bullets_and_marks_read_back_in_order(self):
        pdf = self.render(self.cv("- Built a thing that answers from notes.\n- Wrote 113 tests that run anywhere."))
        text = cvscore.pdf_text(pdf)
        self.assertIn("- Built a thing that answers from notes.", text)
        self.assertRegex(text, r"Selected modules \(out of 100\): [^\n]*\d")
        self.assertEqual(cvscore.parsing(self.cv(""), pdf)[4]["score"], 1.0)  # tables read back in order

    def test_bullets_with_results_outscore_a_paragraph_of_duties(self):
        duties = self.cv("Responsible for the backend. Helped with various features and worked on testing.")
        results = self.cv("- Built the backend in Flask, serving 3 clients.\n- Cut test time from 9 to 2 minutes "
                          "by running suites in parallel.")
        worse, better = cvscore.score(duties, self.render(duties)), cvscore.score(results, self.render(results))
        self.assertGreater(better["mark"], worse["mark"])
        verbs = [next(c["score"] for g in r["groups"] for c in g["checks"] if c["name"].startswith("Lines open"))
                 for r in (worse, better)]
        self.assertLess(verbs[0], verbs[1])

    def test_a_cv_no_parser_can_read_is_capped(self):
        empty = Path(tempfile.mkdtemp()) / "image.pdf"
        empty.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.assertLessEqual(cvscore.score(self.cv("- Built it."), empty)["mark"], 10)

    def test_a_short_readable_cv_is_not_capped_as_unreadable(self):
        """A CV under 250 words still has a text layer; its length is for the length checks to judge."""
        short = self.cv("- Built a parser in Python, with 40 tests.")
        short.update(education=[{"qualification": "Degree", "grade": "Grade",
                                  "meta": "University | Sep 2023 - Jun 2026", "detail": ""}],
                     skills=[], additional=[], moreProjects="", summary="")
        pdf = self.render(short)
        self.assertLess(len(cvscore.pdf_text(pdf).split()), 250)
        mark = cvscore.score(short, pdf)
        readable = next(c for g in mark["groups"] for c in g["checks"] if c["name"] == "Text can be read back")
        self.assertEqual((readable["score"], mark["capped"]), (1.0, []))

    def test_a_labelled_line_is_judged_by_its_verb(self):
        self.assertTrue(cvscore.strong_start("Dissertation, A Title: tested whether models segment images"))
        self.assertTrue(cvscore.strong_start("Built a pipeline over four datasets: results cached by config"))
        self.assertFalse(cvscore.strong_start("Responsible for: testing"))

    def test_evidence_the_cv_does_not_hold_counts_as_missing(self):
        job = {"title": "Engineer", "company": "Acme", "description": "You have strong Python. A 2:1 degree is required."}
        replies = iter([{"requirements": [
                            {"need": "Python", "kind": "must", "weight": 3, "quote": "You have strong Python."},
                            {"need": "Degree", "kind": "knockout", "weight": 3, "quote": "A 2:1 degree is required"},
                            {"need": "Rust", "kind": "must", "weight": 2, "quote": "made up by the model"}]},
                        {"evidence": [{"id": 1, "match": "direct", "quote": "Built a parser in Python"},
                                      {"id": 2, "match": "direct", "quote": "a 2:1 in Physics"}]}])
        saved = cvscore.llm.chat_json, cvscore.gpu.busy
        cvscore.llm.chat_json, cvscore.gpu.busy = lambda *a, **k: next(replies), lambda: None
        try:
            out = cvscore.match(job, "- Built a parser in Python.\nDegree, Grade")
        finally:
            cvscore.llm.chat_json, cvscore.gpu.busy = saved
        # The requirement the advert never stated is dropped; the degree evidence is not in the CV.
        self.assertEqual([r["need"] for r in out["requirements"]], ["Python", "Degree"])
        self.assertEqual([r["match"] for r in out["requirements"]], ["direct", "missing"])
        self.assertEqual((out["label"], out["knockouts_unmet"]), ("Long shot", ["Degree"]))

    def test_evidence_found_only_in_the_record_is_listed_but_not_counted(self):
        """A requirement the record meets and the CV does not is reported as in the record, and lowers coverage."""
        job = {"title": "Research engineer", "company": "Acme",
               "description": "You have strong Python. Research ethics training is required."}
        replies = iter([{"requirements": [
                            {"need": "Python", "kind": "must", "weight": 3, "quote": "You have strong Python."},
                            {"need": "Ethics training", "kind": "knockout", "weight": 3,
                             "quote": "Research ethics training is required"}]},
                        {"evidence": [{"id": 1, "match": "direct", "quote": "Built a parser in Python"},
                                      {"id": 2, "match": "direct",
                                       "quote": "Human-subjects research training, completed 5 March 2026"}]}])
        saved = cvscore.llm.chat_json, cvscore.gpu.busy, cvscore.record.context
        cvscore.llm.chat_json, cvscore.gpu.busy = lambda *a, **k: next(replies), lambda: None
        cvscore.record.context = lambda queries, **k: ("[Certificates/CITI.md > CITI]\n"
                                                       "Human-subjects research training, completed 5 March 2026.")
        try:
            out = cvscore.match(job, "- Built a parser in Python.")
        finally:
            cvscore.llm.chat_json, cvscore.gpu.busy, cvscore.record.context = saved
        self.assertEqual([r["where"] for r in out["requirements"]], ["cv", "record"])
        self.assertEqual((out["in_record"], out["knockouts_unmet"]), (["Ethics training"], ["Ethics training"]))
        self.assertEqual(out["coverage"], 50)


class Answers(unittest.TestCase):
    def setUp(self):
        self.saved = autofill.profile
        autofill.profile = lambda: dict(PROFILE)
        self.job = {"id": "test:1", "source": "adzuna", "company": "Practice Ltd", "title": "Junior Engineer",
                    "salary": "", "description": "", "location": "London"}

    def tearDown(self):
        autofill.profile = self.saved

    def propose(self, **field):
        f = {"hid": "h0", "kind": "text", "type": "text", "label": "", "required": True, **field}
        return autofill.propose(self.job, [f], {}, {})[0]

    def test_a_socio_economic_question_is_not_answered_with_the_university(self):
        f = self.propose(kind="combobox", label="Were you eligible for free school meals? Choose up to 1",
                         options=["No", "Prefer not to say", "Yes"])
        self.assertEqual((f["value"], f["source"]), ("Prefer not to say", "profile: diversity"))

    def test_a_question_that_only_mentions_a_fact_is_left_to_the_model(self):
        f = self.propose(kind="textarea", label="Please detail any additional training courses you have completed. "
                                                "This may be self-led online or in school or college.")
        self.assertIsNone(f["value"])

    def test_a_listed_fact_is_chosen_from_its_list_however_long_the_question(self):
        f = self.propose(kind="combobox", options=["Other University", "University"],
                         label="Which university are you currently attending or did you attend for your "
                               "undergraduate studies? Type the name to search the list provided below.")
        self.assertEqual(f["value"], "University")

    def test_a_number_box_is_not_given_a_sentence(self):
        self.assertIsNone(self.propose(type="number", label="Salary expectations")["value"])
        self.assertEqual(self.propose(type="number", label="Notice period (in weeks)")["value"], "0")

    def test_a_salary_question_gets_a_researched_figure_with_its_reasoning(self):
        """No "open to discussion": a range in words, a number in a number box, and the band holding it in a list."""
        autofill.profile = lambda: {**PROFILE, "salary": ""}
        saved = salary.expect
        salary.expect = lambda job, con=None: {"figure": 38000, "text": "£36,000 to £40,000",
                                               "basis": "ITJobsWatch median for graduate software engineer in London: £38,000"}
        self.addCleanup(setattr, salary, "expect", saved)
        words = self.propose(label="What are your salary expectations?")
        self.assertEqual((words["value"], words["source"]), ("£36,000 to £40,000", "profile: salary"))
        self.assertIn("ITJobsWatch median", words["note"])
        self.assertEqual(self.propose(type="number", label="Salary expectations")["value"], "38000")
        listed = self.propose(kind="select", label="Expected salary", options=["£25,000 - £34,999", "£35,000 - £44,999"])
        self.assertEqual(listed["value"], "£35,000 - £44,999")

    def health(self):
        autofill.profile = lambda: {**PROFILE, "disability": "Yes", "disability_type": "Dyslexia", "guaranteed_interview": "Yes",
                                    "adjustments": "Yes. I would like 25% extra time on timed tests."}

    def test_a_disclosed_disability_is_given_and_other_identity_questions_are_declined(self):
        """The applicant discloses a disability only: gender and the like stay "Prefer not to say"."""
        self.health()
        f = self.propose(kind="radio", label="Do you consider yourself to have a disability?",
                         options=["Yes", "No", "Prefer not to say"])
        self.assertEqual((f["value"], f["source"]), ("Yes", "private: disability"))
        self.assertEqual(self.propose(kind="radio", label="What is your gender?",
                                      options=["Man", "Woman", "Prefer not to say"])["value"], "Prefer not to say")
        kinds = self.propose(kind="checkgroup", label="Which of the following best describes your disability?",
                             options=["Physical impairment", "Neurodivergent condition (e.g. autism, dyslexia)", "Mental health"])
        self.assertEqual(kinds["value"], ["Neurodivergent condition (e.g. autism, dyslexia)"])
        described = self.propose(kind="textarea", label="Please describe the nature of your disability")
        self.assertEqual(described["value"], "Dyslexia")

    def test_caring_for_someone_with_a_disability_is_not_the_applicants_disability(self):
        """Nomura, 2026-10-15: "look after someone ... caused by disability" was answered "Yes, 1-19 hours a week"."""
        self.health()
        carer = ["No", "Yes, 1-19 hours a week", "Yes, 20-49 hours a week", "Yes, 50 hours or more a week", "Prefer not to say"]
        for label in ("Do you look after or care for someone with long term physical or mental ill health caused by "
                      "disability or age?", "Does anyone in your household have a disability?",
                      "Do you have caring responsibilities for a disabled family member?"):
            f = self.propose(kind="combobox", label=label, options=carer)
            self.assertEqual(f["value"], "Prefer not to say", label)
            self.assertFalse(f["source"].startswith("private"), label)

    def test_a_caring_question_gets_the_applicants_answer(self):
        """The applicant cares for no one, and says so; whether they were in care themselves is another question."""
        autofill.profile = lambda: {**PROFILE, "carer": "No"}
        hours = ["Yes, 1-19 hours a week", "Yes, 20-49 hours a week", "No, I do not provide care", "Prefer not to say"]
        nomura = self.propose(kind="combobox", label="Do you look after or care for someone with long term physical or "
                                                     "mental ill health caused by disability or age?", options=hours)
        self.assertEqual((nomura["value"], nomura["source"]), ("No, I do not provide care", "profile: carer"))
        self.assertEqual(self.propose(kind="radio", label="Do you have any caring responsibilities?",
                                      options=["Yes", "No", "Prefer not to say"])["value"], "No")
        self.assertEqual(self.propose(label="Are you a carer?")["value"], "No")
        self.assertEqual(self.propose(kind="radio", label="Have you ever been in care, or are you a care leaver?",
                                      options=["Yes", "No", "Prefer not to say"])["value"], "Prefer not to say")

    def test_disability_questions_in_their_many_wordings(self):
        """Each wording gets the answer the applicant chose, or is left for them where only they can judge."""
        self.health()
        yes_no, mine = ["Yes", "No", "Prefer not to say"], "Yes. I would like 25% extra time on timed tests."
        cases = [
            ("radio", "Do you have a disability? If yes, please give details below.", yes_no, "", "Yes"),
            ("radio", "Does your disability affect your ability to carry out the role?", yes_no, "", None),
            ("radio", "Is your day-to-day activity limited because of a health problem or disability?",
             ["Yes, limited a lot", "Yes, limited a little", "No"], "", None),
            ("radio", "Are you registered disabled?", yes_no, "", None),
            ("textarea", "Please tell us about any disability-related support you need", [], "", mine),
            ("radio", "Do you have any access requirements for the interview?", ["Yes", "No"], "", "Yes"),
            ("text", "Preferred pronouns", [], "We are a Disability Confident employer and offer a guaranteed interview",
             "Prefer not to say"),
            ("checkgroup", "Please select the category that best describes your impairment",
             ["Physical", "Learning disability, difficulty or difference (such as dyslexia, ADHD)", "Prefer not to say"], "",
             ["Learning disability, difficulty or difference (such as dyslexia, ADHD)"]),
            ("radio", "Would you like to hear about our disability network?", ["Yes", "No"], "", None),
        ]
        for kind, label, options, context, expected in cases:
            with self.subTest(label):
                f = self.propose(kind=kind, label=label, options=options, context=context)
                self.assertEqual(f["value"], expected)

    def test_a_question_about_a_formal_diagnosis_is_left_for_the_applicant(self):
        self.health()
        f = self.propose(kind="radio", label="Do you have a formal diagnosis of your disability?", options=["Yes", "No"])
        self.assertIsNone(f["value"])
        self.assertIn("diagnosis", f["note"])

    def test_a_guaranteed_interview_is_taken_and_adjustments_are_asked_for(self):
        self.health()
        self.assertIs(self.propose(kind="checkbox", label="I wish to apply under the Disability Confident guaranteed "
                                                          "interview scheme", required=False)["value"], True)
        self.assertEqual(self.propose(kind="radio", label="Do you require any reasonable adjustments?",
                                      options=["Yes", "No"])["value"], "Yes")
        details = self.propose(kind="textarea", label="If yes, please give details",
                               context="Reasonable adjustments for the assessment process")
        self.assertEqual((details["value"], details["source"]), ("Yes. I would like 25% extra time on timed tests.",
                                                                 "private: adjustments"))

    def test_private_answers_never_reach_the_model_that_writes(self):
        """Letters and essays are written from facts that hold no health answer."""
        self.health()
        self.assertNotIn("Dyslexia", autofill._facts(self.job, {}, None))
        self.assertNotIn("extra time", autofill._facts(self.job, {}, None))

    def test_a_test_invitation_brings_a_to_do_to_ask_for_extra_time(self):
        con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(con.close)
        con.execute("""INSERT INTO emails (msgid, thrid, date, sender, rcpt, subject, kind, company, role, job_id, snippet)
                       VALUES ('m1', '1', ?, 'talent@acme.example', '', 'Your online assessment', 'assessment', 'Acme',
                               'Engineer', NULL, '')""", (db.now(),))
        tasks.private = lambda: {"adjustments": "Yes. 25% extra time."}
        self.addCleanup(setattr, tasks, "private", lambda: {})
        tasks.sync(con)
        titles = [r["title"] for r in con.execute("SELECT title FROM todos ORDER BY priority, id")]
        self.assertEqual(titles, ["Ask Acme for extra time before starting the test", "Complete the Acme assessment"])

    def test_an_optional_sign_up_is_left_alone_and_consent_is_given(self):
        self.assertTrue(self.propose(kind="checkbox", label="Send me the newsletter", required=False)["skip"])
        self.assertIs(self.propose(kind="checkbox", label="I agree to the privacy policy")["value"], True)

    def test_the_source_is_matched_to_the_nearest_kind_the_list_offers(self):
        f = self.propose(kind="select", label="How did you hear about us?",
                         options=["Company website", "Job board", "A friend"])
        self.assertEqual(f["value"], "Job board")

    def test_a_referee_is_not_given_the_applicants_details(self):
        self.assertIsNone(self.propose(label="Email", section="Referee 1")["value"])
        self.assertEqual(self.propose(label="Email", section="About you")["value"], "ada@example.org")

    def test_a_course_start_date_is_not_the_applicants_availability(self):
        self.assertIsNone(self.propose(label="Start date", section="Education")["value"])
        self.assertEqual(self.propose(label="Earliest start date", section="Availability")["value"], "Immediately")

    def test_a_referees_name_is_remembered_as_the_referees(self):
        con = db.connect(Path(tempfile.mkdtemp()) / "jobs.db")
        self.addCleanup(con.close)
        autofill.remember(con, [{"hid": "h0", "kind": "text", "label": "Name", "section": "Referee 1",
                                 "value": "Grace Hopper", "source": "you"}])
        mine = self.propose(label="Name", section="Your details")
        self.assertEqual(mine["value"], "Ada Lovelace")
        f = {"hid": "h0", "kind": "text", "type": "text", "label": "Name", "section": "Referee 1", "required": True}
        self.assertEqual(autofill.propose(self.job, [f], {}, {}, con)[0]["value"], "Grace Hopper")

    def test_a_keyword_answer_the_review_rejects_is_answered_again(self):
        f = self.propose(label="Phone number of your current manager")
        self.assertEqual(f["source"], "profile: phone")
        replies = iter([{"wrong": [{"hid": "h0", "why": "asks for the manager's number"}]}, {"answers": []}])
        saved = autofill.llm.chat_json, autofill.gpu.busy
        autofill.llm.chat_json, autofill.gpu.busy = lambda *a, **k: next(replies), lambda: None
        try:
            autofill.ask_model(self.job, [f], {})
        finally:
            autofill.llm.chat_json, autofill.gpu.busy = saved
        self.assertIsNone(f["value"])
        self.assertIn("manager's number", f["note"])

    def test_a_number_ending_a_sentence_in_the_source_is_supported(self):
        """A source's "2023-2024." holds 2024, so a draft giving 2024 is not flagged."""
        from jobhunt import pack
        self.assertEqual(pack.unsupported_numbers("Editor, 2023 - 2024", "history column, 2023-2024. Commissioned"), [])

    def test_a_security_code_is_found_in_its_email(self):
        self.assertEqual(gmail.code_in("Security code for your application",
                                       "Copy and paste this code into the security code field: aB3dE5fG Then"), "aB3dE5fG")
        self.assertIsNone(gmail.code_in("Welcome", "Use the code below to continue with your application"))


if __name__ == "__main__":
    unittest.main()
