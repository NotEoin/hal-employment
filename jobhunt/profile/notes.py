"""The Profile notes in the record folder: the profile written out as Markdown for reading in Obsidian.

`write_notes(folder)` writes `Profile/Profile.md` (an overview, the basics, certificates, achievements and
links), `Work history.md`, `Education.md`, `Skills.md`, `Projects.md` and `Inconsistencies.md`.

Design decisions, each with its reason:

- Every note is `type: dashboard`, so `record.should_index` leaves it out of search and the AI never reads
  the profile twice (once as facts, once as passages).
- The notes are Hal's: each says that changes belong on the Your record page, and each is written again in
  full. A note whose text has not changed is not written, so the folder's dates and a sync tool stay quiet.
- Hidden items are left out. Sources are wikilinks to the notes they came from, so Obsidian shows them.
- Each note is written to a temporary file first and then renamed over the old one, so Obsidian never
  reads half a note.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from jobhunt import db
from jobhunt.profile import conflicts, merge, store, writing

FOLDER = "Profile"
TOP = ("---\ntype: dashboard\nhal: profile\n---\n"
       "Hal writes this note from your profile and writes it again after each change. To change something, "
       "use the Your record page in Hal.\n")
NOTES = {"Work history": ("job",), "Education": ("education",), "Skills": ("skill",), "Projects": ("project",)}


def _link(rel: str) -> str:
    target = rel[:-len(".md")] if rel.lower().endswith(".md") else rel
    return f"[[{target}]]"


def _sources(item: dict) -> list[str]:
    """A "From:" list of the notes an item came from, each with its first quote."""
    seen, out = set(), []
    for e in item["evidence"]:
        if e["source_rel"] in seen:
            continue
        seen.add(e["source_rel"])
        out.append(f"  - {_link(e['source_rel'])}" + (f': "{e["quote"]}"' if e["quote"] else ""))
    return ["- From:", *out] if out else []


def _item_block(item: dict) -> list[str]:
    head = item["title"] + (f", {item['sub']}" if item["sub"] else "")
    lines = [f"## {head}", ""]
    if item["when"]:
        lines.append(f"- When: {item['when']}")
    lines.append(f"- {item['state_words']}")
    f = item["fields"]
    if f.get("hours") == "part_time":
        lines.append("- Part-time")
    for key, label in (("summary", "Summary"), ("role", "Role"), ("grade", "Grade"), ("level", "Level")):
        if f.get(key) and not (key == "grade" and item["kind"] == "education"):   # the grade is in the heading
            lines.append(f"- {label}: {f[key]}")
    for key, label in (("duties", "Duties"), ("achievements", "Achievements"), ("tools", "Tools")):
        if f.get(key):
            lines.append(f"- {label}:")
            lines += [f"  - {x}" for x in f[key]]
    if f.get("modules"):
        lines += ["", "| Module | Mark |", "| --- | --- |"]
        lines += [f"| {m['name']} | {m.get('mark') or ''} |" for m in f["modules"]]
        lines.append("")
    lines += _sources(item)
    return [*lines, ""]


def _note(title: str, body: list[str]) -> str:
    return TOP + f"\n# {title}\n\n" + "\n".join(body).rstrip() + "\n"


def _list_note(con: sqlite3.Connection, title: str, kinds: tuple[str, ...]) -> str:
    body: list[str] = []
    for kind in kinds:
        for it in store.items(con, kind, hidden=False):
            body += _item_block(it)
    return _note(title, body or ["Nothing here yet."])


def _skills_note(con: sqlite3.Connection) -> str:
    groups: dict[str, list[dict]] = {}
    for it in store.items(con, "skill", hidden=False):
        groups.setdefault(it["fields"].get("category") or "Other skills", []).append(it)
    body: list[str] = []
    for category in sorted(groups, key=lambda c: (c == "Other skills", c.lower())):
        body += [f"## {category[:1].upper()}{category[1:]}", ""]
        for it in groups[category]:
            level = f" ({it['fields']['level']})" if it["fields"].get("level") else ""
            body.append(f"- {it['title']}{level}: {it['state_words']}")
            body += ["  " + s for s in _sources(it)]
        body.append("")
    return _note("Skills", body or ["Nothing here yet."])


def _overview(con: sqlite3.Connection) -> str:
    counts = store.counts(con)
    shown = {k: c["total"] - c["hidden"] for k, c in counts.items()}
    to_check = sum(c["hal"] for c in counts.values())
    open_conflicts = len(conflicts.listing(con))
    body = ["## At a glance", "",
            f"- Jobs: {shown['job']}, see [[Work history]]",
            f"- Education: {shown['education']}, see [[Education]]",
            f"- Skills: {shown['skill']}, see [[Skills]]",
            f"- Projects: {shown['project']}, see [[Projects]]",
            f"- Found by Hal and not checked yet: {to_check}",
            f"- Things that do not add up: {open_conflicts}, see [[Inconsistencies]]", ""]
    basics = store.items(con, "basic", hidden=False)
    if basics:
        body += ["## About you", ""] + [f"- {writing.line('basic', b['fields'])}" for b in basics] + [""]
    for kind, title in (("certificate", "Certificates"), ("achievement", "Achievements"), ("link", "Links")):
        found = store.items(con, kind, hidden=False)
        if found:
            body += [f"## {title}", ""] + [f"- {writing.line(kind, it['fields'])}" for it in found] + [""]
    return _note("Profile", body)


def _inconsistencies(con: sqlite3.Connection) -> str:
    body = []
    for c in conflicts.listing(con):
        body.append(f"- {c['why']}")
        for v in c["values"]:
            body.append(f"  - {v['words'] or merge.date_words(v['value'])}: "
                        + ", ".join(_link(s["source_rel"]) for s in v["sources"]))
    return _note("Inconsistencies", body or ["Nothing in your profile disagrees with anything else."])


def _write(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def write_notes(folder: Path, con: sqlite3.Connection | None = None) -> list[Path]:
    """Writes the Profile notes into the record folder `folder`; returns the notes whose text changed."""
    if con is None:
        with closing(db.connect()) as own:
            return write_notes(folder, own)
    out = Path(folder) / FOLDER
    out.mkdir(parents=True, exist_ok=True)
    notes = {"Profile": _overview(con), "Skills": _skills_note(con), "Inconsistencies": _inconsistencies(con)}
    for title, kinds in NOTES.items():
        notes.setdefault(title, _list_note(con, title, kinds))
    return [out / f"{title}.md" for title, text in sorted(notes.items()) if _write(out / f"{title}.md", text)]
