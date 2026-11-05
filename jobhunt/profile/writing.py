"""The profile as words: the "Profile" block that document prompts get, and one line per item.

`context_block(con, limit)` lists only what the person checked or edited: a fact Hal found and nobody has checked
yet, and a hidden one, never reach a prompt. `with_record(queries)` puts that block above the record passages
`record.context` finds, and is what the pack's letter, fit map and answers read (`pack._kept`).

Design decisions, each with its reason:

- Only checked facts are used (2026-11-05): Hal reads hundreds of facts out of a person's files, and some are
  wrong (a lecturer's course as the person's own skill, a guessed degree). An application must say only what
  the person has seen and agreed to, so the Check your record page asks them first.
- Contact details (email, phone) are left out: no document a prompt writes needs them, and the CV and the
  person's settings already hold them where they belong.
- No line of the block is a bracketed "[file > heading]" label, because `pack.passages` counts those lines
  as record passages the letter used.
- The block is cut at a whole line when it reaches `limit`, kind by kind in `KIND_ORDER`, so the basics and
  jobs are the last to go.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from jobhunt import db, record
from jobhunt.profile import merge, store

KIND_ORDER = ("basic", "job", "education", "project", "skill", "certificate", "achievement", "link")
CONTACT_KEYS = {"email", "phone", "mobile", "telephone", "address"}
LIMIT = 5000               # below the record part's 6000 (`record.context`); a 64K-token writer has room for both
# One item's line at most: a degree with every module, or a job with every duty from five CVs, filled the
# block alone and left no room for education or skills (2026-11-05). Cut at a whole list item.
LINE_MAX = 450
QUOTE_CHARS = 160          # a skill's evidence quote, cut so it never crowds out the other facts
HEADING = "Profile (facts about the applicant from their files, each checked by them):"
USED_STATES = ("confirmed", "edited")


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


# Kinds a person has many of: shown most relevant to the job first, as the block cannot hold them all
# (89 projects filled it in a real profile, 2026-11-05).
RANKED_KINDS = ("project", "skill", "certificate", "achievement")


def _relevance(item: dict, wanted: set[str]) -> int:
    words = set(merge.text_key(" ".join(str(v) for v in item["fields"].values())).split())
    return len(words & wanted)


def _lines(con: sqlite3.Connection, queries: list[str] | None = None) -> list[str]:
    out = []
    wanted = set(merge.text_key(" ".join(queries or [])).split()) - QUERY_STOP
    for kind in KIND_ORDER:
        found = store.items(con, kind, hidden=False)
        if kind == "basic":
            found = _one_location(found)
        if kind in RANKED_KINDS and wanted:
            found = sorted(found, key=lambda it: -_relevance(it, wanted))
        for it in found:
            if it["state"] not in USED_STATES:
                continue
            if kind == "basic" and it["fields"].get("key", "").lower() in CONTACT_KEYS:
                continue
            quote = next((e["quote"] for e in it["evidence"] if e["quote"]), "") if kind == "skill" else ""
            out.append("- " + line(kind, it["fields"], quote))
    return out


def _one_location(basics: list[dict]) -> list[dict]:
    """The basics with one location: the person's own if they set one, else the one most files give.

    A letter needs where the person lives once; seven found places (a home town, a university, an old
    address) read as seven claims (2026-11-05).
    """
    places = [b for b in basics if b["fields"].get("key") == "location"]
    if len(places) < 2:
        return basics
    best = max(places, key=lambda b: (b["state"] in ("confirmed", "edited"), len(b["evidence"])))
    return [b for b in basics if b["fields"].get("key") != "location" or b is best]


QUERY_STOP = {"and", "or", "the", "a", "an", "of", "to", "in", "for", "with", "on", "at", "by", "is", "are", "be",
              "you", "your", "we", "our", "will", "experience", "ability", "able", "skills", "knowledge", "good",
              "strong", "excellent", "work", "working", "role"}


def context_block(con: sqlite3.Connection, limit: int = LIMIT, queries: list[str] | None = None) -> str:
    """The "Profile" block for a writing prompt: the facts the person confirmed or edited, never others.

    "" when the person has checked nothing yet.
    """
    checked = _lines(con, queries)
    if not checked:
        return ""
    parts = [HEADING, *checked]
    out, size = [], 0
    for part in parts:
        if len(part) > LINE_MAX:
            part = part[:LINE_MAX].rsplit("; ", 1)[0] + " ..."
        if size + len(part) + 1 > limit:
            continue                       # a later, shorter fact may still fit
        out.append(part)
        size += len(part) + 1
    if out == [HEADING]:
        return ""                          # a heading with no line under it says nothing
    return "\n".join(out)


def block(queries: list[str] | None = None) -> str:
    """The Profile block from Hal's own database, for callers that hold no connection."""
    with closing(db.connect()) as con:
        return context_block(con, queries=queries)


def with_record(queries: list[str]) -> str:
    """The Profile block above the record's passages for `queries`; "" when both are empty."""
    return "\n\n".join(p for p in (block(queries), record.context(queries)) if p)
