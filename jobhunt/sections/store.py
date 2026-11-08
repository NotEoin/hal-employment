"""The sources of each section of the record (`record_sources`, migration 0028): adding, listing and changing them.

Each row is one source the person added: a file or set of files, a link, an account or a folder, in one
section, maybe for one card (`target_id`). The files of an upload are kept in Hal's data folder
(`files_dir`), so a source can be read again without the person adding it again. Reading a source is the
pipeline's (`sections.pipeline`); this module only keeps the rows.

Design decisions, each with its reason:

- A source's content hash (`sha256`) is checked across every section: the same file added twice, even to two
  sections, is one source, and the person is told where it is already (`by_hash`).
- `title` stays "" until the reader finds one or the person types one, so a name the person gave is never
  replaced by a later read; the page shows the file's name until then (`shape`).
- Only coursework takes a star on the source itself (a showcase piece of the degree), and only CVs are marked
  current; a project's star is on its card (`profile.store.set_star`). The `current` column keeps only the
  person's choice; the current CV shown is `profile.cvs.current_source` (their choice, else the newest-dated),
  and choosing another works out again every card the CVs give.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from jobhunt import db
from jobhunt.core import paths
from jobhunt.profile import cvs
from jobhunt.profile import store as profile_store
from jobhunt.sections import registry, wrong_place

STATES = ("new", "reading", "read", "failed", "kept_out")
STATE_WORDS = {"new": "Waiting to be read", "reading": "Reading", "read": "Read", "failed": "Could not be read",
               "kept_out": "Kept out: it looks private"}
STAR_SECTIONS = ("coursework",)
CURRENT_SECTIONS = ("cv",)
TITLE_MAX = 200            # a title is a line; a longer one is a pasted paragraph
FILES_FOLDER = "record-sources"


def files_root() -> Path:
    """The folder in Hal's data folder that holds the files of every uploaded source."""
    return paths.data_dir() / FILES_FOLDER


def files_dir(source_id: int) -> Path:
    """Where Hal keeps the files of one uploaded source."""
    return files_root() / str(int(source_id))


