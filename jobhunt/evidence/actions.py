"""The record's action kinds: reading the folder again (`record.sync`) and asking it a question (`record.ask`).

Both are persisted actions, so the page starts them and returns at once; progress and the answer arrive
on the event stream and in Activity. Reading the folder is background work (priority 2); a question is
something the person is waiting for (priority 0). Only one read of the folder runs at a time, whoever
asked for it.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt import record
from jobhunt.core import runner

EXAMPLE_NOTE = "# First Aid\n\nEmergency first aid at work, completed 5 March 2025.\n"


def _example_record(world) -> Path:
    """A one-note record inside the made-up world, for gate G9."""
    folder = Path(record._live("VAULT"))
    (folder / "Certificates").mkdir(parents=True, exist_ok=True)
    (folder / "Certificates" / "First Aid.md").write_text(EXAMPLE_NOTE, encoding="utf-8")
    return folder


def _sync_example(world) -> dict:
    _example_record(world)
    return {}


def _ask_example(world) -> dict:
    _example_record(world)
    record.sync()
    return {"q": "When did I do first aid at work?"}


@runner.action("record.sync", area="documents", label="Reading your record",
               why="New or changed files in your record", priority=2, exclusive="kind", example=_sync_example)
def sync(ctx) -> dict:
    """Turns new documents into notes and re-reads changed ones; says what changed."""
    ctx.step("Reading the files in your record")
    out = record.sync()
    if out["converted"]:
        ctx.changed(f"Read {len(out['converted'])} new or changed files.")
    if out["changed"] or out["removed"]:
        ctx.changed(f"{out['changed']} notes read again, {out['removed']} gone.")
    return out


@runner.action("record.ask", area="documents", label="Answering from your record", why="You asked: {q}",
               priority=0, exclusive="none", example=_ask_example)
def ask(ctx, q: str) -> dict:
    """Answers a question from the record, naming the files each fact came from."""
    ctx.step("Looking through your record")
    out = record.ask(q)
    for path in out["sources"]:
        ctx.used("record", path)
    if out.get("note"):
        ctx.step(f"Showing the passages only: {out['note']}")
    else:
        ctx.step("Writing the answer")
    return {"answer": out["answer"], "sources": out["sources"], "note": out.get("note", ""),
            "passages": [{"path": h["path"], "heading": h["heading"], "text": h["text"]}
                         for h in out.get("passages") or []]}
