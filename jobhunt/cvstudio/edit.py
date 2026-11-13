"""Editing the CV line by line: one list of changes saved on the version it was made on, and record cards
made into CV entries with no AI.

- A save is the editor's whole session: its `model.Change` list applied in order to its base version makes
  one new version. A base that is no longer the version in use is refused (`store.StaleVersion`), so a
  save never overwrites a change made elsewhere since the editor opened.
- A line the person added or reworded gets `model.YOUR_WORDS` in its sources: it is in their own words,
  so Hal's suggestions never list it as a writing problem to fix (`suggest.writing_problems`).
- A card becomes an entry field for field, with nothing added: a job's title, employer, dates and duties;
  a qualification's grade and modules; a project's name, link, tools and summary; a certificate's name,
  issuer, date and grade. Each line it gives carries `card:<id>` in its sources, back to its card.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

from jobhunt.cvstudio import model, store
from jobhunt.cvstudio.model import Bullet, Change, CVDoc, CVError, Entry, Module

CARD = "card:"                 # a line's source naming the record card it came from: "card:12"
NOW = "Present"                # the end date of a job or course still going on
# The record card kinds that make CV entries, and the section kinds each may go in.
CARD_SECTIONS = {"job": ("experience", "volunteering"), "education": ("education",), "project": ("projects",),
                 "certificate": ("certifications",)}


def changes_from(raw: Any) -> list[Change]:
    """The editor's changes as `model.Change`s; CVError names the first one that does not fit."""
    if not isinstance(raw, list) or not raw:
        raise CVError("changes: there is nothing to save")
    out = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or c.get("op") not in model.OPS or not isinstance(c.get("target"), str):
            raise CVError(f"changes[{i}]: each change needs an op and a target")
        position = c.get("position")
        if position is not None and (isinstance(position, bool) or not isinstance(position, int)):
            raise CVError(f"changes[{i}].position: expected a whole number")
        out.append(Change(c["op"], c["target"], c.get("value"), position))
    return out


def mark_your_words(before: CVDoc, after: CVDoc) -> CVDoc:
    """`after` with `model.YOUR_WORDS` added to each line that is new with no sources, or reworded."""
    was = {b.id: b.text for s in before.sections for e in s.entries for b in e.bullets}
    for s in after.sections:
        for e in s.entries:
            for b in e.bullets:
                typed = (b.id not in was and not b.sources) or (b.id in was and was[b.id] != b.text)
                if typed and model.YOUR_WORDS not in b.sources:
                    b.sources.append(model.YOUR_WORDS)
    return after


def save(con: sqlite3.Connection, base: str, changes: list[Change], what: str) -> tuple[str, CVDoc, CVDoc]:
    """Applies `changes` to version `base` and saves the result as one new version: (its id, before, after).

    `store.StaleVersion` when `base` is not the version in use; CVError when a change does not fit or
    nothing changed.
    """
    before = store.get(base, con=con)
    after = before
    for c in changes:
        after = model.apply(after, c)
    after = mark_your_words(before, after)
    if model.to_json(after) == model.to_json(before):
        raise CVError("changes: they leave your CV as it was")
    vid = store.new_version(after, origin="manual", what=what, by="you", parent=base, base=base, con=con)
    return vid, before, after


# ------------------------------------------------------------------------------------------ record cards

def _when(fields: dict, key: str) -> str:
    from jobhunt.profile import merge

    return merge.date_words(str(fields.get(key) or ""))


def _lines(texts: list[str], source: str) -> list[Bullet]:
    return [Bullet(id=model.new_id("b"), text=t, sources=[source]) for t in texts if t.strip()]


def entry_from(card: dict) -> Entry:
    """A CV entry made from one record card (`profile.store.item` shape), field for field."""
    from jobhunt.profile import store as profile_store

    kind, f = card["kind"], card["fields"]
    if kind not in CARD_SECTIONS:
        raise CVError(f"card: a {kind} card does not make a CV entry")
    source = f"{CARD}{card['id']}"
    e = Entry(id=model.new_id(model.PREFIX[CARD_SECTIONS[kind][0]]))
    if kind == "job":
        e.heading, e.org, e.location = f.get("title") or "", f.get("employer") or "", f.get("location") or ""
        e.start, e.end = _when(f, "start"), NOW if f.get("current") else _when(f, "end")
        e.bullets = _lines(list(f.get("duties") or []), source)
    elif kind == "education":
        e.heading, e.org, e.grade = profile_store.title(kind, f), f.get("institution") or "", f.get("grade") or ""
        e.start, e.end = _when(f, "start"), NOW if f.get("current") else _when(f, "end")
        e.modules = [Module(name=m["name"], mark=m.get("mark") or "") for m in f.get("modules") or []]
    elif kind == "project":
        e.heading, e.link, e.meta = f.get("name") or "", f.get("link") or "", ", ".join(f.get("tools") or [])
        e.bullets = _lines([f.get("summary") or ""], source)
    else:
        e.heading, e.org, e.grade = f.get("name") or "", f.get("issuer") or "", f.get("grade") or ""
        e.start = _when(f, "date")
    return e


def cards(con: sqlite3.Connection) -> list[dict]:
    """The record cards that can be added to the CV, each with the entry it makes and the sections it fits."""
    from jobhunt.profile import store as profile_store

    out = []
    for kind, sections in CARD_SECTIONS.items():
        for card in profile_store.items(con, kind, hidden=False):
            out.append({"id": card["id"], "kind": kind, "title": card["title"], "sub": card["sub"],
                        "when": card["when"], "state": card["state"], "state_words": card["state_words"],
                        "sections": list(sections), "entry": asdict(entry_from(card))})
    return out
