"""Tidying the record: what earlier reads filed, held to the rules a read follows now, after a backup.

The person presses **Tidy my record** (or runs `jobhunt record tidy`); the `record.ingest` action runs with
`tidy`. `prepare` runs once, when the tidy's batch has nothing pending yet:

1. A backup: a copy of the database (`core.backup.snapshot`) and a `.tar.gz` of the whole record folder, both
   in Hal's backups folder, so the tidy can be undone by hand.
2. Each note already filed is judged again by rules alone: whose it is (`note_role`: the role it was given, or
   from its old `about_owner` and kind), whether it looks private (`privacy.reason`), whether it is a settings
   or tool file (`discover.is_tool_file`), and whether it is slides the person's own notes cover
   (`lectures.Covered`). A supporting file's note (data, a figure, a camera photo, a picture with no
   words) goes, and the file is listed in its unit's note or skipped (`attach`). Facts its role may not
   give leave the profile and the note's key facts, and an employer, institution or issuer the document
   never names leaves its fact; course material loses contact details; code text is fenced. Copies of one
   project keep only the newest copy's note (`versions`). An empty note goes, and a note made from slides is
   course material (`vaultnotes`). Each project folder of the person's own work (`group.projects`) is found
   and put in the batch for its note, and the course units an older read split it into go
   (`projects.find_in_record`).
3. Notes the AI should read again are put back in the batch, to be rewritten in place (`engine._again`):
   converted course material, for a lecture note, and documents whose role the rules cannot settle, such
   as a report no AI has judged as the person's or someone else's. Each is read once: a note the AI wrote
   under these rules says so (`read_by: ai`, and `lecture_note` for course material), so a second tidy
   sends it to the AI only if its lecture note could not be written. Files called someone else's before
   the AI had to name who made them, or whose path names the person (`roles.decide`), are read again too,
   once, and files a rule held as
   private that today's rules let through (`_private_again`).

`finish` runs at the end of every tidy run, and is safe to run again: notes no item points at any more are
deleted with their facts and copied originals, entity notes outside `notes.noted` go (all of People and
Places), links to deleted notes become plain words, and the profile is merged again from its evidence
(`store.rebuild_found`), so the merge rules of today apply to everything. Then the project facts read inside a
project folder are filed on that folder's project card, and a card left with nothing is hidden as part of it
(`projects.fold_cards`).

Notes the person wrote are never touched: only notes with Hal's `hal:` or `type: entity` frontmatter are.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import tarfile
from pathlib import Path, PurePosixPath

from jobhunt.ingest import (
    attach,
    discover,
    frontmatter,
    items,
    lectures,
    notes,
    privacy,
    projects,
    rank,
    roles,
    vaultnotes,
    versions,
)

log = logging.getLogger(__name__)

BACKUP_REASON = "before tidying the record"
ARCHIVE = "record-{stamp}-before-tidy.tar.gz"
GONE_FOLDERS = ("People", "Places")           # entity notes of the kinds a read no longer makes
KEPT_ENTITY_FOLDERS = {"Organisations": "organisations", "Projects": "projects", "Skills": "skills",
                       "Courses": "courses"}
# Kinds of document whose role the rules cannot settle when no AI judged it: the AI reads them again.
JUDGED_BY_AI = ("cv", "letter", "personal_statement", "report", "coursework", "dissertation", "project",
                "repository", "essay", "writing", "other", "certificate", "transcript", "reference")
TEXT = "\n## Text\n\n"
FACTS = "## Key facts\n\n"
FACT_KIND = re.compile(r"^- (?:\*\*Check this:\*\* )?([A-Za-z]+):")     # as `notes._fact_line` writes it
LINK = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]*))?\]\]")


def backup(con: sqlite3.Connection, vault: Path) -> tuple[Path, Path]:
    """Copies the database and packs the record folder into Hal's backups folder; returns both paths."""
    from jobhunt.core import backup as core_backup

    db_copy = core_backup.snapshot(con, BACKUP_REASON)
    archive = db_copy.parent / ARCHIVE.format(stamp=core_backup.utc_stamp())
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(vault, arcname=vault.name)
    return db_copy, archive


