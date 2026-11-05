"""The ingest's stages run in order over one batch: find, group and rank, then read and write each item.

`run` is what the `record.ingest` action calls. Every item is committed as it ends, so a restart (the kind
is resumable) finds the batch's rows and goes on with those still pending; files already hashed are not
hashed again. One file that cannot be read is an error on its own row and never stops the rest.

Each document's role (`roles`: the person's own, taught, someone else's or unsure) is judged on this thread,
which holds the database the person's names come from. Someone else's document is not filed at all and gives
nothing; the others give only the facts their role may.

Duplicates are found by SHA-256 against every item read before, in this batch or an earlier one: the copy
gets no note of its own, and its path is added once to the first copy's note under "Also at".
Copies of one code project are found by `versions.settle` before reading: only the newest is read, and a
newer copy than one read before rewrites that project's note in place.

Up to `ai.parallel` items are with worker threads at once, which only convert and ask the AI; this thread
alone writes, in the items' order, so SQLite and the record each have one writer. A read `again` puts notes
first written by rules back in a batch and rewrites each at its own path, so links to it still hold.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jobhunt import profile, record
from jobhunt.core import events
from jobhunt.ingest import convert, discover, group, items, notes, rank, roles, sources, understand, versions

log = logging.getLogger(__name__)

STEPS = 3                    # finding, reading, linking
ERROR_CHARS = 300
ALSO_AT = "## Also at"
KEPT_SECTIONS = ("## Part of", ALSO_AT, versions.EARLIER)   # added after a note was written; kept on a re-read
NEVER_BY_MODEL = ("code", "image", "media")
AHEAD = 2                    # items handed to workers per worker, so none waits while this thread writes
NO_WEB = "Reading web links is not available in this version of Hal yet."

Progress = Callable[[int, int, str], None]


def new_batch() -> str:
    """A new batch id."""
    return uuid.uuid4().hex


def vault() -> Path:
    """The record folder, made when it is not there yet: the person asked for files to go into it."""
    folder = Path(record._live("VAULT"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Finding --------------------------------------------------------------------------------------

class _Finder:
    """Adds the items of one batch from its folders, zips, drop folder and pages."""

    def __init__(self, con: sqlite3.Connection, batch: str) -> None:
        self.con, self.batch = con, batch
        self.seen = items.seen_paths(con, batch)
        self.left_out: dict[str, int] = {}
        self.zips = 0

    def walk(self, root: Path, prefix: str | None = None) -> None:
        walked = discover.walk(root, prefix)
        for why, n in walked.left_out.items():
            self.left_out[why] = self.left_out.get(why, 0) + n
        repos = group.outermost(walked.repos)
        rel_of = {f.path: f.rel for f in walked.files}
        top = root.name if prefix is None else prefix
        for repo in sorted(repos):
            shown = top if repo == root else f"{top}/{repo.relative_to(root).as_posix()}"
            items.add(self.con, self.batch, path=str(repo), rel=shown, kind="repo", rank=rank.REPO.rank,
                      category=rank.REPO.category, doc_type=rank.REPO.doc_type)
        loose = [f for f in walked.files if group.repo_of(f.path, repos) is None]
        zips = [f for f in loose if f.path.suffix.lower() == ".zip"]
        for z in zips:
            self.unzip(z.path, z.rel)
        files = [f for f in loose if f not in zips]
        courses = group.courses([(f.path, f.rel) for f in files])
        unit_of = {path: folder for folder, members in courses.items() for path, _ in members}
        for folder in sorted(courses):
            items.add(self.con, self.batch, path=str(folder), rel=rel_of[courses[folder][0][0]].rsplit("/", 1)[0],
                      kind="course", rank=rank.COURSE.rank, category=rank.COURSE.category,
                      doc_type=rank.COURSE.doc_type)
        for f in files:
            if str(f.path) in self.seen:
                continue
            discover.fill(f)
            rule = rank.rule_for(f.rel, f.path)
            items.add(self.con, self.batch, path=str(f.path), rel=f.rel, unit=str(unit_of[f.path])
                      if f.path in unit_of else None, size=f.size, sha256=f.sha256, mtime=_iso(f.mtime),
                      magic=f.magic, rank=rule.rank, category=rule.category, doc_type=rule.doc_type, state=f.state)
            self.con.commit()

    def unzip(self, zip_path: Path, shown: str) -> None:
        self.zips += 1
        dest = sources.unpacked_dir(self.batch) / f"{self.zips}-{notes.slug(zip_path.stem)}"
        if not dest.exists():
            report = sources.unpack(zip_path, dest, label=shown)
            for problem in report.problems():
                item = items.add(self.con, self.batch, path=f"{zip_path}!{problem}", rel=shown, state="error")
                items.update(self.con, item, error=problem)
            self.con.commit()
        self.walk(dest, shown)

    def pages(self, urls: list[str]) -> list[str]:
        """Fetches web links through `ingest.web` when it is there; the problems, in words."""
        try:
            from jobhunt.ingest import web
        except ImportError:
            return [NO_WEB]
        try:
            found = web.fetch(urls)
        except (OSError, ValueError) as e:
            return [f"The web links could not be read: {e}"]
        folder = sources.ingest_dir() / "pages" / self.batch
        folder.mkdir(parents=True, exist_ok=True)
        for n, page in enumerate(found, start=1):
            path = folder / f"{n}.md"
            path.write_text(f"# {page.title or page.url}\n\n{page.text}", encoding="utf-8")
            rule = rank.REPO if page.kind == "repo" else (rank.RULES[7] if page.kind == "profile"
                                                         else rank.rule_for(page.title or page.url))
            items.add(self.con, self.batch, path=str(path), rel=page.url, kind="page", size=path.stat().st_size,
                      magic="text", rank=rule.rank, category=rule.category, doc_type=rule.doc_type)
        self.con.commit()
        return []


def find(con: sqlite3.Connection, batch: str, *, folders=(), uploads=(), drop: bool = False, urls=()) -> dict:
    """Adds every item of the batch's inputs; returns what was left out and any problems, in words."""
    f = _Finder(con, batch)
    problems = []
    for given in [*folders, *uploads]:
        path = Path(given).expanduser()
        if not path.exists():
            problems.append(f"{path} is not there any more.")
        elif path.is_file() and path.suffix.lower() == ".zip":
            f.unzip(path, path.name)
        else:
            f.walk(path)
    if drop:
        folder = sources.drop_folder()
        if folder.is_dir():
            f.walk(folder)
    if urls:
        problems += f.pages(list(urls))
    con.commit()
    return {"left_out": f.left_out, "problems": problems}


