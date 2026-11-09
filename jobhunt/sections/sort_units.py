"""The sorter's rules for folders that are one proposal, or none: groups of note folders, practice-site
folders, downloads, and a notes app's own folders.

`sections.sorter` proposes a project for each code project and each folder with a README. On a real
10,000-file dump (2026-11-09) that gave 20 project rows for one folder of notes on known cyber attacks (one
sub-folder a year), ten pre-starred first-year practicals, 3D-print downloads and a notes app's "Templates"
folder as projects. These rules make such folders one row, or leave them out.

Design decisions, each with its reason:

- A group (`groups`) takes every file below its folder, so nothing inside it is proposed on its own:
  - `NOTES`: a folder (never the folder sorted) holding `NOTE_FOLDERS_MIN` or more sub-folders with a
    README that are notes, and whose files are mostly text notes (`NOTES_SHARE`), with no code project. It
    is one proposal at the folder.
  - `PRACTICE`: a folder named after a practice site (`PRACTICE_SITE`: HackerRank, LeetCode, Project Euler,
    Advent of Code and the like). Exercises show practice, not a piece to show an employer, so it is one
    unstarred project.
  - `DOWNLOAD`: a folder named "<Thing> - <6 or 7 digits>", as a 3D-print sharing site names a download
    (`DOWNLOAD_ID`). It is someone else's design, so it is left out as one row.
- A notes app's own folders (`APP_FOLDER`: "+Inbox", "_Session-Logs", "Templates", "Attachments", and any
  name starting "+" or "_") are never projects, whatever README they hold; templates and attachments are
  never documents of the person's either (`app_file`).
- Sibling folders numbered in one series ("practical-1" to "practical-10", "lab1" to "lab8", "week-01" on,
  `series`) are one row in the review (`sort_review`): a real dump gave nine project rows for one module's
  practicals (2026-11-09). They stay one proposal each underneath, as each is a folder of its own.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path, PurePosixPath

from jobhunt.ingest import rank

NOTES, PRACTICE, DOWNLOAD = "notes", "practice", "download"
NOTE_FOLDERS_MIN = 3        # two folders of notes may be two projects; three under one parent are one subject
NOTES_SHARE = 0.8           # "mostly .md or .txt": a picture or two among the notes is fine
PRACTICE_SITE = re.compile(r"(?<![a-z])(?:hacker[ _-]?rank|leet[ _-]?code|project[ _-]?euler|"
                           r"advent[ _-]?of[ _-]?code|code[ _-]?wars|exercism|kattis|codeforces|codility)"
                           r"(?![a-z])", re.I)
DOWNLOAD_ID = re.compile(r"\S\s+-\s+\d{6,7}$")
APP_FOLDER = re.compile(r"^(?:[+_].*|templates?|attachments?)$", re.I)
APP_STORE = re.compile(r"^(?:templates?|attachments?)$", re.I)
SERIES = re.compile(r"^(?P<stem>.*?[a-z])[ _-]?0*\d{1,3}$", re.I)    # "practical-1", "lab1", "week-01"
WORDS = {NOTES: "Folders of your notes on one subject, as one.",
         PRACTICE: "Practice: exercises from a practice site. Hal keeps them out of your showcase.",
         DOWNLOAD: "A download from a sharing site, such as a 3D-print design: someone else's work."}


def series(rel: str) -> str:
    """The numbered series a folder at `rel` belongs to, as its parent and the name without its number
    ("Code/lab" for "Code/lab3"); "" for a name with no number at its end."""
    path = PurePosixPath(rel)
    found = SERIES.match(path.name.strip())
    return f"{path.parent}/{found['stem'].lower()}" if found else ""


def app_folder(folder: Path) -> bool:
    """True for a notes app's own folder, which is never a project."""
    return APP_FOLDER.match(folder.name) is not None


def app_file(rel: str) -> str:
    """Why a file in a notes app's templates or attachments folder is left out; "" for any other file."""
    if any(APP_STORE.match(part) for part in PurePosixPath(rel).parent.parts[1:]):
        return "A notes app's template or attachment, not a document of yours."
    return ""


def _below(path: Path, folder: Path) -> bool:
    return folder in path.parents


def _notes_only(files: list[Path]) -> bool:
    kinds = Counter("text" if f.suffix.lower() in rank.TEXT_SUFFIXES else
                    "code" if f.suffix.lower() in rank.CODE_SUFFIXES else "other" for f in files)
    return not kinds["code"] and kinds["text"] >= NOTES_SHARE * sum(kinds.values())


def _named_groups(base: Path, files: list[Path]) -> dict[Path, str]:
    """The practice-site and download folders below `base`, outermost first."""
    found: dict[Path, str] = {}
    for folder in sorted({p for f in files for p in f.parents if _below(p, base) or p == base}, key=lambda p:
                         len(p.parts)):
        if folder == base or any(_below(folder, g) for g in found):
            continue
        if PRACTICE_SITE.search(folder.name):
            found[folder] = PRACTICE
        elif DOWNLOAD_ID.search(folder.name):
            found[folder] = DOWNLOAD
    return found


def groups(base: Path, files: list[Path], repos: set[Path], folders: set[Path]) -> dict[Path, str]:
    """The folders below `base` that are each one proposal, or one left out, with their kind (see the module
    notes); none is inside another."""
    found = _named_groups(base, files)
    by_parent: dict[Path, list[Path]] = {}
    for folder in folders - repos:
        if folder.parent != base and _notes_only([f for f in files if _below(f, folder)]):
            by_parent.setdefault(folder.parent, []).append(folder)
    for parent, kids in sorted(by_parent.items(), key=lambda kv: len(kv[0].parts)):
        if len(kids) < NOTE_FOLDERS_MIN or any(_below(parent, g) or parent == g for g in found):
            continue
        if _notes_only([f for f in files if _below(f, parent)]) and not any(_below(r, parent) for r in repos):
            found[parent] = NOTES
    return found


def holder(path: Path, found: dict[Path, str]) -> Path | None:
    """The group folder holding `path`, or None."""
    return next((g for g in found if _below(path, g)), None)