def note_role(fm: dict, text: str, words: list[str], in_course: bool) -> str:
    """A filed note's role: the one a read gave it, else worked out from its older `about_owner` answer."""
    role = str(fm.get("role") or "")
    if role in roles.ROLES:
        return role
    about, doc_type = str(fm.get("about_owner") or ""), str(fm.get("doc_type") or "")
    if about == "true":
        return roles.OWN if roles.names_person(text, words) else roles.UNSURE
    if doc_type in roles.TAUGHT_TYPES or doc_type in lectures.MATERIAL_TYPES or in_course:
        return roles.TAUGHT
    return roles.UNSURE


def _original(vault: Path, text: str) -> Path | None:
    """The copy of the original a note embeds from the record's attachments, if any."""
    m = re.search(r"^## Original\n\n!?\[\[([^\]]+)\]\]", text, re.M)
    return vault / m.group(1) if m else None


def drop_note(con: sqlite3.Connection, vault: Path, note_rel: str) -> None:
    """Deletes a Hal note, the copy of its original, and every fact it gave."""
    from jobhunt.profile import store

    path = vault / note_rel
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        if (original := _original(vault, text)) is not None and original.is_file():
            original.unlink()
        path.unlink()
    store.replace_source(con, note_rel, lambda: None)


def _keep_allowed_facts(con: sqlite3.Connection, note_rel: str, role: str, named: str) -> None:
    """Takes out of the profile the facts this note gave that its role may not give; marks the rest's role,
    and leaves out of each an employer, institution or issuer the document (`named`: its path and text)
    never names (`understand.ORG_FIELDS`)."""
    from jobhunt.ingest import understand

    for e in con.execute("""SELECT e.id, i.kind, e.doc_type, e.said FROM profile_evidence e JOIN profile_items i
                            ON i.id = e.item_id WHERE e.source_rel = ?""", (note_rel,)).fetchall():
        if not roles.allowed(role, e["kind"], e["doc_type"]):
            con.execute("DELETE FROM profile_evidence WHERE id = ?", (e["id"],))
            continue
        said = json.loads(e["said"] or "{}")
        kept = {k: v for k, v in said.items()
                if k not in understand.ORG_FIELDS or understand.quote_found(str(v), named)}
        con.execute("UPDATE profile_evidence SET role = ?, said = ? WHERE id = ?", (role, json.dumps(kept), e["id"]))


def _unlink_gone(text: str) -> str:
    """`text` with links to People and Places notes as plain words.

    An older read linked the name in an email ("[[People/ama.pell|ama.pell]]@example.org"), which hid it from
    `lectures.scrub` until `finish` unlinked it again (2026-11-05).
    """
    def plain(m: re.Match) -> str:
        target = m.group(1).strip()
        if target.split("/", 1)[0] not in GONE_FOLDERS:
            return m.group(0)
        return m.group(2) or PurePosixPath(target).name
    return LINK.sub(plain, text)


def _given_facts(body: str, role: str, doc_type: str) -> str:
    """`body` with only the key facts its role gives; an older read listed a lecturer's email too."""
    if FACTS not in body:
        return body
    head, rest = body.split(FACTS, 1)
    block, sep, tail = rest.partition("\n\n")
    kept = [line for line in block.splitlines()
            if (m := FACT_KIND.match(line)) and roles.allowed(role, m.group(1).lower(), doc_type)]
    return head + FACTS + "\n".join(kept) + sep + tail if kept else head + tail


def _rewrite(path: Path, fm: dict, body: str, role: str, source: str) -> None:
    """Writes the note again with its role and only the facts it gives, without a date of birth, course
    material without contact details, code text fenced."""
    fm = {**fm, "role": role, "about_owner": roles.ABOUT_OWNER[role]}
    body = privacy.redact(_given_facts(body, role, str(fm.get("doc_type") or "")))
    if role == roles.TAUGHT:
        body = lectures.scrub(_unlink_gone(body))
    suffix = PurePosixPath(source.split("?")[0]).suffix.lower()
    if suffix in notes.FENCED_SUFFIXES and TEXT in body:
        head, text = body.split(TEXT, 1)
        if not text.lstrip().startswith("```"):
            body = head + TEXT + notes._fenced(text.rstrip("\n")) + "\n"
    notes.write(path, frontmatter.dump(fm) + body)


