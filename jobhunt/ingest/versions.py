"""Copies of one code project: which repository folders are the same project, and which copy is newest.

People keep a project several times over ("Tidewatch", "Tidewatch (2)", "tidewatch-v3-final", the same
repository under two folders, a copy inside a zip). Only the newest copy is read; the others become
`older_version` items with no note and no facts, named under "Earlier versions" in the newest copy's note.
A newer copy found by a later ingest rewrites the project's note in place, so links to it still hold.

`settle` runs before a batch's repositories are read and `record_earlier` after; the engine calls nothing
else here.

Design decisions, each with its reason:

- Two folders are one project, in order of trust: the same git remote (`.git/config`), which names the
  project whatever its folder is called; when either has no remote, related names once copy marks are
  removed (`base_name`), confirmed by shared files (`SAME_SHARE`), so two unrelated "website" folders stay
  apart. Two different remotes are two projects, even under one name: a fork is the person's own work.
- Names are related when one's words hold the other's ("lidar" and "lidar autonavigation system"): a
  shortened folder name is common, and the shared files are what decide.
- Boilerplate any project has (README, LICENSE, .gitignore) never counts as a shared file, and at least
  `MIN_SHARED` other files must be shared, so two tiny projects are not one by their READMEs.
- The newest copy is the one with the latest commit (`.git/logs/HEAD`, read as a file, so no git program is
  run), because copying a folder resets its files' times but not its history. Without a history the
  newest file's time is used (junk folders left out, as an installed dependency is not the person's
  work); then the newest file, then more files, break ties.
- The time used is kept in the item's `mtime`, so a later ingest compares against the copy as it was read,
  even when that folder was a zip's, unpacked and since removed, or is the same folder changed since. An
  earlier copy whose folder is gone is compared by the file tree its note keeps (`convert.repo_text`).
- A newer copy at the same path is the same folder read again: it rewrites the note but is not listed as an
  earlier version of itself; an unchanged one is a `duplicate`, as a file read again is.
"""
from __future__ import annotations

import configparser
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from jobhunt.core import clock
from jobhunt.ingest import convert, discover, items, notes, sources

log = logging.getLogger(__name__)

EARLIER = "## Earlier versions"
STATE = "older_version"
SAME_SHARE = 0.5            # half the smaller copy's files: a copy keeps most of them, a namesake almost none
MIN_SHARED = 2              # one shared file besides boilerplate could be a coincidence, two rarely are
BOILERPLATE = {"readme", "readme.md", "readme.rst", "readme.txt", "license", "license.md", "license.txt",
               "licence", "licence.md", ".gitignore", ".gitattributes", ".editorconfig", "makefile",
               "requirements.txt", "package.json", "pyproject.toml", "setup.py", "dockerfile",
               "changelog.md", "contributing.md", "index.html", "main.py", "app.py"}
# Marks people add to a copy's name, at its end (or "copy of" at its start); removed until none is left.
# A number on its own covers "(2)" once brackets are gone, "v3.1" as "v3 1" and dates such as "2024-05-01";
# "main" and "master" end the folder of a repository downloaded from GitHub as a zip.
MARK = re.compile(r"(?:\s+|^)(?:\(\d+\)|\d+|copy|v\d+|version|final|fixed|old|backup|bak|new|latest|"
                  r"refactored|refactor|working|orig|original|main|master)$")
LEADING = re.compile(r"^(?:copy of|old|new|backup of)\s+")
SEPARATORS = re.compile(r"[\s_\-.]+")
FILES_HEADING = "## Files"


@dataclass(frozen=True)
class Stamp:
    """When a copy last changed: its last commit (None without a history), its newest file, and its files."""

    commit: float | None
    newest_file: float
    files: int

    def key(self) -> tuple[float, float, int]:
        """Sorts copies oldest first."""
        return (self.commit if self.commit is not None else self.newest_file, self.newest_file, self.files)