# Reading --------------------------------------------------------------------------------------

def _rule(row: sqlite3.Row) -> rank.Rule:
    if row["kind"] == "repo":
        return rank.REPO
    if row["kind"] == "course":
        return rank.COURSE
    rule = rank.rule_for(row["rel"], Path(row["path"]) if row["kind"] == "file" else None)
    if row["kind"] == "page":
        return next((r for r in (*rank.RULES, rank.REPO) if r.doc_type == row["doc_type"]), rule)
    if row["unit"] and rule.rich:   # a course's files are light, whatever their names say
        return rank.RULES[9]
    return rule


def _source(row: sqlite3.Row, batch: str) -> str:
    """Where the item came from, as the note's `source` says it: its path, its place in a zip, or its URL."""
    if row["kind"] == "page":
        return row["rel"]
    unpacked = sources.unpacked_dir(batch).resolve()
    return row["rel"] if unpacked in Path(row["path"]).resolve().parents else row["path"]


def _also_at(vault_dir: Path, note_rel: str, where: str) -> None:
    path = vault_dir / note_rel
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    line = f"- `{where}`"
    if line in text:
        return
    if ALSO_AT in text:
        text = text.replace(ALSO_AT + "\n\n", f"{ALSO_AT}\n\n{line}\n", 1)
    else:
        text = text.replace("\n## Original\n", f"\n{ALSO_AT}\n\n{line}\n\n## Original\n", 1)
    notes.write(path, text)


