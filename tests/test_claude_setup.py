"""Claude Code's install, sign-in and status, the AI routes, the allowance in counts, the AI's health line and
its Home item, all with the made-up Claude command line in `tests/support/fake_claude/`.

No test runs the real installer, opens a window or reaches a real account: the installer is a made-up
program that prints the real one's kind of lines, and the sign-in window is the made-up `claude auth login`
run with no window.
"""
import ast
import contextlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import fake_claude
from support.api import HalServer

from jobhunt import ai, db
from jobhunt.ai import ai_actions, ai_home, budget, claude_setup
from jobhunt.ai import health as ai_health
from jobhunt.ai.base import ChatResult, LLMUnavailable
from jobhunt.ai.claude_cli import ClaudeCLIBackend
from jobhunt.core import clock, paths, platform, runner, settings
from jobhunt.core.settings import store

FAKE = fake_claude.FOLDER / "claude"
AI_FOLDER = Path(ai.__file__).resolve().parent
STEP_FILES = [AI_FOLDER.parent / "web" / "static" / "app" / "setup" / "steps" / "ai.js",
              AI_FOLDER.parent / "web" / "static" / "app" / "settings" / "ai.js"]
OUR_MODULES = ["claude_setup.py", "ai_actions.py", "routes.py", "health.py", "ai_home.py"]
BANNED = re.compile(r"command prompt|terminal|run:", re.I)
ADMIN = ("runas", "-verb", "winget", "start-process", "sudo", "elevat")
NOW = "2026-10-23T10:00:00Z"
WAIT_S = 20


def _without_real_claude(path: str) -> str:
    """PATH with every folder that holds a `claude` program left out, so the computer's own never counts."""
    keep = [p for p in path.split(os.pathsep)
            if p and not any((Path(p) / n).exists() for n in ("claude", "claude.exe", "claude.cmd"))]
    return os.pathsep.join(keep)


class FakeInstaller:
    """Stands in for `subprocess.Popen` of Anthropic's installer: records how it was started, then runs the
    made-up `claude install` and puts the made-up command line on PATH, as the real script would."""

    def __init__(self, tmp: Path, *, code: int = 0) -> None:
        self.tmp, self.code, self.calls = tmp, code, []

    def __call__(self, args, **kw):
        self.calls.append((list(args), dict(kw)))
        kw.pop("creationflags", None)       # only Windows accepts it; the call above keeps what Hal asked for
        if self.code == 0:
            os.environ.update({"PATH": f"{fake_claude.FOLDER}{os.pathsep}{os.environ['PATH']}",
                               "FAKE_CLAUDE_MODE": "ok", "FAKE_CLAUDE_STATE": str(self.tmp / "fake-claude-signed-in"),
                               "FAKE_CLAUDE_PYTHON": sys.executable})
            return subprocess.Popen([sys.executable, str(FAKE), "install"], **kw)
        return subprocess.Popen([sys.executable, "-c", "print('Download failed'); raise SystemExit(1)"], **kw)


