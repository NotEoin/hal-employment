"""One approval flow through the real form driver, with a made-up form standing in for the browser.

The engine below answers as `jobhunt.browser` does for a two-page form, in Hal's window or through the
browser helper, and records every press. It never has a Submit to press: what these tests check is what
Hal says, when it moves on by itself, and how the application reaches Sent once the person sends it.
"""
from __future__ import annotations

import contextlib
import unittest
from unittest import mock

from support.world import World, job_id

from jobhunt import autofill, browser, db, documents
from jobhunt.apply import driver
from jobhunt.apply.queries import _app
from jobhunt.core import runner, settings

JOB = "sorrel-ready"
SITE = "jobs.example.org"


def field(hid: str, label: str, **over) -> dict:
    """One question as the engines read it."""
    return {"hid": hid, "sig": label.lower(), "label": label, "kind": "text", "type": "text", "required": True,
            "options": [], "section": "", "context": "", **over}


def page(fields: list[dict], final: bool) -> dict:
    """A form page as `browser.open_form`, `read_here` and `next_step` report it."""
    return {"url": f"https://{SITE}/apply", "site": SITE, "title": "Apply", "fields": fields, "needs": None,
            "sent": False, "closed": False, "applied": False, "blocked": False, "controls": [], "captcha": False,
            "next": not final, "submit": final, "boxes": [], "stayed": False, "problems": []}


class FakeEngine:
    """A two-page made-up form: page 1 asks for a name and an email, page 2 asks why this employer."""

    def __init__(self, page1: list[dict] | None = None, page2: list[dict] | None = None) -> None:
        self.pages = [page1 if page1 is not None else [field("n", "Full name"), field("e", "Email")],
                      page2 if page2 is not None else [field("w", "Why do you want this job?", kind="textarea")]]
        self.at = 0
        self.nexts = 0
        self.thanks = False

    def now(self) -> dict:
        return page(self.pages[self.at], self.at == len(self.pages) - 1)

    def open_form(self, job_id: str, url: str) -> dict:
        self.at = 0
        return self.now()

    def read_here(self, job_id: str) -> dict:
        return self.now()

    def fill(self, job_id: str, fields: list[dict], shot) -> dict:
        return {"new": [], "errors": {}, "next": self.at < len(self.pages) - 1, "screenshot": "", "password": False}

    def next_step(self, job_id: str) -> dict:
        self.nexts += 1
        self.at += 1
        return self.now()

    def peek(self, job_id: str) -> dict:
        if self.thanks:
            return {"form": False, "sent": True, "applied": False, "values": {}, "url": f"https://{SITE}/thanks"}
        return {"form": True, "sent": False, "applied": False, "values": {}, "url": f"https://{SITE}/apply"}

    def patches(self, mode: str) -> list:
        """The patches that put this engine where the driver looks, in `mode` ("window" or "extension")."""
        return [mock.patch.object(browser, name, getattr(self, name))
                for name in ("open_form", "read_here", "fill", "next_step", "peek")] + [
            mock.patch.object(browser, "mode", lambda: mode),
            mock.patch.object(browser, "show", lambda job_id: True),
            mock.patch.object(browser, "mark", lambda job_id, fields, panel: None),
            mock.patch.object(browser, "close", lambda job_id: None),
            mock.patch.object(driver, "watch_tab", lambda job_id: None)]


def answer_all(job, fields, docs, files, con, notes=None):
    """`autofill.propose` for a person whose profile answers every question."""
    return [{**f, "value": f"made-up answer to {f['label']}", "source": "profile"} for f in fields]


def answer_page_one(job, fields, docs, files, con, notes=None):
    """Answers the profile questions; the essay is written by Hal, so the person should read it."""
    return [{**f, "value": "A made-up essay.", "source": "hal", "written": True} if f["kind"] == "textarea"
            else {**f, "value": "Robin Testwell", "source": "profile"} for f in fields]