def _judge(con: sqlite3.Connection, row: sqlite3.Row, desc: dict, text: str) -> None:
    """Sets the document's role and keeps only the facts that role may give (`roles`)."""
    from jobhunt.core import settings

    words = roles.identity(understand.owner(), str(settings.get("you.other_names") or ""), con)
    role = roles.decide(desc.get("said"), doc_type=desc["doc_type"], text=f"{row['rel']}\n{text}", words=words,
                        in_course=bool(row["unit"]))
    desc["role"], desc["about_owner"] = role, roles.ABOUT_OWNER[role]
    desc["facts"] = [f for f in desc["facts"] if roles.allowed(role, f["kind"], desc["doc_type"])]


def _give_facts(con: sqlite3.Connection, desc: dict, note_rel: str) -> int:
    from jobhunt.profile import store

    n = 0
    for f in desc["facts"]:
        try:
            store.add_found(con, f["kind"], f["fields"], {"source_rel": note_rel, "quote": f["quote"],
                                                         "field": f["field"], "doc_type": desc["doc_type"],
                                                         "verified": f["verified"], "role": desc["role"]})
        except ValueError as e:  # one malformed fact from the model is left out, not the whole document
            log.info("left out a fact from %s: %s", note_rel, e)
            continue
        n += 1
    return n


def _pictures_by_ai() -> bool:
    """True when the words read from a certificate or artwork picture go to the AI (`record.ingest_vision`).

    The AI layer takes text only, so it is the picture's OCR text that is read, not the picture itself.
    """
    from jobhunt.core import settings

    return bool(settings.get("record.ingest_vision"))


def by_model(rule: rank.Rule, is_picture: bool, *, every: bool = False) -> bool:
    """True when an item goes to the AI: a rich document, any document when `every` or `record.read_all`
    asks, and a picture's words when `record.ingest_vision` is on. Code files never do: a repository is read
    as one unit."""
    from jobhunt.core import settings

    if is_picture:
        return _pictures_by_ai()
    return rule.rich or ((every or bool(settings.get("record.read_all"))) and rule.doc_type not in NEVER_BY_MODEL)


@dataclass
class _Read:
    """What a worker made of one item: its text and description, or no description for a file with no text."""

    text: str
    desc: dict | None


def _prepare(row: dict, rule: rank.Rule) -> _Read:
    """Converts one item and describes it, by the AI or by rules; touches neither the database nor the record.

    Runs on a worker thread, so several items can be with the AI at once (`ai.parallel`).
    """
    path = Path(row["path"])
    text = convert.repo_text(path) if row["kind"] == "repo" else convert.text_of(path, rule)
    is_picture = path.suffix.lower() in rank.IMAGE_SUFFIXES | rank.MEDIA_SUFFIXES
    if not text.strip() and not is_picture:
        return _Read(text, None)
    if text.strip() and by_model(rule, is_picture, every=bool(row["note"])):
        desc = understand.ask(row["rel"], text, rule)
    else:
        desc = understand.light(row["rel"], text, rule)
        if is_picture and not text.strip():
            desc["title"] = path.stem
    return _Read(text, desc)


def _kept(note_text: str) -> str:
    """The sections of a note that the ingest added after it was written ("Part of", "Also at"), to carry over."""
    out = []
    for heading in KEPT_SECTIONS:
        found = re.search(rf"^{re.escape(heading)}\n.*?(?=^## |\Z)", note_text, re.S | re.M)
        if found:
            out.append(found.group(0).rstrip("\n") + "\n\n")
    return "".join(out)


