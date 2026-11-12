"""The `record.sources` action: reads the sources the person just added to a section, or asked to read again;
and `record.projects_refresh`, which reads again the project sources that changed (`sections.refresh`).

The person waits for it on the page, so it runs at priority 1, and two of them may run at once (two quick
uploads); each source is read in its own transactions (`pipeline.read`), so they never cross. It is resumable:
after a restart it reads the same sources again, which gives the same result. When it has read any (or kept one
out), it publishes a `profile` event naming them, as a read changes the cards: the profile tabs follow those
events, not the action's, and kept showing the cards from before a read (2026-11-09). The refresh runs in the
background, so it waits behind what the person asked for (priority 2), one at a time (`exclusive="kind"`).
Bulk and background reads (read-waiting, more than `BULK_FILES` files, the refresh) are batch work for Hal's
gateway; a few files added on the page are not, as batch work waits behind every other request.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from jobhunt import ai
from jobhunt.ai.hal_gateway import as_batch
from jobhunt.core import events, runner
from jobhunt.sections import pipeline, store

KIND = "record.sources"
REFRESH = "record.projects_refresh"   # also the scheduled job's id (`sections.schedule`)
AI_RETRY_S = 30            # a model parked for a voice question is back within about this
AI_RETRY_MAX_S = 300       # the longest wait between two tries
AI_WAIT_S = 12 * 3600      # a model away longer than a night is not coming back by itself
CHECK_S = 1                # how often a wait looks for a stop
BULK_FILES = 50            # an add of more files is bulk work (as the page asks first past this: sort.js CONFIRM_OVER)
AI_GONE = ("signed_out", "free_plan", "local_only", "not_installed")   # waiting does not bring these back
EXAMPLE_TEXT = ("# Volunteer award\n\nAwarded to Robin Testwell on 5 March 2025 for work at the Made-up Heritage "
                "Trust.\n")


def _example(_world) -> dict:
    """A made-up certificate added to Documents, for gate G9."""
    from jobhunt import db

    con = db.connect()
    try:
        added = pipeline.add_uploads(con, "document", [pipeline.Incoming("Volunteer award.md",
                                                                         EXAMPLE_TEXT.encode("utf-8"))])
    finally:
        con.close()
    return {"ids": added["added"]}


@runner.action(KIND, area="documents", label="Reading what you added to your record",
               why="You added files or links to your record", priority=1, exclusive="none", resumable=True,
               example=_example)
def read_sources(ctx, ids: list, allow: list | None = None, batch: bool = False) -> dict:
    """Reads each source in `ids` (those in `allow` even when they look private); says what each gave. `batch`
    reads them as batch work (`read_all`)."""
    return read_all(ctx, ids, {int(i) for i in allow or []}, batch=batch or len(ids) > BULK_FILES)


def read_all(ctx, ids: list, allowed: set[int], *, batch: bool = False) -> dict:
    """Reads each source in `ids` and the sources found inside them, once each; says what each gave.

    As many sources are with the AI at once as it serves (`ai.at_once`: Hal's writer has two slots), on worker
    threads that only read (`pipeline.run`); this thread alone takes each up and files it (`pipeline.begin`,
    `pipeline.finish`), so the record and the database keep one writer. A source the AI cannot read now is
    tried again, after `AI_RETRY_S` and then longer waits up to `AI_RETRY_MAX_S`, for up to `AI_WAIT_S` in all,
    so a model that stops for a while (another program took the graphics card) holds the read up instead of
    failing it; an AI that is not set up (`AI_GONE`) ends the read with the rest still waiting. Carried on after
    a restart, it reads only what is still waiting, and the sources found inside those read before. With
    `batch` the reads are batch work for Hal's gateway (`as_batch`), so they give the graphics card up to the
    person's chat and voice: a bulk or background read is, and a few files the person just added on the page
    are not, as batch work waits behind every other request (2026-11-12).
    """
    out = {"read": 0, "waiting": 0, "failed": 0, "kept_out": 0, "facts": 0, "missing": 0, "waiting_ai": 0}
    todo = list(dict.fromkeys(int(i) for i in ids))   # grows by the sources a read finds inside one
    if ctx.resumed_from:
        todo = _still_waiting(ctx.con, todo)
    else:
        with ctx.con:
            ctx.con.executemany("UPDATE record_sources SET state = 'new' WHERE id = ?", [(i,) for i in todo])
    with as_batch() if batch else contextlib.nullcontext():
        reading = _Reading(ctx, out, todo, allowed)
        workers = max(1, ai.at_once())
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="record-read") as pool:
            try:
                reading.run(pool, workers)
            finally:
                for job in reading.in_hand.values():
                    job.cancel()
    gone = reading.gone
    if out["read"] or out["kept_out"]:
        events.publish("profile", sources=[int(i) for i in todo])
    if out["read"]:
        ctx.changed(f"Read {out['read']} of what you added, with {out['facts']} facts for your profile.")
    if out["waiting"]:
        ctx.changed(f"{out['waiting']} wait for a reader Hal does not have yet.")
    if out["failed"]:
        ctx.changed(f"{out['failed']} could not be read.")
    if out["waiting_ai"]:
        ctx.changed(f"{out['waiting_ai']} wait for Hal's AI ({gone or 'it did not answer'}); read them again "
                    "once it is back.")
    if out["kept_out"]:
        ctx.changed(f"{out['kept_out']} kept out because they look private.")
    return out


class _Reading:
    """One `read_all`: hands sources to the workers in order, files what they give, and waits out an AI that
    cannot answer for a while, for all of them at once."""

    def __init__(self, ctx, out: dict, todo: list[int], allowed: set[int]) -> None:
        self.ctx, self.out, self.todo, self.allowed = ctx, out, todo, allowed
        self.next = 0                       # the index in `todo` of the next source to hand out
        self.done = 0
        self.in_hand: dict[int, Future] = {}
        self.begun: dict[int, pipeline.Begun] = {}
        self.again: list[int] = []          # sources the AI could not read yet, handed out first
        self.gone = ""                      # why the AI is not coming back; the rest wait
        self.pause = AI_RETRY_S
        self.resume_at = 0.0                # no source is handed out before this, while the AI is away
        self.away_since: float | None = None

    def run(self, pool: ThreadPoolExecutor, workers: int) -> None:
        """Reads until every source is read, waiting or out of reach."""
        while True:
            self._hand_out(pool, workers)
            if not self.in_hand:
                if self.gone or (not self.again and self.next >= len(self.todo)):
                    return
                self._wait_for_ai()
                continue
            finished, _ = wait(list(self.in_hand.values()), timeout=CHECK_S, return_when=FIRST_COMPLETED)
            self.ctx.checkpoint()
            for source_id, job in [(i, j) for i, j in self.in_hand.items() if j in finished]:
                del self.in_hand[source_id]
                self._file(source_id, job.result())

    def _hand_out(self, pool: ThreadPoolExecutor, workers: int) -> None:
        while len(self.in_hand) < workers and not self.gone and time.monotonic() >= self.resume_at:
            if self.again:
                source_id = self.again.pop(0)
            elif self.next < len(self.todo):
                source_id = self.todo[self.next]
                self.next += 1
            else:
                return
            self.ctx.step(f"Reading {self.done + len(self.in_hand) + 1} of {len(self.todo)}", total=len(self.todo))
            try:
                begun = pipeline.begin(self.ctx.con, source_id)
            except LookupError:      # removed while it waited
                self.out["missing"] += 1
                self.done += 1
                continue
            self.begun[source_id] = begun
            # the worker carries this thread's context: the batch token (`as_batch`) and the action's priority
            self.in_hand[source_id] = pool.submit(contextvars.copy_context().run, pipeline.run, begun)

    def _file(self, source_id: int, reading) -> None:
        begun = self.begun.pop(source_id)
        try:
            done = pipeline.finish(self.ctx.con, begun, reading, allow_private=source_id in self.allowed)
        except LookupError:          # removed while it was read
            self.out["missing"] += 1
            self.done += 1
            return
        if done["state"] == "waiting_ai":
            self._ai_away(source_id, done)
            return
        self.pause, self.away_since = AI_RETRY_S, None
        state = {"new": "waiting", "read": "read", "failed": "failed", "kept_out": "kept_out"}[done["state"]]
        self.out[state] += 1
        self.out["facts"] += done["facts"]
        self.todo += [c for c in done.get("children", []) if c not in self.todo]
        self.done += 1
        self.ctx.progress(self.done, len(self.todo))

    def _ai_away(self, source_id: int, done: dict) -> None:
        """The AI could not read `source_id` now: it is tried again after a pause, unless the AI is not coming
        back, when it and every source not read yet wait for the person."""
        now = time.monotonic()
        self.away_since = self.away_since if self.away_since is not None else now
        if done["kind"] in AI_GONE or now - self.away_since >= AI_WAIT_S:
            self.gone = store.row(self.ctx.con, source_id)["error"]
            left = len(self.again) + len(self.todo) - self.next + 1
            self.out["waiting_ai"] += left
            self.done += left
            self.again.clear()
            self.next = len(self.todo)
            return
        self.again.append(source_id)
        if now >= self.resume_at:     # the first of the reads in hand to find the AI away sets the pause
            self.resume_at = now + self.pause
            self.pause = min(self.pause * 2, AI_RETRY_MAX_S)
            self.ctx.step(f"Waiting for Hal's AI, then reading again ({done['kind']})")

    def _wait_for_ai(self) -> None:
        while time.monotonic() < self.resume_at:
            self.ctx.checkpoint()
            time.sleep(min(CHECK_S, max(0.0, self.resume_at - time.monotonic())))


def being_read(con) -> dict[int, str]:
    """The sources a `record.sources` action not yet ended is reading, with that action's id: {source id: id}.
    A source found inside one of them counts too, as a read carried on reads those (`_still_waiting`)."""
    runner.flush()
    held: dict[int, str] = {}
    marks = ",".join("?" * len(runner.ACTIVE))
    for row in con.execute(f"SELECT id, params FROM actions WHERE kind = ? AND state IN ({marks}) ORDER BY created",
                           (KIND, *runner.ACTIVE)):
        for source_id in json.loads(row["params"] or "{}").get("ids", []):
            held.setdefault(int(source_id), row["id"])
    if held:
        marks = ",".join("?" * len(held))
        for row in con.execute(f"SELECT id, parent_id FROM record_sources WHERE parent_id IN ({marks})", list(held)):
            held.setdefault(row["id"], held[row["parent_id"]])
    return held


def _still_waiting(con, ids: list[int]) -> list[int]:
    """Of `ids` and the sources under them, those not read yet: what a read carried on after a restart reads."""
    marks = ",".join("?" * len(ids))
    rows = con.execute(f"""SELECT id FROM record_sources WHERE state IN ('new', 'reading')
                           AND (id IN ({marks}) OR parent_id IN ({marks})) ORDER BY id""",
                       [*ids, *ids]).fetchall()
    return [r["id"] for r in rows]


def _refresh_example(_world) -> dict:
    """A made-up project added by hand and not read yet, which the refresh reads, for gate G9."""
    from jobhunt import db

    con = db.connect()
    try:
        pipeline.add_link(con, "project", "Made-up harbour clean-up day", doc_kind="manual")
    finally:
        con.close()
    return {}


@runner.action(REFRESH, area="documents", label="Checking your projects for changes",
               why="Hal checks your projects regularly for changes", priority=2, exclusive="kind", resumable=True,
               example=_refresh_example)
def refresh_projects(ctx) -> dict:
    """Checks every project source cheaply (`sections.refresh`) and reads again only the ones that changed."""
    from jobhunt.sections import refresh

    ctx.step("Checking your projects")
    ids = refresh.changed(ctx.con)
    out = {**read_all(ctx, ids, set(), batch=True), "checked": ctx.con.execute(
        "SELECT count(*) FROM record_sources WHERE section = ?", (refresh.SECTION,)).fetchone()[0]}
    if not ids:
        ctx.changed("Nothing in your projects changed.")
    return out


def queue(ids: list[int], *, allow: list[int] = (), session_id: str | None = None, remote: bool = False,
          why: str | None = None, batch: bool = False) -> str | None:
    """Starts reading these sources and returns the action's id; None when there is nothing to read. `batch`
    reads them as batch work, as a bulk read from the command line is."""
    if not ids:
        return None
    params = {"ids": [int(i) for i in ids]}
    if allow:
        params["allow"] = [int(i) for i in allow]
    if batch:
        params["batch"] = True
    return runner.start(KIND, params=params, session_id=session_id, remote=remote, why=why)