class Flow(unittest.TestCase):
    def setUp(self):
        self.world = World().__enter__()
        self.addCleanup(self.world.__exit__, None, None, None)
        self.con = self.world.con
        self.job_id = job_id(JOB)
        for p in (mock.patch.object(documents, "render", lambda job, docs: {}),
                  mock.patch.object(autofill, "ask_model", lambda *a, **k: (0, None)),
                  mock.patch.object(autofill, "remember", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def run_in(self, mode: str, engine: FakeEngine, propose=answer_page_one) -> str:
        with mock.patch.object(autofill, "propose", propose), contextlib_all(engine.patches(mode)):
            return driver.act_auto(self.con, self.job_id)

    def form(self) -> dict:
        return _app(self.con, self.job_id)["form"]

    def test_the_approval_words_are_the_same_in_both_modes(self):
        said = {}
        for mode in ("window", "extension"):
            self.con.execute("DELETE FROM applications WHERE job_id = ?", (self.job_id,))
            engine = FakeEngine(page1=[field("n", "Full name"), field("d", "Date of birth")], page2=[])
            # Date of birth stays empty: nothing in the made-up profile answers it.
            propose = lambda job, fields, docs, files, con, notes=None: [  # noqa: E731 - a one-line stand-in
                {**f, "value": None if f["hid"] == "d" else "Robin Testwell", "source": "profile"} for f in fields]
            engine.pages = [engine.pages[0]]
            said[mode] = (self.run_in(mode, engine, propose), self.form()["state"])
        self.assertEqual(said["window"], said["extension"])
        words, state = said["window"]
        self.assertEqual(state, "needs")
        self.assertIn("Hal filled 1 of 2 answers. 1 needs you", words)
        self.assertIn("Check them here or in the form.", words)
        self.assertIn(driver.LAST_PAGE, words)

    def test_middle_pages_with_nothing_to_check_are_passed(self):
        for mode in ("window", "extension"):
            with self.subTest(mode=mode):
                self.con.execute("DELETE FROM applications WHERE job_id = ?", (self.job_id,))
                engine = FakeEngine()
                said = self.run_in(mode, engine)
                form = self.form()
                self.assertEqual(engine.nexts, 1)
                self.assertEqual((form["step"], form["final"], form["state"]), (2, True, "ready"))
                self.assertIn(driver.LAST_PAGE, said)

    def test_a_middle_page_waits_when_pressing_next_is_off(self):
        settings.set("applying.press_next", False)
        self.addCleanup(settings.set, "applying.press_next", True)
        engine = FakeEngine()
        said = self.run_in("window", engine)
        self.assertEqual(engine.nexts, 0)
        self.assertEqual((self.form()["step"], self.form()["state"]), (1, "ready"))
        self.assertNotIn(driver.LAST_PAGE, said)

    def test_the_last_page_hands_over_and_the_watch_moves_the_application_to_sent(self):
        engine = FakeEngine()
        self.run_in("extension", engine, answer_all)
        with contextlib_all(engine.patches("extension")):
            self.assertEqual(driver.act_approve(self.con, self.job_id, []), driver.HANDED_OVER)
            row = self.con.execute("SELECT * FROM applications WHERE job_id = ?", (self.job_id,)).fetchone()
            self.assertIsNotNone(row["handed_over_at"])
            self.assertEqual(self.status(), "applying")
            # The person presses Submit on the site; the watch sees the site's thanks.
            engine.thanks = True
            self.assertFalse(driver._watch_step(self.con, self.job_id, {}))
        row = self.con.execute("SELECT * FROM applications WHERE job_id = ?", (self.job_id,)).fetchone()
        self.assertEqual(self.status(), "applied")
        self.assertEqual(row["sent_how"], "site_confirmed")
        self.assertIsNotNone(row["sent_at"])
        self.assertGreater(row["follow_up_at"], row["sent_at"])
        alert = self.con.execute("SELECT body FROM alerts WHERE job_id = ? ORDER BY id DESC", (self.job_id,)).fetchone()
        self.assertIn("Message the hiring manager?", alert["body"])
        self.assertEqual(engine.nexts, 1)

    def status(self) -> str:
        return self.con.execute("SELECT status FROM jobs WHERE id = ?", (self.job_id,)).fetchone()["status"]


class Kinds(unittest.TestCase):
    """The action kinds carry the flow: a step of their own, then the driver."""

    def test_approving_and_filling_run_as_actions_with_a_why(self):
        with World() as w, mock.patch.object(driver, "act_auto", lambda con, job_id, here, fresh: "filled"), \
                mock.patch.object(documents, "render", lambda job, docs: {}):
            from jobhunt.apply import actions

            aid = actions.approve_documents(w.con, job_id(JOB), {"letter": "A made-up letter."})
            done = runner.wait(aid, 10)
            self.assertEqual((done["state"], done["result"]), ("done", "filled"), done["error"])
            self.assertEqual(done["steps"][0]["text"], "Opening the employer's form")
            why = w.con.execute("SELECT why FROM activity WHERE action_id = ?", (aid,)).fetchone()["why"]
            self.assertEqual(why, "You approved the documents for Software Engineer, Graduate")
            row = w.con.execute("SELECT approved_at, mode FROM applications WHERE job_id = ?",
                                (job_id(JOB),)).fetchone()
            self.assertIsNotNone(row["approved_at"])
            self.assertEqual(row["mode"], browser.mode())
            self.assertTrue(settings.get("setup.first_app_seen"))
            self.assertEqual(documents.load(w.con, w.con.execute("SELECT * FROM jobs WHERE id = ?",
                                                                 (job_id(JOB),)).fetchone())["letter"],
                             "A made-up letter.")
            self.assertEqual(db.STATUSES.count("applying"), 1)


@contextlib.contextmanager
def contextlib_all(patches: list):
    """Enters every patch in a list for the block."""
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


if __name__ == "__main__":
    unittest.main()
