"""The AI layer and its backends: old roles, JSON replies, the provider, the GPU gate, Ollama and local servers."""
import contextlib
import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from support.fake_ai import FakeBackend

from jobhunt import ai, llm
from jobhunt.ai import gpu, provider
from jobhunt.ai.base import LLMUnavailable
from jobhunt.ai.ollama import OllamaBackend
from jobhunt.ai.openai_compat import OpenAICompatBackend, classify
from jobhunt.core import paths, settings
from jobhunt.core.settings import store
from jobhunt.core.settings.catalog import ai as ai_catalog


class InHome(unittest.TestCase):
    """Each test in its own temporary home, so settings and usage rows start empty."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="hal-ai-")))
        stack.enter_context(paths.set_override(self.tmp))
        store.drop_cache()
        stack.callback(store.drop_cache)


class Roles(InHome):
    def test_old_llm_roles_still_work(self):
        with ai.use(FakeBackend(default="fine")) as fake:
            self.assertEqual(llm.chat("You sort email.", "A made-up email.", role="main"), "fine")
            llm.chat("You write.", "A made-up letter.", role="writer")
            llm.chat("You sort.", "A made-up email.", role="fast")
        self.assertEqual([r.role for r in fake.requests()], ["checker", "writer", "quick"])

    def test_chat_json_survives_code_fences(self):
        replies = {"fenced": '```json\n{"fit": true}\n```', "chatty": 'Here you go: {"fit": false} Hope it helps.',
                   "broken": "not JSON at all", "list": "[1, 2]"}
        with ai.use(FakeBackend(replies)):
            self.assertEqual(ai.chat_json("s", "u", purpose="fenced"), {"fit": True})
            self.assertEqual(llm.chat_json("s", "u", purpose="chatty"), {"fit": False})
            self.assertEqual(ai.chat_json("s", "u", purpose="broken"), {})
            self.assertEqual(ai.chat_json("s", "u", purpose="list"), {})

    def test_a_test_run_never_reaches_a_real_model(self):
        with self.assertRaises(LLMUnavailable):
            ai.chat("s", "u")
        self.assertFalse(llm.available())
        self.assertEqual(ai.backend().name, "fake")

    def test_every_call_leaves_a_usage_row_with_sizes_only(self):
        with ai.use(FakeBackend(default="A made-up reply.")):
            ai.chat("System words.", "Secret-free user words.", purpose="letter", priority=1, job_id="job-7")
        from jobhunt import db

        con = db.connect()
        self.addCleanup(con.close)
        row = dict(con.execute("SELECT * FROM ai_usage").fetchone())
        self.assertEqual((row["provider"], row["purpose"], row["priority"], row["ok"], row["job_id"]),
                         ("fake", "letter", 1, 1, "job-7"))
        self.assertEqual((row["chars_in"], row["chars_out"]), (len("System words.") + len("Secret-free user words."),
                                                               len("A made-up reply.")))
        self.assertNotIn("Secret-free", json.dumps(row))

    def test_the_action_priority_is_the_default(self):
        with ai.use(FakeBackend()) as fake, ai.at_priority(0):
            ai.chat("s", "u")
        ai_default = ai.current_priority()
        self.assertEqual((fake.log[0].priority, ai_default), (0, 2))


class Provider(InHome):
    def test_the_ai_provider_never_changes_because_ollama_stopped(self):
        with mock.patch.object(ai_catalog, "ollama_answers", return_value=True), \
                mock.patch.object(ai_catalog.sys, "platform", "linux"):
            self.assertEqual(provider.current(), "local")
        with mock.patch.object(ai_catalog, "ollama_answers", return_value=False), \
                mock.patch.object(ai_catalog.sys, "platform", "linux"):
            store.drop_cache()
            self.assertEqual(provider.current(), "local")
            self.assertEqual(settings.get("ai.provider"), "local")
            settings.set("ai.provider", "claude_cli")       # equals today's default, and still stays
        with mock.patch.object(ai_catalog, "ollama_answers", return_value=True), \
                mock.patch.object(ai_catalog.sys, "platform", "linux"):
            store.drop_cache()
            self.assertEqual(provider.current(), "claude_cli")
        from jobhunt import db

        con = db.connect()
        self.addCleanup(con.close)
        chosen = [r[0] for r in con.execute("SELECT text FROM activity WHERE kind = 'ai.provider_chosen'")]
        self.assertEqual(chosen, ["Hal will use This computer for its AI. Change it in Settings, Hal's brain."])

    def test_the_first_server_start_stores_the_suggestion(self):
        from jobhunt.core import plugins

        self.assertIn("jobhunt.ai.choose_ai_once", [name for name, _ in plugins._start])
        with mock.patch.object(provider, "suggestion", return_value="claude_cli"):
            ai.choose_ai_once()
        self.assertEqual(store.stored("ai.provider"), "claude_cli")

    def test_the_backend_follows_the_stored_choice(self):
        settings.set("ai.provider", "openai_compat", by="setup")
        self.assertEqual(ai._build(provider.current()).name, "openai_compat")
        self.assertEqual(ai._build("claude_cli").name, "claude_cli")
        with self.assertRaises(LLMUnavailable):
            ai._build("crystal_ball")


class GpuGate(InHome):
    def local(self) -> FakeBackend:
        fake = FakeBackend(default="ok")
        fake.local_gpu = True
        return fake

    def test_the_gpu_gate_applies_only_to_local_models(self):
        busy = mock.patch.object(gpu, "holders", return_value=[("made-up-trainer", 9000)])
        with busy, mock.patch.object(gpu, "FOREGROUND_WAIT_S", 0), mock.patch.object(gpu, "POLL_S", 0):
            with ai.use(self.local()) as local, self.assertRaises(LLMUnavailable) as caught:
                ai.chat("s", "u", priority=0)
            self.assertEqual(caught.exception.kind, "gpu_busy")
            self.assertEqual(str(caught.exception),
                             "The graphics card is busy (made-up-trainer). Try again when it finishes.")
            self.assertEqual(local.log, [])
            remote = FakeBackend(default="ok")
            remote.name, remote.online = "claude_cli", True
            with ai.use(remote):
                self.assertEqual(ai.chat("s", "u", priority=0), "ok")
            settings.set("ai.gpu_gate", False)
            with ai.use(self.local()):
                self.assertEqual(ai.chat("s", "u", priority=0), "ok")

    def test_background_work_waits_for_the_card_to_be_free(self):
        answers = iter([[("made-up-trainer", 9000)], []])
        with mock.patch.object(gpu, "holders", side_effect=lambda: next(answers)), mock.patch.object(gpu, "POLL_S", 0):
            with ai.use(self.local()):
                self.assertEqual(ai.chat("s", "u", priority=2), "ok")

    def test_the_old_busy_check_names_the_program(self):
        with mock.patch.object(gpu, "holders", return_value=[("made-up-trainer", 9000)]), ai.use(self.local()):
            from jobhunt import gpu as old_gpu

            self.assertEqual(old_gpu.busy(), "waiting for the GPU: made-up-trainer (9000 MiB)")


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: ClassVar[list] = []
    refuse_format = False
    off = False

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": "small:1b", "size": 1}, {"name": "made-up-embed", "size": 5},
                                        {"name": "big:30b", "size": 30}]})
        else:
            self._send(200, {"data": [{"id": "made-up-model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, body))
        if self.path == "/api/chat":
            self._send(200, {"message": {"content": '{"ok": true}'}})
        elif self.path == "/api/embed":
            self._send(200, {"embeddings": [[0.1, 0.2] for _ in body["input"]]})
        elif type(self).off:
            self._send(503, {"error": {"message": "Hal is off; run `hal on`", "type": "unavailable"}})
        elif "response_format" in body and type(self).refuse_format:
            self._send(400, {"error": "response_format is not supported"})
        else:
            self._send(200, {"choices": [{"message": {"content": "made-up answer"}}]})


class Servers(unittest.TestCase):
    def setUp(self):
        _Handler.seen = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def test_ollama_asks_for_json_at_low_temperature_with_thinking_off(self):
        be = OllamaBackend(self.url)
        out = be.chat(ai.ChatRequest(system="s", user="u", json_out=True, ctx=8192, timeout=5), "big:30b")
        self.assertEqual(out.text, '{"ok": true}')
        body = _Handler.seen[0][1]
        self.assertEqual((body["think"], body["format"], body["options"]), (False, "json", {"temperature": 0.1,
                                                                                          "num_ctx": 8192}))
        self.assertEqual((be.default_model("writer"), be.default_model("quick")), ("big:30b", "small:1b"))
        self.assertTrue(be.status().ready)
        self.assertEqual(be.embed(["a", "b"], "made-up-embed"), [[0.1, 0.2], [0.1, 0.2]])

    def test_a_request_that_names_a_temperature_is_sent_at_it(self):
        OllamaBackend(self.url).chat(ai.ChatRequest(system="s", user="u", timeout=5, temperature=0.4), "big:30b")
        OpenAICompatBackend(self.url).chat(ai.ChatRequest(system="s", user="u", timeout=5, temperature=0.4), "m")
        OpenAICompatBackend(self.url).chat(ai.ChatRequest(system="s", user="u", timeout=5), "m")
        bodies = [b for _, b in _Handler.seen]
        self.assertEqual(bodies[0]["options"], {"temperature": 0.4})
        self.assertEqual([b["temperature"] for b in bodies[1:]], [0.4, 0.7], "other prose keeps 0.7")

    def test_a_stopped_ollama_is_reported_in_words(self):
        status = OllamaBackend("http://127.0.0.1:9").status()
        self.assertFalse(status.ready)
        self.assertEqual(status.fix, "start_ollama")

    def test_a_local_server_that_refuses_json_mode_is_asked_again_without_it(self):
        _Handler.refuse_format = True
        self.addCleanup(setattr, _Handler, "refuse_format", False)
        be = OpenAICompatBackend(self.url)
        out = be.chat(ai.ChatRequest(system="s", user="u", json_out=True, timeout=5), "made-up-model")
        self.assertEqual(out.text, "made-up answer")
        self.assertEqual(["response_format" in b for _, b in _Handler.seen], [True, False])
        self.assertEqual(be.models(), ["made-up-model"])

    def test_a_refusal_carries_the_servers_own_reason(self):
        # Hal's gateway says "Hal is off" after a restart, and Hal Employment showed only "(503)" (2026-11-06)
        _Handler.off = True
        self.addCleanup(setattr, _Handler, "off", False)
        with self.assertRaises(ai.LLMUnavailable) as caught:
            OpenAICompatBackend(self.url).chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertEqual(str(caught.exception), "The local server refused the request (503): Hal is off; run `hal on`")

    def test_where_a_local_server_runs_decides_whether_it_is_online(self):
        self.assertEqual(classify("http://127.0.0.1:8000/v1"), (False, True))
        self.assertEqual(classify("http://localhost:8000/v1"), (False, True))
        self.assertEqual(classify("http://192.168.1.20:8000/v1"), (False, False))
        self.assertEqual(classify("https://models.example.com/v1"), (True, False))


class Counted(unittest.TestCase):
    """A pack's model calls are counted so the cut from about 55 can be measured (2026-11-08)."""

    def test_calls_inside_a_block_are_counted_and_an_outer_block_counts_the_inner(self):
        with mock.patch.object(ai, "chat", lambda *a, **k: "{}"):
            llm.chat("s", "u")
            with llm.counted() as outer:
                llm.chat("s", "u")
                with llm.counted() as inner:
                    llm.chat_json("s", "u")
            llm.chat("s", "u")
        self.assertEqual((outer.n, inner.n), (2, 1))


if __name__ == "__main__":
    unittest.main()