def _again(con: sqlite3.Connection, row: sqlite3.Row, read: _Read, vault_dir: Path, batch: str) -> str | None:
    """Writes a new reading over a note at the same path: the AI's reading of a note first written by
    rules, or a newer copy of a project (`versions.settle`), whose note then names the newer copy."""
    from jobhunt.profile import store

    path = vault_dir / row["note"]
    newer = row["kind"] == "repo"   # `mark_again` never moves a repository, so a repo here is a newer copy
    if read.desc is None or (read.desc.get("by") != "model" and not newer) or not path.is_file():
        # the AI did not answer, or the note is gone: the note stays as it was, to be read again next time
        items.update(con, row["id"], state="done")
        return None
    desc = {**read.desc, "category": row["category"] or read.desc["category"]}   # the note stays in its folder
    _judge(con, row, desc, read.text)
    if desc["role"] == roles.OTHERS:
        store.replace_source(con, row["note"], lambda: None)
        path.unlink()
        items.update(con, row["id"], stage="understood", state="not_mine", model=1, role=desc["role"], note=None)
        return None
    old = path.read_text(encoding="utf-8", errors="replace")
    fm, _ = notes.frontmatter.parse(old)
    source = _source(row, batch) if newer else str(fm.get("source") or row["path"])
    notes.write_source(vault_dir, desc, text=read.text, source=source, sha256=row["sha256"] or "", original=None,
                       at=path)
    if kept := _kept(old):
        text = path.read_text(encoding="utf-8")
        notes.write(path, text.replace("\n## Original\n", f"\n{kept}## Original\n", 1))
    notes.add_mentions(vault_dir, desc, row["note"])
    store.replace_source(con, row["note"], lambda: _give_facts(con, desc, row["note"]))
    items.update(con, row["id"], stage="written", state="done", model=int(desc.get("by") == "model"),
                 doc_type=desc["doc_type"], role=desc["role"])
    return row["note"]


def _finish(con: sqlite3.Connection, row: sqlite3.Row, read: _Read, batch: str, drop_root: Path | None) -> str | None:
    """Writes one prepared item's note, entities and facts; returns the note's path inside the record."""
    vault_dir = vault()
    if row["note"]:
        return _again(con, row, read, vault_dir, batch)
    items.update(con, row["id"], stage="converted")
    if read.desc is None:
        items.update(con, row["id"], state="no_text")
        return None
    desc = read.desc
    _judge(con, row, desc, read.text)
    items.update(con, row["id"], stage="understood", model=int(desc.get("by") == "model"), role=desc["role"])
    if desc["role"] == roles.OTHERS:
        # someone else's document is not the person's record: no note, no copy, no facts; the page names it
        items.update(con, row["id"], state="not_mine")
        return None
    path = Path(row["path"])
    moving = drop_root is not None and drop_root in path.parents
    original = None
    if row["kind"] == "file":
        original = notes.keep_original(vault_dir, path, desc["category"], move=moving)
    note_rel = notes.write_source(vault_dir, desc, text=read.text, source=_source(row, batch),
                                  sha256=row["sha256"] or "", original=original)
    notes.add_mentions(vault_dir, desc, note_rel)
    _give_facts(con, desc, note_rel)
    items.update(con, row["id"], stage="written", state="done", note=note_rel, category=desc["category"],
                 doc_type=desc["doc_type"], role=desc["role"])
    return note_rel


def _copy_of(con: sqlite3.Connection, row: sqlite3.Row, batch: str) -> bool:
    """True when the item is a copy of one read before: it is linked to that note and gets none of its own."""
    if row["note"]:          # a note read again is its own first copy
        return False
    first = items.first_copy(con, row["sha256"], row["id"])
    if first is None:
        return False
    if first["note"] and row["path"] != first["path"]:   # the same file read again is not a copy
        _also_at(vault(), first["note"], _source(row, batch))
    items.update(con, row["id"], state="duplicate", dup_of=first["id"], note=first["note"])
    return True


