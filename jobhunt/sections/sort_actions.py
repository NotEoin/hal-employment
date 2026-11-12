"""The `record.sort` action, which proposes where everything in a big folder goes, and `record.sort_accept`,
which makes the accepted proposals sources and reads them (`sections.sorts`).

Sorting walks thousands of files, so it runs in the background (priority 2), one at a time
(`exclusive="kind"`), and is resumable: run again it proposes afresh and keeps what was accepted. Accepting
is what the person waits for, so it runs at priority 1; its sources are read as an upload would be
(`actions.read_all`), then the project cards they gave that were guessed showcase pieces are starred, and the
proposals still waiting for a card are matched again, as an accepted CV may have given their courses. It adds
one add (`add_id`, from `sorts.accept`); stopped, it keeps what it read and puts the rest back (`sorts.stop_add`).
For a move of an older record (`sections.move`), the cards' states are noted before the reads and the older
notes' evidence is set aside after them. Both publish a `sort` event with the sort's id, which the review
table follows.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt.core import events, runner
from jobhunt.sections import actions, sorts

SORT, ACCEPT = "record.sort", "record.sort_accept"
EXAMPLE_CV = ("# Robin Testwell\n\nCurriculum vitae\n\n## Work experience\n\nVisitor guide, Made-up Heritage "
              "Trust, 2023 to 2024.\n\n## Education\n\nBA Made-up Studies, University of Nowhere, 2021 to 2024.\n")


def _example_folder(world) -> Path:
    folder = Path(world.root) / "big-folder"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "CV.md").write_text(EXAMPLE_CV, encoding="utf-8")
    return folder


def _example(world) -> dict:
    """A made-up folder holding a CV, for gate G9."""
    from jobhunt import db

    con = db.connect()
    try:
        return {"sort_id": sorts.create(con, "folder", [str(_example_folder(world))], "big-folder")}
    finally:
        con.close()


def _accept_example(world) -> dict:
    """The made-up folder sorted, with its CV accepted, for gate G9."""
    from jobhunt import db

    con = db.connect()
    try:
        sort_id = sorts.create(con, "folder", [str(_example_folder(world))], "big-folder")
        sorts.run(con, sort_id)
        sorts.accept(con, sort_id)
        return {"sort_id": sort_id}
    finally:
        con.close()


@runner.action(SORT, area="documents", label="Sorting your folder", why="You asked Hal to sort a big folder",
               priority=2, exclusive="kind", resumable=True, example=_example)
def sort_folder(ctx, sort_id: int) -> dict:
    """Proposes a section for each file and project folder of the sort; reads nothing into the record."""
    ctx.step("Looking through your files")
    out = sorts.run(ctx.con, int(sort_id), progress=lambda n, total: ctx.progress(n, total))
    events.publish("sort", sort_id=int(sort_id))
    ctx.changed(f"Proposed a place for {out['proposed']} files and folders; {out['left_out']} left out.")
    return out


@runner.action(ACCEPT, area="documents", label="Adding what you accepted to your record",
               why="You accepted where Hal proposed your files go", priority=1, exclusive="none", resumable=True,
               example=_accept_example)
def accept_sort(ctx, sort_id: int, add_id: int | None = None) -> dict:
    """Makes the accepted proposals (of one add) sources of their sections and reads them."""
    ctx.step("Adding what you accepted")
    moving = sorts.row_of(ctx.con, sort_id)["kind"] == "move"
    if moving:
        from jobhunt.sections import move

        states = move.before_accept(ctx.con, int(sort_id))
    try:
        ids = sorts.add_accepted(ctx.con, int(sort_id), add_id, checkpoint=ctx.checkpoint)
        out = actions.read_all(ctx, ids, set(), batch=len(ids) > actions.BULK_FILES)
    except runner.Stopped:
        if add_id is not None:
            sorts.stop_add(ctx.con, int(add_id))
        events.publish("sort", sort_id=int(sort_id))
        events.publish("profile", sort_id=int(sort_id))
        raise
    out["starred"] = len(sorts.star_projects(ctx.con, int(sort_id)))
    out["matched"] = sorts.rematch(ctx.con, int(sort_id))
    if moving:
        out["set_aside"] = move.after_accept(ctx.con, int(sort_id), states)
    events.publish("sort", sort_id=int(sort_id))
    if out["matched"]:
        ctx.changed(f"{out['matched']} more files found their course or job; accept them too.")
    return out


def start(con, kind: str, roots: list[str], label: str, *, session_id: str | None = None, remote: bool = False,
          why: str | None = None) -> tuple[int, str]:
    """Makes a sort and starts it; returns (its id, the action's id). RuntimeError while another sort runs."""
    sort_id = sorts.create(con, kind, roots, label)
    return sort_id, begin(con, sort_id, session_id=session_id, remote=remote, why=why)


def begin(con, sort_id: int, *, session_id: str | None = None, remote: bool = False, why: str | None = None) -> str:
    """Starts sorting a sort already made; the action's id. RuntimeError, with the sort removed when it has never
    run, while another sort runs."""
    try:
        return runner.start(SORT, params={"sort_id": int(sort_id)}, session_id=session_id, remote=remote,
                            why=why or "You asked Hal to sort a big folder")
    except RuntimeError as e:
        if sorts.row_of(con, sort_id)["state"] == "new":
            sorts.remove(con, sort_id)
        raise RuntimeError("Hal is still sorting another folder. Try again when it is done.") from e


def queue_inbox() -> int | None:
    """Starts sorting the drop folder when it holds files no sort of it has proposed yet; the new sort's id, else
    None (nothing new, or another sort is running, which the next scheduled check follows)."""
    from jobhunt import db
    from jobhunt.ingest import sources as ingest_sources

    folder = ingest_sources.drop_folder()
    if not folder.is_dir():
        return None
    con = db.connect()
    try:
        if not sorts.inbox_has_new(con, folder):
            return None
        try:
            sort_id, _ = start(con, "inbox", [str(folder)], folder.name, why="New files in your drop folder")
        except RuntimeError:
            return None
        return sort_id
    finally:
        con.close()


def queue_accept(sort_id: int, add_id: int | None = None, *, session_id: str | None = None,
                 remote: bool = False) -> str:
    """Starts making a sort's accepted proposals (of one add) sources; the action's id, kept on the add. Its subject
    names the sort (`subject`), so a move is not finished or undone while it adds."""
    params = {"sort_id": int(sort_id)} if add_id is None else {"sort_id": int(sort_id), "add_id": int(add_id)}
    action_id = runner.start(ACCEPT, subject=subject(sort_id), params=params, session_id=session_id, remote=remote,
                             why="You accepted where Hal proposed your files go")
    if add_id is not None:
        from jobhunt import db

        con = db.connect()
        try:
            sorts.set_add_action(con, int(add_id), action_id)
        finally:
            con.close()
    return action_id


def subject(sort_id: int) -> str:
    """The subject of the action adding a sort's accepted proposals."""
    return f"sort:{int(sort_id)}"


def adding(sort_id: int) -> bool:
    """True while Hal is adding what was accepted in this sort."""
    return runner.running_for(subject(sort_id)) is not None
