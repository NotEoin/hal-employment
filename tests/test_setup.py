"""Tests of the setup guide's server side: steps saved as they go, skipped steps on Home, About
you never resetting what was changed elsewhere, the record folder, the Gmail test and phone alerts.

Every test runs a real server in its own temporary home. Gmail and ntfy are fakes put in place of the one
call each module makes, and the first search is never really started, so nothing here reaches the network.
The person is made up.
"""
from __future__ import annotations

import ast
import contextlib
import imaplib
import re
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from ui.test_words import DETAILS, REMOTE_PAGE, literals

from jobhunt import extension, record
from jobhunt.core.settings import secrets
from jobhunt.setup import email_test, phone, state

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "jobhunt" / "web" / "static" / "app"
GOOD_PASSWORD = "abcd efgh ijkl mnop"     # a made-up app password, typed as Google shows it
ADDRESS = "ffion.marchbank@example.com"
NAME = "Ffion Marchbank"
# Words that would send someone to a terminal or a text editor (no step or message may).
COMMAND_WORDS = re.compile(r"command prompt|command line|\bterminal\b|powershell|cmd\.exe|\bconsole\b|"
                           r"\bopen a shell\b|text editor|notepad|\bedit (the|this|your|a) [\w .-]*file\b|"
                           r"\brun this command\b|\btype (this|the) command\b|\brun:? (python|uv|git|sudo)\b", re.I)
# Python modules whose strings reach the page: every route, Home and "Is Hal working?" provider, and setup.
PAGE_WORD_MODULES = ("routes.py", "_routes.py", "home.py", "health.py", "_health.py")
# A developer's old data layout is the one place a command is named: it exists only in a checkout made with
# git, never in an install the bootstrap made, so its Home item and its "Is Hal working?" line may say it.
DEVELOPER_ONLY = {"jobhunt/core/home.py", "jobhunt/core/health.py"}


class FakeGmail:
    """Stands in for `imaplib.IMAP4_SSL`: accepts only the made-up app password, and counts sign-ins."""

    logins: ClassVar[list[str]] = []

    def __init__(self, host: str, timeout: float) -> None:
        self.host, self.timeout = host, timeout

    def login(self, address: str, password: str) -> None:
        FakeGmail.logins.append(address)
        if password != GOOD_PASSWORD.replace(" ", ""):
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    def logout(self) -> None:
        """Signs out."""


class Sent:
    """Stands in for `urlopen`: keeps each request instead of sending it."""

    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request, timeout: float):
        self.requests.append(request)
        return contextlib.nullcontext()


class SetupRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        self.first_search = self.enterContext(mock.patch("jobhunt.setup.routes.start_first_search",
                                                         return_value=None))

    def put(self, path: str, body: dict) -> dict:
        r = self.c.request("PUT", "/api/v2" + path, body)
        self.assertEqual(r.status, 200, r.text)
        return r.json()

    def get(self, path: str) -> dict:
        r = self.c.get("/api/v2" + path)
        self.assertEqual(r.status, 200, r.text)
        return r.json()

    def post(self, path: str, body: dict | None = None) -> dict:
        r = self.c.post("/api/v2" + path, body or {})
        self.assertEqual(r.status, 200, r.text)
        return r.json()

    def about(self) -> tuple[dict, dict]:
        """About you as the page loads it: the values by key, and the fields."""
        fields = self.get("/setup/about")["fields"]
        return {f["key"]: f["value"] for f in fields}, {f["key"]: f for f in fields}

    def walk(self, *, skip: tuple[str, ...] = ()) -> None:
        """Goes through every step as the guide's Continue and Skip buttons do."""
        for s in state.STEPS:
            self.put(f"/setup/{s.id}", {"state": "current"})
            self.put(f"/setup/{s.id}", {"state": "skipped" if s.id in skip else "done"})

    def test_setup_saves_as_you_go_and_can_be_left_halfway(self) -> None:
        first = self.get("/setup")
        self.assertEqual(first["step"], "welcome")
        self.assertFalse(self.get("/session")["setup_done"], "a new install opens the guide by itself")
        self.assertEqual([s["id"] for s in first["steps"]],
                         ["welcome", "cv", "about", "goal", "record", "ai", "helper", "email", "phone", "updates",
                          "done"])
        self.put("/setup/welcome", {"state": "done"})
        self.put("/setup/about", {"state": "current"})
        seen, _ = self.about()
        saved = self.put("/setup/about/fields", {"values": {"you.name": NAME}, "seen": seen})["saved"]
        self.assertTrue(saved["you.name"]["ok"])
        self.post("/setup/later")

        again = self.get("/setup")
        self.assertEqual(again["step"], "about", "the guide opens where the person left it")
        self.assertEqual({s["id"]: s["state"] for s in again["steps"]}["welcome"], "done")
        self.assertTrue(self.get("/session")["setup_done"], "after Save and finish later the guide stays closed")
        self.assertEqual(self.about()[0]["you.name"], NAME)
        todo = [t["step"] for t in self.get("/home")["setup"]["todo"]]
        self.assertIn("about", todo)
        self.assertNotIn("welcome", todo)
        self.first_search.assert_not_called()

    def test_skipped_steps_stay_on_home(self) -> None:
        self.assertIsNone(self.get("/home")["setup"], "nothing is listed before the guide is opened")
        self.walk(skip=("email", "phone"))
        home = self.get("/home")["setup"]
        self.assertEqual([t["step"] for t in home["todo"]], ["email", "phone"])
        self.assertEqual((home["done"], home["total"]), (7, 9))
        self.assertEqual(home["todo"][1]["title"], "Phone alerts")
        self.assertTrue(self.get("/session")["setup_done"])
        self.first_search.assert_called_once()

        self.put("/setup/email", {"state": "done"})
        self.put("/setup/phone", {"state": "done"})
        self.assertIsNone(self.get("/home")["setup"])

    def test_home_marks_which_steps_left_are_optional(self) -> None:
        # Home said "The rest are optional." while needed steps were still left
        self.put("/setup/welcome", {"state": "current"})
        self.put("/setup/welcome", {"state": "done"})
        self.post("/setup/later")
        todo = {t["step"]: t["optional"] for t in self.get("/home")["setup"]["todo"]}
        self.assertFalse(todo["about"])
        self.assertTrue(todo["email"] and todo["phone"] and todo["updates"])

    def test_setup_says_when_an_old_helper_polled(self) -> None:
        # `found.old_helper` named a finder nobody provided, so it was always null
        self.assertIsNone(self.get("/setup")["found"]["old_helper"])
        with mock.patch.object(extension, "old_helper_seen", return_value=True):
            self.assertEqual(self.get("/setup")["found"]["old_helper"], {"protocol": 0})

    def test_a_step_that_is_needed_cannot_be_skipped(self) -> None:
        r = self.c.request("PUT", "/api/v2/setup/about", {"state": "skipped"})
        self.assertEqual(r.status, 409)
        self.assertIn("Save and finish later", r.json()["error"]["message"])

    def test_finishing_twice_starts_the_first_search_once(self) -> None:
        self.walk()
        self.put("/setup/done", {"state": "done"})
        self.first_search.assert_called_once()

    def test_running_a_step_again_never_wipes_other_changes(self) -> None:
        seen, _ = self.about()
        self.put("/setup/about/fields", {"values": {"you.city": "Lowmoor", "goal.min_pay": "22,000"}, "seen": seen})
        self.walk()
        # later, in Settings
        self.put("/settings/you.city", {"value": "Eastwick"})
        self.put("/settings/notify.phone", {"value": False})

        # the guide again, from Settings: the page loads, the person presses Continue on every step
        values, _ = self.about()
        self.assertEqual(values["you.city"], "Eastwick")
        self.assertEqual(self.put("/setup/about/fields", {"values": values, "seen": values})["saved"], {})
        self.walk()
        self.assertEqual(self.about()[0]["you.city"], "Eastwick")
        self.assertEqual(self.about()[0]["goal.min_pay"], 22000)
        self.assertFalse(self.get("/settings")["values"]["notify.phone"])

    def test_phone_alerts_keep_their_code_and_switch_on_once(self) -> None:
        first = self.post("/notify/phone/topic")
        self.assertTrue(first["new"])
        self.assertIn("<svg", first["qr_svg"])
        topic = secrets.get(phone.SECRET)
        self.assertNotIn(topic, str(first), "the code itself is never sent to the page as text")
        self.assertTrue(self.get("/settings")["values"]["notify.phone"])
        self.put("/settings/notify.phone", {"value": False})

        again = self.post("/notify/phone/topic")
        self.assertFalse(again["new"])
        self.assertEqual(again["qr_svg"], first["qr_svg"], "a phone already subscribed keeps working")
        self.assertEqual(secrets.get(phone.SECRET), topic)
        self.assertFalse(self.get("/settings")["values"]["notify.phone"])
        self.assertTrue(self.post("/notify/phone/topic", {"new": True})["new"])
        self.assertNotEqual(secrets.get(phone.SECRET), topic)

    def test_a_test_alert_goes_to_the_saved_code(self) -> None:
        sent = self.enterContext(mock.patch.object(phone, "URLOPEN", Sent()))
        self.assertFalse(self.post("/notify/phone/test")["ok"], "no code yet")
        self.post("/notify/phone/topic")
        reply = self.post("/notify/phone/test")
        self.assertEqual(reply, {"ok": True, "words": "Sent. Did your phone buzz?"})
        self.assertEqual(sent.requests[0].full_url, phone.NTFY + secrets.get(phone.SECRET))
        self.assertIn(b"test", sent.requests[0].data)

    def test_right_to_work_keeps_its_saved_value(self) -> None:
        self.put("/settings/you.right_to_work", {"value": "need_sponsorship"})
        values, fields = self.about()
        self.assertEqual(values["you.right_to_work"], "need_sponsorship")
        self.assertIn(["need_sponsorship", "I would need sponsorship"], fields["you.right_to_work"]["choices"])
        self.put("/setup/about/fields", {"values": values, "seen": values})
        self.walk()
        self.assertEqual(self.about()[0]["you.right_to_work"], "need_sponsorship")
        self.assertEqual(self.get("/settings")["values"]["you.right_to_work"], "need_sponsorship")

    def test_pay_is_read_leniently_and_says_what_hal_understood(self) -> None:
        seen, _ = self.about()
        saved = self.put("/setup/about/fields", {"values": {"goal.min_pay": "22,000"}, "seen": seen})["saved"]
        self.assertEqual(saved["goal.min_pay"], {"ok": True, "value": 22000,
                                                 "words": "Hal reads this as 22,000 pounds a year"})
        bad = self.put("/setup/about/fields", {"values": {"goal.min_pay": "lots"}, "seen": seen})["saved"]
        self.assertEqual(bad["goal.min_pay"], {"ok": False, "words": "Hal could not read that. Try 24000 or 24k."})
        self.assertEqual(self.about()[0]["goal.min_pay"], 22000)

    def test_about_you_refuses_fields_it_does_not_have(self) -> None:
        r = self.c.request("PUT", "/api/v2/setup/about/fields", {"values": {"ai.provider": "x"}, "seen": {}})
        self.assertEqual(r.status, 400)

    def test_the_email_step_tests_the_password_before_saving_it(self) -> None:
        FakeGmail.logins = []
        self.enterContext(mock.patch.object(email_test, "IMAP", FakeGmail))

        short = self.post("/email/test", {"address": ADDRESS, "password": "abcd efgh"})
        self.assertFalse(short["ok"])
        self.assertEqual(short["field"], "password")
        self.assertEqual(FakeGmail.logins, [], "a password of the wrong length is not tried")

        wrong = self.post("/email/test", {"address": ADDRESS, "password": "zzzz yyyy xxxx wwww"})
        self.assertFalse(wrong["ok"])
        self.assertIn("did not accept", wrong["words"])
        self.assertEqual(self.get("/settings")["secrets"]["email.app_password"], "not saved")
        self.assertEqual(self.get("/settings")["values"]["email.address"], "")

        right = self.post("/email/test", {"address": ADDRESS, "password": GOOD_PASSWORD})
        self.assertEqual(right, {"ok": True, "words": "Connected. Hal can read your Gmail (read only).", "field": None})
        self.assertEqual(self.get("/settings")["secrets"]["email.app_password"], "saved")
        self.assertEqual(secrets.get(email_test.SECRET), GOOD_PASSWORD.replace(" ", ""))
        self.assertEqual(self.get("/settings")["values"]["email.address"], ADDRESS)
        self.assertNotIn(GOOD_PASSWORD.replace(" ", ""), str(right))

    def test_gmail_out_of_reach_is_said_in_plain_words(self) -> None:
        class Offline(FakeGmail):
            def __init__(self, host: str, timeout: float) -> None:
                raise OSError("no route to host")

        self.enterContext(mock.patch.object(email_test, "IMAP", Offline))
        reply = self.post("/email/test", {"address": ADDRESS, "password": GOOD_PASSWORD})
        self.assertEqual(reply["words"], email_test.OFFLINE)
        self.assertEqual(self.get("/settings")["secrets"]["email.app_password"], "not saved")

    def test_the_record_folder_is_made_with_its_subfolders(self) -> None:
        made = self.post("/setup/record")
        folder = Path(record._live("VAULT"))
        for name in ("Certificates", "Education", "Jobs", "Volunteering", "References", "Inbox", "Private"):
            self.assertTrue((folder / name).is_dir(), name)
        self.assertTrue((folder / "Start Here.md").is_file())
        self.assertFalse(made["found"])

        (folder / "Certificates" / "First aid.txt").write_text("A made-up certificate.", encoding="utf-8")
        again = self.post("/setup/record")
        self.assertTrue(again["found"], "an existing record is found, not made again")
        self.assertEqual(again["files"], 1)
        self.assertTrue((folder / "Certificates" / "First aid.txt").is_file())


