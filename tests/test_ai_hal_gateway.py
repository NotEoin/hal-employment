"""Hal's local model: the gateway backend, its token file, detection and the never-silently-Claude rule."""
import http.server
import json
import os
import shutil
import socketserver
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from typing import ClassVar
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from test_ai_backends import InHome

from jobhunt import ai
from jobhunt.ai import provider
from jobhunt.ai.base import LLMUnavailable
from jobhunt.ai.hal_gateway import (
    DEFAULT_URL,
    GatewayBackend,
    as_batch,
    default_socket,
    gateway_answers,
    loose_mode_warning,
    read_token,
)
from jobhunt.core import settings
from jobhunt.core.settings import store
from jobhunt.core.settings.catalog import ai as ai_catalog

TOKEN = "made-up-token"
BATCH_TOKEN = "made-up-batch-token"


class _Gateway(http.server.BaseHTTPRequestHandler):
    seen: ClassVar[list] = []
    remote_reply: ClassVar[bool] = False
    redirect: ClassVar[bool] = False
    aliases: ClassVar[list] = []
    refuse: ClassVar[tuple | None] = None    # (status, error code, message) for every chat
    slots: ClassVar[int] = 0                 # the `meta.slots` listed for made-up-model; 0 lists none


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
        if self.headers.get("Authorization") not in (f"Bearer {TOKEN}", f"Bearer {BATCH_TOKEN}"):
            self._send(401, {"error": "bad token"})
            return False
        return True

    def do_GET(self):
        if not self._allowed():
            return
        if type(self).redirect:
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        listed = [{"id": "made-up-online", "owned_by": "remote:cloud.example.invalid"},
                  {"id": "made-up-model", "owned_by": "ollama"}]
        if type(self).slots:
            listed[1]["meta"] = {"slots": type(self).slots}
        self._send(200, {"data": listed + [{"id": a, "owned_by": "ollama"} for a in type(self).aliases]})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if not self._allowed():
            return
        if type(self).refuse:
            code, kind, message = type(self).refuse
            self._send(code, {"error": {"message": message, "type": kind, "code": kind}})
            return
        self._send(200, {"choices": [{"message": {"content": "made-up answer"}}]})


