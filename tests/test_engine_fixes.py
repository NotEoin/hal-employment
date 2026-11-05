"""The engine fixes, each against its practice page, in Hal's own browser and through the helper.

Each page is in `tests/site/practice/` and served by `support.practice_site.PracticeSite`, which counts every
form post, so each test also proves Hal sent nothing. The fixes are generic engine code, never a
branch for a test. The browser tests run with `HAL_TEST_BROWSER=1`; the rules beside them run always.
"""
import contextlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from support import legacy
from support.legacy import Practice
from support.practice_site import HelperBrowser, PracticeSite

from jobhunt import browser, db, extension
from jobhunt.apply import driver, queries
from jobhunt.sites import notes

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule

BROWSER_TESTS = os.environ.get("HAL_TEST_BROWSER") == "1"
WAIT_S = 30
POLL_S = 0.25
POPUP_MS = 1500   # the practice page opens its form this long after it loads
IDLE_S = 4        # how long the test waits in plain Python, as Hal does between looks


class SiteKeys(unittest.TestCase):
    """Sites, sign-ins and the helper's page rules, without a browser."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = browser.__dict__.get("SIGNED_IN")
        browser.SIGNED_IN = Path(self.tmp.name) / "signed-in.json"
        self.addCleanup(lambda: setattr(browser, "SIGNED_IN", saved) if saved else browser.__dict__.pop("SIGNED_IN"))

    def test_each_workday_employer_has_its_own_sign_in_state(self):
        """Regression: signing in to one Workday employer marked every Workday employer signed in, so Hal left
        Apply to the person on all of them."""
        acme = "https://acme.wd3.myworkdayjobs.com/en-GB/careers/job/London/Graduate_R1"
        zenith = "https://zenith.wd5.myworkdayjobs.com/External/job/Leeds/Analyst_R2"
        browser.note_signed_in(acme)
        self.assertTrue(browser.signed_in_at(acme))
        self.assertFalse(browser.signed_in_at(zenith))
        # both engines decide on the same record
        facts = {"url": zenith, "controls": []}
        self.assertFalse(browser._signed_in(facts))
        self.assertTrue(browser._signed_in({**facts, "url": acme}))
        # Greenhouse keeps the employer in the address, and its embedded form names it
        browser.note_signed_in("https://boards.greenhouse.io/fernvale/jobs/4001")
        self.assertTrue(browser.signed_in_at("https://boards.greenhouse.io/fernvale/jobs/4002"))
        self.assertFalse(browser.signed_in_at("https://boards.greenhouse.io/quillworks/jobs/4003"))
        self.assertEqual(browser.site_key("https://job-boards.greenhouse.io/embed/job_app?for=Fernvale&token=1"),
                         "boards.greenhouse.io/fernvale")

    def test_a_site_is_named_by_its_domain_and_shared_systems_keep_the_employer(self):
        self.assertEqual(queries._site("https://careers.acme.co.uk/jobs/2"), "acme.co.uk")
        self.assertEqual(queries._site("https://secure.reed.co.uk/apply"), "reed.co.uk")
        self.assertEqual(queries._site("https://acme.wd3.myworkdayjobs.com/en-GB/careers/job/1"),
                         "acme.wd3.myworkdayjobs.com")

    def test_google_forms_are_read_and_the_rest_of_google_is_not(self):
        self.assertIsNone(browser.NOT_FORM_FRAME.search("https://docs.google.com/forms/d/e/1FAIpQ/viewform"))
        for url in ("https://www.google.com/recaptcha/api2/anchor", "https://docs.google.com/document/d/1/edit",
                    "https://accounts.google.com/o/oauth2", "https://www.googletagmanager.com/ns.html"):
            self.assertIsNotNone(browser.NOT_FORM_FRAME.search(url), url)
        self.assertIn("docs.google.com/forms", extension.PATTERNS["frames"])

    def test_the_fill_limit_grows_with_the_number_of_fields(self):
        """Regression: a form of a dozen slow lists hit the helper's fixed 180 s fill limit."""
        asked = []

        def ask(cmd, timeout=90, **args):
            asked.append((cmd, timeout))
            if cmd == "fill":
                return {"errors": {}, "notTaken": []}
            return {"url": "https://jobs.example.org/1", "fields": [], "next": False, "submit": True, "password": False}

        fields = [{"hid": f"h{i}", "kind": "combobox", "value": "York", "sig": f"s{i}"} for i in range(12)]
        with mock.patch.object(extension, "_ask", ask):
            extension.fill("test:slow", fields, None)
            extension.fill("test:slow", fields[:2], None)
        fills = [t for cmd, t in asked if cmd == "fill"]
        self.assertGreaterEqual(fills[0], 12 * extension.FILL_PER_FIELD_S)
        self.assertEqual(fills[1], extension.FILL_WAIT)

    def test_a_file_the_helper_could_not_upload_is_an_error(self):
        def ask(cmd, timeout=90, **args):
            if cmd == "fill":
                return {"errors": {}, "notTaken": ["h3"]}
            return {"url": "https://jobs.example.org/1", "fields": [], "next": False, "submit": True, "password": False}

        fields = [{"hid": "h3", "kind": "file", "value": str(Path(self.tmp.name) / "cv.pdf"), "sig": "file|cv|CV"}]
        with mock.patch.object(extension, "_ask", ask):
            out = extension.fill("test:upload", fields, None)
        self.assertEqual(out["errors"], {"h3": browser.UPLOAD_FAILED})

    def test_the_helper_sees_code_walls_bot_checks_and_closed_adverts_as_hals_window_does(self):
        """Regression: the helper judged walls by the page title alone."""
        page = {"url": "https://jobs.example.org/apply/1", "title": "Apply", "headings": "", "password": False,
                "apply": False, "fields": [{"kind": "text", "label": "Code", "form": 0}], "text": ""}
        code = {**page, "text": "Enter the code we sent to your email address."}
        check = {**page, "fields": [], "text": "Checking your browser before you reach the page."}
        self.assertEqual(extension._needs(code), "code")
        self.assertEqual(extension._needs(check), "check")
        self.assertEqual(extension._needs(page), "")
        # six boxes, one a digit, are still a page asking for a code (Oracle, testers 2026-11-04)
        digits = [{"kind": "text", "label": f"Enter verification code digit {n} of six.", "form": 0, "hid": f"h{n}"}
                  for n in range(1, 7)]
        self.assertEqual(extension._needs({**page, "fields": digits}), "code")
        survey = {"title": "Apply", "headings": [], "password": False, "text": "", "url": page["url"], "controls": []}
        self.assertEqual(browser._needs(survey, digits), "code")
        self.assertEqual(browser.code_boxes(digits, ""), [f"h{n}" for n in range(1, 7)])
        closed = {**page, "fields": [], "url": "https://boards.greenhouse.io/fernvale/jobs/1?error=true",
                  "captcha": False, "next": False, "submit": False, "unchecked": False}
        self.assertTrue(extension._report(closed, "", False)["closed"])
        # a board's banner at the top of the page, not in a heading (Adzuna, testers 2026-11-04)
        banner = {**closed, "url": "https://jobs.example.org/jobs/1",
                  "text": "What? Where? Search Unfortunately, this job is no longer available. Popular jobs"}
        self.assertTrue(extension._report(banner, "", False)["closed"])
        self.assertFalse(extension._report({**banner, "apply": True}, "", False)["closed"])
        survey = {"title": "Jobs", "headings": [], "url": banner["url"], "text": banner["text"], "controls": []}
        self.assertTrue(browser._closed(survey, 200))
        self.assertFalse(browser._closed({**survey, "text": "Apply now. " * 80 + banner["text"]}, 200))
        for said in ("Applications Closed", "Applications for this position have now closed.",
                     "This listing went offline.", "Sorry, the listing that you're looking for is expired."):
            self.assertTrue(browser._closed({**survey, "text": said}, 200), said)
        self.assertFalse(browser._closed({**survey, "text": "Applications close on 30 November."}, 200))

    def test_a_form_opens_behind_when_preferences_say_not_to_bring_it_forward(self):
        from jobhunt.core import settings

        settings.set("applying.bring_forward", False, by="you")
        self.addCleanup(settings.reset, "applying.bring_forward")
        asked = []
        with mock.patch.object(extension, "_ask", lambda cmd, **k: asked.append((cmd, k)) or {}), \
                mock.patch.object(extension, "_advance", lambda job_id: {}):
            extension.open_form("test:behind", "https://jobs.example.org/apply/1")
        self.assertEqual(asked[0][0], "open")
        self.assertIs(asked[0][1]["front"], False)

    def test_a_job_boards_search_boxes_are_not_the_application(self):
        """Regression (testers, 2026-11-05): Totaljobs' "Job title, skill or company" and "Town, city or postcode"
        were taken for an application form's two questions."""
        for label in ("Job title, skill or company", "Town, city or postcode", "Job title, skills or keywords",
                      "What?", "City, town or zip code"):
            self.assertTrue(browser.not_the_application({"label": label}), label)
        for label in ("Postcode", "Town or city", "Job title", "Current company", "Location (City)"):
            self.assertFalse(browser.not_the_application({"label": label}), label)

    def test_the_browsers_words_for_a_page_that_went_away_are_not_shown(self):
        """Regression (testers, 2026-11-05): "Frame with ID 0 was removed." reached the Form step as it was."""
        self.assertEqual(extension.plain_error("Frame with ID 0 was removed."),
                         "the page changed while Hal was reading it; press \"Fill it in again\"")
        self.assertIn("tab was closed", extension.plain_error("No tab with id: 12."))
        self.assertEqual(extension.plain_error("a made-up error"), "a made-up error")

    def test_an_account_services_address_marks_a_sign_in_page(self):
        """Regression (testers, 2026-11-05): account.ycombinator.com asked only for an email address, and Hal
        typed it in and pressed on."""
        self.assertTrue(browser.sign_in_page("Y Combinator", "https://account.ycombinator.com/?continue=x", "Email"))
        self.assertTrue(browser.sign_in_page("", "https://login.example.org/", ""))
        self.assertFalse(browser.sign_in_page("Junior Analyst", "https://jobs.example.org/apply", "Your details"))

    def test_an_upload_that_appears_later_does_not_get_a_second_cv(self):
        """Regression (testers, 2026-11-04): after an Ashby form rebuilt itself, its new upload box was taken for
        the CV's on its own, so the CV went in twice; Lever's, relabelled after each upload, took it four times
        (2026-11-05)."""
        placed = [{"hid": "h1", "kind": "file", "source": "documents: CV", "value": "/made/up/cv.pdf"}]
        guessed = [{"hid": "h9", "kind": "file", "source": "documents: CV", "value": "/made/up/cv.pdf",
                    "note": "taken to be the CV upload; check this"}]
        self.assertTrue(driver._one_cv(placed, [dict(guessed[0])])[0]["skip"])
        # Lever relabels its upload after each file, and the relabelled box names the CV itself.
        named = {"hid": "h7", "kind": "file", "source": "documents: CV", "value": "/made/up/cv.pdf", "note": "",
                 "label": "Resume/CV EOIN EXAMPLE CV.PDF Success!"}
        self.assertTrue(driver._one_cv(placed, [named])[0]["skip"])
        self.assertEqual(driver._one_cv([], [dict(guessed[0])])[0]["value"], "/made/up/cv.pdf")

    def test_the_tab_is_brought_forward_for_work_but_never_for_a_watch(self):
        """Regression: every command, a watch's look every 3 s too, made the job's tab the active one, so two
        watched jobs flipped Hal's window between their tabs."""
        asked = []

        def ask(cmd, timeout=90, **args):
            asked.append((cmd, args.get("front")))
            return {"url": "https://jobs.example.org/1", "title": "Apply", "fields": [], "text": "", "headings": "",
                    "password": False, "apply": False, "unchecked": False, "next": False, "submit": False,
                    "captcha": False, "frame": "", "signOut": False, "stayed": False}

        with mock.patch.object(extension, "_ask", ask):
            extension.peek("test:watched")
            extension.read_here("test:watched")
        self.assertEqual(asked, [("read", False), ("read", True)])