class NoCommandPrompt(unittest.TestCase):
    def js_words(self) -> list[tuple[str, int, str]]:
        out = []
        for path in sorted(APP.rglob("*.js")):
            rel = path.relative_to(APP).as_posix()
            if rel.startswith("lib/") or rel == REMOTE_PAGE:
                continue
            out += [(rel, t.line, t.text) for t in literals(path.read_text(encoding="utf-8"), rel) if not t.details]
        return out

    def py_words(self) -> list[tuple[str, int, str]]:
        out = []
        for path in sorted((ROOT / "jobhunt").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not (path.name.endswith(PAGE_WORD_MODULES) or rel.startswith("jobhunt/setup/")):
                continue
            if rel in DEVELOPER_ONLY:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docs = {id(n.body[0].value) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                    and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            out += [(rel, n.lineno, n.value) for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]
        return out

    def test_no_screen_or_message_tells_anyone_to_open_a_command_prompt(self) -> None:
        found = [f"{f}:{line}: {text.strip()[:80]}" for f, line, text in self.js_words() + self.py_words()
                 if COMMAND_WORDS.search(text)]
        self.assertEqual(found, [])

    def test_the_scan_would_catch_a_command_prompt(self) -> None:
        for words in ("Open a Command Prompt and type this", "Run: python -m jobhunt serve",
                      "Edit the config file in Notepad", "Open PowerShell"):
            self.assertRegex(words, COMMAND_WORDS)
        self.assertTrue(DETAILS)


if __name__ == "__main__":
    unittest.main()
