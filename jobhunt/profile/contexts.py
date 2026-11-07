"""Where each experience was done: part of a degree, a university team project, at work, or in the person's
own time.

Each job, course, project, achievement and certificate (`tiers.TIERED_KINDS`) keeps a `context`
(`writing.stage.CONTEXTS`: degree, team, work, personal, or '' when nothing shows it) and `context_by` (`hal`
or `person`), migration 0026. `found(item, employers)` is Hal's reading of a card; `current(item, employers)`
is the context writing uses; `refresh(con)` stores Hal's reading for every card the person has not set;
`set_context` is the person's choice; `shown(item, stored)` is what a shaped item carries.

Design decisions, each with its reason:

- Each card states its context, and the prompts and `writing.drafts.wrong_context` keep drafts to it: real
  letters called a personal project part of a degree, a university team project a personal project and
  coursework "personal work" (2026-11-07).
- A job is work and a course is degree work unless the person says otherwise. A project, achievement or
  certificate takes its context from its own words and evidence (`writing.stage.project_kind`), and none
  when nothing shows it: a guess would be repeated in every letter.
- A context the person chose is never changed by Hal, as with tiers (`profile.tiers`).
- Writing reads Hal's context afresh from the evidence (`current`) rather than the stored one, so a card read
  in since the last refresh is still described as its evidence says.
"""
from __future__ import annotations

import sqlite3

from jobhunt import db
from jobhunt.profile import store
from jobhunt.writing import stage

BY_HAL, BY_PERSON = "hal", "person"
CONTEXT_KINDS = ("job", "education", "project", "achievement", "certificate")   # as `tiers.TIERED_KINDS`
# The person's choice on a card, in the words the Check your record page shows; the keys are
# `writing.stage.CONTEXTS`, written out because `stage` is still loading when the profile package is.
CHOICE_WORDS = {"degree": "Part of my degree", "team": "University team project", "work": "At work",
                "personal": "In my own time"}
BY_KIND = {"job": "work", "education": "degree"}   # unless the person says otherwise

def found(item: dict, worked_for: list[str]) -> str:
    """Hal's context for a shaped `item` from its kind, fields and evidence; "" when nothing shows it."""
    kind = item["kind"]
    if kind in BY_KIND:
        return BY_KIND[kind]
    if kind not in CONTEXT_KINDS:
        return ""
    evidence = item.get("evidence") or []
    texts = [str(v) for v in item["fields"].values() if isinstance(v, str) and v]
    texts += [str(t) for e in evidence for t in (e.get("quote"), e.get("source_rel")) if t]
    return stage.context_of(stage.project_kind(texts, [str(e.get("doc_type") or "") for e in evidence], worked_for))


def current(item: dict, worked_for: list[str]) -> str:
    """The context writing uses for a shaped `item`: the person's choice, else Hal's reading of it now."""
    if item.get("context_by") == BY_PERSON and item.get("context") in stage.CONTEXTS:
        return item["context"]
    return found(item, worked_for)


def shown(item: dict, stored) -> dict:
    """The context a shaped `item` carries: {context, context_by, context_words}; `stored` holds its columns.

    The person's choice wins, then Hal's stored reading, then the kind's own (a job is work); a kind with no
    context has none.
    """
    if item["kind"] not in CONTEXT_KINDS:
        return {"context": "", "context_by": "", "context_words": ""}
    context, by = stored["context"], stored["context_by"]
    if context not in stage.CONTEXTS or by not in (BY_HAL, BY_PERSON):
        context = BY_KIND.get(item["kind"], "")
        by = BY_HAL if context else ""
    return {"context": context, "context_by": by, "context_words": CHOICE_WORDS.get(context, "")}


def set_context(con: sqlite3.Connection, item_id, context: str) -> dict:
    """The person says where an experience was done; Hal never changes it after this. Returns the item.

    Raises ValueError for a context that is not one of `writing.stage.CONTEXTS` or an item of a kind with no
    context, and LookupError for an unknown item.
    """
    item = store.item(con, item_id)
    if context not in stage.CONTEXTS:
        raise ValueError(f"{context!r} is not a context; use one of {', '.join(stage.CONTEXTS)}.")
    if item["kind"] not in CONTEXT_KINDS:
        raise ValueError(f"A {item['kind']} has no context.")
    with con:
        con.execute("UPDATE profile_items SET context = ?, context_by = ?, updated = ? WHERE id = ?",
                    (context, BY_PERSON, db.now(), item["id"]))
    return store.item(con, item["id"])


def refresh(con: sqlite3.Connection) -> int:
    """Stores Hal's context for every card the person has not set; returns how many changed."""
    # the Profile block reads contexts, so its module is imported when first needed
    from jobhunt.profile.writing import employers

    worked_for = employers(con)
    changed = 0
    with con:
        for kind in CONTEXT_KINDS:
            for item in store.items(con, kind):
                if item["context_by"] == BY_PERSON:
                    continue
                context = found(item, worked_for)
                row = con.execute("SELECT context, context_by FROM profile_items WHERE id = ?",
                                  (item["id"],)).fetchone()
                if (row["context"], row["context_by"]) != (context, BY_HAL if context else ""):
                    con.execute("UPDATE profile_items SET context = ?, context_by = ? WHERE id = ?",
                                (context, BY_HAL if context else "", item["id"]))
                    changed += 1
    return changed