def read_one(con: sqlite3.Connection, row: sqlite3.Row, batch: str, drop_root: Path | None) -> str | None:
    """Reads one pending item into a note; returns the note's path inside the record, or None for none."""
    if _copy_of(con, row, batch):
        return None
    return _finish(con, row, _prepare(dict(row), _rule(row)), batch, drop_root)


def _read_items(con: sqlite3.Connection, todo: list[sqlite3.Row], batch: str, drop_root: Path | None,
              progress: Progress, done: int, total: int) -> list[str]:
    """Reads the items in order, up to `ai.parallel` of them with workers at once; returns the notes written.

    Workers only convert and ask; this thread writes, in the items' own order, so the record and the
    database see one writer and a CV still lands before the notes. A file whose bytes match one handed to a
    worker already waits for it, then is linked as its copy rather than read twice.
    """
    from jobhunt.core import settings

    workers = max(1, int(settings.get("ai.parallel")))
    written: list[str] = []
    queue = iter(todo)
    in_hand: deque[tuple[sqlite3.Row, Future | None]] = deque()
    handed: set[str] = set()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest")
    try:
        while True:
            while len(in_hand) < workers * AHEAD and (row := next(queue, None)) is not None:
                sha = row["sha256"] or ""
                if not row["note"] and sha and sha in handed:
                    in_hand.append((row, None))
                    continue
                if sha:
                    handed.add(sha)
                in_hand.append((row, pool.submit(_prepare, dict(row), _rule(row))))
            if not in_hand:
                break
            row, job = in_hand.popleft()
            progress(done, total, row["rel"])
            try:
                if _copy_of(con, row, batch) or job is None:
                    note = None
                else:
                    note = _finish(con, row, job.result(), batch, drop_root)
            except Exception as e:  # one unreadable file must not stop the rest; its row keeps the words
                log.warning("could not read %s: %s", row["rel"], e)
                items.update(con, row["id"], state="error", error=str(e)[:ERROR_CHARS] or type(e).__name__)
                note = None
            con.commit()
            if note:
                written.append(note)
            done += 1
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    progress(done, total, "")
    return written


def mark_again(con: sqlite3.Connection, batch: str) -> int:
    """Puts every note written by rules from a document back in `batch` to be read by the AI; returns how many.

    Each file's newest row is moved, so its note is rewritten in place and its links still lead to it. Code
    and pictures are left as they are (see `by_model`), and so is a file no longer on this computer.
    """
    rows = con.execute("""SELECT * FROM ingest_items WHERE id IN (SELECT max(id) FROM ingest_items
                          WHERE state = 'done' AND model = 0 AND kind = 'file' AND note IS NOT NULL GROUP BY path)
                          ORDER BY rank, rel""").fetchall()
    n = 0
    for row in rows:
        path = Path(row["path"])
        is_picture = path.suffix.lower() in rank.IMAGE_SUFFIXES | rank.MEDIA_SUFFIXES
        if is_picture or not by_model(_rule(row), False, every=True) or not path.is_file():
            continue
        con.execute("UPDATE ingest_items SET batch = ?, state = 'pending', stage = 'discovered' WHERE id = ?",
                    (batch, row["id"]))
        n += 1
    con.commit()
    return n