# Who the project is ---------------------------------------------------------------------------------

def normal_remote(url: str) -> str:
    """A git remote as host/path in lower case, whether written as https, ssh or scp form, with or
    without `.git`."""
    url = url.strip().lower().rstrip("/")
    url = re.sub(r"^[a-z+]+://", "", url)
    url = re.sub(r"^[^@/]+@", "", url)
    url = re.sub(r"^([^/:]+):(?!\d+/)", r"\1/", url)       # scp form, git@host:user/repo
    url = re.sub(r":\d+/", "/", url)                       # a port
    return url.removesuffix(".git")


def remote(folder: Path) -> str:
    """The folder's git remote, normalised (`normal_remote`); "origin" first, else the first one; "" for
    none."""
    config = folder / ".git" / "config"
    if not config.is_file():
        return ""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(config.read_text(encoding="utf-8", errors="replace"))
    except configparser.Error as e:
        log.info("could not read %s: %s", config, e)
        return ""
    names = [s for s in parser.sections() if s.startswith('remote "') and parser.has_option(s, "url")]
    names.sort(key=lambda s: s != 'remote "origin"')
    return normal_remote(parser.get(names[0], "url")) if names else ""


def base_name(name: str) -> str:
    """A folder's name without its copy marks: "Tidewatch-v3-final" and "tidewatch (2)" are "tidewatch"."""
    name = SEPARATORS.sub(" ", name.lower().removesuffix(".zip")).strip()
    name = LEADING.sub("", name)
    while (cut := MARK.sub("", name).strip()) != name and cut:
        name = cut
    return name


def project_key(folder: Path) -> str:
    """What names the project: "remote:<host/path>" from its git remote, else "name:<base name>".

    A name key alone is not enough to call two folders one project; `same` also asks for shared files.
    """
    found = remote(folder)
    return f"remote:{found}" if found else f"name:{base_name(folder.name)}"


def _related(a: str, b: str) -> bool:
    wa, wb = set(a.split()), set(b.split())
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def files_of(folder: Path) -> set[str]:
    """The folder's files as paths inside it, without junk folders and binaries."""
    return {p.relative_to(folder).as_posix() for p in convert.repo_files(folder)}


def _cut(paths: set[str]) -> set[str]:
    """The paths as a note's file tree shows them (`convert.TREE_DEPTH` parts)."""
    return {"/".join(PurePosixPath(p).parts[:convert.TREE_DEPTH]) for p in paths}


def shared(a: set[str], b: set[str]) -> bool:
    """True when two file lists share at least `SAME_SHARE` of the smaller one, boilerplate left out."""
    a = {p for p in a if PurePosixPath(p).name.lower() not in BOILERPLATE}
    b = {p for p in b if PurePosixPath(p).name.lower() not in BOILERPLATE}
    both = len(a & b)
    return both >= MIN_SHARED and both >= SAME_SHARE * min(len(a), len(b))


# How new each copy is -------------------------------------------------------------------------------

def last_commit(folder: Path) -> float | None:
    """The time of the folder's last commit, checkout or pull, from `.git/logs/HEAD`; None without one."""
    log_file = folder / ".git" / "logs" / "HEAD"
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    times = []
    for line in lines:
        # "<old> <new> Name <email> <unix time> <zone>\t<message>" (git-reflog format)
        found = re.search(r"> (\d+) [+-]\d{4}", line.split("\t", 1)[0])
        if found:
            times.append(float(found.group(1)))
    return max(times) if times else None


def stamp(folder: Path) -> Stamp:
    """When the copy last changed (`Stamp`)."""
    newest, count = 0.0, 0
    for here, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not discover.is_junk_dir(d, in_repo=True)]
        for name in names:
            if discover.is_binary_name(name):
                continue
            try:
                newest = max(newest, (Path(here) / name).stat().st_mtime)
            except OSError as e:   # a file gone or unreadable mid-walk says nothing about the copy's age
                log.debug("left %s out of the copy's time: %s", name, e)
                continue
            count += 1
    return Stamp(last_commit(folder), newest, count)


