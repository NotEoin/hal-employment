"""Project folders: one project for a folder of the person's own work, however many notes it holds.

`group.projects` finds the folders (a README at the top, in a folder of the person's own work or a mix, not a
repository, not a collection of projects). This module gives each its name, its note and its facts:

- The name (`name_of`) is the README's first heading, else the folder's name.
- A project fact read in a document inside the folder (`facts_in`) is a fact about the folder's project:
  its name becomes the project's, and it keeps only the tools and links it gives, so a ticket, a plan or a
  write-up is evidence of the one project card, never a card of its own. The README keeps its summary, role
  and dates too, as it says what the whole project is. Notes inside still give skills and quotes. A real
  research workspace gave about 180 project cards, one per ticket and write-up (2026-11-08).
- A document that lists projects keeps its project facts as they are: a CV, letter or transcript
  (`LISTS_PROJECTS`), and any document whose project facts name three or more projects
  (`merge.lists_projects`), such as a dossier or plan describing all the person's work. A portfolio folder's
  dossier gave its card ten other projects' facts (2026-11-08).
- So does a fact whose project is plainly another one of the person's (`Others`): a card a file outside the
  folder speaks for, a repository or another project folder, matched by a close name or a distinctive word
  the names share, as a note names a repository's project in its own words.
- Each folder gets one note (`write_notes`) listing its files' notes, which link back to it under "Part of",
  as a course's do; its data files and figures are listed in it (`attach`).
- A tidy (`find_in_record`, `fold_cards`) holds a record an older read filed to the same rules: it finds the
  project folders of the files read before, takes them out of the course units they were split into, and
  files each project fact from inside again on the folder's project card, hiding a card left with nothing
  as "Part of <project>" (`store.fold_away`). Cards the person checked are never touched.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import sqlite3
from pathlib import Path, PurePosixPath

from jobhunt.ingest import discover, group, items, notes, rank, roles

log = logging.getLogger(__name__)

KIND = "project"
RULE = rank.RULES[7]           # a project's rank, kind and folder in the record
README_CHARS = 4000            # the title is the first heading, near the top
NAME_MAX = 80                  # a heading longer than this is a sentence, not a project's name
GENERIC_TITLES = {"readme", "overview", "introduction", "about", "index", "contents", "notes"}
HEADING = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)
MARKUP = re.compile(r"[*_`]|\[([^\]]*)\]\([^)]*\)")
# Documents that list the person's projects wherever they are kept: their project facts stay as they are.
LISTS_PROJECTS = ("cv", "letter", "transcript")
KEPT = ("tools", "link")                                       # what a note inside the folder adds
KEPT_TOP = ("summary", "role", "period", *KEPT)                # what the README adds
PART_OF = "\n## Part of\n"
ORIGINAL = "\n## Original\n"
WHY = "Part of {name}"
# A name's words: "LIDAR", "GPU" in "GPUScheduler", "Lidar" and "Autonavigation" in "Lidar-Autonavigation-System"
WORD = re.compile(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+")
# A shared word this long ties two names unless it is common in project names; "voice" in "Voice interface"
# and the repository "hal-voice" does, "tool" or "data" never (2026-11-08).
DISTINCT_MIN = 5
GENERIC_WORDS = {"about", "analysis", "application", "based", "basic", "build", "design", "draft", "final",
                 "first", "notes", "personal", "programme", "project", "projects", "report", "research", "simple",
                 "small", "study", "system", "using", "version", "website", "write"}
CHECKED = ("confirmed", "edited", "hidden")    # states only the person gives a card that is not merged


@functools.lru_cache(maxsize=256)
def name_of(folder: str) -> str:
    """A project folder's name: its README's first heading, else the folder's own name."""
    path = Path(folder)
    try:
        readme = next((p for p in sorted(path.iterdir()) if p.is_file() and discover.has_readme([p.name])), None)
    except OSError:
        readme = None
    if readme is not None:
        try:
            with readme.open(encoding="utf-8", errors="replace") as f:
                found = HEADING.search(f.read(README_CHARS))
        except OSError:
            found = None
        title = " ".join(MARKUP.sub(r"\1", found.group(1)).split()) if found else ""
        if title and len(title) <= NAME_MAX and title.lower() not in GENERIC_TITLES:
            return title
    return path.name


def _units(con: sqlite3.Connection) -> dict[Path, str]:
    """Every project folder a read found, by folder, with its name."""
    rows = con.execute("SELECT DISTINCT path FROM ingest_items WHERE kind = ? AND state IN ('pending', 'done')",
                       (KIND,)).fetchall()
    return {Path(r[0]): name_of(r[0]) for r in rows}


