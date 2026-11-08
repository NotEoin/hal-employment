"""Supporting files: the data, figures, logs and models beside a piece of work, listed with it, never notes.

A dump of about 12,000 files gave 337 notes of camera photos and 3D-print renders, notes of a dissertation
supplement's CSV exports and figures, and notes of diffs and circuit files (2026-11-06). None of them says
anything about the person on its own, and each buried the documents that do. So these files are never
converted, never sent to the AI and give no facts:

- data (`DATA_SUFFIXES`), and anything but a document in a results folder (`RESULTS_FOLDERS`);
- diffs and patches, logs, and circuit, CAD and 3D files (`DIFF_SUFFIXES`, `LOG_SUFFIXES`, `DESIGN_SUFFIXES`);
- pictures and recordings Hal reads no words from: every picture that does not rank as a certificate or
  artwork (`convert.text_of` reads no others), every camera photo (`camera_photo`) whatever its folder, and
  a certificate or artwork picture whose words came back empty (`engine._finish` leaves it to `settle`).

`settle` runs once a batch's documents and course notes are written. A supporting file inside a unit gets
state `attached`: `dup_of` the unit's item, `note` the unit's note, and its path inside the unit listed
under "## Files" in that note (`list_in`). A unit (`Units.find`) is the nearest folder above the file, and
below the folder the person gave (or that folder, when it is a project folder), that is a course unit, a
project folder (`group.projects`) or a repository, or holds a document of a kind of work (`roles.WORK_TYPES`)
or a repository; a folder named as work ("Dissertation", "Final project", `rank.work_named`) reaches the work
documents anywhere below it. Outside any unit the file is `skipped`, with the reason in its error column.
`tidy` holds notes an older read filed to the same rule.
"""
from __future__ import annotations

import re
import sqlite3
import struct
from pathlib import Path, PurePosixPath

from jobhunt import record
from jobhunt.ingest import items, notes, rank, roles

STATE = "attached"
FILES = "## Files"
ORIGINAL = "\n## Original\n"
NO_WORDS = "a picture with no words"
NO_UNIT = "a data, log or design file with no project around it"
FOLDER_UNITS = ("course", "repo", "project")    # units that are a whole folder (`group`), not one document
LISTS = ("course", "project")                   # units whose note's text is the list of their files
DATA_SUFFIXES = {".csv", ".tsv", ".json", ".jsonl", ".xlsx", ".xls", ".ods", ".parquet", ".npy", ".npz", ".mat",
                 ".h5", ".hdf5", ".sav", ".dta", ".arff"}
DIFF_SUFFIXES = {".diff", ".patch"}
LOG_SUFFIXES = {".log"}
DESIGN_SUFFIXES = {".circ", ".scad", ".stl", ".3mf", ".gcode", ".blend", ".obj", ".ply", ".fbx", ".step", ".stp",
                   ".fcstd", ".f3d", ".dxf", ".dwg", ".kicad_pcb", ".kicad_sch"}
PICTURE_SUFFIXES = rank.IMAGE_SUFFIXES | rank.MEDIA_SUFFIXES
RESULTS_FOLDERS = re.compile(r"^(?:results?|outputs?|exports?|figures?|figs|plots|renders?|data|datasets?|logs?)$",
                             re.I)
DOCUMENTS = rank.BINARY_DOCS | rank.SLIDE_SUFFIXES | {".md", ".markdown"}   # still read in a results folder
WORDS_READ = ("certificate", "artwork")          # the pictures `convert.text_of` reads words from
CAMERA_NAME = re.compile(r"^(?:dsc[fn]?|img|pxl|gopr|mvi|vid)[_-]?\d{3,}", re.I)
EXIF_READ = 64 * 1024        # a JPEG's Exif block comes in its first segments, well inside this
EXIF_HEADER = b"Exif\x00\x00"
JPEG_START = b"\xff\xd8"
TIFF_STARTS = (b"II*\x00", b"MM\x00*")
TIFF_HEADER = 8              # byte order, 42 and the first directory's offset (TIFF 6.0, section 2)
IFD_ENTRY = 12               # tag, type, count and value or offset (TIFF 6.0, section 2)
IFD_ENTRIES_MAX = 512        # more entries than any camera writes: a broken file, not a photo
TAG_MAKE = 0x010F            # the camera maker (Exif 2.32, table 4)
TYPE_ASCII = 2
INLINE_BYTES = 4             # a value of up to four bytes sits in the entry itself (TIFF 6.0, section 2)


