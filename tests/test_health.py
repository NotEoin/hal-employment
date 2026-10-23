"""Is Hal working?: every area's checks with their Fix buttons, and the browsers Hal found."""
import os
import time
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401 - sets the test home before jobhunt is imported
from support.api import HalServer

from jobhunt.core import health_routes, runner


@dataclass(frozen=True)
class Fix:
    label: str
    action: str | None = None
    route: str | None = None


def result(ok, level, text, fix=None):
    return SimpleNamespace(ok=ok, level=level, text=text, fix=fix)


def check(cid, name, fn):
    return SimpleNamespace(id=cid, name=name, run=fn)


def slow():
    time.sleep(1)
    return result(True, "ok", "Too late.")


def broken():
    raise OSError("a made-up failure")


CHECKS = [
    check("test.sites", "Job sites", lambda: result(False, "warn", "A made-up site has no key.",
                                                    Fix("Add a free key", route="settings-sites"))),
    check("test.ai", "AI", lambda: result(True, "ok", "Signed in.", Fix("Test now", action="test.health_fix"))),
    check("test.paused", "Schedule", lambda: result(False, "warn", "Hal is paused.",
                                                    Fix("Carry on", action="/status/pause"))),
    check("test.slow", "Slow", slow),
    check("test.broken", "Broken", broken),
]


class Health(unittest.TestCase):
    def setUp(self):
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.client = self.hal.signed_in()
        fake = SimpleNamespace(CHECKS=CHECKS)
        patcher = mock.patch.object(health_routes.plugins, "modules", return_value=[fake])
        patcher.start()
        self.addCleanup(patcher.stop)
        quiet = mock.patch.object(health_routes, "log")   # the broken check's traceback is expected
        self.log = quiet.start()
        self.addCleanup(quiet.stop)

    def test_every_check_answers_even_when_one_is_slow_or_broken(self):
        with mock.patch.object(health_routes, "CHECK_TIMEOUT_S", 0.2):
            rows = health_routes.run_checks(timeout=0.2)
        self.assertEqual([r["id"] for r in rows], ["test.sites", "test.ai", "test.paused", "test.slow", "test.broken"])
        self.assertEqual(rows[3]["text"], health_routes.SLOW_WORDS)
        self.assertEqual(rows[4]["text"], health_routes.FAILED_WORDS)
        self.assertEqual(health_routes.worst(rows), "warn")
        self.log.exception.assert_called_once()

    def test_the_route_lists_checks_with_their_fix(self):
        body = self.client.get("/api/v2/health").json()
        sites = body["checks"][0]
        self.assertEqual(sites["fix"], {"label": "Add a free key", "route": "settings-sites"})
        self.assertEqual(body["worst"], "warn")

    def test_a_fix_names_its_page_or_starts_its_action(self):
        self.assertEqual(self.client.post("/api/v2/health/test.sites/fix").json(), {"route": "settings-sites"})
        runner.action("test.health_fix", area="settings", label="Testing", why="You pressed Test now")(
            lambda ctx: ctx.step("testing"))
        self.addCleanup(runner.forget, "test.health_fix")
        reply = self.client.post("/api/v2/health/test.ai/fix").json()
        self.assertEqual(runner.wait(reply["action_id"])["state"], "done")
        self.assertEqual(self.client.post("/api/v2/health/test.none/fix").status, 404)
        # regression: the core's checks name a POST route, which was started as an action kind and failed
        self.assertEqual(self.client.post("/api/v2/health/test.paused/fix").json(), {"post": "/status/pause"})


class Browsers(unittest.TestCase):
    def test_settings_lists_the_chromium_browsers_found_and_firefox_as_not_supported(self):
        # a fake PATH lookup: Windows' own `which` finds only names with a PATHEXT ending, never a Linux binary
        on_path = {"opera", "google-chrome", "microsoft-edge", "brave-browser", "vivaldi-stable", "firefox"}
        with mock.patch.object(health_routes.shutil, "which", lambda name: f"/usr/bin/{name}" if name in on_path
                               else None), mock.patch.object(os, "name", "posix"), \
                mock.patch.object(health_routes, "_platform_browsers", return_value=None):
            rows = {b["id"]: b for b in health_routes.browsers()}
        for bid in ("opera", "chrome", "edge", "brave", "vivaldi"):
            self.assertTrue(rows[bid]["found"], bid)
            self.assertTrue(rows[bid]["supported"], bid)
        self.assertFalse(rows["chromium"]["found"])
        self.assertEqual((rows["firefox"]["found"], rows["firefox"]["supported"]), (True, False))
        self.assertEqual(rows["firefox"]["name"], "Firefox (not supported yet)")


if __name__ == "__main__":
    unittest.main()
