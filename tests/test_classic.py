"""Classic, today's screens, keeps working through the new server with a session cookie (gate G3):
its page and files at /classic/, the queue, approving documents, filling the form, the helper's poll and
saving the CV. Browser and model work is replaced by stand-ins; the routes and their checks are real.
"""
import unittest
from unittest import mock
from urllib.parse import quote

from support.api import HalServer
from support.world import World, job_id

from jobhunt import extension
from jobhunt.core import settings
from jobhunt.web import server

POLL_WAIT_S = 0.2   # the helper's long poll, shortened so the test does not wait 20 seconds


class Classic(unittest.TestCase):
    """The made-up world, served by the new server."""

    def setUp(self):
        self.world = World().__enter__()
        self.addCleanup(self.world.__exit__, None, None, None)
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.c = self.hal.signed_in()
        self.started = []
        for name in ("act_auto", "act_refill", "act_rerender_cvs"):
            patch = mock.patch.object(server, name, lambda *a, _n=name, **k: self.started.append(_n))
            patch.start()
            self.addCleanup(patch.stop)
        for patch in (mock.patch.object(extension, "POLL_WAIT", POLL_WAIT_S),
                      # PDFs are drawn by the CV code, which has its own tests
                      mock.patch.object(server.documents, "render", lambda job, docs: {})):
            patch.start()
            self.addCleanup(patch.stop)

    def job(self, key: str) -> str:
        return f"/api/job/{quote(job_id(key), safe='')}"

    def test_classic_works_on_the_new_server(self):
        page = self.c.get("/classic/")
        self.assertEqual(page.status, 200)
        self.assertIn('href="/classic/app.css"', page.text)
        self.assertIn('src="/classic/app.js"', page.text)
        self.assertEqual(self.c.get("/classic/app.js").headers["Content-Type"], "text/javascript")
        self.assertEqual(self.c.get("/classic").status, 302)

        queue = self.c.get("/api/queue").json()
        self.assertEqual({j["id"] for j in queue["jobs"]}, {job_id("sorrel-ready"), job_id("wexford-ready")})

        approved = self.c.post(f"{self.job('sorrel-ready')}/docs-approve", {"docs": {"letter": "Made-up words."}})
        self.assertEqual(approved.status, 200, approved.text)
        row = self.world.con.execute("SELECT detail FROM events WHERE job_id = ? ORDER BY id DESC",
                                     (job_id("sorrel-ready"),)).fetchone()
        self.assertEqual(row["detail"], "documents approved")

        filled = self.c.post(f"{self.job('wexford-ready')}/form-fill", {"fields": []})
        self.assertEqual(filled.status, 200, filled.text)
        server.ACTIONS.clear()

        self.assertEqual(self.c.post("/api/ext/poll").json(), {})

        with mock.patch.object(server.cv, "save_base", return_value=["summary"]) as save, \
                mock.patch.object(server.cv, "render"), mock.patch.object(server.cv, "load", return_value={}), \
                mock.patch.object(server, "base_cv", return_value={"cv": {}}):
            saved = self.c.post("/api/cv", {"cv": {"summary": "A made-up summary."}})
        self.assertEqual(saved.status, 200, saved.text)
        self.assertEqual(saved.json()["saved"], 1)
        save.assert_called_once_with({"summary": "A made-up summary."})
        server.ACTIONS.clear()

    def test_classic_needs_a_session(self):
        signed_out = self.hal.client()
        self.assertEqual(signed_out.get("/classic/").status, 401)
        self.assertEqual(signed_out.get("/api/queue").status, 401)
        self.assertEqual(signed_out.post("/api/todos", {"title": "Made-up task"}).status, 401)

    def test_the_old_helper_polls_only_while_the_setting_allows_it(self):
        old = self.hal.client()
        self.assertEqual(old.post("/api/ext/poll").status, 401)
        settings.set("advanced.allow_old_helper", True)
        self.addCleanup(settings.reset, "advanced.allow_old_helper")
        self.assertEqual(old.post("/api/ext/poll").json(), {})
        self.assertEqual(old.get("/api/queue").status, 401)

    def test_classic_is_hidden_when_switched_off(self):
        settings.set("advanced.developer_mode", False)
        self.addCleanup(settings.reset, "advanced.developer_mode")
        self.assertEqual(self.c.get("/classic/").status, 404)
        settings.set("advanced.classic", True)
        self.addCleanup(settings.reset, "advanced.classic")
        self.assertEqual(self.c.get("/classic/").status, 200)


if __name__ == "__main__":
    unittest.main()