def is_picture(rel: str) -> bool:
    """True for a picture or a recording, by its name."""
    return PurePosixPath(rel).suffix.lower() in PICTURE_SUFFIXES


def _directory_make(tiff: bytes) -> str:
    """The camera maker in a TIFF structure's first directory; "" for none."""
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None or len(tiff) < TIFF_HEADER:
        return ""
    try:
        (first,) = struct.unpack_from(order + "I", tiff, 4)
        (count,) = struct.unpack_from(order + "H", tiff, first)
        for n in range(min(count, IFD_ENTRIES_MAX)):
            entry = first + 2 + n * IFD_ENTRY
            tag, kind, size, value = struct.unpack_from(order + "HHII", tiff, entry)
            if tag == TAG_MAKE and kind == TYPE_ASCII:
                at = entry + 8 if size <= INLINE_BYTES else value
                return tiff[at:at + size].split(b"\x00")[0].decode("ascii", "replace").strip()
    except struct.error:     # a directory that runs past the bytes read is a broken file, not a camera's
        return ""
    return ""


def camera_make(path: Path) -> str:
    """The camera maker a JPEG's or TIFF's Exif names ("Canon"); "" for none or a file that cannot be read."""
    try:
        with path.open("rb") as f:
            head = f.read(EXIF_READ)
    except OSError:
        return ""
    if head[:4] in TIFF_STARTS:
        return _directory_make(head)
    if head[:2] != JPEG_START or (at := head.find(EXIF_HEADER)) < 0:
        return ""
    return _directory_make(head[at + len(EXIF_HEADER):])


def camera_photo(path: Path) -> bool:
    """True for a photo a camera or phone took: named as cameras name them, or with a camera maker in its Exif."""
    return bool(CAMERA_NAME.match(path.stem)) or bool(camera_make(path))


def kind_of(rel: str, path: Path) -> str:
    """What kind of supporting file this is ("data", "diff", "log", "design", "picture"); "" for none."""
    p = PurePosixPath(rel)
    suffix = p.suffix.lower()
    for kind, suffixes in (("data", DATA_SUFFIXES), ("diff", DIFF_SUFFIXES), ("log", LOG_SUFFIXES),
                           ("design", DESIGN_SUFFIXES)):
        if suffix in suffixes:
            return kind
    if suffix in PICTURE_SUFFIXES:
        read = suffix in record.IMAGES and rank.rule_for(rel).doc_type in WORDS_READ
        return "picture" if not read or camera_photo(path) else ""
    if suffix not in DOCUMENTS and any(RESULTS_FOLDERS.match(part) for part in p.parts[1:-1]):
        return "data"
    return ""


def supporting(row: sqlite3.Row | dict) -> bool:
    """True when the item is a supporting file, listed with its unit rather than read (see the header)."""
    return row["kind"] == "file" and bool(kind_of(row["rel"], Path(row["path"])))


def _first(found: list[sqlite3.Row]) -> sqlite3.Row:
    """The unit a file goes with when several are in reach: the shallowest, best-ranked one."""
    return min(found, key=lambda r: (len(Path(r["path"]).parts),
                                     r["rank"] if r["rank"] is not None else rank.OTHER.rank, r["rel"]))


