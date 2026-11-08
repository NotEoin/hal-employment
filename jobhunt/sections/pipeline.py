"""The pipeline of one source: keep what the person added, read it, write its note, and file its facts.

`add_uploads` and `add_link` keep a new source (`sections.store`); `read` runs its section's reader
(`sections.readers`), writes its note in the record under the section's folder, and replaces its facts in the
profile in one transaction (`profile.store.swap_source`); `remove` takes it out with its note and facts.
Reading is slow, so routes start the `record.sources` action (`sections.actions`), which calls `read`.

Design decisions, each with its reason:

- Uploads are kept in Hal's data folder (`store.files_dir`), never only in the record, so a source can be read
  again when its reader improves or the person moves it, and removing it removes the copy too.
- A zip, or files sent with a `relative_path` (a folder chosen in the browser), are one source of several
  files: a project folder or a portfolio is one thing, not a hundred.
- The same content in any section is one source (`store.by_hash`): the person is told where it is already.
- The note goes in `Sources/<section folder>/` of the record, with `{target}` the title of the card the source
  belongs to (`Sources/Education/BSc History/Coursework/`), so the record's folders mirror the sections. A
  source read again keeps its note's path while its folder stays the same, so links to it still work.
- Every fact carries the section's role and the kind of document's type (which sets its rank), its source's id,
  and the place and line it came from; a fact of a kind the section may not create is left out.
- The privacy check of the bulk read applies (`ingest.privacy.held_back`): a source that looks private gets no
  note and no facts until the person lets it in (`read(..., allow_private=True)`).
- A reader not written yet (`readers.NotReadYet`) leaves the source waiting, state `new`, with the words saying
  so; it is not a failure.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jobhunt import db, record
from jobhunt.ingest import notes, privacy, roles
from jobhunt.ingest import sources as ingest_sources
from jobhunt.profile import store as profile_store
from jobhunt.sections import readers, registry, store, wrong_place

log = logging.getLogger(__name__)
CHUNK = 1 << 20            # files are hashed 1 MiB at a time
SKIPPED_PARTS = {"__MACOSX", ".git", ".DS_Store", "Thumbs.db", "desktop.ini"}
ZIP = ".zip"


@dataclass(frozen=True)
class Incoming:
    """One uploaded file: its name, its bytes, and its path inside a folder the person chose ("" for none)."""

    name: str
    data: bytes
    relative_path: str = ""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def set_hash(files: list[tuple[str, int, str]]) -> str:
    """The content hash of a source of several files: their paths and hashes, in order, so a renamed or changed
    file makes another source."""
    return _sha("\n".join(f"{rel}\0{digest}" for rel, _, digest in sorted(files)).encode("utf-8"))


def _clean_rel(raw: str) -> str:
    """A path inside an uploaded folder, safe to write under the source's folder; ValueError when it is not."""
    rel = ingest_sources.safe_member(raw or "")
    if rel is None:
        raise ValueError(f"{raw} is not a path inside the folder you chose.")
    return rel.as_posix()


def _skipped(rel: str) -> bool:
    return any(part in SKIPPED_PARTS or part.startswith(".") for part in PurePosixPath(rel).parts)


def _groups(uploads: list[Incoming]) -> list[tuple[str, list[Incoming]]]:
    """The uploads as sources: a zip each, each folder by its top name, and every other file on its own."""
    singles, folders = [], {}
    for up in uploads:
        if up.relative_path:
            rel = _clean_rel(up.relative_path)
            if not _skipped(rel):
                folders.setdefault(rel.split("/")[0], []).append(Incoming(up.name, up.data, rel))
        else:
            name = Path(up.name).name.strip()
            if not name or name.startswith("."):
                raise ValueError("Files whose names start with a dot cannot be added.")
            singles.append((name, [Incoming(name, up.data)]))
    return singles + sorted(folders.items())


def _unpack(zip_name: str, data: bytes, into: Path) -> list[str]:
    """Unpacks a zip into `into` with the bulk read's guards; returns what it refused, in plain words."""
    held = into.parent / f".{into.name}.zip"
    held.write_bytes(data)
    try:
        report = ingest_sources.unpack(held, into, label=zip_name)
    finally:
        held.unlink(missing_ok=True)
    for path in sorted(into.rglob("*"), reverse=True):
        if path.is_file() and _skipped(path.relative_to(into).as_posix()):
            path.unlink()
    return report.problems()


def _listed(folder: Path) -> list[tuple[str, int, str]]:
    return [(p.relative_to(folder).as_posix(), p.stat().st_size, _file_sha(p))
            for p in sorted(folder.rglob("*")) if p.is_file()]


