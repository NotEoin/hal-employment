"""Copies of one project in a dump: which project folders are the same project, and which copy the sorter keeps.

`sections.sorter` proposes one row a project folder. A real 10,000-file dump (2026-11-09) held the same
project as "repos/portfolio-repos/Hal", "personal projects/Hal" and "repos/unversioned/Hal-12gb", and pairs
such as "canvas downloader" and "canvas-downloader", most of them with no git folder, so the git remote could
not tell them apart and each was proposed. `mark` marks all copies but one.

Design decisions, each with its reason:

- Two folders are one project when their names are one once normalised (`copy_name`: case, separators, and
  copy marks such as "-main", "-v3", "-final", "-fixed", "(2)" or a size like "-12gb"). Copies kept for
  years differ in content, so a real dump's "Hal", "Hal-12gb", "sprite_game" and "sprite-game" copies were
  not grouped when their content had to overlap (2026-11-09); the name is enough.
- A generic name (`GENERIC`: "website", "app", "project", "notes" and the like) is not: such folders are one
  project only when their files' content overlaps (`same_content`: `SAME_SHARE` of the smaller copy's files
  by SHA-256, boilerplate left out), their file paths overlap (`versions.shared`), or their READMEs say the
  same (`README_SAME` of their words, each README with `README_WORDS_MIN` or more). So two unrelated
  "website" folders stay two.
- Folders of different names are one project when their READMEs' first headings are one once normalised
  (`heading`), that heading is not generic, and its words are all in each folder's name:
  "BOIN-LIDAR-Autonavigation-System" is a copy of "Lidar-Autonavigation-System" only when its README says
  so, and a template's heading ("Getting Started with Create React App") names no folder. A prefix alone
  never makes a copy.
- A bare number is not a copy mark, unlike in the bulk read (`versions.base_name`), and two folders whose
  names differ only by numbers are never one project, nor in one group (`numbered_apart`): "lab1" and "lab2"
  are two exercises (`sort_units.series`), not one project saved twice.
- The same git remote makes two folders one project whatever their names; two different remotes are two
  projects, as a fork is the person's own work.
- The copy kept is one outside an "old", "archive", "backup", "unversioned" or "copies" folder (`ARCHIVED`)
  when there is one, then the one with the newest file (`versions.stamp`), then the one with more files: a
  copy put away was put away on purpose, and copying a folder keeps its files' times. The review shows the
  others as "Older copies" on the row kept, where the person can keep another (`sorts.keep_copy`).
- Files are hashed only for folders that share a generic name with another, each up to `HASH_BYTES_MAX`,
  and only a README's first `HEADING_BYTES` are read for its heading, so a dump of thousands of projects
  reads little.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Protocol

from jobhunt.ingest import discover, versions

log = logging.getLogger(__name__)

SEPARATORS = re.compile(r"[\s_\-.]+")
LEADING = re.compile(r"^(?:copy of|old|new|backup of)\s+")
# Marks people add to a project's folder when they copy it; never a bare number (see the module notes).
MARK = re.compile(r"(?:\s+|^)(?:\(\d+\)|copy|v\d+|version\s*\d*|final|fixed|old|backup|bak|new|latest|main|master|"
                  r"orig|original|working|refactored|\d+\s*[kmgt]b)$")
ARCHIVED = re.compile(r"^(?:old|archived?|archives|backups?|unversioned|copies)(?![a-z])", re.I)
# Names many unrelated projects share, normalised by `copy_name`: a name like these says nothing on its own.
GENERIC = {"website", "web", "site", "web site", "my website", "personal website", "homepage", "blog", "app",
           "apps", "my app", "project", "projects", "my project", "final project", "group project", "code", "src",
           "source", "test", "tests", "testing", "demo", "example", "examples", "sample", "notes", "homework",
           "assignment", "coursework", "portfolio", "frontend", "backend", "api", "server", "client", "game",
           "bot", "scripts", "script", "tools", "utils", "misc", "untitled", "new folder", "temp", "tmp",
           "practice", "playground", "sandbox", "lab", "labs", "docs", "documentation", "data", "hello world",
           "readme", "template", "starter"}
NAME_MIN = 3                   # a name of one or two letters ("ai", "js") names no one project
HEADING = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$|^(.+)\n=+\s*$", re.M)
HEADING_BYTES = 4096           # a README's first heading is at its top
SAME_SHARE = versions.SAME_SHARE        # half the smaller copy's files, as the bulk read asks
README_SAME = 0.6              # a README rewritten a little keeps most of its words; another project's does not
README_WORDS_MIN = 8           # words of 3 letters or more; a title and a line ("# Website ...") name no project
HASH_BYTES_MAX = 4 * 1024 * 1024        # a source file is far smaller; a bigger one is data, not what names a copy
CHUNK = 1024 * 1024


class Unit(Protocol):
    """A project row as `mark` reads and marks it (`sorter.Proposal`)."""

    path: Path
    rel: str
    files: list[Path]
    state: str
    copy_of: str
    why: str


def copy_name(name: str) -> str:
    """A project folder's name without case, separators or copy marks: "Hal-12gb" and "hal" are "hal"."""
    name = SEPARATORS.sub(" ", name.lower().removesuffix(".zip")).strip()
    name = LEADING.sub("", name)
    while (cut := MARK.sub("", name).strip()) != name and cut:
        name = cut
    return name