class Units:
    """The record's units by folder, read once, to find the one each supporting file belongs to.

    A note being read again in place (pending, with its note) is a unit too: a tidy sends a report back to the
    AI before it reaches the figures beside it.
    """

    def __init__(self, con: sqlite3.Connection) -> None:
        self.at: dict[Path, sqlite3.Row] = {}            # course, project and repository units, by their folder
        self.work: dict[Path, list[sqlite3.Row]] = {}    # work documents and repositories, by the folder above
        for r in con.execute("""SELECT id, kind, path, rel, note, doc_type, rank FROM ingest_items
                                WHERE state IN ('done', 'pending') AND note IS NOT NULL
                                AND kind IN ('course', 'repo', 'project', 'file')
                                ORDER BY id DESC"""):
            path = Path(r["path"])
            if r["kind"] in FOLDER_UNITS:
                self.at.setdefault(path, r)
            if r["kind"] == "repo" or (r["kind"] == "file" and r["doc_type"] in roles.WORK_TYPES):
                self.work.setdefault(path.parent, []).append(r)

    def find(self, path: Path, rel: str) -> sqlite3.Row | None:
        """The unit of the file at `path` (`rel` inside the folder the person gave), nearest folder first.

        The folder given is no unit, unless it is a project folder: a person may give one project's folder.
        """
        between = len(PurePosixPath(rel).parts) - 2      # the folders below the one given
        if between < 0:
            return None                                  # the person gave this one file
        for n, folder in enumerate(list(path.parents)[:between + 1]):
            if n == between:
                found = self.at.get(folder)
                return found if found is not None and found["kind"] == "project" else None
            if folder in self.at:
                return self.at[folder]
            if here := self.work.get(folder):
                return _first(here)
            if rank.work_named(folder.name) and (below := [r for f, found in self.work.items()
                                                           if folder in f.parents for r in found]):
                return _first(below)
        return None


def _base(unit: sqlite3.Row) -> Path:
    """The folder a unit's files are named from: its own for a course or repository, else its document's."""
    path = Path(unit["path"])
    return path if unit["kind"] in FOLDER_UNITS else path.parent


def list_in(vault: Path, unit: sqlite3.Row, name: str) -> None:
    """Adds `name` to the "## Files" list of the unit's note, once.

    A course or project note's list is its text; any other note gets its own list above "## Original", as the
    document's text below may have a "## Files" heading of its own (a repository's file tree does).
    """
    path = vault / unit["note"]
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    line = f"- `{name}`\n"
    if line in text:
        return
    head, sep, tail = (text, "", "") if unit["kind"] in LISTS else text.partition(ORIGINAL)
    found = re.search(rf"(?:^|\n){re.escape(FILES)}\n\n(?:- .*\n)*", head)
    if found:
        head = head[:found.end()] + line + head[found.end():]
    else:
        head = head.rstrip("\n") + f"\n\n{FILES}\n\n{line}"
    notes.write(path, head + sep + tail)


def place(con: sqlite3.Connection, vault: Path, row: sqlite3.Row, units: Units) -> str:
    """Attaches one supporting file to its unit, or skips it when it has none; returns the state it got."""
    unit = units.find(Path(row["path"]), row["rel"])
    if unit is None:
        items.update(con, row["id"], state="skipped", note=None,
                     error=NO_WORDS if is_picture(row["rel"]) else NO_UNIT)
        return "skipped"
    try:
        name = Path(row["path"]).relative_to(_base(unit)).as_posix()
    except ValueError:
        name = Path(row["path"]).name
    list_in(vault, unit, name)
    items.update(con, row["id"], state=STATE, dup_of=unit["id"], note=unit["note"], error=None)
    return STATE


def settle(con: sqlite3.Connection, batch: str, vault: Path) -> dict[str, int]:
    """Attaches or skips the batch's files still pending once its documents and units are written: the
    supporting files, and pictures read with no words. Returns how many got each state."""
    left = con.execute("SELECT * FROM ingest_items WHERE batch = ? AND state = 'pending' AND kind = 'file' "
                       "ORDER BY rel", (batch,)).fetchall()
    if not left:
        return {}
    units, out = Units(con), {}
    for row in left:
        state = place(con, vault, row, units)
        out[state] = out.get(state, 0) + 1
    return out