def add_uploads(con: sqlite3.Connection, section_id: str, uploads: list[Incoming], *, doc_kind: str | None = None,
                target_id=None) -> dict:
    """Keeps uploaded files as new sources of a section; returns {"added": [ids], "duplicates": [{name, id,
    section_label}], "problems": [words]}.

    ValueError for a kind of document or card that does not fit the section, a name or path that is not safe,
    or nothing to add; LookupError for an unknown section or card.
    """
    section = registry.get(section_id)
    kind = registry.doc_kind(section, doc_kind)
    target = store.target_card(con, section, target_id)
    if not uploads:
        raise ValueError("Choose one or more files to add.")
    out: dict = {"added": [], "duplicates": [], "problems": []}
    work = store.files_root()
    work.mkdir(parents=True, exist_ok=True)
    for name, group in _groups(uploads):
        with tempfile.TemporaryDirectory(dir=work, prefix=".new-") as tmp:
            staged = Path(tmp) / "files"
            staged.mkdir()
            if len(group) == 1 and not group[0].relative_path and Path(name).suffix.lower() == ZIP:
                out["problems"] += _unpack(name, group[0].data, staged)
                held = registry.FILES
            else:
                for up in group:
                    target_path = staged / (up.relative_path or name)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_bytes(up.data)
                held = registry.FILES if group[0].relative_path else registry.UPLOAD
            if group[0].relative_path:     # the folder's own name is the source's, not a level inside it
                staged = staged / name
            files = _listed(staged)
            if not files:
                out["problems"].append(f"{name} had no files Hal could keep.")
                continue
            digest = files[0][2] if held == registry.UPLOAD else set_hash(files)
            if (same := store.by_hash(con, digest)) is not None:
                out["duplicates"].append({"name": name, "id": same["id"],
                                          "section_label": registry.get(same["section"]).label})
                continue
            with con:
                source_id = store.add(con, section.id, held=held, doc_kind=kind.id,
                                      locator=name if held == registry.FILES else "", sha256=digest,
                                      target_id=target, files=files)
            dest = store.files_dir(source_id)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(staged), dest)
        out["added"].append(source_id)
    return out


def add_link(con: sqlite3.Connection, section_id: str, locator: str, *, doc_kind: str | None = None,
             target_id=None) -> dict:
    """Keeps a link, account, folder path or hand-added name as a new source; returns {"added": [id]} or
    {"added": [], "duplicates": [...]} when the same one is in the record already.

    The route checks a folder is on this computer and outside the record; this checks the words fit the kind.
    ValueError for a kind of document that is not a link, a link that is not a web address, or no words.
    """
    section = registry.get(section_id)
    kind = registry.doc_kind(section, doc_kind)
    if kind.held not in (registry.LINK, registry.ACCOUNT, registry.FOLDER, registry.MANUAL):
        raise ValueError(f"Add a {kind.label.lower()} as a file.")
    words = " ".join(str(locator or "").split())
    if not words:
        raise ValueError("Type or paste what to add.")
    if kind.held == registry.LINK and not words.lower().startswith(("http://", "https://")):
        raise ValueError("Paste a web address that starts with http:// or https://.")
    if kind.held == registry.ACCOUNT:
        words = words.rstrip("/").rsplit("/", 1)[-1].lstrip("@")
    target = store.target_card(con, section, target_id)
    if kind.held != registry.MANUAL and (same := store.by_locator(con, kind.held, words)) is not None:
        return {"added": [], "duplicates": [{"name": words, "id": same["id"],
                                             "section_label": registry.get(same["section"]).label}]}
    with con:
        source_id = store.add(con, section.id, held=kind.held, doc_kind=kind.id, locator=words, target_id=target)
    return {"added": [source_id], "duplicates": []}


def _source(con: sqlite3.Connection, r: sqlite3.Row) -> readers.Source:
    folder = store.files_dir(r["id"])
    files = tuple(readers.SourceFile(rel, folder / rel) for rel in store.file_rels(con, r["id"]))
    target = None
    if r["target_id"] is not None:
        try:
            target = profile_store.item(con, r["target_id"])
        except LookupError:
            target = None
    return readers.Source(id=r["id"], section=r["section"], doc_kind=r["doc_kind"], title=r["title"],
                          held=r["kind"], locator=r["locator"], files=files, target=target)


def vault() -> Path:
    """The record folder the notes go in."""
    return Path(record._live("VAULT"))


def note_folder(section: registry.Section, target: dict | None) -> str:
    """The folder inside the record where a source's note goes: the section's, with its card's title."""
    name = notes.slug(target["title"]) if target and target.get("title") else "Not linked"
    return f"{notes.SOURCES}/" + "/".join(notes.slug(part) for part in section.folder.format(target=name).split("/"))


def _drop_note(note_rel: str | None) -> None:
    if not note_rel:
        return
    root = vault().resolve()
    path = (root / note_rel).resolve()
    if root in path.parents:
        path.unlink(missing_ok=True)


def _found_in(quote: str, text: str) -> bool:
    return " ".join(quote.split()).lower() in " ".join(text.split()).lower()


def _evidence(section: registry.Section, kind: registry.DocKind, source_id: int, note_rel: str,
              reading: readers.Reading) -> list[tuple[str, dict, dict]]:
    """The reading's facts as (kind, fields, evidence) for the profile; kinds the section may not create are
    left out."""
    out = []
    for fact in reading.facts:
        if fact.kind not in section.creates:
            log.info("left out a %s from source %s: %s gives no such facts", fact.kind, source_id, section.id)
            continue
        out.append((fact.kind, fact.fields, {
            "source_rel": note_rel, "quote": fact.quote.quote, "field": fact.quote.field, "doc_type": kind.doc_type,
            "verified": _found_in(fact.quote.quote, reading.text), "role": section.evidence_role,
            "source_id": source_id, "place": fact.quote.place, "line": fact.quote.line}))
    return out