def forget_notes() -> None:
    """Removes the notes a test added and the uses they recorded, so the next test has only the built-in ones."""
    with contextlib.closing(db.connect()) as con:
        con.execute("DELETE FROM site_events")
        con.execute("DELETE FROM site_notes")
        con.commit()


class NoteHooks(unittest.TestCase):
    """The engines' note hooks, without a browser."""

    def setUp(self):
        self.addCleanup(forget_notes)

    def test_each_field_transform_types_the_answer_as_the_note_says(self):
        self.assertEqual(browser.transformed("Leeds, United Kingdom", "city_only"), "Leeds")
        self.assertEqual(browser.transformed("07700 900-460", "digits_only"), "07700900460")
        self.assertEqual(browser.transformed("+44 7700 900460", "uk_phone"), "07700900460")
        self.assertEqual(browser.transformed("2026-09-01", "date:%d/%m/%Y"), "01/09/2026")
        self.assertEqual(browser.transformed("A long answer", "max_len:6"), "A long")
        with self.assertRaises(ValueError):
            browser.transformed("Leeds", "shout")

    def test_a_field_note_changes_only_its_own_question_and_leaves_a_trace(self):
        url = "https://jobs.example.org/apply/7"
        notes.add("job", "https://jobs.example.org/apply/", "field_transform", {"label": "^location"},
                  {"transform": "city_only"}, "In \"Location\", type only the city")
        fields = [{"hid": "h1", "kind": "text", "label": "Location", "value": "Leeds, United Kingdom"},
                  {"hid": "h2", "kind": "text", "label": "Address", "value": "1 Made-up Row, Leeds"}]
        out, trace = browser.with_notes("test:city", url, fields)
        self.assertEqual([f["value"] for f in out], ["Leeds", "1 Made-up Row, Leeds"])
        self.assertEqual(fields[0]["value"], "Leeds, United Kingdom", "the approved answer itself is kept")
        self.assertEqual(len(trace), 1)
        self.assertIn("Typed 'Leeds' for Location", trace[0]["what"])
        self.assertIn("Without it Hal would type 'Leeds, United Kingdom'", trace[0]["what"])
        # named in "Notes Hal used on this site" with its words, as a note that answered a question is
        self.assertEqual((trace[0]["text"], trace[0]["origin"]), ("In \"Location\", type only the city", "you"))
        self.assertEqual(out[0]["notes_used"], trace)
        # the form keeps the line on the field and in its list of notes used, once
        form = {"fields": [dict(f) for f in fields]}
        driver._note_uses(form, {"notes_used": trace})
        driver._note_uses(form, {"notes_used": trace})
        self.assertEqual(form["fields"][0]["notes_used"], trace)
        self.assertEqual(form["notes_used"], trace)
        self.assertNotIn("notes_used", form["fields"][1])

    def test_with_no_notes_the_engines_behave_as_before(self):
        fields = [{"hid": "h1", "kind": "text", "label": "Location", "value": "Leeds, United Kingdom"}]
        self.assertEqual(browser.with_notes("test:none", "https://jobs.example.org/apply/7", fields), (fields, []))
        self.assertEqual(browser.banner_words(browser.notes_for("https://jobs.example.org/")),
                         (browser.COOKIE_BUTTONS, None))
        job = {"id": "test:lever", "url": "https://jobs.lever.co/fernvale/1234"}
        self.assertEqual(browser.application_url(job), "https://jobs.lever.co/fernvale/1234/apply")

    def test_an_open_url_note_wins_over_the_built_in_rule(self):
        notes.add("site", "fernvale.org.uk", "open_url", {}, {"append": "/application-form"},
                  "Open the application form directly, not the advert page")
        job = {"id": "test:open", "url": "https://careers.fernvale.org.uk/jobs/12?ref=board"}
        self.assertEqual(browser.application_url(job), "https://careers.fernvale.org.uk/jobs/12/application-form")

    def test_a_cookie_note_says_which_button_closes_the_banner_and_which_never_to_press(self):
        notes.add("site", "fernvale.org.uk", "cookie_button", {}, {"text": "Only the needed ones", "never": ["OK"]},
                  "Close the cookie banner with \"Only the needed ones\"")
        words, never = browser.banner_words(browser.notes_for("https://careers.fernvale.org.uk/jobs/12"))
        self.assertTrue(words[0].search("Only the needed ones"))
        self.assertTrue(never.search("OK"))
        self.assertIsNone(never.search("OK to continue"))