def newest(folders: list[Path]) -> Path:
    """The newest copy of a project among `folders` (see `Stamp`); ValueError for none."""
    if not folders:
        raise ValueError("newest needs at least one folder")
    return max(folders, key=lambda f: stamp(f).key())


# One batch ------------------------------------------------------------------------------------------

@dataclass
class _Copy:
    """One copy taking part in a batch's grouping: a pending repository item, or an earlier note's item."""

    row: sqlite3.Row
    pending: bool
    name: str
    remote: str = ""
    files: set[str] | None = None
    from_note: bool = False
    key: tuple[float, float, int] = (0.0, 0.0, 0)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tree_of(vault: Path, note_rel: str) -> set[str]:
    """The file tree an earlier copy's note keeps, for a copy whose folder is gone."""
    path = vault / note_rel
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    if FILES_HEADING not in text:
        return set()
    tree = text.rsplit(FILES_HEADING, 1)[1].split("\n## ", 1)[0]
    return {line.strip() for line in tree.splitlines() if line.strip() and not line.startswith("... and ")}


def _copy(row: sqlite3.Row, pending: bool, vault: Path, pending_paths: set[str]) -> _Copy:
    folder = Path(row["path"])
    c = _Copy(row, pending, base_name(PurePosixPath(row["rel"]).name))
    on_disk = folder.is_dir()
    if on_disk:
        c.remote, c.files = remote(folder), files_of(folder)
    else:
        c.files, c.from_note = _tree_of(vault, row["note"]), True
    if pending:
        c.key = stamp(folder).key()
    elif row["mtime"]:
        at = clock.parse(row["mtime"]).timestamp()
        c.key = (at, at, 0)
    elif on_disk and row["path"] not in pending_paths:   # a note from before copies were compared
        c.key = stamp(folder).key()
    return c


def same(a: _Copy, b: _Copy) -> bool:
    """True when two copies are one project (see the module's header)."""
    if a.row["path"] == b.row["path"]:
        return True
    if a.remote and b.remote:
        return a.remote == b.remote
    if not _related(a.name, b.name):
        return False
    fa, fb = a.files or set(), b.files or set()
    if a.from_note or b.from_note:
        fa, fb = _cut(fa), _cut(fb)
    return shared(fa, fb)


