"""The profile as words: the "Profile" block that document prompts get, and one line per item.

`context_block(con, limit)` lists what the person checked or edited first, then what Hal found and nobody has
checked yet, and never a hidden item. `with_record(queries)` puts that block above the record passages
`record.context` finds, and is what the pack's letter, fit map and answers read (`pack._kept`).

Design decisions, each with its reason:

- Contact details (email, phone) are left out: no document a prompt writes needs them, and the CV and the
  person's settings already hold them where they belong.
- No line of the block is a bracketed "[file > heading]" label, because `pack.passages` counts those lines
  as record passages the letter used.
- The block is cut at a whole line when it reaches `limit`, so the checked facts, which come first, are the
  last to go.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from jobhunt import db, record
from jobhunt.profile import merge, store

KIND_ORDER = ("basic", "job", "education", "project", "skill", "certificate", "achievement", "link")
CONTACT_KEYS = {"email", "phone", "mobile", "telephone", "address"}
LIMIT = 3000               # about a third of what a prompt's record part may hold (`record.context`'s 6000)
QUOTE_CHARS = 160          # a skill's evidence quote, cut so it never crowds out the other facts
HEADING = "Profile (facts about the applicant from their files; the ones they checked come first):"
CHECKED = "Checked by the applicant:"
UNCHECKED = "Found in their files, not checked yet:"


def _period(fields: dict) -> str:
    words = store.when(fields)
    return f" ({words})" if words else ""


def line(kind: str, fields: dict, quote: str = "") -> str:
    """One item as a line of plain words for a prompt."""
    f = fields
    if kind == "job":
        where = ", ".join(p for p in (f.get("employer"), f.get("location")) if p)
        out = f"Job: {f.get('title') or 'a role'}" + (f" at {where}" if where else "") + _period(f)
        if f.get("hours") == "part_time":
            out += ", part-time"
        if f.get("duties"):
            out += ". Duties: " + "; ".join(f["duties"])
        if f.get("achievements"):
            out += ". Achievements: " + "; ".join(f["achievements"])
        return out
    if kind == "education":
        course = " ".join(p for p in (f.get("qualification"), f.get("subject")) if p) or "a course"
        out = f"Education: {course}" + (f" at {f['institution']}" if f.get("institution") else "") + _period(f)
        if f.get("grade"):
            out += f", grade {f['grade']}"
        if f.get("modules"):
            out += ". Modules: " + "; ".join(m["name"] + (f" ({m['mark']})" if m.get("mark") else "")
                                             for m in f["modules"])
        return out
    if kind == "skill":
        level = ", ".join(p for p in (f.get("level"), f.get("category")) if p)
        out = f"Skill: {f.get('name', '')}" + (f" ({level})" if level else "")
        return out + (f'. Shown by: "{quote[:QUOTE_CHARS]}"' if quote else "")
    if kind == "project":
        out = f"Project: {f.get('name', '')}" + (f" ({f['period']})" if f.get("period") else "")
        for label, key in (("", "summary"), ("Role", "role")):
            if f.get(key):
                out += f". {label + ': ' if label else ''}{f[key]}"
        return out + (". Tools: " + ", ".join(f["tools"]) if f.get("tools") else "")
    if kind == "certificate":
        out = f"Certificate: {f.get('name', '')}" + (f" from {f['issuer']}" if f.get("issuer") else "")
        if f.get("date"):
            out += f" ({merge.date_words(f['date'])})"
        return out + (f", grade {f['grade']}" if f.get("grade") else "")
    if kind == "achievement":
        out = f"Achievement: {f.get('title', '')}" + (f" ({merge.date_words(f['date'])})" if f.get("date") else "")
        return out + (f". {f['detail']}" if f.get("detail") else "")
    if kind == "basic":
        return f"{f.get('key', '').capitalize()}: {f.get('value', '')}"
    return f"Link: {f.get('label') or f.get('url', '')}" + (f" {f['url']}" if f.get("label") else "")


def _lines(con: sqlite3.Connection, states: tuple[str, ...]) -> list[str]:
    out = []
    for kind in KIND_ORDER:
        for it in store.items(con, kind, hidden=False):
            if it["state"] not in states:
                continue
            # A found fact no quote was found for is the model's word alone: it reached a letter as a degree
            # from a university the person never went to (2026-11-05). It waits for the person to confirm it.
            if it["state"] == "hal" and not any(e.get("verified", True) for e in it["evidence"]):
                continue
            if kind == "basic" and it["fields"].get("key", "").lower() in CONTACT_KEYS:
                continue
            quote = next((e["quote"] for e in it["evidence"] if e["quote"]), "") if kind == "skill" else ""
            out.append("- " + line(kind, it["fields"], quote))
    return out


def context_block(con: sqlite3.Connection, limit: int = LIMIT) -> str:
    """The "Profile" block for a writing prompt: checked and edited facts, then found ones; never hidden ones,
    and never a found one whose every quote was missing from its document.

    "" when the profile holds nothing to say.
    """
    checked = _lines(con, ("confirmed", "edited"))
    found = _lines(con, ("hal",))
    if not checked and not found:
        return ""
    parts = [HEADING]
    if checked:
        parts += [CHECKED, *checked]
    if found:
        parts += [UNCHECKED, *found]
    out, size = [], 0
    for part in parts:
        if size + len(part) + 1 > limit:
            break
        out.append(part)
        size += len(part) + 1
    while out and out[-1] in (HEADING, CHECKED, UNCHECKED):
        out.pop()                          # a heading with no line under it says nothing
    return "\n".join(out)


def block() -> str:
    """The Profile block from Hal's own database, for callers that hold no connection."""
    with closing(db.connect()) as con:
        return context_block(con)


def with_record(queries: list[str]) -> str:
    """The Profile block above the record's passages for `queries`; "" when both are empty."""
    return "\n\n".join(p for p in (block(), record.context(queries)) if p)