class Fixes:
    """Each fix against its practice page, run by a subclass in one mode. `helper` says which."""

    helper = False

    @classmethod
    def start_site(cls):
        cls.site = PracticeSite().__enter__()
        cls.cv = Path(cls.tmp.name) / "Ada-Lovelace-CV.pdf"

    def setUp(self):
        self.site.reset()
        self.addCleanup(forget_notes)

    def practice_job(self, name: str, page: str) -> str:
        job_id = self.job(f"{'helper' if self.helper else 'window'}-fix-{name}", page)
        self.con.execute("UPDATE jobs SET url = ? WHERE id = ?", (f"{self.site.base}/{page}", job_id))
        self.con.commit()
        return job_id

    def on_page(self, job_id: str, page: str, act):
        """Runs `act(tab)` in the job's tab, as the person would."""
        if self.helper:
            return self.in_tab(page, act)
        return browser.BROWSER.call(lambda b: act(b.tab(job_id)))

    def opened(self, name: str, page: str) -> tuple[str, dict]:
        job_id = self.practice_job(name, page)
        return job_id, browser.open_form(job_id, f"{self.site.base}/{page}")

    @staticmethod
    def answers(found: dict, values: dict) -> list[dict]:
        """The page's fields with these answers, by label, as the person approved them."""
        out = []
        for f in found["fields"]:
            value = next((v for label, v in values.items() if f["label"].startswith(label)), None)
            if value is not None:
                out.append({**f, "value": value})
        return out

    def fill(self, job_id: str, fields: list[dict]) -> dict:
        return browser.fill(job_id, fields, Path(self.tmp.name) / "shots" / "fix.png")

    def test_a_failed_upload_is_never_reported_as_done(self):
        """Regression: a file field was always counted as held, so a CV the site dropped
        was reported as uploaded."""
        job_id, found = self.opened("upload", "upload-check.html")
        fields = self.answers(found, {"First name": "Ada", "Last name": "Lovelace", "Email": "ada@example.org",
                                      "CV": str(self.cv)})
        out = self.fill(job_id, fields)
        cv = next(f for f in fields if f["kind"] == "file")
        self.assertEqual(out["errors"], {cv["hid"]: browser.UPLOAD_FAILED})
        # the Form step shows it as refused, to put right by hand
        marks = driver._marks({"fields": [{**f, "error": out["errors"].get(f["hid"], "")} for f in fields]})
        self.assertEqual(next(m for m in marks if m["hid"] == cv["hid"])["state"], "error")
        self.assertEqual(self.site.sent(), 0)

    def test_an_upload_the_site_lists_by_name_is_done(self):
        job_id, found = self.opened("upload-kept", "upload-kept.html")
        fields = self.answers(found, {"First name": "Ada", "Last name": "Lovelace", "CV": str(self.cv)})
        self.assertEqual(self.fill(job_id, fields)["errors"], {})
        self.assertIn(self.cv.name, self.on_page(job_id, "upload-kept.html", lambda tab: tab.inner_text("#taken")))
        self.assertEqual(self.site.sent(), 0)

    def test_the_cookie_clicker_never_presses_a_consent_box_in_the_form(self):
        """Regression: "I agree" was pressed wherever it was, the form's own consent too."""
        job_id, found = self.opened("consent", "consent-in-form.html")
        self.assertTrue(found["fields"])
        page = "consent-in-form.html"
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.evaluate("() => window.consentPressed")), 0)
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.locator("#banner").count()), 0)
        self.assertEqual(self.site.sent(), 0)

    def test_a_list_that_takes_several_answers_gets_every_one(self):
        """Regression: a multi-select list got its answers' names joined into one value."""
        job_id, found = self.opened("multi", "multiselect.html")
        langs = next(f for f in found["fields"] if f["label"].startswith("Which languages"))
        self.assertTrue(langs.get("multiple"))
        out = self.fill(job_id, [{**langs, "value": ["French", "Welsh"]}])
        self.assertEqual(out["errors"], {})
        chosen = self.on_page(job_id, "multiselect.html", lambda tab: tab.evaluate(
            "() => [...document.querySelector('#langs').selectedOptions].map(o => o.text)"))
        self.assertEqual(chosen, ["French", "Welsh"])
        self.assertEqual(self.site.sent(), 0)

    def test_a_note_to_type_only_the_city_passes_a_form_that_refuses_the_rest(self):
        page = "city-only.html"
        job_id, found = self.opened("city", page)
        values = {"First name": "Ada", "Last name": "Lovelace", "Location": "Leeds, United Kingdom"}
        self.fill(job_id, self.answers(found, values))
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.input_value("#loc")), "", "the form refuses it")
        notes.add("job", f"{self.site.base}/{page}", "field_transform", {"label": "^location"},
                  {"transform": "city_only"}, "In \"Location\", type only the city")
        out = self.fill(job_id, self.answers(found, values))
        self.assertEqual(out["errors"], {})
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.input_value("#loc")), "Leeds")
        self.assertEqual(len(out["notes_used"]), 1)
        self.assertEqual(out["notes_used"][0]["field"], "Location")
        self.assertEqual(self.site.sent(), 0)

    def test_a_note_names_the_control_that_leads_to_the_form(self):
        page = "apply-first.html"
        _job_id, found = self.opened("first-without", page)
        self.assertEqual(found["fields"], [], "Hal does not know the site's own words for Apply")
        notes.add("job", f"{self.site.base}/{page}", "press_first", {}, {"control": "Express your interest"},
                  "Press \"Express your interest\" before looking for the form")
        _job_id, found = self.opened("first-with", page)
        self.assertEqual([f["label"] for f in found["fields"]], ["First name", "Last name", "Email"])
        self.assertEqual(self.site.sent(), 0)

    def test_a_job_boards_search_alerts_and_pop_up_are_passed_on_the_way_to_the_form(self):
        """Regression (testers, 2026-11-04): on an Adzuna advert Hal filled the board's job search and its
        job-alert box as the application, under a job-alert pop-up, and never pressed Apply."""
        job_id, found = self.opened("board", "board-advert.html")
        self.assertEqual([f["label"] for f in found["fields"]], ["First name", "Last name", "Email", "Phone"])
        self.assertEqual(self.on_page(job_id, "single.html",
                                      lambda tab: tab.evaluate("() => sessionStorage.getItem('popupClosed')")), "1")
        self.assertEqual((self.site.sent(), self.site.posts["/alerts"]), (0, 0))

    def test_a_job_alert_box_opened_by_apply_is_left_by_its_link_to_the_job(self):
        """Regression (testers, 2026-11-04): Adzuna's Apply opened a job-alert box placed absolutely under it, and
        Hal said "no Apply button recognised" instead of following "No thanks, take me to the job"."""
        job_id, found = self.opened("capture", "apply-capture.html")
        self.assertEqual([f["label"] for f in found["fields"]], ["First name", "Last name", "Email", "Phone"])
        self.assertEqual(self.on_page(job_id, "single.html",
                                      lambda tab: tab.evaluate("() => sessionStorage.getItem('popupClosed')")), "1")
        self.assertEqual((self.site.sent(), self.site.posts["/alerts"]), (0, 0))

    def test_a_list_that_found_nothing_is_closed_and_an_open_one_hides_no_questions(self):
        """Regression (testers, 2026-11-04): an Ashby location list left open on "No results" hid the rest of the
        form, so Hal read three questions, took the page for a new one and filled the whole form again."""
        page = "hides-while-listed.html"
        job_id, found = self.opened("listed", page)
        everything = ["First name", "Last name", "Where are you currently located?", "Do you need sponsorship?"]
        self.assertEqual([f["label"] for f in found["fields"]], everything)
        out = browser.fill(job_id, self.answers(found, {"Where are you": "Atlantis"}), driver._shot(job_id))
        self.assertTrue(next(iter(out["errors"].values())).startswith("no option"), out["errors"])
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.get_attribute("#loc", "aria-expanded")), "false")
        # The person opens the list themselves while Hal looks.
        self.on_page(job_id, page, lambda tab: tab.focus("#loc"))
        self.assertEqual([f["label"] for f in browser.read_here(job_id)["fields"]], everything)
        self.assertEqual(self.site.sent(), 0)

    def test_a_sign_in_page_that_asks_for_the_email_first_is_left_to_the_person(self):
        """Regression (testers, 2026-11-05): a sign-in page that said so only in its opening words was filled in
        with the applicant's email address and Continue was pressed."""
        page = "welcome-back.html"
        job_id, found = self.opened("email-first", page)
        self.assertEqual((found["needs"], found["fields"]), ("sign-in", []))
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.input_value("#em")), "")
        self.assertEqual(self.site.sent(), 0)

    def test_an_apply_link_in_the_pages_footer_is_not_the_jobs(self):
        """Regression (testers, 2026-11-05): on a Hacker News job post Hal pressed the forum's own "Apply to YC"
        link, from its row of links at the foot of the page, and reached Y Combinator's sign-in."""
        page = "forum-post.html"
        job_id, found = self.opened("forum", page)
        self.assertEqual((found["fields"], found["needs"]), ([], ""))
        self.assertTrue(self.on_page(job_id, page, lambda tab: tab.url.endswith(page)))
        self.assertEqual(self.site.sent(), 0)

    def test_a_searched_list_is_typed_into_until_it_offers_the_answer(self):
        """Regression (testers, 2026-11-04): the helper was never told a list could be typed into, so Ashby's
        location list, which offers places only once you type, was left empty: "no option matching"."""
        page = "hides-while-listed.html"
        job_id, found = self.opened("searched", page)
        out = browser.fill(job_id, self.answers(found, {"Where are you": "London, United Kingdom"}),
                           driver._shot(job_id))
        self.assertEqual(out["errors"], {})
        self.assertEqual(self.on_page(job_id, page, lambda tab: tab.input_value("#loc")),
                         "London, Greater London, England, United Kingdom")
        self.assertEqual(self.site.sent(), 0)

    def test_a_boards_search_and_alerts_in_its_one_page_form_are_passed_on_the_way_to_the_form(self):
        """Regression (testers, 2026-11-04): on ProductionBase, whose whole page is one form, Hal filled the
        board's job search ("Job Types", "Industry") as the application."""
        _job_id, found = self.opened("one-form", "one-form-board.html")
        self.assertEqual([f["label"] for f in found["fields"]], ["First name", "Last name", "Email", "Phone"])
        self.assertEqual(self.site.sent(), 0)

    def test_answers_survive_a_form_that_rebuilds_itself_after_the_cv_is_uploaded(self):
        """Regression (testers, 2026-11-04): an Ashby form read the uploaded CV and built itself again, and ten of
        Hal's answers were lost "no longer on the page"."""
        page = "rebuilds-on-upload.html"
        job_id, found = self.opened("rebuild", page)
        fields = self.answers(found, {"CV": str(self.cv), "First name": "Ada", "Last name": "Lovelace",
                                      "Email": "ada@example.org"})
        for f in fields:
            f["source"] = "documents: CV" if f["kind"] == "file" else "profile"
        out = driver._fill_uploads_first(job_id, fields)
        self.assertEqual(out["errors"], {})
        shown = self.on_page(job_id, page, lambda tab: (tab.evaluate("() => window.rebuilds"), tab.input_value("#fn"),
                                                        tab.input_value("#ln"), tab.input_value("#em")))
        self.assertEqual(shown, (1, "Ada", "Lovelace", "ada@example.org"))
        self.assertEqual(self.site.sent(), 0)

    def test_with_pop_ups_left_alone_in_preferences_hal_closes_none(self):
        from jobhunt.core import settings

        settings.set("applying.close_popups", False, by="you")
        self.addCleanup(settings.reset, "applying.close_popups")
        job_id, _found = self.opened("board-off", "board-advert.html")
        page = "board-advert.html"
        # Hal's own window may follow the Apply link's address past the pop-up; either way Hal never closed it.
        closed = self.on_page(job_id, page, lambda tab: tab.evaluate("() => sessionStorage.getItem('popupClosed')"))
        self.assertIsNone(closed)
        self.assertEqual(self.site.posts["/alerts"], 0)

    def test_a_google_form_in_a_frame_is_read(self):
        """Regression: every google.com/ frame was passed over, Google Forms too."""
        _job_id, found = self.opened("gform", "gform.html")
        labels = [f["label"] for f in found["fields"]]
        self.assertEqual(labels[:3], ["First name", "Last name", "Email"])
        self.assertEqual(self.site.sent(), 0)


