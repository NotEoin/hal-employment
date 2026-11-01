"""Hal's local model: the gateway backend, its token file, detection and the never-silently-Claude rule."""
import http.server
import json
import threading
import unittest
from typing import ClassVar
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_ai_backends import InHome

from jobhunt import ai
from jobhunt.ai import provider
from jobhunt.ai.base import LLMUnavailable
from jobhunt.ai.hal_gateway import GatewayBackend, gateway_answers, read_token
from jobhunt.core import settings
from jobhunt.core.settings import store
from jobhunt.core.settings.catalog import ai as ai_catalog

TOKEN = "made-up-token"


class _Gateway(http.server.BaseHTTPRequestHandler):
    seen: ClassVar[list] = []
    remote_reply: ClassVar[bool] = False

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        if type(self).remote_reply:
            self.send_header("X-Hal-Remote", "cloud.example.invalid")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, {"error": "bad token"})
            return False
        return True

    def do_GET(self):
        if self._allowed():
            self._send(200, {"data": [{"id": "made-up-online", "owned_by": "remote:cloud.example.invalid"},
                                      {"id": "made-up-model", "owned_by": "ollama"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self._allowed():
            self._send(200, {"choices": [{"message": {"content": "made-up answer"}}]})


class Gateway(InHome):
    def setUp(self):
        super().setUp()
        _Gateway.seen = []
        _Gateway.remote_reply = False
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.token = self.tmp / "gateway-token"
        self.token.write_text(TOKEN + "\n", encoding="utf-8")

    def test_the_token_is_the_first_line_of_the_file(self):
        self.token.write_text(f"  {TOKEN}  \nignored\n", encoding="utf-8")
        self.assertEqual(read_token(self.token), TOKEN)
        self.assertIsNone(read_token(self.tmp / "missing"))

    def test_a_chat_carries_the_bearer_token_and_asks_the_gateway(self):
        be = GatewayBackend(self.url, self.token)
        out = be.chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertEqual(out.text, "made-up answer")
        self.assertEqual(_Gateway.seen[-1], ("/v1/chat/completions", f"Bearer {TOKEN}"))

    def test_online_models_behind_the_gateway_are_never_offered(self):
        be = GatewayBackend(self.url, self.token)
        self.assertEqual(be.models(), ["made-up-model"])
        self.assertEqual(be.default_model("writer"), "made-up-model")

    def test_an_online_model_is_refused_before_the_prompt_leaves(self):
        # Hal mode sent the prompt to whatever model was named, including an online one the gateway fronts
        be = GatewayBackend(self.url, self.token)
        with self.assertRaises(LLMUnavailable) as caught:
            be.chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-online")
        self.assertEqual(caught.exception.kind, "local_only")
        self.assertNotIn("/v1/chat/completions", [path for path, _ in _Gateway.seen])

    def test_a_reply_from_an_online_backend_is_refused(self):
        _Gateway.remote_reply = True
        with self.assertRaises(LLMUnavailable) as caught:
            GatewayBackend(self.url, self.token).chat(ai.ChatRequest(system="s", user="u", timeout=5),
                                                      "made-up-model")
        self.assertIn("online model", str(caught.exception))

    def test_a_rotated_token_is_used_without_a_restart(self):
        be = GatewayBackend(self.url, self.token)
        self.assertTrue(be.status().ready)
        self.token.write_text("rotated\n", encoding="utf-8")
        self.assertIn("did not accept the token", be.status().why)

    def test_a_missing_token_file_says_where_it_looked(self):
        why = GatewayBackend(self.url, self.tmp / "none").status().why
        self.assertIn("none", why)

    def test_a_stopped_gateway_is_reported_in_words(self):
        self.httpd.shutdown()
        self.assertIn("does not answer", GatewayBackend(self.url, self.token).status().why)

    def test_the_gateway_owns_the_card_so_the_gpu_gate_does_not_apply(self):
        self.assertFalse(GatewayBackend(self.url, self.token).local_gpu)

    def test_detection_needs_the_gateway_to_accept_the_token(self):
        self.assertTrue(gateway_answers(self.url, self.token))
        self.token.write_text("wrong\n", encoding="utf-8")
        self.assertFalse(gateway_answers(self.url, self.token))
        self.assertFalse(gateway_answers(self.url, self.tmp / "none"))

    def test_hal_is_suggested_when_the_gateway_answers(self):
        with mock.patch.object(ai_catalog, "hal_answers", return_value=True):
            self.assertEqual(provider.suggestion(), "hal")

    def test_a_test_run_never_probes_a_live_hal(self):
        with mock.patch.object(ai_catalog, "gateway_answers", create=True) as probe:
            self.assertFalse(ai_catalog.hal_answers(settings.get))
        probe.assert_not_called()

    def test_a_stopped_gateway_never_sends_the_work_to_claude(self):
        settings.set("ai.provider", "hal", by="setup")
        settings.set("ai.hal_url", self.url, by="setup")
        settings.set("ai.hal_token_file", str(self.token), by="setup")
        self.httpd.shutdown()
        store.drop_cache()
        with mock.patch.dict("os.environ", {"HAL_TEST_REAL_AI": "1"}):
            self.assertEqual(ai.backend().name, "hal")
            with self.assertRaises(LLMUnavailable):
                ai.chat("s", "u", timeout=2)
            self.assertEqual(provider.current(), "hal")


if __name__ == "__main__":
    unittest.main()
