"""The `record.sources` action: reads the sources the person just added to a section, or asked to read again;
and `record.projects_refresh`, which reads again the project sources that changed (`sections.refresh`).

The person waits for it on the page, so it runs at priority 1, and two of them may run at once (two quick
uploads); each source is read in its own transactions (`pipeline.read`), so they never cross. It is resumable:
after a restart it reads the same sources again, which gives the same result. When it has read any (or kept one
out), it publishes a `profile` event naming them, as a read changes the cards: the profile tabs follow those
events, not the action's, and kept showing the cards from before a read (2026-11-09). The refresh runs in the
background, so it waits behind what the person asked for (priority 2), one at a time (`exclusive="kind"`).
"""
from __future__ import annotations

import time

from jobhunt.core import events, runner
from jobhunt.sections import pipeline, store

KIND = "record.sources"
REFRESH = "record.projects_refresh"   # also the scheduled job's id (`sections.schedule`)
AI_RETRY_S = 30            # a model parked for a voice question is back within about this
AI_RETRY_MAX_S = 300       # the longest wait between two tries
AI_WAIT_S = 12 * 3600      # a model away longer than a night is not coming back by itself
CHECK_S = 1                # how often a wait looks for a stop
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
def read_sources(ctx, ids: list, allow: list | None = None) -> dict:
    """Reads each source in `ids` (those in `allow` even when they look private); says what each gave."""
    return read_all(ctx, ids, {int(i) for i in allow or []})


def read_all(ctx, ids: list, allowed: set[int]) -> dict:
    """Reads each source in `ids` and the sources found inside them, once each; says what each gave.

    A source the AI cannot read now is tried again, after `AI_RETRY_S` and then longer waits up to
    `AI_RETRY_MAX_S`, for up to `AI_WAIT_S` in all, so a model that stops for a while (another program took the
    graphics card) holds the read up instead of failing it; an AI that is not set up (`AI_GONE`) ends the read
    with the rest still waiting. Carried on after a restart, it reads only what is still waiting, and the
    sources found inside those read before.
    """
    out = {"read": 0, "waiting": 0, "failed": 0, "kept_out": 0, "facts": 0, "missing": 0, "waiting_ai": 0}
    todo = list(dict.fromkeys(int(i) for i in ids))   # grows by the sources a read finds inside one
    if ctx.resumed_from:
        todo = _still_waiting(ctx.con, todo)
    else:
        with ctx.con:
            ctx.con.executemany("UPDATE record_sources SET state = 'new' WHERE id = ?", [(i,) for i in todo])
    gone = ""
    for n, source_id in enumerate(todo):
        ctx.step(f"Reading {n + 1} of {len(todo)}", total=len(todo))
        try:
            store.row(ctx.con, source_id)
        except LookupError:      # removed while it waited
            out["missing"] += 1
            continue
        if gone:
            out["waiting_ai"] += 1
            continue
        done = _read_waiting_for_ai(ctx, source_id, source_id in allowed)
        if done["state"] == "waiting_ai":
            gone = done.get("why", "")
            out["waiting_ai"] += 1
            continue
        state = {"new": "waiting", "read": "read", "failed": "failed", "kept_out": "kept_out"}[done["state"]]
        out[state] += 1
        out["facts"] += done["facts"]
        todo += [c for c in done.get("children", []) if c not in todo]
        ctx.progress(n + 1, len(todo))
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



def _still_waiting(con, ids: list[int]) -> list[int]:
    """Of `ids` and the sources under them, those not read yet: what a read carried on after a restart reads."""
    marks = ",".join("?" * len(ids))
    rows = con.execute(f"""SELECT id FROM record_sources WHERE state IN ('new', 'reading')
                           AND (id IN ({marks}) OR parent_id IN ({marks})) ORDER BY id""",
                       [*ids, *ids]).fetchall()
    return [r["id"] for r in rows]


def _read_waiting_for_ai(ctx, source_id: int, allow_private: bool) -> dict:
    """`pipeline.read`, tried again while the AI cannot answer for a while; `why` says why it gave up."""
    deadline = time.monotonic() + AI_WAIT_S
    pause = AI_RETRY_S
    while True:
        done = pipeline.read(ctx.con, source_id, allow_private=allow_private)
        if done["state"] != "waiting_ai":
            return done
        if done["kind"] in AI_GONE or time.monotonic() >= deadline:
            return {**done, "why": store.row(ctx.con, source_id)["error"]}
        ctx.step(f"Waiting for Hal's AI, then reading again ({done['kind']})")
        until = time.monotonic() + pause
        while time.monotonic() < until:
            ctx.checkpoint()
            time.sleep(min(CHECK_S, max(0.0, until - time.monotonic())))
        pause = min(pause * 2, AI_RETRY_MAX_S)

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
    out = {**read_all(ctx, ids, set()), "checked": ctx.con.execute(
        "SELECT count(*) FROM record_sources WHERE section = ?", (refresh.SECTION,)).fetchone()[0]}
    if not ids:
        ctx.changed("Nothing in your projects changed.")
    return out


def queue(ids: list[int], *, allow: list[int] = (), session_id: str | None = None, remote: bool = False,
          why: str | None = None) -> str | None:
    """Starts reading these sources and returns the action's id; None when there is nothing to read."""
    if not ids:
        return None
    params = {"ids": [int(i) for i in ids]}
    if allow:
        params["allow"] = [int(i) for i in allow]
    return runner.start(KIND, params=params, session_id=session_id, remote=remote, why=why)
