"""The Check your record queue: the facts Hal found that the person has not checked yet, most important first.

`queue(con)` works the queue out from the items' states each time, so a person who stops half way and comes
back later starts where they left off, with nothing to save. It gives:

- `conflicts`: the open conflicts, asked first as "Which is right?", because settling one checks a fact too.
- `cards`: one card per fact for the basics (name, email, phone, location), jobs, education, certificates,
  achievements, projects and links, in that order, then the skills in groups.
- `more`: the skills the page keeps under "Show more": those past the first `SKILLS_FIRST`, then the weak
  ones (`store.is_weak`).
- `done` and `total`: how many found facts are checked (confirmed, edited or hidden) and how many there are
  to check in all, leaving out what waits under "Show more".

Design decisions, each with its reason:

- Skills come in groups of the file each one rests on most, so the person keeps or drops all the skills of
  one module or one project with one choice: a real record held hundreds of skills (2026-11-05), too many
  to check one at a time.
- Skills are ranked by their evidence (the person's own documents, how many files name them, how much the
  best file is trusted) before the groups are made, so the first ones asked about are the likeliest true.
- Items added by hand are never queued: the person typed them, so they are already checked.
"""
from __future__ import annotations

import sqlite3

from jobhunt.profile import conflicts, merge, store

# The order of importance: who the person is, then their history, then the lists; skills come last.
KIND_ORDER = ("basic", "job", "education", "certificate", "achievement", "project", "link")
# A CV lists ten to thirty skills; past that the rest are likely course topics, so they wait under "Show more".
SKILLS_FIRST = 30


def _basic_order(item: dict) -> int:
    key = item["fields"].get("key", "")
    return store.BASIC_KEYS.index(key) if key in store.BASIC_KEYS else len(store.BASIC_KEYS)


def _source_strength(e: dict) -> tuple:
    return (e.get("role", "") == store.OWN_ROLE, merge.rank(e["doc_type"]), bool(e["verified"]), bool(e["quote"]))


def best_source(item: dict) -> dict | None:
    """The evidence an item rests on most: the person's own document, then the most trusted file, then a quote
    found in its file; None when it has no evidence."""
    return max(item["evidence"], key=_source_strength, default=None)


def strength(item: dict) -> tuple:
    """How well a skill is shown, for sorting strongest first: own documents, files, trust, sources."""
    ev = item["evidence"]
    best = best_source(item)
    return (sum(e.get("role", "") == store.OWN_ROLE for e in ev), len({e["source_rel"] for e in ev}),
            _source_strength(best) if best else (), len(ev))


def _groups(skills: list[dict], part: str) -> list[dict]:
    """Skills in groups by the file each rests on most, in the order their first skill comes; `part` keeps the
    keys of one file's groups in different parts of the queue apart."""
    out: dict[str, dict] = {}
    for item in skills:
        best = best_source(item)
        rel = best["source_rel"] if best else ""
        out.setdefault(rel, {"type": "skills", "key": f"{part}:{rel}", "source_rel": rel, "items": []})
        out[rel]["items"].append(item)
    return [_with_best(c) for c in out.values()]


def _with_best(card: dict) -> dict:
    """The card with `best`: for each item, the id of the evidence to show (`best_source`), or None."""
    return {**card, "best": [(best_source(i) or {}).get("id") for i in card["items"]]}


def queue(con: sqlite3.Connection) -> dict:
    """What is left to check: {"conflicts", "cards", "more", "done", "total"} (see the module docstring).

    A card is {"type": "item" | "skills", "key", "source_rel", "items": [ITEM], "best": [evidence id]}; an
    item card holds one item and a `source_rel` of "", a skills card the file its skills rest on most. `best`
    names, for each item, the source whose quote the page shows.
    """
    found = {kind: [i for i in store.items(con, kind) if i["origin"] == "found"] for kind in store.KINDS}
    done = sum(i["state"] != "hal" for kind in found.values() for i in kind)
    waiting = {kind: [i for i in items if i["state"] == "hal"] for kind, items in found.items()}
    cards = []
    for kind in KIND_ORDER:
        items = waiting[kind]
        if kind == "basic":
            items = sorted(items, key=_basic_order)
        cards += [_with_best({"type": "item", "key": f"item:{i['id']}", "source_rel": "", "items": [i]})
                  for i in items]
    skills = waiting["skill"]
    strong = sorted((i for i in skills if not store.is_weak(i)), key=strength, reverse=True)
    weak = sorted((i for i in skills if store.is_weak(i)), key=strength, reverse=True)
    cards += _groups(strong[:SKILLS_FIRST], "skills")
    more = _groups(strong[SKILLS_FIRST:], "more") + _groups(weak, "weak")
    left = sum(len(c["items"]) for c in cards)
    return {"conflicts": conflicts.listing(con), "cards": cards, "more": more, "done": done, "total": done + left}
