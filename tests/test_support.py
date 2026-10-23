"""The test harness's own promises: no test reaches the internet.

It guards against tests that pass on a fast computer and fail on a slow CI runner: a real web search that
outlived its wait.
"""
import socket
import time
import unittest
import urllib.error
import urllib.request

from support import offline
from support.world import World

from jobhunt import research

WAIT_S = 10
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


if __name__ == "__main__":
    unittest.main()
