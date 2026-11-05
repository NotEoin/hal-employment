"""The ingest's stages run in order over one batch: find, group and rank, then read and write each item.

`run` is what the `record.ingest` action calls. Every item is committed as it ends, so a restart (the kind
is resumable) finds the batch's rows and goes on with those still pending; files already hashed are not
hashed again. One file that cannot be read is an error on its own row and never stops the rest.

Duplicates are found by SHA-256 against every item read before, in this batch or an earlier one: the copy
gets no note of its own, and its path is added once to the first copy's note under "Also at".
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from jobhunt import record
from jobhunt.ingest import convert, discover, group, items, notes, rank, sources, understand

log = logging.getLogger(__name__)

STEPS = 3                    # finding, reading, linking
WEB_PAGE_CAP = 30           # pages read from one link's site, as the design of the web reader sets it
ERROR_CHARS = 300
ALSO_AT = "## Also at"
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
            found = web.fetch(urls, page_cap=WEB_PAGE_CAP)
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
    path.write_text(text, encoding="utf-8")


def _give_facts(con: sqlite3.Connection, desc: dict, note_rel: str) -> int:
    from jobhunt.profile import store

    n = 0
    for f in desc["facts"]:
        store.add_found(con, f["kind"], f["fields"], {"source_rel": note_rel, "quote": f["quote"],
                                                     "field": f["field"], "doc_type": desc["doc_type"],
                                                     "verified": f["verified"]})
        n += 1
    return n


def _pictures_by_ai() -> bool:
    """True when the words read from a certificate or artwork picture go to the AI (`record.ingest_vision`).

    The AI layer takes text only, so it is the picture's OCR text that is read, not the picture itself.
    """
    from jobhunt.core import settings

    return bool(settings.get("record.ingest_vision"))


def read_one(con: sqlite3.Connection, row: sqlite3.Row, batch: str, drop_root: Path | None) -> str | None:
    """Reads one pending item into a note; returns the note's path inside the record, or None for none."""
    vault_dir = vault()
    first = items.first_copy(con, row["sha256"], row["id"])
    if first is not None:
        if first["note"] and row["path"] != first["path"]:   # the same file read again is not a copy
            _also_at(vault_dir, first["note"], _source(row, batch))
        items.update(con, row["id"], state="duplicate", dup_of=first["id"], note=first["note"])
        return None
    rule = _rule(row)
    path = Path(row["path"])
    text = convert.repo_text(path) if row["kind"] == "repo" else convert.text_of(path, rule)
    items.update(con, row["id"], stage="converted")
    # Committed before the model is asked: the AI layer writes its usage row on a connection of its own, and
    # would wait out the busy timeout behind this one's open write.
    con.commit()
    is_picture = path.suffix.lower() in rank.IMAGE_SUFFIXES | rank.MEDIA_SUFFIXES
    if not text.strip() and not is_picture:
        items.update(con, row["id"], state="no_text")
        return None
    if text.strip() and (_pictures_by_ai() if is_picture else rule.rich):
        desc = understand.ask(row["rel"], text, rule)
    else:
        desc = understand.light(row["rel"], text, rule)
        if is_picture and not text.strip():
            desc["title"] = path.stem
    items.update(con, row["id"], stage="understood", model=int(desc.get("by") == "model"))
    moving = drop_root is not None and drop_root in path.parents
    original = None
    if row["kind"] == "file":
        original = notes.keep_original(vault_dir, path, desc["category"], move=moving)
    note_rel = notes.write_source(vault_dir, desc, text=text, source=_source(row, batch), sha256=row["sha256"] or "",
                                  original=original)
    notes.add_mentions(vault_dir, desc, note_rel)
    _give_facts(con, desc, note_rel)
    items.update(con, row["id"], stage="written", state="done", note=note_rel, category=desc["category"],
                 doc_type=desc["doc_type"])
    return note_rel


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
            path.write_text(path.read_text(encoding="utf-8").replace(
                "\n## Original\n", f"\n## Part of\n\n- {notes.link(note_rel, title)}\n\n## Original\n", 1),
                encoding="utf-8")
        items.update(con, unit["id"], stage="written", state="done", note=note_rel)
        written.append(note_rel)
    return written


def run(con: sqlite3.Connection, batch: str, *, folders=(), uploads=(), drop: bool = False, urls=(),
        step: Callable[[str], None], progress: Progress) -> dict:
    """The whole ingest of one batch; returns its counts, what was left out and the problems found."""
    step("Finding your files")
    found = find(con, batch, folders=folders, uploads=uploads, drop=drop, urls=urls)
    step("Reading each file, your CV and other rich documents first")
    drop_root = sources.drop_folder().resolve() if drop else None
    todo = [r for r in items.pending(con, batch) if r["kind"] != "course"]
    written: list[str] = []
    total = sum(items.counts(con, batch)["states"].values())
    done = total - len(todo)
    for row in todo:
        progress(done, total, row["rel"])
        try:
            note = read_one(con, row, batch, drop_root)
        except Exception as e:  # one unreadable file must not stop the rest; its row keeps the words
            log.warning("could not read %s: %s", row["rel"], e)
            items.update(con, row["id"], state="error", error=str(e)[:ERROR_CHARS] or type(e).__name__)
            note = None
        con.commit()
        if note:
            written.append(note)
        done += 1
    progress(done, total, "")
    step("Linking your notes")
    written += _courses(con, batch)
    vault_dir = vault()
    notes.link_pass(vault_dir, written)
    notes.write_indexes(vault_dir)
    con.commit()
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