def _courses(con: sqlite3.Connection, batch: str) -> list[str]:
    """Writes each course unit's summary note, linking its files' notes, and links them back to it."""
    vault_dir, written = vault(), []
    for unit in con.execute("SELECT * FROM ingest_items WHERE batch = ? AND kind = 'course' AND state = 'pending'",
                            (batch,)).fetchall():
        members = con.execute("""SELECT rel, note FROM ingest_items WHERE batch = ? AND unit = ? AND note IS NOT NULL
                                 ORDER BY rel""", (batch, unit["path"])).fetchall()
        if not members:
            items.update(con, unit["id"], state="no_text")
            continue
        texts = []
        for m in members:
            _, body = notes.frontmatter.parse((vault_dir / m["note"]).read_text(encoding="utf-8", errors="replace"))
            texts.append(body)
        title = Path(unit["path"]).name
        desc = understand.light(unit["rel"] + "/", "\n\n".join(texts), rank.COURSE)
        desc["title"] = title
        desc["summary"] = f"{len(members)} files of notes and slides from {title}."
        files = ["## Files", "", *(f"- {notes.link(m['note'])}" for m in members)]
        note_rel = notes.write_source(vault_dir, desc, text="\n".join(files), source=unit["path"], sha256="",
                                      original=None)
        for m in members:
            path = vault_dir / m["note"]
            notes.write(path, path.read_text(encoding="utf-8").replace(
                "\n## Original\n", f"\n## Part of\n\n- {notes.link(note_rel, title)}\n\n## Original\n", 1))
        items.update(con, unit["id"], stage="written", state="done", note=note_rel)
        written.append(note_rel)
    return written


def drop_has_new(con: sqlite3.Connection) -> bool:
    """True when the drop folder holds a file no ingest has finished with (by path and size)."""
    folder = sources.drop_folder()
    if not folder.is_dir():
        return False
    known = {(r[0], r[1]) for r in con.execute(
        f"SELECT path, size FROM ingest_items WHERE state IN ({','.join('?' * len(items.FINAL))})", items.FINAL)}
    for here, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not discover.is_junk_dir(d) and not d.startswith(".")]
        for name in names:
            path = Path(here) / name
            if name.startswith(".") or discover.is_binary_name(name) or path.is_symlink():
                continue
            if (str(path), path.stat().st_size) not in known:
                return True
    return False


def run(con: sqlite3.Connection, batch: str, *, folders=(), uploads=(), drop: bool = False, urls=(),
        again: bool = False, step: Callable[[str], None], progress: Progress) -> dict:
    """The whole ingest of one batch; returns its counts, what was left out and the problems found.

    With `again`, the batch is the notes written by rules from documents, read now by the AI (`mark_again`);
    on a carry-on after a restart they are already in the batch.
    """
    step("Finding your files")
    found = find(con, batch, folders=folders, uploads=uploads, drop=drop, urls=urls)
    if again and not items.pending(con, batch):
        mark_again(con, batch)
    step("Reading each file, your CV and other rich documents first")
    drop_root = sources.drop_folder().resolve() if drop else None
    versions.settle(con, batch, vault())
    todo = [r for r in items.pending(con, batch) if r["kind"] != "course"]
    total = sum(items.counts(con, batch)["states"].values())
    written = _read_items(con, todo, batch, drop_root, progress, total - len(todo), total)
    versions.record_earlier(con, batch, vault())
    step("Linking your notes")
    written += _courses(con, batch)
    vault_dir = vault()
    notes.link_pass(vault_dir, written)
    notes.write_indexes(vault_dir)
    con.commit()
    if con.execute("SELECT 1 FROM profile_items LIMIT 1").fetchone():
        profile.write_notes(vault_dir, con)
    # the store publishes nothing per fact, so a large ingest does not flood the event stream
    events.publish("profile", batch=batch)
    if not items.pending(con, batch):
        sources.remove(sources.unpacked_dir(batch))
        sources.remove(sources.ingest_dir() / "pages" / batch)
        for given in uploads:
            if sources.uploads_dir().resolve() in Path(given).resolve().parents:
                Path(given).unlink(missing_ok=True)
    try:
        record.sync()
    except record.RecordUnavailable as e:
        found["problems"].append(str(e))
    c = items.counts(con, batch)
    return {"batch": batch, "notes": len(written), "states": c["states"], "categories": c["categories"],
            "left_out": found["left_out"], "problems": found["problems"]}
