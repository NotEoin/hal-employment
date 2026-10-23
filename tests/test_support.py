"""The test harness's own promises: no test reaches the internet, and no action outlives the test that started it.

Both guard against tests that pass on a fast computer and fail on a slow CI runner: a real web search that
outlived its wait, and an action left running that broke the next test with "Hal has no record of that action".
"""
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request

from support import offline
from support.actions import finished, wait_for_actions
from support.world import World

from jobhunt import research
from jobhunt.core import runner

WAIT_S = 10
SHORT_S = 0.3        # how long the short made-up action takes, and the wait that gives up on a held one
AT_ONCE_S = 1.0      # a refusal answers at once; a real lookup or connection could take seconds
PUBLIC = ("93.184.215.14", 443)   # a public address; nothing may connect to it from a test


class Offline(unittest.TestCase):
    def test_a_name_off_this_computer_is_never_looked_up(self):
        started = time.monotonic()
        with self.assertRaises(socket.gaierror) as caught:
            socket.getaddrinfo("html.duckduckgo.com", 443)
        self.assertIsInstance(caught.exception, offline.NoLookup)
        self.assertLess(time.monotonic() - started, AT_ONCE_S)

    def test_a_public_address_is_never_connected_to(self):
        started = time.monotonic()
        with self.assertRaises(offline.NoRoute):
            socket.create_connection(PUBLIC, timeout=WAIT_S)
        self.assertLess(time.monotonic() - started, AT_ONCE_S)

    def test_code_under_test_reads_it_as_being_offline(self):
        # a regression: the cover letter's company research asked DuckDuckGo from inside a test
        with self.assertRaises(urllib.error.URLError) as caught:
            urllib.request.urlopen("https://jobs.example.org/", timeout=WAIT_S)
        self.assertIsInstance(caught.exception.reason, offline.NoLookup)

    def test_this_computer_is_still_reachable(self):
        with socket.create_server(("127.0.0.1", 0)) as server:
            socket.create_connection(server.getsockname(), timeout=WAIT_S).close()
        self.assertTrue(socket.getaddrinfo("localhost", None))
        self.assertTrue(socket.getaddrinfo(socket.gethostname(), None))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.connect(("192.0.2.1", 9))   # sends nothing; a test finds this computer's address this way

    def test_a_world_finds_no_company_pages(self):
        with World():
            self.assertEqual(research.company_pages("Riverside Heritage Trust"), [])


class Actions(unittest.TestCase):
    def kind(self, name: str, fn) -> None:
        runner.action(name, area="find", label="Checking made-up work", why="A test asked for it")(fn)
        self.addCleanup(runner.forget, name)

    def test_a_world_closes_only_once_its_actions_have_ended(self):
        # a regression: an action outlived its world and broke the next test with "Hal has no record of that action"
        self.kind("test.short", lambda ctx: time.sleep(SHORT_S))
        with World():
            runner.start("test.short")
        self.assertTrue(runner.idle(0), "an action of the world was still going after it closed")

    def test_an_action_still_going_is_stopped_and_named(self):
        release = threading.Event()
        self.addCleanup(runner.idle, WAIT_S)
        self.addCleanup(release.set)
        self.kind("test.held", lambda ctx: release.wait(WAIT_S))
        with World():
            action_id = runner.start("test.held")
            with self.assertRaises(AssertionError) as caught:
                wait_for_actions(SHORT_S)
            release.set()
            self.assertEqual(finished(action_id)["state"], "stopped")
        self.assertIn("test.held", str(caught.exception))

    def test_finished_names_the_step_an_action_is_still_on(self):
        release = threading.Event()
        self.addCleanup(runner.idle, WAIT_S)
        self.addCleanup(release.set)

        def held(ctx):
            ctx.step("reading the made-up advert", total=2)
            release.wait(WAIT_S)

        self.kind("test.stuck", held)
        with World():
            action_id = runner.start("test.stuck")
            with self.assertRaises(AssertionError) as caught:
                finished(action_id, SHORT_S)
            release.set()
            finished(action_id)
        self.assertIn("test.stuck is still running", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