def _repo_names(con: sqlite3.Connection) -> list[str]:
    return [Path(r[0]).name for r in con.execute("SELECT DISTINCT path FROM ingest_items WHERE kind = 'repo' "
                                                 "AND state = 'done'")]


def _words(name: str) -> dict[str, bool]:
    """A name's words, lower case, each with whether it is written as an acronym ("LIDAR", "GPU")."""
    return {w.lower(): w.isupper() for w in WORD.findall(name or "")}


def _distinctive(word: str, acronym: bool) -> bool:
    return acronym or (len(word) >= DISTINCT_MIN and word not in GENERIC_WORDS)


class Others:
    """The person's other projects, as seen from one project folder: every project card some file outside the
    folder speaks for (or the person checked or added), with the names of the cards Hal merged into it
    (`profile.duplicates`), each repository and each other project folder. A card only the folder's own files
    speak for, such as a ticket an older read made a card of, is not one of them.

    `names(name, own)` is True when a project fact named `name` read inside the folder whose project is `own`
    is about one of them: close to one of their names (`merge.same_item`, `merge._name_close`), or sharing a
    distinctive word with one name alone, such as an acronym ("LIDAR Autonomous Navigation" and the
    repository "Lidar-Autonavigation-System"), which no rule on whole names ties together (2026-11-08).
    """

    def __init__(self, groups: list[list[str]]) -> None:
        self.groups = [g for g in groups if any(g)]
        self.counts: dict[str, int] = {}
        for g in self.groups:
            for w in {w for n in g for w in _words(n)}:
                self.counts[w] = self.counts.get(w, 0) + 1

    @classmethod
    def of(cls, con: sqlite3.Connection, folder: Path, units: dict[Path, str]) -> Others:
        """The other projects as seen from `folder`, one of the project folders `units`."""
        from jobhunt.profile import duplicates, merge

        start = str(folder) + os.sep
        listings = duplicates.listing_files(con)
        cards: dict[int, dict] = {}
        for r in con.execute("""SELECT i.id, i.fields, i.state, i.origin, i.merged_into, i.merged_why,
                                e.source_rel, e.doc_type, r.path FROM profile_items i
                                LEFT JOIN profile_evidence e ON e.item_id = i.id
                                LEFT JOIN ingest_items r ON r.note = e.source_rel AND r.state = 'done'
                                AND r.kind IN ('file', 'repo') WHERE i.kind = ?""", (KIND,)):
            card = cards.setdefault(r["id"], {"name": json.loads(r["fields"]).get("name", ""), "known": False,
                                              "into": r["merged_into"], "why": r["merged_why"]})
            # a file outside the folder (or one no read filed, such as a CV given another way), a file listing
            # projects, or the person, says the card is a project of its own
            card["known"] = card["known"] or (r["state"] in CHECKED or r["origin"] != "found") or (
                r["source_rel"] is not None and (r["path"] is None or not r["path"].startswith(start)
                                                 or r["source_rel"] in listings
                                                 or merge.doc_group(r["doc_type"]) in LISTS_PROJECTS))
        groups = {i: [c["name"]] for i, c in cards.items() if c["into"] is None and c["known"]}
        for c in cards.values():
            if c["into"] in groups and c["why"].startswith(duplicates.MERGED):
                groups[c["into"]].append(c["name"])
        found = list(groups.values())
        for name in [n for f, n in units.items() if f != folder] + _repo_names(con):
            # a repository is often a card too: one project counted twice would make its words look common
            if not any(merge.same_item(KIND, {"name": name}, {"name": n}) for g in found for n in g):
                found.append([name])
        return cls(found)

    def names(self, name: str, own: str) -> bool:
        """True when a project fact named `name`, read inside the folder whose project is `own`, is plainly
        about one of the other projects (see the class notes)."""
        from jobhunt.profile import merge

        if not name or merge.same_item(KIND, {"name": name}, {"name": own}):
            return False
        mine, own_words = _words(name), _words(own)
        for aka in self.groups:
            if any(merge.same_item(KIND, {"name": n}, {"name": own}) for n in aka if n):
                continue                   # the folder's own card, under one of its names
            if any(merge.same_item(KIND, {"name": name}, {"name": n}) or merge._name_close(name, n)
                   for n in aka if n):
                return True
            theirs = {w: a for n in aka for w, a in _words(n).items()}
            if any(_distinctive(w, mine[w] or theirs[w]) and self.counts.get(w, 0) == 1 and w not in own_words
                   for w in set(mine) & set(theirs)):
                return True
        return False