def generic(name: str) -> bool:
    """True when a `copy_name` is shared by many unrelated projects ("website") or too short to name one."""
    return name in GENERIC or len(name.replace(" ", "")) < NAME_MIN


def numbered_apart(a: str, b: str) -> bool:
    """True when two names differ only by their numbers: "lab1" and "lab2", "practical-1" and "practical-2"."""
    return a != b and re.sub(r"\d+", "#", a) == re.sub(r"\d+", "#", b)


def heading(u: Unit) -> str:
    """A project folder's README's first heading as `copy_name` gives it; "" with no README or heading."""
    readme = next((f for f in u.files if f.parent == u.path and discover.has_readme([f.name])), None)
    if readme is None:
        return ""
    try:
        with readme.open("rb") as f:
            text = f.read(HEADING_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    m = HEADING.search(text)
    return copy_name((m.group(1) or m.group(2)).strip()) if m else ""


def archived(rel: str) -> bool:
    """True when a folder at `rel` sits in an old, archive, backup, unversioned or copies folder."""
    return any(ARCHIVED.match(part.strip()) for part in PurePosixPath(rel).parts[1:-1])


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    try:
        if path.stat().st_size > HASH_BYTES_MAX:
            return ""
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
    except OSError as e:     # a file gone or unreadable mid-sort says nothing about which project it is
        log.debug("left %s out of copy detection: %s", path, e)
        return ""
    return h.hexdigest()


def _hashes(u: Unit) -> set[str]:
    return {d for f in u.files if f.name.lower() not in versions.BOILERPLATE and (d := _digest(f))}


def same_content(a: set[str], b: set[str]) -> bool:
    """True when two copies' file hashes share `SAME_SHARE` of the smaller set, and at least one."""
    both = len(a & b)
    return both >= 1 and both >= SAME_SHARE * min(len(a), len(b))


def _readme_words(u: Unit) -> set[str]:
    readme = next((f for f in u.files if f.parent == u.path and discover.has_readme([f.name])), None)
    try:
        return set(re.findall(r"[a-z0-9]{3,}", readme.read_text(encoding="utf-8", errors="replace").lower())) \
            if readme else set()
    except OSError:
        return set()


def _readmes_same(a: set[str], b: set[str]) -> bool:
    return min(len(a), len(b)) >= README_WORDS_MIN and len(a & b) >= README_SAME * max(len(a), len(b))


def _facts(u: Unit, seen: dict) -> tuple[set[str], set[str], set[str]]:
    """A copy's file hashes, file paths and README words, worked out once a sort."""
    if u.rel not in seen:
        seen[u.rel] = (_hashes(u), {f.relative_to(u.path).as_posix() for f in u.files}, _readme_words(u))
    return seen[u.rel]


def _same(a: Unit, b: Unit, seen: dict, by: str) -> bool:
    """True when `a` and `b`, of one normalised name or (`by` "heading") README heading, are one project (see
    the module notes)."""
    ra, rb = versions.remote(a.path), versions.remote(b.path)
    if ra and rb:
        return ra == rb
    if by == "heading" or not generic(copy_name(a.path.name)):
        return True
    (ha, pa, wa), (hb, pb, wb) = _facts(a, seen), _facts(b, seen)
    return same_content(ha, hb) or versions.shared(pa, pb) or _readmes_same(wa, wb)


def _keep(group: list[Unit]) -> Unit:
    """The copy kept: one not put away, then the newest file, then the most files."""
    def key(u: Unit) -> tuple:
        stamp = versions.stamp(u.path)
        return (not archived(u.rel), stamp.newest_file, stamp.files, u.rel)
    return max(group, key=key)


def mark(units: list[Unit], copy_state: str) -> None:
    """Marks every copy of a project among `units` but the one kept as `copy_state`, naming the one kept."""
    parent = {u.rel: u.rel for u in units}

    def root(rel: str) -> str:
        while parent[rel] != rel:
            parent[rel] = parent[parent[rel]]
            rel = parent[rel]
        return rel

    names = {u.rel: copy_name(u.path.name) for u in units}
    joined = {u.rel: [names[u.rel]] for u in units}       # each group's names, so no group joins lab1 and lab2

    def apart(x: str, y: str) -> bool:
        return any(numbered_apart(m, n) for m in joined[x] for n in joined[y])

    buckets: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        buckets[f"name:{names[u.rel]}"].append(u)
        if found := versions.remote(u.path):
            buckets[f"remote:{found}"].append(u)
        if (title := heading(u)) and not generic(title) and set(title.split()) <= set(names[u.rel].split()):
            buckets[f"heading:{title}"].append(u)
    seen: dict = {}
    for key, members in buckets.items():
        by = key.split(":", 1)[0]
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                ra, rb = root(a.rel), root(b.rel)
                if ra != rb and not apart(ra, rb) and (by == "remote" or _same(a, b, seen, by)):
                    parent[ra] = rb
                    joined[rb] += joined.pop(ra)
    groups: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        groups[root(u.rel)].append(u)
    for group in groups.values():
        if len(group) < 2:
            continue
        kept = _keep(group)
        for u in group:
            if u is not kept:
                u.state, u.copy_of, u.why = copy_state, kept.rel, f"Another copy of {kept.rel}, which Hal keeps."