@unittest.skipUnless(BROWSER_TESTS, "browser test: set HAL_TEST_BROWSER=1")
class InHalsWindow(Fixes, Practice):
    """Each fix in Hal's own browser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_site()

    @classmethod
    def tearDownClass(cls):
        try:
            browser.BROWSER.call(lambda b: b._ctx and b._ctx.close())
        except Exception:  # the browser never started
            pass
        cls.site.__exit__(None, None, None)
        super().tearDownClass()

    def test_hals_window_keeps_up_while_hal_is_idle(self):
        """Regression: the browser thread waited on its queue between calls, so Playwright heard nothing from
        Hal's window while Hal was idle; on Windows a person's own Submit there could wait."""
        job_id = self.practice_job("idle", "popup-later.html")

        def open_tab(b):
            page = b.context().new_page()
            b.follow(job_id, page)
            page.goto(f"{self.site.base}/popup-later.html?ms={POPUP_MS}")

        browser.BROWSER.call(open_tab)
        # the page opens its form in a new tab while this thread waits in plain Python, as Hal does
        deadline = time.monotonic() + IDLE_S
        while len(browser.BROWSER.pages.get(job_id, [])) < 2 and time.monotonic() < deadline:
            time.sleep(POLL_S)
        self.assertEqual(len(browser.BROWSER.pages.get(job_id, [])), 2, "Hal's window heard nothing while idle")

    def test_the_submit_block_stays_while_another_command_still_holds_it(self):
        """Regression: two commands shared one block, and the first to end took it from the other."""
        job_id, _found = self.opened("block", "single.html")

        def hold(b):
            page = b.tab(job_id)
            page.evaluate(browser.BLOCK_JS)
            page.evaluate(browser.BLOCK_JS)
            first = page.evaluate(browser.UNBLOCK_JS)
            page.evaluate("() => document.querySelector('form').submit()")
            still = page.evaluate("() => Boolean(window.__halUnblock)")
            last = page.evaluate(browser.UNBLOCK_JS)
            gone = page.evaluate("() => !window.__halUnblock")
            return first, still, last, gone

        first, still, last, gone = browser.BROWSER.call(hold)
        self.assertEqual(first, [])
        self.assertTrue(still)
        self.assertEqual(last, ["a script on the page tried to send the form"])
        self.assertTrue(gone)
        self.assertEqual(self.site.sent(), 0)


@unittest.skipUnless(BROWSER_TESTS, "browser test: set HAL_TEST_BROWSER=1")
class WithTheHelper(Fixes, HelperBrowser, Practice):
    """Each fix through the browser helper, in a browser that loads it as the applicant's own does."""

    helper = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_site()
        cls.start_helper(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.stop_helper()
        cls.site.__exit__(None, None, None)
        super().tearDownClass()

    def tearDown(self):
        driver.stop_watching()
        self.close_tabs()
        super().tearDown()

    def active(self, address: str) -> bool:
        """True when the helper's tab at this address is the one shown in its window."""
        return self.on_helper(lambda: self.worker.evaluate(
            """address => chrome.tabs.query({}).then(ts => ts.filter(t => t.url.includes(address)).pop().active)""",
            address))

    def test_this_window_brings_the_tab_forward_before_reading(self):
        """Regression: with "Open forms in: This window" the tab opened behind the person's and was never
        brought forward, so its dropdowns never opened (L background.js:83, browser-apply.md 4)."""
        page = "background-tab.html"
        job_id = self.practice_job("this-window", page)
        extension._ask("open", timeout=60, job=job_id, url=f"{self.site.base}/{page}", window="same")
        self.assertFalse(self.active(page), "the form opens behind the person's own tab")
        found = extension.read_here(job_id)
        self.assertTrue(self.active(page))
        office = next(f for f in found["fields"] if f["label"] == "Nearest office")
        self.assertEqual(self.fill(job_id, [{**office, "value": "York"}])["errors"], {})
        self.assertEqual(self.in_tab(page, lambda tab: tab.inner_text("#city")), "York")
        self.assertEqual(self.in_tab(page, lambda tab: tab.evaluate("() => window.openedHidden")), 0)
        # The person goes back to their own tab; Hal's watch looks without taking the window from them.
        self.on_helper(lambda: self.ctx.pages[0].bring_to_front())
        self.assertFalse(self.active(page))
        self.assertTrue(extension.peek(job_id)["form"])
        self.assertFalse(self.active(page))
        self.assertEqual(self.site.sent(), 0)

    def test_the_submit_block_stays_while_another_command_still_holds_it(self):
        """Regression: `halBlock` returned early when a block was on, so two commands in one tab shared it and
        the first to end took it from the other."""
        page = "single.html"
        job_id = self.practice_job("block", page)
        extension._ask("open", timeout=60, job=job_id, url=f"{self.site.base}/{page}")
        held = self.on_helper(lambda: self.worker.evaluate("""async address => {
            const [tab] = (await chrome.tabs.query({})).filter(t => t.url.includes(address));
            const run = func => chrome.scripting.executeScript({ target: { tabId: tab.id }, world: 'MAIN', func })
              .then(([r]) => r.result);
            await run(halBlock);
            await run(halBlock);
            const first = await run(halUnblock);
            await run(() => document.querySelector('form').submit());
            const still = await run(() => Boolean(window.__halUnblock));
            const last = await run(halUnblock);
            const gone = await run(() => !window.__halUnblock);
            return { first, still, last, gone };
        }""", page))
        self.assertEqual(held, {"first": [], "still": True, "last": ["a script on the page tried to send the form"],
                                "gone": True})
        self.assertEqual(self.site.sent(), 0)


del Fixes  # the mixin's tests run only through the two modes above

if __name__ == "__main__":
    unittest.main()
