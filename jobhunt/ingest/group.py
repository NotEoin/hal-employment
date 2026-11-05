"""Putting files together into units: a code repository, or the notes and slides of one course.

- A repository is one unit. Its files are never items of their own: the unit is summarised from its README,
  docs, manifest, language mix and file tree (`convert.repo_text`), because a hundred source files read one
  by one say less about the person than the README does, and cost a hundred model calls.
- A folder whose own files are mostly notes or slides, `COURSE_MIN` or more of them, is a course unit:
  each file still gets a light note, and the unit gets one summary note linking them.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from jobhunt.ingest import rank

COURSE_MIN = 3            # two files are a pair of notes; three on one subject are a course's worth
COURSE_SHARE = 0.5        # at least half the folder's own files must be notes or slides


def outermost(repos: set[Path]) -> set[Path]:
    """The repositories not inside another one: a nested project belongs to the one around it."""
    return {r for r in repos if not any(o in r.parents for o in repos)}


def repo_of(path: Path, repos: set[Path]) -> Path | None:
    """The repository holding `path`, or None."""
    return next((r for r in repos if r in path.parents), None)


def courses(files: list[tuple[Path, str]]) -> dict[Path, list[tuple[Path, str]]]:
    """Course folders and their files, from (path, rel) pairs not inside a repository."""
    by_folder: dict[Path, list[tuple[Path, str]]] = defaultdict(list)
    for path, rel in files:
        by_folder[path.parent].append((path, rel))
    out = {}
    for folder, members in by_folder.items():
        light = [m for m in members if rank.rule_for(m[1]).doc_type in ("notes", "slides")]
        if len(light) >= COURSE_MIN and len(light) >= COURSE_SHARE * len(members):
            out[folder] = light
    return out
