"""Project folders: one project for a folder of the person's own work, however many notes it holds.

`group.projects` finds the folders (a README at the top, in a folder of the person's own work or a mix, not a
repository, not a collection of projects). This module gives each its name, its note and its facts:

- The name (`name_of`) is the README's first heading, else the folder's name.
- A project fact read in a document inside the folder (`facts_in`) is a fact about the folder's project:
  its name becomes the project's, and it keeps only the tools and links it gives, so a ticket, a plan or a
  write-up is evidence of the one project card, never a card of its own. The README keeps its summary, role
  and dates too, as it says what the whole project is. Notes inside still give skills and quotes. A real
  research workspace gave about 180 project cards, one per ticket and write-up (2026-11-08).
- Two kinds of document keep their project facts as they are: a CV, letter or transcript (`LISTS_PROJECTS`),
  which lists all the person's projects wherever it is kept, and a document whose project is plainly another
  one with a unit of its own (another project folder, or a repository).
- Each folder gets one note (`write_notes`) listing its files' notes, which link back to it under "Part of",
  as a course's do; its data files and figures are listed in it (`attach`).
- A tidy (`find_in_record`, `fold_cards`) holds a record an older read filed to the same rules: it finds the
  project folders of the files read before, takes them out of the course units they were split into, and
  files each project fact from inside again on the folder's project card, hiding a card left with nothing
  as "Part of <project>" (`store.fold_away`). Cards the person checked are never touched.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import sqlite3
from pathlib import Path, PurePosixPath

from jobhunt.ingest import discover, group, items, notes, rank, roles

log = logging.getLogger(__name__)

KIND = "project"
RULE = rank.RULES[7]           # a project's rank, kind and folder in the record
README_CHARS = 4000            # the title is the first heading, near the top
NAME_MAX = 80                  # a heading longer than this is a sentence, not a project's name
GENERIC_TITLES = {"readme", "overview", "introduction", "about", "index", "contents", "notes"}
HEADING = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)
MARKUP = re.compile(r"[*_`]|\[([^\]]*)\]\([^)]*\)")
# Documents that list the person's projects wherever they are kept: their project facts stay as they are.
LISTS_PROJECTS = ("cv", "letter", "transcript")
KEPT = ("tools", "link")                                       # what a note inside the folder adds
KEPT_TOP = ("summary", "role", "period", *KEPT)                # what the README adds
PART_OF = "\n## Part of\n"
ORIGINAL = "\n## Original\n"
WHY = "Part of {name}"


@functools.lru_cache(maxsize=256)
def name_of(folder: str) -> str:
    """A project folder's name: its README's first heading, else the folder's own name."""
    path = Path(folder)
    try:
        readme = next((p for p in sorted(path.iterdir()) if p.is_file() and discover.has_readme([p.name])), None)
    except OSError:
        readme = None
    if readme is not None:
        try:
            with readme.open(encoding="utf-8", errors="replace") as f:
                found = HEADING.search(f.read(README_CHARS))
        except OSError:
            found = None
        title = " ".join(MARKUP.sub(r"\1", found.group(1)).split()) if found else ""
        if title and len(title) <= NAME_MAX and title.lower() not in GENERIC_TITLES:
            return title
    return path.name


def _units(con: sqlite3.Connection) -> dict[Path, str]:
    """Every project folder a read found, by folder, with its name."""
    rows = con.execute("SELECT DISTINCT path FROM ingest_items WHERE kind = ? AND state IN ('pending', 'done')",
                       (KIND,)).fetchall()
    return {Path(r[0]): name_of(r[0]) for r in rows}


def _repo_names(con: sqlite3.Connection) -> list[str]:
    return [Path(r[0]).name for r in con.execute("SELECT DISTINCT path FROM ingest_items WHERE kind = 'repo' "
                                                 "AND state = 'done'")]


def _other(name: str, own: str, others: list[str]) -> bool:
    """True when a project named `name` is plainly one of `others`, not the folder's own project `own`."""
    from jobhunt.profile import merge

    def same(a: str, b: str) -> bool:
        return merge.same_item(KIND, {"name": a}, {"name": b})

    return bool(name) and not same(name, own) and any(same(name, o) for o in others if o)