def redirect(fields: dict, own: str, others: Others, top: bool) -> dict:
    """A project fact read inside the project folder named `own`, as a fact about that project (see the module
    notes); `fields` as they are when they plainly name one of the `others`."""
    if others.names(str(fields.get("name") or ""), own):
        return fields
    return {"name": own, **{k: v for k, v in fields.items() if k in (KEPT_TOP if top else KEPT) and v}}


def _is_top(path: Path, folder: Path) -> bool:
    return path.parent == folder and discover.has_readme([path.name])


def _names(facts: list[dict]) -> list[str]:
    return [str(f["fields"].get("name") or "") for f in facts if f["kind"] == KIND]


def facts_in(con: sqlite3.Connection, row: sqlite3.Row | dict, desc: dict) -> list[dict]:
    """The document's facts, its project facts as facts about the project folder it is in (`redirect`), unless
    the document lists projects (`LISTS_PROJECTS`, or `merge.lists_projects`)."""
    from jobhunt.profile import merge

    facts = desc["facts"]
    if row["kind"] != "file" or merge.doc_group(desc["doc_type"]) in LISTS_PROJECTS \
            or not any(f["kind"] == KIND for f in facts) or merge.lists_projects(_names(facts)):
        return facts
    units = _units(con)
    path = Path(row["path"])
    folder = group.project_of(path, set(units))
    if folder is None:
        return facts
    others = Others.of(con, folder, units)
    top = _is_top(path, folder)
    return [{**f, "fields": redirect(f["fields"], units[folder], others, top)} if f["kind"] == KIND else f
            for f in facts]


def _under(folder: Path) -> tuple[int, str]:
    """The SQL arguments that pick the paths inside `folder`: their length and start."""
    start = str(folder) + os.sep
    return len(start), start


def part_of(path: Path, link: str) -> None:
    """Names the note of the unit a note is part of under "## Part of", in place of any named before."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    line = f"- {link}"
    if line in text:
        return
    head, sep, tail = text.partition(ORIGINAL)
    if not sep:
        return
    kept, skipping = [], False
    for each in head.split("\n"):
        if each == PART_OF.strip():
            skipping = True                # the course unit an older read named goes; this unit takes its place
        elif skipping and not each.startswith("- ") and each:
            skipping = False
        if not skipping:
            kept.append(each)
    notes.write(path, "\n".join(kept).rstrip("\n") + f"\n{PART_OF}\n{line}\n{sep}{tail}")


def _note_of(con: sqlite3.Connection, folder: Path, vault: Path) -> str | None:
    """The note a read before wrote for this project folder, when it is still there."""
    for r in con.execute("SELECT note FROM ingest_items WHERE kind = ? AND path = ? AND state = 'done' "
                         "AND note IS NOT NULL ORDER BY id DESC", (KIND, str(folder))):
        if (vault / r[0]).is_file():
            return r[0]
    return None


def write_notes(con: sqlite3.Connection, batch: str, vault: Path) -> list[str]:
    """Writes each project folder's note, listing its files' notes, and links them back to it; the notes
    written. A folder whose files gave no note gets none."""
    from jobhunt.ingest import understand

    written = []
    for unit in con.execute("SELECT * FROM ingest_items WHERE batch = ? AND kind = ? AND state = 'pending'",
                            (batch, KIND)).fetchall():
        folder = Path(unit["path"])
        size, start = _under(folder)
        members = con.execute("""SELECT rel, note FROM ingest_items WHERE state = 'done' AND note IS NOT NULL
                                 AND kind IN ('file', 'repo') AND substr(path, 1, ?) = ?
                                 GROUP BY note ORDER BY rel""", (size, start)).fetchall()
        if not members:
            items.update(con, unit["id"], state="no_text")
            continue
        name = name_of(str(folder))
        lines = [f"- {notes.link(m['note'])}" for m in members]
        note_rel = _note_of(con, folder, vault)
        if note_rel is not None:
            path = vault / note_rel
            text = path.read_text(encoding="utf-8")
            new = [line for line in lines if line not in text]
            if new:
                notes.write(path, text.replace("## Files\n\n", "## Files\n\n" + "\n".join(new) + "\n", 1))
        else:
            desc = understand.light(unit["rel"] + "/", name, RULE)
            desc["title"] = name
            desc["summary"] = f"{len(members)} files from {name}."
            note_rel = notes.write_source(vault, desc, text="\n".join(["## Files", "", *lines]), source=str(folder),
                                          sha256="", original=None)
        for m in members:
            part_of(vault / m["note"], notes.link(note_rel, name))
        items.update(con, unit["id"], stage="written", state="done", note=note_rel)
        written.append(note_rel)
    return written


# Tidying a record an older read filed ---------------------------------------------------------------------

def _root(path: Path, rel: str) -> Path | None:
    """The folder the person gave, from a file's path and its `rel` inside it; None when they do not fit."""
    depth = len(PurePosixPath(rel).parts) - 1
    return path.parents[depth - 1] if 0 < depth <= len(path.parents) else None


