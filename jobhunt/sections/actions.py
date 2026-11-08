"""The `record.sources` action: reads the sources the person just added to a section, or asked to read again.

The person waits for it on the page, so it runs at priority 1, and two of them may run at once (two quick
uploads); each source is read in its own transactions (`pipeline.read`), so they never cross. It is resumable:
after a restart it reads the same sources again, which gives the same result. When it has read any (or kept one
out), it publishes a `profile` event naming them, as a read changes the cards: the profile tabs follow those
events, not the action's, and kept showing the cards from before a read (2026-11-09).
"""
from __future__ import annotations

from jobhunt.core import events, runner
from jobhunt.sections import pipeline, store

KIND = "record.sources"
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
    allowed = {int(i) for i in allow or []}
    out = {"read": 0, "waiting": 0, "failed": 0, "kept_out": 0, "facts": 0, "missing": 0}
    todo = list(ids)             # grows by the sources a read finds inside one (an account's projects)
    for n, source_id in enumerate(todo):
        ctx.step(f"Reading {n + 1} of {len(todo)}", total=len(todo))
        try:
            store.row(ctx.con, source_id)
        except LookupError:      # removed while it waited
            out["missing"] += 1
            continue
        done = pipeline.read(ctx.con, int(source_id), allow_private=int(source_id) in allowed)
        state = {"new": "waiting", "read": "read", "failed": "failed", "kept_out": "kept_out"}[done["state"]]
        out[state] += 1
        out["facts"] += done["facts"]
        todo += done.get("children", [])
        ctx.progress(n + 1, len(todo))
    if out["read"] or out["kept_out"]:
        events.publish("profile", sources=[int(i) for i in todo])
    if out["read"]:
        ctx.changed(f"Read {out['read']} of what you added, with {out['facts']} facts for your profile.")
    if out["waiting"]:
        ctx.changed(f"{out['waiting']} wait for a reader Hal does not have yet.")
    if out["failed"]:
        ctx.changed(f"{out['failed']} could not be read.")
    if out["kept_out"]:
        ctx.changed(f"{out['kept_out']} kept out because they look private.")
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