def redirect(fields: dict, own: str, others: list[str], top: bool) -> dict:
    """A project fact read inside the project folder named `own`, as a fact about that project (see the module
    notes); `fields` as they are when they plainly name one of `others`."""
    if _other(str(fields.get("name") or ""), own, others):
        return fields
    return {"name": own, **{k: v for k, v in fields.items() if k in (KEPT_TOP if top else KEPT) and v}}


def _is_top(path: Path, folder: Path) -> bool:
    return path.parent == folder and discover.has_readme([path.name])


def facts_in(con: sqlite3.Connection, row: sqlite3.Row | dict, desc: dict) -> list[dict]:
    """The document's facts, its project facts as facts about the project folder it is in (`redirect`)."""
    from jobhunt.profile import merge

    facts = desc["facts"]
    if row["kind"] != "file" or merge.doc_group(desc["doc_type"]) in LISTS_PROJECTS \
            or not any(f["kind"] == KIND for f in facts):
        return facts
    units = _units(con)
    path = Path(row["path"])
    folder = group.project_of(path, set(units))
    if folder is None:
        return facts
    others = [n for f, n in units.items() if f != folder] + _repo_names(con)
    top = _is_top(path, folder)
    return [{**f, "fields": redirect(f["fields"], units[folder], others, top)} if f["kind"] == KIND else f
            for f in facts]


def _under(folder: Path) -> tuple[int, str]:
    """The SQL arguments that pick the paths inside `folder`: their length and start."""
    start = str(folder) + os.sep
    return len(start), start


def part_of(path: Path, link: str) -> None:
    """Names the note of the unit a note is part of under "## Part of", in place of any named before."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    line = f"- {link}"
    if line in text:
        return
    head, sep, tail = text.partition(ORIGINAL)
    if not sep:
        return
    kept, skipping = [], False
    for each in head.split("\n"):
        if each == PART_OF.strip():
            skipping = True                # the course unit an older read named goes; this unit takes its place
        elif skipping and not each.startswith("- ") and each:
            skipping = False
        if not skipping:
            kept.append(each)
    notes.write(path, "\n".join(kept).rstrip("\n") + f"\n{PART_OF}\n{line}\n{sep}{tail}")


def _note_of(con: sqlite3.Connection, folder: Path, vault: Path) -> str | None:
    """The note a read before wrote for this project folder, when it is still there."""
    for r in con.execute("SELECT note FROM ingest_items WHERE kind = ? AND path = ? AND state = 'done' "
                         "AND note IS NOT NULL ORDER BY id DESC", (KIND, str(folder))):
        if (vault / r[0]).is_file():
            return r[0]
    return None


def write_notes(con: sqlite3.Connection, batch: str, vault: Path) -> list[str]:
    """Writes each project folder's note, listing its files' notes, and links them back to it; the notes
    written. A folder whose files gave no note gets none."""
    from jobhunt.ingest import understand

    written = []
    for unit in con.execute("SELECT * FROM ingest_items WHERE batch = ? AND kind = ? AND state = 'pending'",
                            (batch, KIND)).fetchall():
        folder = Path(unit["path"])
        size, start = _under(folder)
        members = con.execute("""SELECT rel, note FROM ingest_items WHERE state = 'done' AND note IS NOT NULL
                                 AND kind IN ('file', 'repo') AND substr(path, 1, ?) = ?
                                 GROUP BY note ORDER BY rel""", (size, start)).fetchall()
        if not members:
            items.update(con, unit["id"], state="no_text")
            continue
        name = name_of(str(folder))
        lines = [f"- {notes.link(m['note'])}" for m in members]
        note_rel = _note_of(con, folder, vault)
        if note_rel is not None:
            path = vault / note_rel
            text = path.read_text(encoding="utf-8")
            new = [line for line in lines if line not in text]
            if new:
                notes.write(path, text.replace("## Files\n\n", "## Files\n\n" + "\n".join(new) + "\n", 1))
        else:
            desc = understand.light(unit["rel"] + "/", name, RULE)
            desc["title"] = name
            desc["summary"] = f"{len(members)} files from {name}."
            note_rel = notes.write_source(vault, desc, text="\n".join(["## Files", "", *lines]), source=str(folder),
                                          sha256="", original=None)
        for m in members:
            part_of(vault / m["note"], notes.link(note_rel, name))
        items.update(con, unit["id"], stage="written", state="done", note=note_rel)
        written.append(note_rel)
    return written


# Tidying a record an older read filed ---------------------------------------------------------------------

def _root(path: Path, rel: str) -> Path | None:
    """The folder the person gave, from a file's path and its `rel` inside it; None when they do not fit."""
    depth = len(PurePosixPath(rel).parts) - 1
    return path.parents[depth - 1] if 0 < depth <= len(path.parents) else None


