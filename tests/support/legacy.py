"""Shared setup for the tests moved out of tests/test_apply.py: the practice site, the made-up applicant,
the stand-in model, and module setup that keeps salary research and private answers out of every test.

Each test module imports `setUpModule` and `tearDownModule` from here, so unittest runs them for it.
The practice site has an advert with a cookie banner, a two-page form built from the widgets real
application forms use and a confirmation page. Nothing leaves the machine.
"""
import functools
import http.server
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"

from jobhunt import autofill, browser, db, documents, gmail, record, salary
from jobhunt.apply import queries
from jobhunt.core import runner

# An employment record that does not exist, so no test reads the real one or calls Ollama.
record.VAULT = Path(tempfile.mkdtemp()) / "no-record"
_SAVED = salary.itjobswatch, salary.adverts


def setUpModule():
    """No salary research from the web or the real job database, and none of the applicant's private answers."""
    salary.itjobswatch = lambda phrase, london: None
    salary.adverts = lambda con, job, name: []


def tearDownModule():
    salary.itjobswatch, salary.adverts = _SAVED

SITE = Path(__file__).resolve().parent.parent / "site"
EXTENSION = Path(__file__).resolve().parent.parent.parent / "extension"
PROFILE = {
    "first_name": "Ada", "last_name": "Lovelace", "full_name": "Ada Lovelace", "preferred_name": "Ada",
    "email": "ada@example.org", "phone": "07700 900460", "city": "London", "country": "United Kingdom",
    "location": "London, United Kingdom", "right_to_work": "Yes", "sponsorship": "No",
    "notice": "None, available immediately", "start": "Immediately", "salary": "Open",
    "university": "Fernvale University", "degree": "BA (Hons) Made-up Studies",
    "grade": "2:1", "grad_year": "2026", "office": "Yes", "relocate": "Yes",
    "diversity": "Prefer not to say", "nationality": "British",
}
PROSE = ("I want to work here because the practice forms are a good test of careful engineering, "
         "and careful engineering is what I enjoy most.")


def fake_model(job, fields, docs, con=None, problems=None, notes=None):
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
                     documents.files, documents.load, gmail.security_code, browser.search, db.connect)
        autofill.profile = lambda: dict(PROFILE)
        autofill.ask_model = fake_model
        autofill.choose_control = lambda job, found: ""
        documents.render = lambda job, docs: {}
        documents.files = lambda job: {"cv": str(cv)}
        documents.load = lambda con, job: {"letter": "", "answers": []}
        gmail.security_code = lambda since: "A1b2C3d4"
        browser.search = lambda queries, pause=0: ([[]], False)
        # Whatever opens the database itself, such as the web helper, opens the test's copy.
        connect = db.connect
        db.connect = lambda path=tmp / "jobs.db": connect(path)
        cls.con = db.connect()

    @classmethod
    def tearDownClass(cls):
        from jobhunt.apply import driver

        # tab watches and the driver's actions hold the database open; Windows cannot remove it until they end
        driver.stop_watching()
        runner.idle()
        cls.con.close()
        (autofill.profile, autofill.ask_model, autofill.choose_control, documents.render, documents.files,
         documents.load, gmail.security_code, browser.search, db.connect) = cls.saved
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def job(self, name: str, page: str):
        job_id = f"test:{name}"
        db.upsert(self.con, {"id": job_id, "source": "test", "company": "Practice Ltd", "title": "Junior Engineer",
                             "location": "London", "url": f"{self.base}/{page}", "description": "A practice job.",
                             "salary": "", "posted_at": None, "board_size": None})
        db.set_status(self.con, job_id, "ready")
        self.con.execute("DELETE FROM answers2")
        self.con.commit()
        runner.ACTIONS[job_id] = {"name": "test", "state": "running", "message": "", "at": db.now()}
        return job_id

    def form(self, job_id: str) -> dict:
        return queries._app(self.con, job_id)["form"]

    def status(self, job_id: str) -> str:
        return self.con.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]

    def field(self, job_id: str, label: str) -> dict:
        return next(f for f in self.form(job_id)["fields"] if f["label"].startswith(label))
