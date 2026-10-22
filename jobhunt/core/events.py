"""In-process publish and subscribe, with the ring buffer the event stream replays from.

`publish(type, **data)` gives the event the next sequence number, keeps it in a ring of `RING_SIZE` and
offers it to every subscriber. Each subscriber has a bounded queue; one that falls `QUEUE_SIZE` behind is
emptied and gets a single `resync`, so a stuck page can never hold the server's memory. Payloads carry
ids, never private data: the page fetches what it needs through the API, which checks the session.
"""
from __future__ import annotations

import itertools
import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

RING_SIZE = 1000
QUEUE_SIZE = 1000
RESYNC = "resync"


@dataclass(frozen=True)
class Event:
    """One published event."""

    seq: int
    type: str
    data: dict[str, Any]


_lock = threading.Lock()
_seq = itertools.count(1)
_ring: deque[Event] = deque(maxlen=RING_SIZE)
_subs: set[Subscription] = set()


class Subscription:
    """A subscriber's queue; `get` waits for the next event, `close` stops delivery."""

    def __init__(self) -> None:
        self._q: queue.Queue[Event] = queue.Queue(QUEUE_SIZE)

    def _offer(self, ev: Event) -> None:
        try:
            self._q.put_nowait(ev)
        except queue.Full:
            # The reader fell behind: drop what it missed and tell it to fetch everything again.
            with self._q.mutex:
                self._q.queue.clear()
            self._q.put_nowait(Event(ev.seq, RESYNC, {}))

    def get(self, timeout: float | None = None) -> Event | None:
        """The next event, or None after `timeout` seconds."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        """Stops delivering events to this subscriber."""
        with _lock:
            _subs.discard(self)


def publish(type: str, **data: Any) -> Event:
    """Sends an event to every subscriber and keeps it for replay."""
    with _lock:
        ev = Event(next(_seq), type, dict(data))
        _ring.append(ev)
        subs = list(_subs)
    for sub in subs:
        sub._offer(ev)
    return ev


def subscribe() -> Subscription:
    """A new subscription to every event published from now on."""
    sub = Subscription()
    with _lock:
        _subs.add(sub)
    return sub


def since(seq: int) -> list[Event] | None:
    """The events after `seq`, or None when the ring no longer reaches back that far."""
    with _lock:
        events = list(_ring)
    if seq > (events[-1].seq if events else 0):
        return None  # an id from before this server started: its events are gone
    if events and seq < events[0].seq - 1:
        return None
    return [e for e in events if e.seq > seq]


def last_seq() -> int:
    """The newest sequence number published so far (0 before the first)."""
    with _lock:
        return _ring[-1].seq if _ring else 0
