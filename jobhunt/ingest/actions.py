"""The `record.ingest` action: one bulk read of folders, zips, the drop folder and web links into the record.

After the read, the experiences found twice under different names are merged (`profile.actions.dedupe`), then
the experiences in the profile are sorted into tiers (`profile.actions.sort`), so the Check your record page
can show the best ones first. The result then says how many skills the person can stand behind and how many
are only mentioned in their files (`profile.check.skill_counts`), which the Overview shows.

Background work (priority 2), one at a time (`exclusive="kind"`), and resumable: after a restart the runner
starts it again with the same params, and the batch's `ingest_items` rows say what is left. `queue` is how
routes and the scheduled read of the record start one. `origins` is kept only for the folders the person
said were their own work or course material, so a params row from before reads as a mix. With `allow`, the
batch is the files the person let in after they were kept out as private (`privacy.requeue`); a carry-on
finds them in the batch already.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt.core import runner
from jobhunt.ingest import engine, items, privacy, roles
from jobhunt.profile import actions as profile_actions
from jobhunt.profile import check

STEPS = engine.STEPS + 2     # the engine's steps, merging the experiences found twice, then sorting them

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
           urls: list | None = None, again: bool = False, allow: list | None = None, tidy: bool = False,
           origins: dict | None = None) -> dict:
    """Reads every file of the inputs into notes in the record; says how many and what could not be read.

    `origins` maps a folder or upload to where the person said it came from (`roles.ORIGINS`).
    """
    def step(text: str) -> None:
        ctx.step(text, total=STEPS)

    if allow and not items.seen_paths(ctx.con, batch):
        privacy.requeue(ctx.con, batch, allow)

    out = engine.run(ctx.con, batch, folders=folders or [], uploads=uploads or [], drop=drop, urls=urls or [],
                     again=again, tidy=tidy, step=step, progress=ctx.progress, origins=origins or {})
    step("Merging experiences found twice")
    profile_actions.dedupe(ctx)
    step("Sorting your experiences")
    profile_actions.sort(ctx)
    states = out["states"]
    if out["notes"]:
        ctx.changed(f"Wrote {out['notes']} notes in your record.")
    for state, words in (("duplicate", "copies of files already read"),
                         ("older_version", "older copies of a project, named in its newest copy's note"),
                         ("covered", "slides of lectures your own notes already cover"),
                         ("attached", "data files, figures and photos listed with their project or course"),
                         ("not_downloaded", "not downloaded yet"),
                         ("no_text", "with no text Hal could read"), ("error", "could not be read"),
                         ("private", "kept out because they look private")):
        if states.get(state):
            ctx.changed(f"{states[state]} {words}.")
    # after sorting, as a skill is usable when it is a tool of a core or supporting project
    out["skills"] = check.skill_counts(ctx.con)
    if any(out["skills"].values()):
        ctx.changed(f"{out['skills']['usable']} skills you can stand behind; {out['skills']['mentioned']} only "
                    "mentioned in your files, folded away on Check your record.")
    return out


def queue(*, folders=(), uploads=(), drop: bool = False, urls=(), again: bool = False, allow=(), tidy: bool = False,
          session_id: str | None = None, remote: bool = False, why: str | None = None,
          origins: dict | None = None) -> str:
    """Starts an ingest of these inputs and returns its action id; RuntimeError while another is running.

    `again` reads the notes written by rules from documents with the AI, in place (`engine.mark_again`);
    `allow` reads the private items with these ids, which the person let in (`privacy.requeue`); `tidy`
    holds what the record already has to today's rules (`tidy.py`). `origins` maps a folder or upload to
    where the person said it came from (`roles.ORIGINS`); the rest are a mix. ValueError for an origin
    that is not one of them.
    """
    params = {"batch": engine.new_batch(), "folders": [str(f) for f in folders],
              "uploads": [str(u) for u in uploads], "drop": bool(drop), "urls": [str(u) for u in urls],
              "again": bool(again), "tidy": bool(tidy)}
    said = {str(k): roles.origin_of(v) for k, v in (origins or {}).items()}
    if chosen := {k: v for k, v in said.items() if v != roles.MIXED}:
        params["origins"] = chosen
    if allow:
        params["allow"] = [int(i) for i in allow]
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
