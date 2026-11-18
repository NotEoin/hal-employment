"""Waiting for the runner's background actions in tests, with failures that say what was still going.

The runner is one per process, while each test has its own home. An action left going when a test ends
writes into the next test's home, or shows there as "Hal has no record of that action", so the test that
started it must be the one that fails. `HalServer` and `World` both call `wait_for_actions` on the way out.
A test whose request also starts a record read waits for that read too, and builds the shared document
converter first (`warm_document_reader`), so a one-off cost is never inside a timed wait.
"""
from __future__ import annotations

import time

from jobhunt import markitdown_loader
from jobhunt.core import runner

IDLE_S = 10   # the longest a test's background action may take to end once the test is over


def wait_for_actions(timeout: float = IDLE_S) -> None:
    """Waits for the test's actions to end; one that outlives `timeout` is stopped and named in the failure.

    Without this, a slow action only shows as a `PermissionError` when its home is removed on Windows, or as
    "FOREIGN KEY constraint failed" when it writes its steps into the next test's database.
    """
    if runner.idle(timeout):
        return
    with runner._lock:  # the runner's own view of what is still going
        going = [f"{live.kind.kind} ({live.subject}, {live.state}, {time.monotonic() - live.started:.1f} s: "
                 f"{live.text!r})" for live in runner._LIVE.values()]
        ending = sorted(runner._ENDING)
        closing = runner._executing[0]
    for live_id in list(runner._LIVE):
        runner.stop(live_id)
    runner.idle(timeout)
    raise AssertionError(f"actions still going {timeout} s after the test: {going or 'none'}; rows still being "
                         f"written for: {ending or 'none'}; action threads still closing: {closing}")


def finished(action_id: str, timeout: float = IDLE_S) -> dict:
    """The action once it has ended, as `runner.get` gives it; a failure naming its step if it has not."""
    done = runner.wait(action_id, timeout)
    if done["state"] not in runner.FINAL:
        raise AssertionError(f"{done['kind']} is still {done['state']} after {timeout} s, at step "
                             f"{done['step_n']} of {done['step_total']}: {done['message']!r}")
    return done


def warm_document_reader() -> None:
    """Builds the shared MarkItDown converter now, outside any timed wait.

    A record read builds it on first use: 3.8 s on an idle machine and over 10 s on a loaded one (measured
    2026-11-18), so the first test in a process to add a PDF to the record ran past its wait for the read.
    """
    markitdown_loader.markitdown_converter()