def _empty(row: sqlite3.Row, body: str, templates: vaultnotes.Templates) -> bool:
    """True when a filed note's file is empty (`vaultnotes.is_empty`), judged from the file itself while it is
    there, as the link pass may have changed its note's text."""
    path = Path(row["path"])
    text = body.split(TEXT, 1)[1] if TEXT in body else ""
    if path.suffix.lower() in vaultnotes.MARKDOWN and path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
    return vaultnotes.is_empty(row["rel"], text, templates, vaultnotes.front(path))


def _left_out(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return discover.is_tool_file(parts[-1]) or any(p.startswith(".") for p in parts[:-1])


def _versions(con: sqlite3.Connection, vault: Path) -> int:
    """Keeps only the newest copy's note of each project read before copies were told apart."""
    rows = con.execute("""SELECT * FROM ingest_items WHERE kind = 'repo' AND state = 'done' AND note IS NOT NULL
                          ORDER BY id""").fetchall()
    copies = [versions._copy(r, False, vault, set()) for r in rows if (vault / r["note"]).is_file()]
    dropped = 0
    for group in versions._groups(copies):
        if len({c.row["note"] for c in group}) < 2:
            continue
        best = max(group, key=lambda c: c.key)
        older = [c for c in group if c is not best]
        for c in older:
            if c.row["note"] != best.row["note"]:
                drop_note(con, vault, c.row["note"])
            items.update(con, c.row["id"], state=versions.STATE, dup_of=best.row["id"], note=None)
            dropped += 1
        versions._write_earlier(vault, best.row["note"], [versions._where(c.row) for c in older])
    return dropped


def prepare(con: sqlite3.Connection, batch: str, vault: Path) -> dict:
    """Backs up, then holds every filed note to today's rules; returns what it did, by kind of change."""
    from jobhunt.core import settings
    from jobhunt.ingest import engine, understand

    db_copy, archive = backup(con, vault)
    log.info("tidy: backed up the database to %s and the record to %s", db_copy, archive)
    words = roles.identity(understand.owner(), str(settings.get("you.other_names") or ""), con,
                           own=roles.own_links())
    covered = lectures.Covered(con)
    done = {"left_out": 0, "private": 0, "covered": 0, "again": 0, "older_version": 0, "judged": 0,
            attach.STATE: 0, "skipped": 0, "empty": 0}
    units: attach.Units | None = None
    templates = vaultnotes.Templates.of(con.execute("SELECT DISTINCT path, rel FROM ingest_items "
                                                    "WHERE kind = 'file'").fetchall())
    done["older_version"] = _versions(con, vault)
    done["projects"] = projects.find_in_record(con, batch, vault)
    rows = con.execute("""SELECT * FROM ingest_items WHERE state = 'done' AND note IS NOT NULL
                          AND kind IN ('file', 'repo', 'page') ORDER BY rank, kind = 'file', rel""").fetchall()
    for row in rows:
        path = vault / row["note"]
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body = frontmatter.parse(text)
        if not fm.get("hal"):
            continue                      # a note the person wrote
        if _left_out(row["rel"]):
            drop_note(con, vault, row["note"])
            items.update(con, row["id"], state="skipped", error="settings or tool file", note=None)
            done["left_out"] += 1
            continue
        if row["kind"] == "file" and (attach.supporting(row) or (attach.is_picture(row["rel"]) and TEXT not in body)):
            drop_note(con, vault, row["note"])
            # built at the first supporting file: rows go by rank, so the documents before it are settled
            units = units or attach.Units(con)
            done[attach.place(con, vault, row, units)] += 1
            continue
        if row["kind"] == "file" and _empty(row, body, templates):
            drop_note(con, vault, row["note"])
            items.update(con, row["id"], state="skipped", error=vaultnotes.EMPTY, note=None)
            done["empty"] += 1
            continue
        if why := privacy.held_back(dict(row), body):
            drop_note(con, vault, row["note"])
            privacy.hold(con, row["id"], why)
            items.update(con, row["id"], note=None)
            done["private"] += 1
            continue
        doc_type = str(fm.get("doc_type") or row["doc_type"] or "")
        if lectures.is_material(row["rel"], doc_type) and (hit := covered.find(row["rel"], Path(row["rel"]).stem)):
            drop_note(con, vault, row["note"])
            engine._also_at(vault, hit[1], str(fm.get("source") or row["path"]))
            items.update(con, row["id"], state="covered", dup_of=hit[0], note=hit[1], role=roles.TAUGHT)
            done["covered"] += 1
            continue
        derived = row["kind"] == "file" and vaultnotes.taught_from_source(vaultnotes.front(Path(row["path"])))
        role = roles.TAUGHT if derived else note_role(fm, f"{row['rel']}\n{body}", words,
                                                     bool(row["unit"]) or "\n## Part of\n" in body)
        _keep_allowed_facts(con, row["note"], role, f"{row['rel']}\n{body.split(TEXT, 1)[-1]}")
        _rewrite(path, fm, body, role, str(fm.get("source") or row["path"]))
        items.update(con, row["id"], role=role, **({"model": items.BY_SOURCE} if derived else {}))
        if role == roles.TAUGHT and doc_type in lectures.NOTE_TYPES and lectures.is_typed(row["rel"]):
            covered.add(row["id"], row["rel"], Path(row["rel"]).stem, row["note"])
        done["judged"] += 1
        if not derived and _ask_again(row, role, doc_type, fm):
            con.execute("UPDATE ingest_items SET batch = ?, state = 'pending', stage = 'discovered' WHERE id = ?",
                        (batch, row["id"]))
            done["again"] += 1
    done["not_mine_again"] = _not_mine_again(con, batch, words)
    done["private_again"] = _private_again(con, batch)
    con.commit()
    return done


def _private_again(con: sqlite3.Connection, batch: str) -> int:
    """Puts back in the batch the files a rule held as private that today's rules let through.

    A results table's decimals were taken for a card number (2026-11-06). Files held on the AI's word stay
    held: reading them again would send their text to the AI.
    """
    from jobhunt.ingest import convert, engine

    again = 0
    for row in con.execute("""SELECT * FROM ingest_items WHERE state = 'private' AND allowed = 0
                              AND private IN ({})""".format(",".join("?" * len(privacy.RULE_REASONS))),
                           privacy.RULE_REASONS).fetchall():
        path = Path(row["path"])
        if path.is_file() and not privacy.reason(row["rel"], convert.text_of(path, engine._rule(row))):
            con.execute("""UPDATE ingest_items SET batch = ?, state = 'pending', stage = 'discovered', private = NULL
                           WHERE id = ?""", (batch, row["id"]))
            again += 1
    return again


def _not_mine_again(con: sqlite3.Connection, batch: str, words: list[str]) -> int:
    """Puts back in the batch the files called someone else's before the AI had to say who made them, or
    whose path names the person (`words`), which `roles.decide` no longer lets be someone else's."""
    again = 0
    for row in con.execute("""SELECT id, rel, path, error FROM ingest_items WHERE state = 'not_mine'""").fetchall():
        old = not str(row["error"] or "").startswith(roles.MADE_BY)
        if (old or (words and roles.names_person(row["rel"], words))) and Path(row["path"]).exists():
            con.execute("""UPDATE ingest_items SET batch = ?, state = 'pending', stage = 'discovered', role = '',
                           error = NULL WHERE id = ?""", (batch, row["id"]))
            again += 1
    return again


def _ask_again(row: sqlite3.Row, role: str, doc_type: str, fm: dict) -> bool:
    """True when the AI should read a filed note again: course material from a converted file, for its
    lecture note, or a document of a kind whose role only the AI can settle and no AI has judged yet, such as
    coursework in a course folder, which older reads filed by rules (`rank.in_unit`)."""
    if row["kind"] == "page" or not Path(row["path"]).exists():
        return False
    if row["unit"] and rank.in_unit(row["rel"], rank.RULES[9]).rich and fm.get("read_by") != "ai":
        return True
    if role == roles.TAUGHT:
        return not lectures.is_typed(row["rel"]) and row["kind"] == "file" and fm.get("lecture_note") != "true"
    return role == roles.UNSURE and doc_type in JUDGED_BY_AI and fm.get("read_by") != "ai"


def _pointed_at(con: sqlite3.Connection) -> set[str]:
    """The notes a read still stands behind: the bulk read's, and those of the sources added to each section."""
    return {r[0] for r in con.execute("SELECT DISTINCT note FROM ingest_items WHERE note IS NOT NULL "
                                      "UNION SELECT note_rel FROM record_sources WHERE note_rel IS NOT NULL")}


def _sweep_sources(con: sqlite3.Connection, vault: Path) -> int:
    """Deletes Hal's source notes no item points at any more (someone else's, an older copy, private)."""
    root = vault / notes.SOURCES
    if not root.is_dir():
        return 0
    kept, n = _pointed_at(con), 0
    for path in root.rglob("*.md"):
        if path.stem == notes.INDEX:
            continue
        rel = path.relative_to(vault).as_posix()
        if rel in kept:
            continue
        fm, _ = frontmatter.parse(path.read_text(encoding="utf-8", errors="replace"))
        if fm.get("hal") and fm.get("type") == "source":
            drop_note(con, vault, rel)
            n += 1
    return n


def _profile_names(con: sqlite3.Connection) -> dict[str, set[str]]:
    """The entity keys the profile's visible items name, by entity kind."""
    out: dict[str, set[str]] = {k: set() for k in KEPT_ENTITY_FOLDERS.values()}
    for r in con.execute("SELECT kind, fields FROM profile_items WHERE state != 'hidden'"):
        f = json.loads(r["fields"])
        for k in notes.ORG_FIELDS:
            if f.get(k):
                out["organisations"].add(notes.entity_key(str(f[k])))
        if r["kind"] == "skill" and f.get("name"):
            out["skills"].add(notes.entity_key(str(f["name"])))
        if r["kind"] == "project" and f.get("name"):
            out["projects"].add(notes.entity_key(str(f["name"])))
    return out


def _sweep_entities(con: sqlite3.Connection, vault: Path) -> int:
    """Deletes Hal's entity notes a read no longer makes: People and Places, and others the profile does not
    name. Courses stay while a note still links to them."""
    names, n = _profile_names(con), 0
    for folder in (*GONE_FOLDERS, *KEPT_ENTITY_FOLDERS):
        root = vault / folder
        if not root.is_dir():
            continue
        kind = KEPT_ENTITY_FOLDERS.get(folder)
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            fm, _ = frontmatter.parse(text)
            if fm.get("type") != "entity":
                continue                  # a note the person wrote
            if kind == "courses":
                alive = any((vault / f"{m}.md").is_file() for m, _ in LINK.findall(text.split(notes.MENTIONED, 1)[-1]))
                if alive:
                    continue
            elif kind is not None and notes.entity_key(path.stem) in names[kind]:
                continue
            path.unlink()
            n += 1
        if folder in GONE_FOLDERS and not any(root.iterdir()):
            root.rmdir()
    return n


def _unlink_missing(vault: Path) -> int:
    """Turns links to notes that are gone into plain words, in Hal's notes; returns how many notes changed."""
    changed = 0
    for path in vault.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, _ = frontmatter.parse(text)
        if not (fm.get("hal") or fm.get("type") == "entity"):
            continue

        def plain(m: re.Match) -> str:
            target = m.group(1).strip()
            if (vault / f"{target}.md").is_file() or (vault / target).is_file() or "/" not in target:
                return m.group(0)
            return m.group(2) or PurePosixPath(target).name
        new = LINK.sub(plain, text)
        if new != text:
            notes.write(path, new)
            changed += 1
    return changed


def finish(con: sqlite3.Connection, vault: Path) -> dict:
    """Deletes what no longer belongs, merges the profile again and rewrites the indexes; returns counts."""
    from jobhunt.profile import store

    out = {"notes_deleted": _sweep_sources(con, vault)}
    store.rebuild_found(con)
    out["project_cards_folded"] = projects.fold_cards(con)
    out["entities_deleted"] = _sweep_entities(con, vault)
    out["links_unlinked"] = _unlink_missing(vault)
    notes.write_indexes(vault)
    con.commit()
    return out