def find_in_record(con: sqlite3.Connection, batch: str, vault: Path) -> int:
    """Finds the project folders of the files older reads filed and puts them in the batch, to get their notes;
    their files leave the course units they were split into, whose notes go. Returns how many were found."""
    from jobhunt.ingest import tidy

    rows = con.execute("""SELECT * FROM ingest_items WHERE kind = 'file' AND state = 'done' AND note IS NOT NULL
                          AND origin != ?""", (roles.COURSE,)).fetchall()
    roots: dict[Path, tuple[str, str]] = {}
    for r in rows:
        root = _root(Path(r["path"]), r["rel"])
        if root is not None and root not in roots and root.is_dir():
            roots[root] = (PurePosixPath(r["rel"]).parts[0], r["origin"])
    known = {Path(r[0]) for r in con.execute("SELECT path FROM ingest_items WHERE kind = ? AND state = 'done'",
                                             (KIND,))}
    found = 0
    for root, (top, origin) in roots.items():
        walked = discover.walk(root, top)
        for folder in sorted(group.projects(walked.readmes, walked.repos)):
            shown = top if folder == root else f"{top}/{folder.relative_to(root).as_posix()}"
            if folder not in known:
                items.add(con, batch, path=str(folder), rel=shown, kind=KIND, rank=RULE.rank,
                          category=RULE.category, doc_type=RULE.doc_type, origin=origin)
                found += 1
            size, start = _under(folder)
            for course in con.execute("""SELECT id, note FROM ingest_items WHERE kind = 'course' AND state = 'done'
                                         AND (path = ? OR substr(path, 1, ?) = ?)""",
                                      (str(folder), size, start)).fetchall():
                if course["note"]:
                    tidy.drop_note(con, vault, course["note"])
                items.update(con, course["id"], state="skipped", note=None, error=f"part of {shown}")
            con.execute("UPDATE ingest_items SET unit = NULL WHERE kind = 'file' AND substr(path, 1, ?) = ?",
                        (size, start))
    con.commit()
    return found


def fold_cards(con: sqlite3.Connection) -> int:
    """Files each project fact an older read took from a document inside a project folder again on the
    folder's project card (`facts_in`), and hides each card Hal found that is left with nothing, as part of
    that project. Returns how many cards were folded."""
    from jobhunt.profile import merge, store

    units = _units(con)
    if not units:
        return 0
    others = _repo_names(con)
    paths = {r["note"]: Path(r["path"]) for r in con.execute(
        "SELECT note, path FROM ingest_items WHERE kind = 'file' AND state = 'done' AND note IS NOT NULL")}
    moved: dict[int, tuple[int, str]] = {}
    for e in con.execute("""SELECT e.id, e.item_id, e.source_rel, e.doc_type, e.said FROM profile_evidence e
                            JOIN profile_items i ON i.id = e.item_id WHERE i.kind = ? AND i.state = 'hal'
                            AND i.origin = 'found' ORDER BY e.id""", (KIND,)).fetchall():
        path = paths.get(e["source_rel"])
        folder = group.project_of(path, set(units)) if path is not None else None
        if folder is None or merge.doc_group(e["doc_type"]) in LISTS_PROJECTS:
            continue
        said = json.loads(e["said"] or "{}")
        name = units[folder]
        fields = redirect(said, name, [n for f, n in units.items() if f != folder] + others, _is_top(path, folder))
        if fields == said:
            continue
        try:
            target = store.refile(con, e["id"], fields)
        except (LookupError, ValueError) as err:   # a fact the store now refuses stays where it was
            log.info("left a project fact where it was: %s", err)
            continue
        if target != e["item_id"]:
            moved[e["item_id"]] = (target, name)
    folded = 0
    for item_id, (target, name) in moved.items():
        row = con.execute("SELECT state FROM profile_items WHERE id = ?", (item_id,)).fetchone()
        if row is None or con.execute("SELECT 1 FROM profile_evidence WHERE item_id = ? LIMIT 1",
                                      (item_id,)).fetchone():
            continue                 # a card another file still speaks for, such as a CV, stays
        store.fold_away(con, item_id, target, WHY.format(name=name))
        folded += 1
    return folded