def _write_note(r: sqlite3.Row, section: registry.Section, kind: registry.DocKind, source: readers.Source,
                reading: readers.Reading, name: str) -> str:
    """Writes the source's note under its section's folder; returns its path inside the record."""
    root = vault()
    folder = root / note_folder(section, source.target)
    folder.mkdir(parents=True, exist_ok=True)
    title = r["title"] or reading.title or readers.title_in(reading.text, name)
    old = r["note_rel"]
    at = root / old if old and (root / old).parent == folder and (root / old).exists() else \
        notes.free_note(folder, title, r["sha256"])
    role = section.evidence_role
    desc = {"category": section.label, "doc_type": kind.doc_type, "title": title, "tags": [section.id],
            "period": reading.date, "entities": {"organisations": [], "skills": [], "courses": []},
            "role": role, "about_owner": roles.ABOUT_OWNER[role], "summary": reading.summary,
            "facts": [{"kind": f.kind, "fields": f.fields, "quote": f.quote.quote, "field": f.quote.field,
                       "verified": _found_in(f.quote.quote, reading.text)} for f in reading.facts
                      if f.kind in section.creates], "by": "rules"}
    rel = notes.write_source(root, desc, text=reading.text, source=source.locator or name, sha256=r["sha256"],
                             original=None, at=at)
    if old and old != rel:
        _drop_note(old)
    return rel


def read(con: sqlite3.Connection, source_id, *, allow_private: bool = False) -> dict:
    """Reads one source with its section's reader, writes its note and puts its facts in the profile.

    Returns {"id", "state", "facts", "left_out", "hidden", "suggestion"}; a reader not written yet leaves the
    source waiting (state `new`), and one that cannot read it marks it failed with the reason. LookupError for
    a source that is not there.
    """
    r = store.row(con, source_id)
    section = registry.get(r["section"])
    kind = registry.DOC_KINDS[r["doc_kind"]]
    source = _source(con, r)
    name = store.shape(con, r)["name"]
    store.set_state(con, r["id"], "reading")
    out = {"id": r["id"], "state": "", "facts": 0, "left_out": 0, "hidden": [], "suggestion": ""}
    try:
        reading = readers.get(section.reader_of(r["doc_kind"])).read(source)
    except readers.NotReadYet as e:
        store.set_state(con, r["id"], "new", str(e))
        return {**out, "state": "new"}
    except (OSError, ValueError) as e:
        log.warning("could not read source %s: %s", r["id"], e)
        store.set_state(con, r["id"], "failed", f"Hal could not read it: {e}")
        return {**out, "state": "failed"}
    why = "" if allow_private else privacy.held_back({"allowed": False, "rel": name}, reading.text)
    if why:
        gone = profile_store.swap_source(con, r["id"], [])
        _drop_note(r["note_rel"])
        with con:
            con.execute("UPDATE record_sources SET note_rel = NULL WHERE id = ?", (r["id"],))
        store.set_state(con, r["id"], "kept_out", why)
        return {**out, "state": "kept_out", "hidden": gone["hidden"]}
    note_rel = _write_note(r, section, kind, source, reading, name)
    facts = _evidence(section, kind, r["id"], note_rel, reading)
    swapped = profile_store.swap_source(con, r["id"], facts)
    suggestion = wrong_place.suggest(section.id, name, reading.text)
    now = db.now()
    with con:
        con.execute("""UPDATE record_sources SET title = CASE WHEN title = '' THEN ? ELSE title END, dated = ?,
                       note_rel = ?, revision = revision + 1, last_checked = ?,
                       last_changed = CASE WHEN ? OR revision = 0 THEN ? ELSE last_changed END,
                       state = 'read', error = '', suggestion = ? WHERE id = ?""",
                    (reading.title or readers.title_in(reading.text, name), reading.date, note_rel, now,
                     int(swapped["changed"]), now, suggestion, r["id"]))
    return {**out, "state": "read", "facts": swapped["added"], "left_out": len(reading.facts) - len(facts)
            + len(swapped["left_out"]), "hidden": swapped["hidden"], "suggestion": suggestion}


def remove(con: sqlite3.Connection, source_id) -> dict:
    """Takes a source out of the record: its facts, its note and Hal's copy of its files; returns {"hidden": [ids
    of the cards Hal found that no source names any more, now hidden]}. LookupError when it is not there."""
    r = store.row(con, source_id)
    gone = profile_store.swap_source(con, r["id"], [])
    _drop_note(r["note_rel"])
    shutil.rmtree(store.files_dir(r["id"]), ignore_errors=True)
    with con:
        con.execute("DELETE FROM record_sources WHERE id = ?", (r["id"],))
    return {"hidden": gone["hidden"]}