class ConsoleStandIn:
    """Stands in for the platform's visible window: runs the command with no window and records it."""

    def __init__(self) -> None:
        self.opened: list[tuple[list[str], str]] = []
        self.procs: list[subprocess.Popen] = []

    def __call__(self, args: list[str], title: str) -> int:
        self.opened.append((list(args), title))
        run = [sys.executable, str(FAKE), *args[1:]] if sys.platform == "win32" else list(args)
        proc = subprocess.Popen(run, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.procs.append(proc)
        return proc.pid

    def close(self) -> None:
        for proc in self.procs:
            proc.wait(WAIT_S)


class InHome(unittest.TestCase):
    """A temporary Hal home and home folder, no Claude Code on PATH, and a fixed clock."""

    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-claude-setup-"))).resolve()
        stack.enter_context(mock.patch.dict(os.environ, {
            "HOME": str(self.tmp), "USERPROFILE": str(self.tmp), "PATH": _without_real_claude(os.environ["PATH"]),
            clock.ENV_FAKE_NOW: NOW}))
        for name in ("FAKE_CLAUDE_MODE", "FAKE_CLAUDE_STATE", "FAKE_CLAUDE_LOG", "FAKE_CLAUDE_PLAN"):
            os.environ.pop(name, None)
        stack.enter_context(paths.set_override(self.tmp / "hal"))
        paths.ensure()
        store.drop_cache()
        stack.callback(store.drop_cache)
        claude_setup.forget()
        stack.callback(claude_setup.forget)
        stack.callback(runner.idle, WAIT_S)
        self.stack = stack

    def fake(self, mode: str = "ok", *, signed_in: bool = True, **env) -> fake_claude.FakeClaude:
        claude_setup.forget()
        return self.stack.enter_context(fake_claude.on_path(self.tmp, mode=mode, signed_in=signed_in, **env))

    def console(self) -> ConsoleStandIn:
        stand_in = ConsoleStandIn()
        self.stack.callback(stand_in.close)
        self.stack.enter_context(mock.patch.object(claude_setup, "_open_console", stand_in))
        self.stack.enter_context(mock.patch.object(claude_setup, "POLL_S", 0.05))
        return stand_in


class Install(InHome):
    def test_claude_installs_with_no_console_and_no_admin_prompt(self):
        installer = FakeInstaller(self.tmp)
        added = []
        with mock.patch.object(claude_setup, "_popen", installer), \
                mock.patch.object(claude_setup, "_windows", lambda: True), \
                mock.patch.object(claude_setup, "add_to_user_path", lambda folder: added.append(folder) or True):
            self.assertIsNone(claude_setup.version())
            done = runner.run(ai_actions.INSTALL, params={"button": "Install"})
        self.assertEqual(done["state"], "done", done["error"])
        self.assertEqual(done["result"], {"ok": True, "version": "2.1.293"})
        (args, kw), = installer.calls
        self.assertEqual(args, ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-Command", "irm https://claude.ai/install.ps1 | iex"])
        self.assertEqual(kw["creationflags"], claude_setup.CREATE_NO_WINDOW)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertFalse([w for w in ADMIN if w in " ".join(args).lower()])
        self.assertEqual(added, [self.tmp / ".local" / "bin"])
        steps = [s["text"] for s in done["steps"]]
        self.assertEqual(steps[0], "Installing Claude (this usually takes about a minute)")
        self.assertIn("Installing Claude Code native build...", steps)
        self.assertIn("Added Claude to your programs list", steps)
        self.assertFalse([s for s in steps if "%" in s])
        # Windows 11 install test: the installer's own advice to edit Environment Variables reached the step list
        self.assertFalse([s for s in steps if "PATH" in s or "Environment Variables" in s or "Setup notes" in s], steps)

    def test_linux_installs_with_anthropics_script_for_this_person_only(self):
        installer = FakeInstaller(self.tmp)
        with mock.patch.object(claude_setup, "_popen", installer), \
                mock.patch.object(claude_setup, "_windows", lambda: False):
            self.assertEqual(claude_setup.install(lambda text: None), "2.1.293")
        (args, kw), = installer.calls
        self.assertEqual(args, ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"])
        self.assertEqual(kw["creationflags"], 0)

    def test_a_failed_install_says_so_and_offers_to_try_again(self):
        with mock.patch.object(claude_setup, "_popen", FakeInstaller(self.tmp, code=1)), \
                mock.patch.object(claude_setup, "_windows", lambda: False):
            done = runner.run(ai_actions.INSTALL, params={"button": "Install"})
        self.assertEqual(done["state"], "error")
        self.assertIn("Download failed", [s["text"] for s in done["steps"]])
        self.assertEqual(done["error"], claude_setup.INSTALL_FAILED)

    def test_tests_never_run_the_real_installer(self):
        with self.assertRaises(RuntimeError):
            claude_setup.install(lambda text: None)

    def test_the_user_path_gains_claudes_folder_once(self):
        folder = r"C:\Users\made-up\.local\bin"
        self.assertEqual(claude_setup.user_path_with(r"C:\Tools", folder), r"C:\Tools;" + folder)
        self.assertIsNone(claude_setup.user_path_with(r"C:\Tools;" + folder, folder))
        self.assertEqual(claude_setup.user_path_with("", folder), folder)

    def test_an_installed_claude_is_not_installed_again(self):
        self.fake()
        done = runner.run(ai_actions.INSTALL, params={"button": "Install"})
        self.assertEqual(done["result"], {"ok": True, "version": "2.1.293"})
        self.assertIn("Claude is already installed (version 2.1.293)", [s["text"] for s in done["steps"]])


class SignIn(InHome):
    def test_the_sign_in_step_ticks_when_claude_says_signed_in(self):
        self.fake(signed_in=False, FAKE_CLAUDE_LOGIN_S="0.3")
        console = self.console()
        with HalServer() as hal:
            c = hal.signed_in()
            before = c.get("/api/v2/ai/status?fresh=1").json()
            self.assertEqual((before["installed"], before["signed_in"], before["fix"]), (True, False, "claude_sign_in"))
            aid = c.post("/api/v2/ai/claude/sign-in").json()["action_id"]
            done = runner.wait(aid, WAIT_S)
            after = c.get("/api/v2/ai/status").json()
        self.assertEqual(done["state"], "done", done["error"])
        self.assertEqual(done["result"]["account"], "Signed in as robin.example@example.com (Claude Pro)")
        (args, title), = console.opened
        self.assertEqual((Path(args[0]).parent.name, args[1:], title),
                         ("fake_claude", ["auth", "login"], "Claude sign-in"))
        self.assertIn("Waiting for you to sign in", [s["text"] for s in done["steps"]])
        self.assertTrue(after["signed_in"])
        self.assertEqual(after["account"], "Signed in as robin.example@example.com (Claude Pro)")
        self.assertTrue(after["ready"])

    def test_signing_in_when_already_signed_in_opens_no_window(self):
        self.fake(FAKE_CLAUDE_PLAN="max")
        console = self.console()
        done = runner.run(ai_actions.SIGN_IN, params={"button": "Sign in"})
        self.assertEqual(done["result"]["account"], "Signed in as robin.example@example.com (Claude Max)")
        self.assertEqual(console.opened, [])

    def test_a_sign_in_nobody_finishes_stops_waiting_with_words(self):
        self.fake(signed_in=False, FAKE_CLAUDE_LOGIN_S="0")
        self.console()
        os.environ["FAKE_CLAUDE_STATE"] = str(self.tmp / "missing" / "state")   # the made-up login signs nobody in
        with self.assertRaises(RuntimeError) as e:
            claude_setup.sign_in(lambda text: None, wait_s=0.2)
        self.assertEqual(str(e.exception), claude_setup.NO_SIGN_IN)

    def test_sign_in_is_refused_in_a_remote_session_with_words(self):
        self.fake(signed_in=False)
        console = self.console()
        with HalServer() as hal:
            c = hal.signed_in(remote=True, remote_host="desktop")
            refused = c.post("/api/v2/ai/claude/sign-in")
            out = c.post("/api/v2/ai/claude/sign-out")
            st = c.get("/api/v2/ai/status").json()
        self.assertEqual(refused.status, 409)
        self.assertEqual(refused.json()["error"]["code"], "remote")
        self.assertEqual(refused.json()["error"]["message"], "Do this on desktop, the computer Hal runs on.")
        self.assertEqual(out.status, 409)
        self.assertEqual(console.opened, [])
        self.assertEqual(st["remote_host"], "desktop")
        step_js = STEP_FILES[0].read_text(encoding="utf-8")
        self.assertIn("Sign in on \" + host() + \", the computer Hal runs on.", step_js)

    def test_sign_out_asks_claude_code_to_log_out(self):
        fake = self.fake()
        self.assertEqual(claude_setup.account_words(claude_setup.auth_status(fresh=True)),
                         "Signed in as robin.example@example.com (Claude Pro)")
        claude_setup.sign_out()
        self.assertFalse(fake.state.exists(), "the fake's sign-in is gone")
        self.assertEqual(claude_setup.account_words(claude_setup.auth_status(fresh=True)), "Not signed in")


class Status(InHome):
    def test_auth_status_json_fills_the_account_words(self):
        acct = claude_setup.parse_status(0, '{"loggedIn": true, "email": "sam@example.org", "subscriptionType": "pro"}')
        self.assertEqual(claude_setup.account_words(acct), "Signed in as sam@example.org (Claude Pro)")
        no_plan = claude_setup.parse_status(0, '{"loggedIn": true, "email": "sam@example.org"}')
        self.assertEqual(claude_setup.account_words(no_plan), "Signed in")
        out = claude_setup.parse_status(1, '{"loggedIn": false}')
        self.assertEqual(claude_setup.account_words(out), "Not signed in")

    def test_an_older_claude_that_prints_text_is_read_by_its_words(self):
        self.assertTrue(claude_setup.parse_status(0, "Logged in as a made-up account").signed_in)
        self.assertFalse(claude_setup.parse_status(0, "Not logged in. Please run /login").signed_in)
        self.assertFalse(claude_setup.parse_status(1, "").signed_in)
        self.assertEqual(claude_setup.account_words(claude_setup.parse_status(0, "Logged in")), "Signed in")

    def test_no_claude_code_reads_as_not_installed(self):
        self.assertIsNone(claude_setup.executable())
        self.assertIsNone(claude_setup.auth_status())
        self.assertEqual(claude_setup.account_words(None), "Claude Code is not installed yet.")

    def test_the_status_is_kept_a_minute_unless_asked_afresh(self):
        fake = self.fake()
        self.assertTrue(claude_setup.auth_status().signed_in)
        fake.state.unlink()
        self.assertTrue(claude_setup.auth_status().signed_in)
        self.assertFalse(claude_setup.auth_status(fresh=True).signed_in)

    def test_the_model_choices_are_claudes_three(self):
        with HalServer() as hal:
            settings.set("ai.provider", "claude_cli")
            body = hal.signed_in().get("/api/v2/ai/models").json()
        self.assertEqual(body, {"writer": ["opus", "sonnet", "haiku"], "checker": ["opus", "sonnet", "haiku"],
                                "quick": ["sonnet", "haiku"]})


class Plans(InHome):
    def test_a_free_plan_is_explained_in_plain_words(self):
        fake = self.fake(FAKE_CLAUDE_PLAN="free")
        with HalServer() as hal, ai.use(ClaudeCLIBackend()):
            settings.set("ai.provider", "claude_cli")
            c = hal.signed_in()
            st = c.get("/api/v2/ai/status?fresh=1").json()
            done = runner.wait(c.post("/api/v2/ai/test").json()["action_id"], WAIT_S)
        self.assertEqual((st["plan_ok"], st["ready"], st["why"]), (False, False, claude_setup.FREE_PLAN))
        self.assertEqual(st["plan"], "Claude Free")
        self.assertEqual(done["result"]["words"],
                         "Your Claude plan cannot run Claude Code. It needs Claude Pro or Max.")
        self.assertEqual(fake.calls(), [], "the plan message comes before any call")

    def test_a_plan_claude_refuses_shows_the_same_words_after_the_call(self):
        fake = self.fake("free_plan")
        with ai.use(ClaudeCLIBackend()):
            done = runner.run(ai_actions.TEST, params={"button": "Test now"})
        self.assertEqual(done["result"]["words"], claude_setup.FREE_PLAN)
        self.assertEqual(len(fake.calls()), 1)

    def test_the_test_says_how_long_claude_took(self):
        fake = self.fake()
        with ai.use(ClaudeCLIBackend()):
            done = runner.run(ai_actions.TEST, params={"button": "Test now"})
        self.assertTrue(done["result"]["ok"])
        self.assertRegex(done["result"]["words"], r"^Claude answered in \d+\.\d seconds?$")
        call, = fake.calls()
        self.assertEqual(call["system"], "Reply with the single word ready.")
        self.assertEqual(call["argv"][call["argv"].index("--model") + 1], "haiku")

    def test_a_plan_hal_does_not_know_goes_on_to_the_test(self):
        self.fake(FAKE_CLAUDE_PLAN="claude_studio")
        with ai.use(ClaudeCLIBackend()):
            done = runner.run(ai_actions.TEST, params={"button": "Test now"})
        self.assertTrue(done["result"]["ok"])
        self.assertEqual(claude_setup.plan_words("claude_studio"), "Claude Studio")


class Allowance(InHome):
    def test_the_allowance_row_shows_counts_not_guesses(self):
        self.fake()
        with HalServer() as hal:
            for n in range(23):
                budget.record(ChatResult(text="ready", provider="claude_cli", model="haiku", ms=900),
                              priority=2 if n < 12 else 0, purpose="screen", chars_in=10, chars_out=5)
            budget.record(LLMUnavailable("Claude could not answer just now."), priority=2, purpose="screen",
                          chars_in=10, chars_out=0, provider="claude_cli")
            settings.set("ai.provider", "claude_cli")
            st = hal.signed_in().get("/api/v2/ai/status").json()
        self.assertEqual((st["used_today"], st["background_today"], st["cap"]), (23, 12, 30))
        self.assertEqual(st["allowance"],
                         "Hal used Claude 23 times today. 12 were background checks (your limit is 30).")
        self.assertNotIn("%", st["allowance"])
        js = STEP_FILES[1].read_text(encoding="utf-8")
        self.assertNotIn("%", js)
        self.assertNotIn("5-hour", js)


class Recommend(InHome):
    def test_hal_suggests_claude_on_a_laptop_without_a_graphics_card(self):
        out = claude_setup.recommend({"memory_gb": 8, "gpu": None, "laptop": True})
        self.assertEqual(out["pick"], "claude_cli")
        self.assertEqual(out["why"], "This laptop has 8 GB of memory and no suitable graphics card.")
        machine = {"memory_gb": 8, "gpu": None, "cpus": 4}
        with HalServer() as hal, \
                mock.patch.object(platform.current(), "machine", lambda: machine), \
                mock.patch.object(claude_setup, "has_battery", lambda: True):
            body = hal.signed_in().get("/api/v2/ai/recommend").json()
        self.assertEqual(body["pick"], "claude_cli")
        self.assertEqual(body["machine"], {"memory_gb": 8, "gpu": None, "laptop": True})

    def test_a_desktop_with_a_strong_graphics_card_is_offered_this_computer(self):
        out = claude_setup.recommend({"memory_gb": 64, "gpu": "Made-up Graphics 9000", "laptop": False})
        self.assertEqual(out["pick"], "local")
        self.assertIn("This computer has 64 GB of memory and a strong graphics card", out["why"])
        small = claude_setup.recommend({"memory_gb": 8, "gpu": "Made-up Graphics 100", "laptop": True})
        self.assertEqual(small["pick"], "claude_cli")


class Expiry(InHome):
    def test_sign_in_again_appears_when_the_sign_in_expires(self):
        fake = self.fake()
        with HalServer() as hal, ai.use(ClaudeCLIBackend()):
            settings.set("ai.provider", "claude_cli")
            c = hal.signed_in()
            self.assertEqual(ai.chat("Reply with the single word ready.", "Ready?", role="quick", purpose="screen",
                                     priority=0), "ready")
            self.assertEqual(ai_health.ai_check().level, "ok")
            self.assertNotIn("ai.claude_sign_in", [n["id"] for n in c.get("/api/v2/home").json()["needs"]])

            fake.state.unlink()                                   # Claude's sign-in runs out
            os.environ["FAKE_CLAUDE_MODE"] = "signed_out"
            with self.assertRaises(LLMUnavailable) as e:
                ai.chat("Reply with the single word ready.", "Ready?", role="quick", purpose="screen", priority=0)
            self.assertEqual(e.exception.kind, "signed_out")
            needs = {n["id"]: n for n in c.get("/api/v2/home").json()["needs"]}
            claude_setup.forget()
            check = next(r for r in c.get("/api/v2/health").json()["checks"] if r["id"] == "ai")
            st = c.get("/api/v2/ai/status?fresh=1").json()

            fake.state.touch()                                    # signed in again, and an answer works
            os.environ["FAKE_CLAUDE_MODE"] = "ok"
            ai.chat("Reply with the single word ready.", "Ready?", role="quick", purpose="screen", priority=0)
            later = [n["id"] for n in c.get("/api/v2/home").json()["needs"]]
        self.assertEqual(needs["ai.claude_sign_in"]["title"], "Sign in to Claude again")
        self.assertEqual(needs["ai.claude_sign_in"]["act"], {"label": "Sign in again", "route": "setup-ai"})
        self.assertEqual(check["name"], "AI (Claude subscription)")
        self.assertEqual((check["level"], check["text"]), ("bad", claude_setup.SIGNED_OUT))
        self.assertEqual(check["fix"], {"label": "Sign in to Claude again", "route": "setup-ai"})
        self.assertEqual((st["signed_in"], st["fix"]), (False, "claude_sign_in"))
        self.assertNotIn("ai.claude_sign_in", later)

    def test_the_health_line_says_how_long_the_last_answer_took(self):
        self.fake()
        budget.record(ChatResult(text="ready", provider="claude_cli", model="haiku", ms=2100), priority=0,
                      purpose="ai_test", chars_in=10, chars_out=5)
        self.assertEqual(ai_health.ai_check().text, "Signed in. Last answer took 2.1 seconds.")
        with contextlib.closing(db.connect()) as con:
            self.assertIsNone(ai_home.signed_out_since(con))


class Words(unittest.TestCase):
    def test_no_words_on_screen_send_anyone_to_a_command_prompt(self):
        found = []
        for name in OUR_MODULES:
            tree = ast.parse((AI_FOLDER / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                named = isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper()
                                                             for t in node.targets)
                if named and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    if BANNED.search(node.value.value):
                        found.append(f"{name}: {node.value.value}")
        for js in STEP_FILES:
            for line in js.read_text(encoding="utf-8").splitlines():
                if BANNED.search(line) and not line.lstrip().startswith(("/*", "*", "//")):
                    found.append(f"{js.name}: {line.strip()}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
