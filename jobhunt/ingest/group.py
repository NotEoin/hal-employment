"""Putting files together into units: a code repository, a project folder of the person's own, or the notes
and slides of one course.

- A repository is one unit. Its files are never items of their own: the unit is summarised from its README,
  docs, manifest, language mix and file tree (`convert.repo_text`), because a hundred source files read one
  by one say less about the person than the README does, and cost a hundred model calls.
- A project folder (`projects`) is a folder with a README at its top, in a folder the person said is their
  own work or a mix, that is not a repository. It is one unit with all its sub-folders: each file still gets
  a note, but every project fact read in it is evidence of the one project (`ingest.projects`). A research
  workspace with no `.git` (a README, numbered plan notes, sub-folders of tickets and write-ups) was split
  into course units, and each note that named a piece of work became a project card of its own: about 180
  of a profile's 243 project cards (2026-11-08). A folder of course material is never one: a module folder
  holds several pieces of coursework.
- A folder that holds `COLLECTION_MIN` or more other projects (repositories or README folders) is a
  collection, such as a "Projects" folder with its own README, not one project: the projects inside are
  units of their own.
- A folder whose own files are mostly notes or slides, `COURSE_MIN` or more of them, and that is in no project
  folder, is a course unit: each file still gets a note, light unless its name says it is work such as
  coursework or a report (`rank.in_unit`), and the unit gets one summary note linking them.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from jobhunt.ingest import rank

COURSE_MIN = 3            # two files are a pair of notes; three on one subject are a course's worth
COURSE_SHARE = 0.5        # at least half the folder's own files must be notes or slides
# One project inside another is its code or a part of it; two or more make the folder a collection of projects.
COLLECTION_MIN = 2


def outermost(repos: set[Path]) -> set[Path]:
    """The repositories not inside another one: a nested project belongs to the one around it."""
    return {r for r in repos if not any(o in r.parents for o in repos)}


def repo_of(path: Path, repos: set[Path]) -> Path | None:
    """The repository holding `path`, or None."""
    return next((r for r in repos if r in path.parents), None)


def projects(readmes: set[Path], repos: set[Path]) -> set[Path]:
    """The project folders among the folders with a README at their top (see the module notes): not a
    repository or inside one, not a collection of `COLLECTION_MIN` or more projects, and not inside another
    project folder. The caller leaves them out for course material."""
    found = {f for f in readmes if f not in repos and repo_of(f, repos) is None}
    units = found | outermost(repos)

    def inside(f: Path) -> int:
        """How many projects `f` holds, a project inside one of them counted with it."""
        below = {o for o in units if f in o.parents}
        return len({o for o in below if not any(b in o.parents for b in below)})

    kept = {f for f in found if inside(f) < COLLECTION_MIN}
    return {f for f in kept if not any(o in f.parents for o in kept)}


def project_of(path: Path, folders: set[Path]) -> Path | None:
    """The project folder holding `path`, or None."""
    return next((f for f in folders if f in path.parents), None)


def courses(files: list[tuple[Path, str]]) -> dict[Path, list[tuple[Path, str]]]:
    """Course folders and their files, from (path, rel) pairs not inside a repository or project folder."""
    by_folder: dict[Path, list[tuple[Path, str]]] = defaultdict(list)
    for path, rel in files:
        by_folder[path.parent].append((path, rel))
    out = {}
    for folder, members in by_folder.items():
        light = [m for m in members if rank.rule_for(m[1]).doc_type in ("notes", "slides")]
        if len(light) >= COURSE_MIN and len(light) >= COURSE_SHARE * len(members):
            out[folder] = light
    return out
