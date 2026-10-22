"""The event stream: heartbeat, replay with Last-Event-ID, resync, the four-stream cap, the poller
that brings other processes' rows in, and the bus underneath.
"""
import http.client
import json
import unittest
from unittest import mock

from support.api import TIMEOUT_S, HalServer

from jobhunt import db
from jobhunt.core import events
from jobhunt.web import auth, sse

FAST_PING_S = 0.3


class Stream:
    """One open event stream, read line by line."""

    def __init__(self, hal: HalServer, cookie: str, last_id: str | None = None) -> None:
        self.con = http.client.HTTPConnection("127.0.0.1", hal.port, timeout=TIMEOUT_S)
        headers = {"Host": f"127.0.0.1:{hal.port}", "Cookie": cookie}
        if last_id is not None:
            headers["Last-Event-ID"] = last_id
        self.con.request("GET", "/api/v2/events", headers=headers)
        self.reply = self.con.getresponse()

    def frame(self) -> list[str]:
        """The next block of lines up to a blank line."""
        lines = []
        while True:
            line = self.reply.fp.readline().decode("utf-8")
            if line == "":
                raise EOFError("the stream ended")
            if line == "\n":
                if lines:
                    return lines
                continue
            lines.append(line.rstrip("\n"))

    def event(self) -> tuple[str, dict]:
        """The next event's type and data, skipping pings."""
        while True:
            lines = self.frame()
            if lines[0].startswith(":"):
                continue
            fields = dict(line.split(": ", 1) for line in lines)
            return fields["event"], json.loads(fields["data"])

    def close(self) -> None:
        """Closes the connection."""
        self.con.close()


def cookie_of(client) -> str:
    """The client's session cookie header."""
    name = auth.session_cookie_name()
    return f"{name}={client.jar[name]}"


class EventStream(unittest.TestCase):
    """Through the real server."""

    def setUp(self):
        patch = mock.patch.object(sse, "PING_S", FAST_PING_S)
        patch.start()
        self.addCleanup(patch.stop)
        self.hal = HalServer().__enter__()
        self.addCleanup(self.hal.__exit__, None, None, None)
        self.cookie = cookie_of(self.hal.signed_in())

    def open(self, last_id: str | None = None) -> Stream:
        s = Stream(self.hal, self.cookie, last_id)
        self.addCleanup(s.close)
        self.assertEqual(s.reply.status, 200)
        self.assertEqual(s.frame(), [f"retry: {sse.RETRY_MS}"])
        return s

    def test_the_event_stream_sends_a_heartbeat_and_replays(self):
        s = self.open()
        self.assertTrue(s.reply.headers["Content-Type"].startswith("text/event-stream"))
        first = events.publish("toast", text="A made-up toast")
        self.assertEqual(s.event(), ("toast", {"text": "A made-up toast"}))
        self.assertEqual(s.frame(), [": ping"])
        second = events.publish("job", id="made-up:1")
        s.close()
        again = self.open(last_id=str(first.seq))
        lines = again.frame()
        self.assertEqual(lines[0], f"id: {second.seq}")
        self.assertEqual(lines[1], "event: job")

    def test_an_id_the_ring_no_longer_holds_gets_resync(self):
        s = self.open(last_id=str(events.last_seq() + 1000))
        self.assertEqual(s.event(), ("resync", {}))

    def test_a_fifth_stream_closes_the_oldest_with_bye(self):
        streams = [self.open() for _ in range(sse.MAX_STREAMS)]
        session_id = auth._hash(self.cookie.split("=", 1)[1])
        self.assertEqual(sse.open_count(session_id), sse.MAX_STREAMS)
        self.open()
        self.assertEqual(streams[0].event(), ("bye", {}))

    def test_the_stream_needs_a_session(self):
        con = http.client.HTTPConnection("127.0.0.1", self.hal.port, timeout=TIMEOUT_S)
        self.addCleanup(con.close)
        con.request("GET", "/api/v2/events", headers={"Host": f"127.0.0.1:{self.hal.port}"})
        self.assertEqual(con.getresponse().status, 401)


class Poller(unittest.TestCase):
    """Rows written by the CLI and scheduled children reach the page."""

    def test_rows_written_by_another_process_are_published(self):
        with HalServer():
            con = db.connect()
            self.addCleanup(con.close)
            db.alert(con, "info", "Written before the poller started")
            con.commit()
            poller = sse.DbPoller(db.connect, stop=None)
            poller.poll(con)
            sub = events.subscribe()
            self.addCleanup(sub.close)
            alert_id = db.alert(con, "good", "A made-up alert", "Made-up words")
            con.execute("""INSERT INTO activity (at, area, kind, text, why) VALUES ('2026-10-22T09:00:00Z', 'find',
                           'find.search', 'Searched made-up sites', 'It was time for a search')""")
            con.commit()
            poller.poll(con)
            got = [sub.get(timeout=1), sub.get(timeout=1)]
            self.assertEqual({e.type for e in got}, {"alert", "activity"})
            self.assertIn(alert_id, [e.data.get("id") for e in got])
            poller.poll(con)
            self.assertIsNone(sub.get(timeout=0.1))


class Bus(unittest.TestCase):
    """The in-process publish and subscribe."""

    def test_a_reader_that_falls_behind_gets_one_resync(self):
        sub = events.subscribe()
        self.addCleanup(sub.close)
        with mock.patch.object(events, "QUEUE_SIZE", 3):
            small = events.subscribe()
            self.addCleanup(small.close)
        for n in range(5):
            events.publish("job", id=f"made-up:{n}")
        self.assertEqual(small.get(timeout=1).type, events.RESYNC)
        self.assertEqual(sub.get(timeout=1).data, {"id": "made-up:0"})

    def test_replay_stops_where_the_ring_stops(self):
        with mock.patch.object(events, "_ring", events.deque(maxlen=2)):
            a = events.publish("job", id="a")
            b = events.publish("job", id="b")
            c = events.publish("job", id="c")
            self.assertEqual(events.since(b.seq), [c])
            self.assertIsNone(events.since(a.seq - 1))


if __name__ == "__main__":
    unittest.main()