class Gateway(InHome):
    def setUp(self):
        super().setUp()
        _Gateway.seen = []
        _Gateway.remote_reply = False
        _Gateway.redirect = False
        _Gateway.aliases = []
        _Gateway.refuse = None
        _Gateway.slots = 0
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

    @unittest.skipIf(os.name == "nt", "file modes are POSIX only")
    def test_a_token_file_open_to_group_or_others_is_warned_about(self):
        be = GatewayBackend(self.url, self.token)
        for mode, warned in ((0o600, False), (0o400, False), (0o640, True), (0o604, True)):
            with self.subTest(mode=oct(mode)):
                self.token.chmod(mode)
                self.assertEqual(loose_mode_warning(self.token) is not None, warned)
                status = be.status()
                self.assertTrue(status.ready, "a loose mode only warns")
                self.assertEqual("warning" in status.detail, warned)
        self.assertIsNone(loose_mode_warning(self.tmp / "missing"))

    def test_windows_never_warns_about_a_token_files_mode(self):
        """Regression: Windows reports every file as 0666, so every Windows user got the warning."""
        self.token.chmod(0o644)
        with mock.patch("jobhunt.ai.hal_gateway.POSIX_MODES", False):
            self.assertIsNone(loose_mode_warning(self.token))

    def test_a_chat_carries_the_bearer_token_and_asks_the_gateway(self):
        be = GatewayBackend(self.url, self.token)
        out = be.chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertEqual(out.text, "made-up answer")
        self.assertEqual(_Gateway.seen[-1], ("/v1/chat/completions", f"Bearer {TOKEN}"))

    def test_a_read_of_the_record_goes_out_with_the_batch_token(self):
        batch = self.tmp / "gateway-batch-token"
        batch.write_text(BATCH_TOKEN + "\n", encoding="utf-8")
        be = GatewayBackend(self.url, self.token, batch)
        with as_batch():
            be.chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertEqual(_Gateway.seen[-1][1], f"Bearer {BATCH_TOKEN}")
        be.chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertEqual(_Gateway.seen[-1][1], f"Bearer {TOKEN}", "what the person asks for keeps the owner token")

    def test_without_a_batch_token_a_read_still_goes_out_with_the_owner_token(self):
        be = GatewayBackend(self.url, self.token, self.tmp / "no-batch-token")
        with as_batch(), self.assertLogs("jobhunt.ai.hal_gateway", "WARNING"):
            be.chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertEqual(_Gateway.seen[-1][1], f"Bearer {TOKEN}")

    def test_a_gateway_giving_the_card_to_someone_else_makes_the_read_wait(self):
        """Regression: a batch request refused with 503 `gpu_busy` was not told apart from a broken gateway."""
        _Gateway.refuse = (503, "gpu_busy", "Hal's models are in use; batch work waits for them")
        with self.assertRaises(LLMUnavailable) as caught:
            GatewayBackend(self.url, self.token).chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertEqual(caught.exception.kind, "gpu_busy")

    def test_another_refusal_still_says_the_gateways_reason(self):
        _Gateway.refuse = (503, "hal_off", "Hal is off; run `hal on`")
        with self.assertRaises(LLMUnavailable) as caught:
            GatewayBackend(self.url, self.token).chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
        self.assertIn("Hal is off", str(caught.exception))
        self.assertNotEqual(caught.exception.kind, "gpu_busy")

    def test_each_job_asks_for_the_gateway_alias_that_fits_it(self):
        """Regression: every job got the gateway's first model, so prose went to hal-chat, not hal-writer."""
        _Gateway.aliases = ["hal-chat", "hal-writer"]
        be = GatewayBackend(self.url, self.token)
        self.assertEqual([be.default_model(r) for r in ("writer", "checker", "quick")],
                         ["hal-writer", "hal-writer", "hal-writer"],
                         "checking on hal-chat beside the writer ran on the CPU and timed out (2026-11-12)")
        _Gateway.aliases = ["hal-chat"]
        self.assertEqual([be.default_model(r) for r in ("writer", "checker")], ["made-up-model", "hal-chat"],
                         "with no writer, checking still finds the chat model")

    def test_the_gateway_says_how_many_requests_a_model_serves_at_once(self):
        be = GatewayBackend(self.url, self.token)
        self.assertEqual(be.slots("made-up-model"), 0, "a gateway that does not say gives no slots")
        _Gateway.slots = 2
        self.assertEqual((be.slots("made-up-model"), be.slots("made-up-other")), (2, 0))

    def test_hal_keeps_as_many_calls_with_the_gateway_as_its_model_serves(self):
        """Hal read the record one file at a time while Hal's writer served two at once."""
        settings.set("ai.parallel", 1, by="you")
        _Gateway.slots = 2
        with ai.use(GatewayBackend(self.url, self.token)):
            self.assertEqual(ai.at_once("checker"), 2)
        settings.set("ai.parallel", 4, by="you")
        with ai.use(GatewayBackend(self.url, self.token)):
            self.assertEqual(ai.at_once("checker"), 2, "four calls on two slots timed out queueing (2026-11-12)")
        _Gateway.slots = 0
        with ai.use(GatewayBackend(self.url, self.token)):
            self.assertEqual(ai.at_once("checker"), 4, "a gateway that does not say leaves it to the setting")

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

    def test_the_token_is_never_sent_off_this_computer(self):
        # any address in `ai.hal_url` got the owner token, so a wrong or planted address could collect it
        be = GatewayBackend("http://192.0.2.10:8741/v1", self.token)
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("sent")):
            self.assertIn("on this computer", be.status().why)
            with self.assertRaises(LLMUnavailable):
                be.chat(ai.ChatRequest(system="s", user="u", timeout=5), "made-up-model")
            self.assertFalse(gateway_answers("http://192.0.2.10:8741/v1", self.token))

    def test_a_redirect_is_not_followed_with_the_token(self):
        _Gateway.redirect = True
        self.assertIn("302", GatewayBackend(self.url, self.token).status().why)
        self.assertNotIn("/elsewhere", [path for path, _ in _Gateway.seen])

    def test_a_proxy_from_the_environment_never_sees_the_token(self):
        dead_proxy = {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "no_proxy": "",
                      "NO_PROXY": ""}
        # urllib's shared opener reads the proxy variables once, so a fresh one is made under them
        with mock.patch.dict("os.environ", dead_proxy), mock.patch.object(urllib.request, "_opener", None):
            self.assertTrue(GatewayBackend(self.url, self.token).status().ready)

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


@unittest.skipIf(os.name == "nt", "Unix sockets are POSIX only")
class GatewaySocket(InHome):
    """The gateway's always-on Unix listener, used when the TCP one is off (Hal mode 20 s calls, 2026-11-05)."""

    def setUp(self):
        super().setUp()
        _Gateway.seen = []
        _Gateway.remote_reply = False
        _Gateway.redirect = False
        # a short directory: a socket path over about 100 bytes is refused
        self.run = Path(tempfile.mkdtemp(prefix="hal-run-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.run, True)
        self.server = socketserver.ThreadingUnixStreamServer(str(self.run / "gateway.sock"), _Gateway)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = self.tmp / "gateway-token"
        self.token.write_text(TOKEN + "\n", encoding="utf-8")
        env = mock.patch.dict("os.environ", {"HAL_RUNTIME_DIR": str(self.run)})
        env.start()
        self.addCleanup(env.stop)

    def test_the_default_address_reaches_the_gateway_through_its_socket(self):
        be = GatewayBackend(DEFAULT_URL, self.token)
        self.assertEqual(be.socket, self.run / "gateway.sock")
        self.assertTrue(be.status().ready)
        self.assertEqual(be.models(), ["made-up-model"])
        self.assertEqual(_Gateway.seen[-1], ("/v1/models", f"Bearer {TOKEN}"))

    def test_an_address_set_by_hand_never_uses_the_socket(self):
        self.assertIsNone(GatewayBackend("http://127.0.0.1:9/v1", self.token).socket)

    def test_a_test_run_never_looks_in_the_real_runtime_directory(self):
        with mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": str(self.run.parent)}):
            os.environ.pop("HAL_RUNTIME_DIR")
            (self.run.parent / "hal").mkdir(exist_ok=True)
            self.assertIsNone(default_socket())


if __name__ == "__main__":
    unittest.main()
