"""The Check your record queue: the facts Hal found that the person has not checked yet, most important first.

`queue(con)` works the queue out from the items' states each time, so a person who stops half way and comes
back later starts where they left off, with nothing to save. It gives:

- `conflicts`: the open conflicts, asked first as "Which is right?", because settling one checks a fact too.
- `cards`: one card per fact for the basics (name, email, phone, location), jobs, education, certificates,
  achievements, projects and links, in that order, then the skills in groups.
- `more`: the skills the page keeps under "Show more": those past the first `SKILLS_FIRST`, then the weak
  ones (`store.is_weak`).
- `studied`: the skills only course material names (`store.is_studied`), by name, which the page shows
  apart as "Studied in your course": they are not the person's to keep or drop, only to claim.
- `done` and `total`: how many found facts are checked (confirmed, edited or hidden) and how many there are
  to check in all, leaving out what waits under "Show more" and the studied skills.

Design decisions, each with its reason:

- Skills come in groups of the file each one rests on most, so the person keeps or drops all the skills of
  one module or one project with one choice: a real record held hundreds of skills (2026-11-05), too many
  to check one at a time.
- Skills are ranked by their evidence (the person's own documents, how many files name them, how much the
  best file is trusted) before the groups are made, so the first ones asked about are the likeliest true.
- Items added by hand are never queued: the person typed them, so they are already checked.
- A card Hal folded into another (`store.fold_away`: a duplicate, or a note's card inside a project folder)
  is neither checked nor waiting, so it is left out of `done` and `total`; the page lists the duplicates
  apart (`duplicates.listing`).

`best(con)` gives "Your best experiences", which the page shows above the queue: the jobs, courses,
projects, achievements and certificates in the core tier, then supporting, then not yet sorted
(`tiers.order`), each tier ranked by its evidence (`strength`) and then by how recent it is, at most
`BEST_MAX` of them, so the person can check what Hal would lead with in one short sitting. The archived ones
come apart, ranked the same way, for the page to fold away. Hidden items are in neither.
"""
from __future__ import annotations

import sqlite3

from jobhunt.profile import conflicts, merge, store, tiers

# The order of importance: who the person is, then their history, then the lists; skills come last.
KIND_ORDER = ("basic", "job", "education", "certificate", "achievement", "project", "link")
# A CV lists ten to thirty skills; past that the rest are likely course topics, so they wait under "Show more".
SKILLS_FIRST = 30


def _basic_order(item: dict) -> int:
    key = item["fields"].get("key", "")
    return store.BASIC_KEYS.index(key) if key in store.BASIC_KEYS else len(store.BASIC_KEYS)


def source_strength(e: dict) -> tuple:
    """How far one piece of evidence is trusted, for sorting: the person's own, rank, verified, quoted."""
    return (e.get("role", "") == store.OWN_ROLE, merge.rank(e["doc_type"]), bool(e["verified"]), bool(e["quote"]))


def best_source(item: dict) -> dict | None:
    """The evidence an item rests on most: the person's own document, then the most trusted file, then a quote
    found in its file; None when it has no evidence."""
    return max(item["evidence"], key=source_strength, default=None)


def strength(item: dict) -> tuple:
    """How well an item is shown, for sorting strongest first: own documents, files, trust, sources."""
    ev = item["evidence"]
    best = best_source(item)
    return (sum(e.get("role", "") == store.OWN_ROLE for e in ev), len({e["source_rel"] for e in ev}),
            source_strength(best) if best else (), len(ev))


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
    """What is left to check: {"conflicts", "cards", "more", "studied", "done", "total"} (see the module notes).

    A card is {"type": "item" | "skills", "key", "source_rel", "items": [ITEM], "best": [evidence id]}; an
    item card holds one item and a `source_rel` of "", a skills card the file its skills rest on most. `best`
    names, for each item, the source whose quote the page shows.
    """
    # a card Hal folded into another (`store.fold_away`) was not checked by the person, nor is it left to check
    found = {kind: [i for i in store.items(con, kind) if i["origin"] == "found" and i["merged_into"] is None]
             for kind in store.KINDS}
    done = sum(i["state"] != "hal" for kind in found.values() for i in kind)
    waiting = {kind: [i for i in items if i["state"] == "hal"] for kind, items in found.items()}
    cards = []
    for kind in KIND_ORDER:
        items = waiting[kind]
        if kind == "basic":
            items = sorted(items, key=_basic_order)
        cards += [_with_best({"type": "item", "key": f"item:{i['id']}", "source_rel": "", "items": [i]})
                  for i in items]
    studied = sorted((i for i in waiting["skill"] if store.is_studied(i)),
                     key=lambda i: merge.skill_key(i["fields"].get("name", "")))
    skills = [i for i in waiting["skill"] if not store.is_studied(i)]
    strong = sorted((i for i in skills if not store.is_weak(i)), key=strength, reverse=True)
    weak = sorted((i for i in skills if store.is_weak(i)), key=strength, reverse=True)
    cards += _groups(strong[:SKILLS_FIRST], "skills")
    more = _groups(strong[SKILLS_FIRST:], "more") + _groups(weak, "weak")
    left = sum(len(c["items"]) for c in cards)
    return {"conflicts": conflicts.listing(con), "cards": cards, "more": more, "studied": studied, "done": done,
            "total": done + left}


# One short sitting: about twenty to thirty cards (2026-11-07); past that the person stops reading.
BEST_MAX = 25
NO_DATE = -1


def recency(item: dict) -> int:
    """The last month an item was true, as `merge.month_index` counts; "present" is this month, no date -1."""
    fields = item["fields"]
    found = merge.span(fields)
    if found is not None:
        return found[1]
    point = merge.month_index(fields.get("end") or fields.get("date") or "", "end")
    return point if point is not None else NO_DATE


def ranked(items: list[dict]) -> list[dict]:
    """Items by tier (`tiers.order`), then strongest evidence first, then most recent first."""
    out = sorted(items, key=recency, reverse=True)
    out.sort(key=strength, reverse=True)       # stable: equal evidence keeps the newest first
    out.sort(key=tiers.order)
    return out


def best(con: sqlite3.Connection) -> dict:
    """{"best": [ITEM], "archived": [ITEM]}: the experiences to check first, and the archived ones."""
    order = ranked([i for kind in tiers.TIERED_KINDS for i in store.items(con, kind, hidden=False)])
    return {"best": [i for i in order if i["tier"] != tiers.ARCHIVE][:BEST_MAX],
            "archived": [i for i in order if i["tier"] == tiers.ARCHIVE]}
