"""Copies of one project in a dump: which project folders are the same project, and which copy the sorter keeps.

`sections.sorter` proposes one row a project folder. A real 10,000-file dump (2026-11-09) held the same
project as "repos/portfolio-repos/Hal", "personal projects/Hal" and "repos/unversioned/Hal-12gb", and pairs
such as "canvas downloader" and "canvas-downloader", most of them with no git folder, so the git remote could
not tell them apart and each was proposed. `mark` marks all copies but one.

Design decisions, each with its reason:

- Two folders are one project when their names are one once normalised (`copy_name`: case, separators, and
  copy marks such as "-main", "-v3", "-final", "-fixed", "(2)" or a size like "-12gb"), and either their
  files' content overlaps (`same_content`: `SAME_SHARE` of the smaller copy's files by SHA-256, boilerplate
  left out), their file paths overlap (`versions.shared`), or, when the file sets differ, their READMEs say
  the same (`README_SAME` of their words, each README with `README_WORDS_MIN` or more). A name alone is
  never enough, so two unrelated "website" folders stay two.
- A bare number is not a copy mark, unlike in the bulk read (`versions.base_name`): "lab1" and "lab2" are
  two exercises (`sort_units.series`), not one project saved twice.
- The same git remote makes two folders one project whatever their names; two different remotes are two
  projects, as a fork is the person's own work.
- The copy kept is one outside an "old", "archive", "backup" or "unversioned" folder (`ARCHIVED`) when there
  is one, then the one with the newest file (`versions.stamp`), then the one with more files: a copy put
  away was put away on purpose, and copying a folder keeps its files' times.
- Files are hashed only for folders that share a name with another, each up to `HASH_BYTES_MAX`, so a dump
  of thousands of projects reads only the few that may be copies.
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
ARCHIVED = re.compile(r"^(?:old|archived?|archives|backups?|unversioned)(?![a-z])", re.I)
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


def archived(rel: str) -> bool:
    """True when a folder at `rel` sits in an old, archive, backup or unversioned folder."""
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


def _same(a: Unit, b: Unit, seen: dict) -> bool:
    """True when `a` and `b`, of one normalised name, are one project (see the module notes)."""
    ra, rb = versions.remote(a.path), versions.remote(b.path)
    if ra and rb:
        return ra == rb
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

    buckets: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        buckets[f"name:{copy_name(u.path.name)}"].append(u)
        if found := versions.remote(u.path):
            buckets[f"remote:{found}"].append(u)
    seen: dict = {}
    for key, members in buckets.items():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if root(a.rel) != root(b.rel) and (key.startswith("remote:") or _same(a, b, seen)):
                    parent[root(a.rel)] = root(b.rel)
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
