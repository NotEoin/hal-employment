"""End-to-end tests of applying, against the practice site in tests/site.

The site has an advert with a cookie banner, a two-page form built from the
widgets real application forms use (hidden radios, a Yes/No button pair, a
searchable dropdown, a question that appears only for one answer, an upload,
an emailed security code) and a confirmation page. Nothing leaves the machine.

Run with: .venv/bin/python -m unittest discover tests
"""
import functools
import http.server
import os
import tempfile
import threading
import unittest
from pathlib import Path

os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"

from jobhunt import adopt, autofill, browser, db, documents, extension, gmail  # noqa: E402
from jobhunt.web import server  # noqa: E402

SITE = Path(__file__).parent / "site"
EXTENSION = Path(__file__).resolve().parent.parent / "extension"
PROFILE = {
    "first_name": "Ada", "last_name": "Lovelace", "full_name": "Ada Lovelace", "preferred_name": "Ada",
    "email": "ada@example.org", "phone": "07000 000000", "city": "London", "country": "United Kingdom",
    "location": "London, United Kingdom", "right_to_work": "Yes", "sponsorship": "No",
    "notice": "None, available immediately", "start": "Immediately", "salary": "Open",
    "salary_unadvertised": "Open", "university": "University", "degree": "Degree",
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

    def test_a_security_code_is_found_in_its_email(self):
        self.assertEqual(gmail.code_in("Security code for your application",
                                       "Copy and paste this code into the security code field: aB3dE5fG Then"), "aB3dE5fG")
        self.assertIsNone(gmail.code_in("Welcome", "Use the code below to continue with your application"))


if __name__ == "__main__":
    unittest.main()