def add(con: sqlite3.Connection, section: str, *, held: str, doc_kind: str, locator: str = "", sha256: str = "",
        target_id: int | None = None, files: list[tuple[str, int, str]] = ()) -> int:
    """Adds a source row in state `new` with its files [(rel, size, sha256)]; returns its id. The caller commits."""
    registry.get(section)
    now = db.now()
    source_id = con.execute("INSERT INTO record_sources (section, target_id, kind, doc_kind, locator, sha256, added) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (section, target_id, held, doc_kind, locator, sha256, now)).lastrowid
    con.executemany("INSERT INTO record_source_files (source_id, rel, size, sha256) VALUES (?, ?, ?, ?)",
                    [(source_id, rel, size, digest) for rel, size, digest in files])
    return source_id


def row(con: sqlite3.Connection, source_id) -> sqlite3.Row:
    """The row of one source; LookupError when there is none."""
    try:
        key = int(source_id)
    except (TypeError, ValueError):
        raise LookupError("That source is not in your record.") from None
    found = con.execute("SELECT * FROM record_sources WHERE id = ?", (key,)).fetchone()
    if found is None:
        raise LookupError("That source is not in your record.")
    return found


def file_rels(con: sqlite3.Connection, source_id: int) -> list[str]:
    """The paths of a source's files inside it, in order."""
    return [r[0] for r in con.execute("SELECT rel FROM record_source_files WHERE source_id = ? ORDER BY rel",
                                      (source_id,))]


def by_hash(con: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    """The source with this content hash in any section, or None."""
    if not sha256:
        return None
    return con.execute("SELECT * FROM record_sources WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)).fetchone()


def by_locator(con: sqlite3.Connection, held: str, locator: str) -> sqlite3.Row | None:
    """The link, account or folder source with this locator in any section, or None."""
    return con.execute("SELECT * FROM record_sources WHERE kind = ? AND locator = ? ORDER BY id LIMIT 1",
                       (held, locator)).fetchone()


def shape(con: sqlite3.Connection, r: sqlite3.Row) -> dict:
    """A source as the page shows it."""
    section = registry.get(r["section"])
    rels = file_rels(con, r["id"])
    name = rels[0] if len(rels) == 1 else (r["locator"] or (rels[0].split("/")[0] if rels else ""))
    kind = registry.DOC_KINDS.get(r["doc_kind"])
    hint = r["suggestion"].removeprefix(wrong_place.KIND_PREFIX) if r["suggestion"].startswith(
        wrong_place.KIND_PREFIX) else ""
    suggestion = None if hint else registry.SECTIONS.get(r["suggestion"])
    return {"id": r["id"], "section": r["section"], "section_label": section.label, "target_id": r["target_id"],
            "held": r["kind"], "doc_kind": r["doc_kind"], "doc_kind_label": kind.label if kind else r["doc_kind"],
            "title": r["title"] or name, "name": name, "files": len(rels), "locator": r["locator"],
            "starred": bool(r["starred"]), "can_star": r["section"] in STAR_SECTIONS,
            "current": r["section"] in CURRENT_SECTIONS and r["id"] == cvs.current_source(con),
            "can_be_current": r["section"] in CURRENT_SECTIONS, "current_chosen": bool(r["current"]),
            "dated": r["dated"], "note_rel": r["note_rel"],
            "added": r["added"], "last_checked": r["last_checked"], "last_changed": r["last_changed"],
            "revision": r["revision"], "state": r["state"], "state_words": STATE_WORDS.get(r["state"], r["state"]),
            "error": r["error"], "suggestion": "" if hint else r["suggestion"],
            "suggestion_label": suggestion.label if suggestion else "", "suggestion_kind": hint,
            "suggestion_kind_label": registry.DOC_KINDS[hint].label if hint in registry.DOC_KINDS else ""}


def listing(con: sqlite3.Connection, section: str | None = None, target: int | None = None) -> list[dict]:
    """The sources of one section (every section when None), for one card when `target` is given, oldest first."""
    sql, args = "SELECT * FROM record_sources WHERE 1 = 1", []
    if section:
        registry.get(section)
        sql += " AND section = ?"
        args.append(section)
    if target is not None:
        sql += " AND target_id = ?"
        args.append(int(target))
    return [shape(con, r) for r in con.execute(sql + " ORDER BY id", args)]


def get(con: sqlite3.Connection, source_id) -> dict:
    """One source as the page shows it; LookupError when there is none."""
    return shape(con, row(con, source_id))


def target_card(con: sqlite3.Connection, section: registry.Section, target_id) -> int | None:
    """`target_id` checked as a card of the kind `section` belongs to; None when none is given and none is needed.

    ValueError when the section needs a card and none is given, or the card is of another kind; LookupError for a
    card that is not there.
    """
    if target_id in (None, ""):
        if section.target_required:
            raise ValueError(f"Choose which {section.target} {section.label.lower()} belongs to.")
        return None
    if not section.target and not section.targets:
        raise ValueError(f"{section.label} do not belong to a card.")
    try:
        key = int(target_id)
    except (TypeError, ValueError):
        raise ValueError("The card should be given by its id.") from None
    found = con.execute("SELECT kind FROM profile_items WHERE id = ?", (key,)).fetchone()
    if found is None:
        raise LookupError("That card is not in your profile.")
    if not section.belongs_to(found["kind"]):
        kinds = " or ".join(k for k in (section.target, *section.targets) if k)
        raise ValueError(f"{section.label} belong to a {kinds}, not a {found['kind']}.")
    return key


def change(con: sqlite3.Connection, source_id, changes: dict) -> dict:
    """Changes a source's title, kind of document, card, star, current mark or section; returns the source.

    Moving it to another section takes that section's default kind of document unless one is given, and drops
    the suggestion. ValueError for a change that does not fit, LookupError for a source or card that is not
    there. The caller reads a moved source again, so its note and facts follow it.
    """
    r = row(con, source_id)
    unknown = sorted(set(changes) - {"title", "doc_kind", "target_id", "starred", "current", "section"})
    if unknown:
        raise ValueError(f"A source has no {', '.join(unknown)} to change.")
    section = registry.get(str(changes.get("section") or r["section"]))
    moved = section.id != r["section"]
    sets: dict = {}
    if "title" in changes:
        title = " ".join(str(changes["title"] or "").split())
        if len(title) > TITLE_MAX:
            raise ValueError(f"Keep the title under {TITLE_MAX} characters.")
        sets["title"] = title
    if moved or "doc_kind" in changes:
        kind = changes.get("doc_kind") or (r["doc_kind"] if section.accepts(r["doc_kind"]) else None)
        sets["doc_kind"] = registry.doc_kind(section, kind).id
    if moved or "target_id" in changes:
        sets["target_id"] = target_card(con, section, changes.get("target_id", r["target_id"] if not moved else None))
    for flag, allowed in (("starred", STAR_SECTIONS), ("current", CURRENT_SECTIONS)):
        if flag in changes:
            if not isinstance(changes[flag], bool):
                raise ValueError(f"Say whether it is {flag}: true or false.")
            if section.id not in allowed:
                raise ValueError(f"{section.label} cannot be {'starred' if flag == 'starred' else 'marked current'}.")
            sets[flag] = int(changes[flag])
    if moved:
        sets.update({"section": section.id, "suggestion": "", "starred": 0, "current": 0})
    with con:
        if sets.get("current"):
            con.execute("UPDATE record_sources SET current = 0 WHERE section = ?", (section.id,))
        if sets:
            con.execute(f"UPDATE record_sources SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                        (*sets.values(), r["id"]))
    if "current" in sets or (moved and r["section"] in CURRENT_SECTIONS):
        profile_store.refold_sources(con, [*cvs.cv_sources(con), r["id"]])
    return get(con, r["id"])


def set_state(con: sqlite3.Connection, source_id: int, state: str, error: str = "") -> None:
    """Sets a source's state and the words on why it failed or was kept out; commits."""
    if state not in STATES:
        raise ValueError(f"{state!r} is not a state of a source.")
    with con:
        con.execute("UPDATE record_sources SET state = ?, error = ? WHERE id = ?", (state, error, source_id))
