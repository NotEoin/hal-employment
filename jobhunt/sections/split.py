"""Monorepos: "Split into projects by folder" turns the parts of one code project into projects of their own.

`parts(root)` finds the folders inside a code project that are projects themselves: a top folder with its own
manifest (`ingest.discover.is_repo`), or a folder inside a usual home for parts (`PART_HOMES`: `packages`,
`apps`, `services` and the like) that has one. `split(con, source_id)` adds each as a child source of kind
`repo`, held as a folder, with the repository's root in its `meta.git_root`, so its dates and the person's
share come from the commits that touched that folder only (`repo.git_history`).

Design decisions, each with its reason:

- The person asks for it: Hal never splits a project on its own, as one project with a `web/` and an `api/`
  folder is usually told as one.
- The project itself stays, as the whole; each part becomes its own project beside it, starred and removed on
  its own, and removing the whole removes its parts.
- Splitting again adds only parts not there yet (`pipeline.add_children`), and a part the person removed is
  not added back.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from jobhunt.ingest import discover
from jobhunt.sections import pipeline, readers, registry, repo, store

log = logging.getLogger(__name__)

PART_HOMES = {"packages", "apps", "services", "projects", "libs", "modules", "crates", "plugins", "tools"}
SPLIT_KINDS = store.SPLIT_KINDS
PARTS_MAX = 50              # parts taken from one project; more is a collection, better added as a folder


def _names(folder: Path) -> list[Path]:
    try:
        return sorted(folder.iterdir())
    except OSError as e:       # a folder Hal may not open holds no parts it can read
        log.info("could not list %s: %s", folder, e)
        return []


def _entries(folder: Path) -> list[Path]:
    return [p for p in _names(folder) if p.is_dir() and not p.is_symlink()
            and not discover.is_junk_dir(p.name, in_repo=True)]


def _is_part(folder: Path) -> bool:
    return discover.is_repo([p.name for p in _names(folder)])


def parts(root: Path) -> list[Path]:
    """The folders inside a code project that are projects of their own, at most `PARTS_MAX`."""
    found = []
    for top in _entries(root):
        if top.name.lower() in PART_HOMES:
            found += [p for p in _entries(top) if _is_part(p)]
        elif _is_part(top):
            found.append(top)
    return found[:PARTS_MAX]


def split(con: sqlite3.Connection, source_id) -> list[int]:
    """Adds each part of a code project as its own source under it; returns the new ids, to be read.

    ValueError for a source that is not a code project, or one with no parts; LookupError for one that is not
    there.
    """
    r = store.row(con, source_id)
    if r["doc_kind"] not in SPLIT_KINDS or r["section"] != "project":
        raise ValueError("Only a code project can be split into projects by folder.")
    root = repo.root_of(pipeline.source_of(con, r))
    git_root = root if (root / ".git").exists() else None
    found = parts(root)
    if not found:
        raise ValueError("Hal found no folders inside this project that are projects of their own.")
    new = pipeline.add_children(con, r["id"], [readers.Child("repo", registry.FOLDER, str(p)) for p in found])
    for child_id in new:
        store.set_meta(con, child_id, git_root=str(git_root) if git_root else None, split_from=r["id"])
    return new
