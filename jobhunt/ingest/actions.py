"""The `record.ingest` action: one bulk read of folders, zips, the drop folder and web links into the record.

Background work (priority 2), one at a time (`exclusive="kind"`), and resumable: after a restart the runner
starts it again with the same params, and the batch's `ingest_items` rows say what is left. `queue` is how
routes and the scheduled read of the record start one.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt.core import runner
from jobhunt.ingest import engine

EXAMPLE_CV = """# Robin Testwell

Curriculum vitae

## Work experience

Visitor guide, Made-up Heritage Trust, Leeds, 2023 to 2024.

## Education

BA Made-up Studies, University of Nowhere, 2021 to 2024.
"""
EXAMPLE_NOTES = "# Week 1\n\nIntroduction to the module: reading lists and the first seminar.\n"


def _example(world) -> dict:
    """A made-up folder with a CV and a note, for gate G9."""
    folder = Path(world.root) / "files-to-read"
    (folder / "Lectures").mkdir(parents=True, exist_ok=True)
    (folder / "CV.md").write_text(EXAMPLE_CV, encoding="utf-8")
    (folder / "Lectures" / "week 1 notes.md").write_text(EXAMPLE_NOTES, encoding="utf-8")
    return {"batch": "example", "folders": [str(folder)]}


@runner.action("record.ingest", area="documents", label="Reading files into your record",
               why="You gave Hal files to read into your record", priority=2, exclusive="kind", resumable=True,
               example=_example)
def ingest(ctx, batch: str, folders: list | None = None, uploads: list | None = None, drop: bool = False,
           urls: list | None = None) -> dict:
    """Reads every file of the inputs into notes in the record; says how many and what could not be read."""
    def step(text: str) -> None:
        ctx.step(text, total=engine.STEPS)

    out = engine.run(ctx.con, batch, folders=folders or [], uploads=uploads or [], drop=drop, urls=urls or [],
                     step=step, progress=ctx.progress)
    states = out["states"]
    if out["notes"]:
        ctx.changed(f"Wrote {out['notes']} notes in your record.")
    for state, words in (("duplicate", "copies of files already read"), ("not_downloaded", "not downloaded yet"),
                         ("no_text", "with no text Hal could read"), ("error", "could not be read")):
        if states.get(state):
            ctx.changed(f"{states[state]} {words}.")
    return out


def queue(*, folders=(), uploads=(), drop: bool = False, urls=(), session_id: str | None = None,
          remote: bool = False, why: str | None = None) -> str:
    """Starts an ingest of these inputs and returns its action id; RuntimeError while another is running."""
    params = {"batch": engine.new_batch(), "folders": [str(f) for f in folders],
              "uploads": [str(u) for u in uploads], "drop": bool(drop), "urls": [str(u) for u in urls]}
    return runner.start("record.ingest", params=params, session_id=session_id, remote=remote, why=why)


def queue_drop() -> str | None:
    """Starts an ingest of the drop folder when it holds files not read yet; its action id, else None."""
    from jobhunt import db

    con = db.connect()
    try:
        new = engine.drop_has_new(con)
    finally:
        con.close()
    if not new:
        return None
    try:
        return queue(drop=True, why="New files in your drop folder")
    except RuntimeError:
        return None      # an ingest is running already; the next scheduled read starts this one
