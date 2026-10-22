"""Adopting a form the applicant opened: which job it belongs to, or a new one named from its page.

Moved by class from tests/test_apply.py; the test bodies are unchanged.
"""
import tempfile
import unittest
from pathlib import Path

from support import legacy

from jobhunt import adopt, db

# unittest runs these for this module: no salary research, and no private answers.
setUpModule, tearDownModule = legacy.setUpModule, legacy.tearDownModule


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


if __name__ == "__main__":
    unittest.main()