def _groups(copies: list[_Copy]) -> list[list[_Copy]]:
    parent = list(range(len(copies)))

    def top(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(copies):
        for j in range(i + 1, len(copies)):
            b = copies[j]
            if (a.pending or b.pending) and top(i) != top(j) and same(a, b):
                parent[top(j)] = top(i)
    out: dict[int, list[_Copy]] = {}
    for i, c in enumerate(copies):
        out.setdefault(top(i), []).append(c)
    return list(out.values())


def _earlier_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """The newest done row of each repository note already written, in any batch."""
    return con.execute("""SELECT * FROM ingest_items WHERE id IN (SELECT max(id) FROM ingest_items
                          WHERE kind = 'repo' AND state = 'done' AND note IS NOT NULL GROUP BY note)
                          ORDER BY id""").fetchall()


def settle(con: sqlite3.Connection, batch: str, vault: Path) -> int:
    """Leaves only the newest copy of each project in `batch` to be read; returns how many copies were set
    aside.

    Each pending repository gets its time in `mtime`. An older copy becomes `older_version` (`dup_of` the
    newest, no note); a newest copy of a project with a note already is given that note, so it is read
    over it in place (`engine._again`).
    """
    todo = con.execute("SELECT * FROM ingest_items WHERE batch = ? AND kind = 'repo' AND state = 'pending' "
                       "ORDER BY id", (batch,)).fetchall()
    if not todo:
        return 0
    paths = {r["path"] for r in todo}
    copies = [_copy(r, True, vault, paths) for r in todo]
    copies += [_copy(r, False, vault, paths) for r in _earlier_rows(con)]
    aside = 0
    for c in copies:
        if c.pending:
            items.update(con, c.row["id"], mtime=_iso(c.key[0]))
    for group in _groups(copies):
        if not any(c.pending for c in group):
            continue
        earlier = [c for c in group if not c.pending]
        known = max(earlier, key=lambda c: c.key[0]) if earlier else None
        best = max((c for c in group if c.pending), key=lambda c: c.key)
        # whole seconds, as `mtime` keeps them: an unchanged folder read again is not newer than itself
        if known is not None and int(best.key[0]) <= int(known.key[0]):
            best = known          # not newer than the copy already read: its note stands
        for c in group:
            if not c.pending or c is best:
                continue
            same_folder = known is not None and c.row["path"] == known.row["path"] and best is known
            items.update(con, c.row["id"], state="duplicate" if same_folder else STATE, dup_of=best.row["id"],
                         note=best.row["note"] if same_folder else None)
            aside += 1
        if best.pending and known is not None:
            items.update(con, best.row["id"], note=known.row["note"])
    con.commit()
    return aside


def _where(row: sqlite3.Row) -> str:
    """Where a copy was, as a note names it: its path, or its place in a zip."""
    unpacked = sources.unpacked_dir(row["batch"]).resolve()
    return row["rel"] if unpacked in Path(row["path"]).resolve().parents else row["path"]


def _write_earlier(vault: Path, note_rel: str, places: list[str]) -> None:
    path = vault / note_rel
    if not path.is_file() or not places:
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    section = EARLIER + "\n\n" + "".join(f"- `{p}`\n" for p in places) + "\n"
    found = re.search(rf"^{re.escape(EARLIER)}\n.*?(?=^## |\Z)", text, re.S | re.M)
    if found:
        text = text[:found.start()] + section + text[found.end():]
    else:
        text = text.replace("\n## Original\n", f"\n{section}## Original\n", 1)
    notes.write(path, text)


def record_earlier(con: sqlite3.Connection, batch: str, vault: Path) -> None:
    """Names every older copy of each project this batch touched under "Earlier versions" in its note.

    A copy read before whose note a newer one rewrote becomes `older_version` of the newer one here, once
    the newer one is read: an error reading it leaves the earlier copy as the project's note.
    """
    targets = {r["dup_of"] for r in con.execute(
        "SELECT dup_of FROM ingest_items WHERE batch = ? AND state = ? AND dup_of IS NOT NULL", (batch, STATE))}
    targets |= {r["id"] for r in con.execute(
        "SELECT id FROM ingest_items WHERE batch = ? AND kind = 'repo' AND state = 'done' AND note IS NOT NULL",
        (batch,))}
    for target in sorted(targets):
        row = con.execute("SELECT * FROM ingest_items WHERE id = ?", (target,)).fetchone()
        if row is None or row["state"] != "done" or not row["note"]:
            continue
        replaced = [r["id"] for r in con.execute(
            "SELECT id FROM ingest_items WHERE kind = 'repo' AND state = 'done' AND note = ? AND id < ?",
            (row["note"], target))]
        for old in replaced:
            items.update(con, old, state=STATE, dup_of=target)
        if replaced:
            marks = ",".join("?" * len(replaced))
            con.execute(f"UPDATE ingest_items SET dup_of = ? WHERE state = ? AND dup_of IN ({marks})",
                        (target, STATE, *replaced))
        older = con.execute("SELECT * FROM ingest_items WHERE state = ? AND dup_of = ? ORDER BY mtime DESC, id",
                            (STATE, target)).fetchall()
        here = _where(row)
        places = list(dict.fromkeys(p for p in (_where(r) for r in older) if p != here))
        _write_earlier(vault, row["note"], places)
    con.commit()