def find_in_record(con: sqlite3.Connection, batch: str, vault: Path) -> int:
    """Finds the project folders of the files older reads filed and puts them in the batch, to get their notes;
    their files leave the course units they were split into, whose notes go. Returns how many were found."""
    from jobhunt.ingest import tidy

    rows = con.execute("""SELECT * FROM ingest_items WHERE kind = 'file' AND state = 'done' AND note IS NOT NULL
                          AND origin != ?""", (roles.COURSE,)).fetchall()
    roots: dict[Path, tuple[str, str]] = {}
    for r in rows:
        root = _root(Path(r["path"]), r["rel"])
        if root is not None and root not in roots and root.is_dir():
            roots[root] = (PurePosixPath(r["rel"]).parts[0], r["origin"])
    known = {Path(r[0]) for r in con.execute("SELECT path FROM ingest_items WHERE kind = ? AND state = 'done'",
                                             (KIND,))}
    found = 0
    for root, (top, origin) in roots.items():
        walked = discover.walk(root, top)
        for folder in sorted(group.projects(walked.readmes, walked.repos)):
            shown = top if folder == root else f"{top}/{folder.relative_to(root).as_posix()}"
            if folder not in known:
                items.add(con, batch, path=str(folder), rel=shown, kind=KIND, rank=RULE.rank,
                          category=RULE.category, doc_type=RULE.doc_type, origin=origin)
                found += 1
            size, start = _under(folder)
            for course in con.execute("""SELECT id, note FROM ingest_items WHERE kind = 'course' AND state = 'done'
                                         AND (path = ? OR substr(path, 1, ?) = ?)""",
                                      (str(folder), size, start)).fetchall():
                if course["note"]:
                    tidy.drop_note(con, vault, course["note"])
                items.update(con, course["id"], state="skipped", note=None, error=f"part of {shown}")
            con.execute("UPDATE ingest_items SET unit = NULL WHERE kind = 'file' AND substr(path, 1, ?) = ?",
                        (size, start))
    con.commit()
    return found


def fold_cards(con: sqlite3.Connection) -> int:
    """Files each project fact an older read took from a document inside a project folder again on the
    folder's project card (`facts_in`), and hides each card Hal found that is left with nothing, as part of
    that project. Returns how many cards were folded."""
    from jobhunt.profile import duplicates, merge, store

    units = _units(con)
    if not units:
        return 0
    listings = duplicates.listing_files(con)
    others: dict[Path, Others] = {}
    paths = {r["note"]: Path(r["path"]) for r in con.execute(
        "SELECT note, path FROM ingest_items WHERE kind = 'file' AND state = 'done' AND note IS NOT NULL")}
    moved: dict[int, tuple[int, str]] = {}
    for e in con.execute("""SELECT e.id, e.item_id, e.source_rel, e.doc_type, e.said FROM profile_evidence e
                            JOIN profile_items i ON i.id = e.item_id WHERE i.kind = ? AND i.state = 'hal'
                            AND i.origin = 'found' ORDER BY e.id""", (KIND,)).fetchall():
        path = paths.get(e["source_rel"])
        folder = group.project_of(path, set(units)) if path is not None else None
        if folder is None or merge.doc_group(e["doc_type"]) in LISTS_PROJECTS or e["source_rel"] in listings:
            continue
        said = json.loads(e["said"] or "{}")
        name = units[folder]
        if folder not in others:          # as the cards were before this pass moved any
            others[folder] = Others.of(con, folder, units)
        fields = redirect(said, name, others[folder], _is_top(path, folder))
        if fields == said:
            continue
        try:
            target = store.refile(con, e["id"], fields)
        except (LookupError, ValueError) as err:   # a fact the store now refuses stays where it was
            log.info("left a project fact where it was: %s", err)
            continue
        if target != e["item_id"]:
            moved[e["item_id"]] = (target, name)
    folded = 0
    for item_id, (target, name) in moved.items():
        row = con.execute("SELECT state FROM profile_items WHERE id = ?", (item_id,)).fetchone()
        if row is None or con.execute("SELECT 1 FROM profile_evidence WHERE item_id = ? LIMIT 1",
                                      (item_id,)).fetchone():
            continue                 # a card another file still speaks for, such as a CV, stays
        store.fold_away(con, item_id, target, WHY.format(name=name))
        folded += 1
    return folded
