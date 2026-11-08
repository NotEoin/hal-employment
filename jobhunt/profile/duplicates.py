"""Duplicate experiences: cards that are one project, course or achievement under different names, merged
after a read, with a way back.

A real read left five cards for one dissertation (its titles over its life), two for one autonomous
navigation project and two for one network sniffer, under names `merge.same_item` cannot tie together
(2026-11-08). `refresh(con)` runs once after each read (`profile.actions.dedupe`):

1. The cards it may merge (`candidates`): projects, courses and achievements Hal found and nobody checked,
   not hidden, and not taken out of a merge by the person before (`store.APART`). A card the person confirmed
   or edited is never merged without their action.
2. The one rule that is certain (`certain`): two cards with the same project link are one.
3. The AI (`profile_same` prompt, checker role, JSON, `BATCH` cards a call, each with its name, summary,
   tools, dates, links and files) groups the rest. A shared file is shown to it, never taken as proof: a
   shared listing merged a portfolio into a dissertation (2026-11-08). A CV, letter or transcript lists
   every experience, and so does a file whose project facts name three or more projects (`listing_files`),
   such as a dossier of all the person's work: sharing one says nothing, and the AI is not shown it.
4. Rules that are never broken: cards whose dates are `YEARS_APART` or more years apart and that share no
   file are two experiences (`never`), and so are two projects holding different repositories or project
   folders (`apart`, `card_units`), whatever the AI says. A card holding a repository or project folder of
   its own and a card holding none are one only when their names (and the unit's folder name) share a
   distinctive word (`unrelated`, `projects.distinctive_words`) and the AI agrees, or they share a project
   link: a personal follow-up repository was merged into the dissertation it followed (2026-11-08), while a
   repository "probe_sniffer" is the CV's "Probe Request Sniffer".

Each group is merged into its strongest card (`target`: the one the person's CV names, else the best shown,
`check.strength`), which keeps its name unless a better-ranked source names it otherwise (`store.refold`).
Every card merged into it is one the AI put with it or that shares its link: pairs never chain, as a chain
put a tool pack and an unrelated card into one scheduler's card (2026-11-08). A card merged away is never a
target (`store.merge_into`).
The others are hidden as "Merged into <card>" with their evidence moved to it (`store.merge_into`). The Check
your record page lists each merge under "Hal merged these" (`listing`) with **Undo** (`undo`), which puts the
cards back as they were and keeps them apart from then on. Without an AI only the certain rule merges.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import PurePath

from jobhunt.profile import check, merge, store

log = logging.getLogger(__name__)

KINDS = ("project", "education", "achievement")
PURPOSE = "profile_same"
# Cards per call: a card is about 300 characters, so 60 are about 5K tokens, well inside a 64K context.
BATCH = 60
TIMEOUT_S = 180
YEARS_APART = 2            # a course and a project two or more years apart are two, unless one file holds both
MONTHS = 12
MERGED = "Merged into "    # `merged_why` of a merged card, then the card's name; "Part of ..." is a project fold
LISTS = ("cv", "letter", "transcript")    # documents that list every experience: sharing one says nothing
FIELD_CHARS = 300
LIST_ITEMS = 8
FILES = 5


def candidates(con: sqlite3.Connection, kind: str) -> list[dict]:
    """The cards of `kind` Hal may merge on its own (see the module notes), as shaped items."""
    return [i for i in store.items(con, kind, hidden=False) if i["state"] == "hal" and i["origin"] == "found"
            and i["merged_why"] != store.APART]


def listing_files(con: sqlite3.Connection) -> set[str]:
    """The files that list projects (`merge.lists_projects`): their project facts name `merge.LISTING_NAMES` or
    more projects. A fact a tidy filed again on its folder's card (`store.refile`) counts under the name of the
    card it came from (`moved_from`)."""
    names: dict[str, list[str]] = {}
    for r in con.execute("""SELECT e.source_rel, e.said, f.fields AS was FROM profile_evidence e
                            JOIN profile_items i ON i.id = e.item_id LEFT JOIN profile_items f ON f.id = e.moved_from
                            WHERE i.kind = 'project'"""):
        said = json.loads(r["was"] or r["said"] or "{}")
        names.setdefault(r["source_rel"], []).append(str(said.get("name") or ""))
    return {source for source, said in names.items() if merge.lists_projects(said)}


def _files(item: dict, listings: frozenset[str] = frozenset()) -> set[str]:
    """The files of one piece of work the card was found in: not a CV, letter or transcript, nor a file that
    lists projects (`listings`)."""
    return {e["source_rel"] for e in item["evidence"]
            if merge.doc_group(e["doc_type"]) not in LISTS and e["source_rel"] not in listings}


def _link(item: dict) -> str:
    return merge.project_link(str(item["fields"].get("link") or "")) if item["kind"] == "project" else ""


def _span(item: dict) -> tuple[int, int] | None:
    """The card's first and last month, from its dates, its period or its date; None when it has none."""
    fields = item["fields"]
    if fields.get("period") and not fields.get("start"):
        start, end, current = merge.parse_period(str(fields["period"]))
        fields = {"start": start, "end": end, "current": current}
    if (found := merge.span(fields)) is not None:
        return found
    point = merge.month_index(str(fields.get("date") or ""))
    return (point, point) if point is not None else None


def certain(a: dict, b: dict) -> bool:
    """True when two cards are surely one: the same project link."""
    link = _link(a)
    return bool(link and link == _link(b))


def card_units(con: sqlite3.Connection, found: list[dict], listings: frozenset[str] = frozenset()) -> dict:
    """Each project card's repositories and project folders: {card id: {path}}. A repository's note counts for
    every card it speaks for, a note inside a project folder only for the folder's own card, and a file that
    lists projects (`listings`) for none."""
    from jobhunt.ingest import projects

    by_note = projects.note_units(con)
    out: dict[int, set[str]] = {}
    for item in found:
        name = {"name": str(item["fields"].get("name") or "")}
        held = set()
        for e in item["evidence"]:
            unit = by_note.get(e["source_rel"])
            if unit is None or e["source_rel"] in listings:
                continue
            path, own = unit
            if own is None or merge.same_item("project", {"name": own}, name):
                held.add(path)
        out[item["id"]] = held
    return out


def apart(a: dict, b: dict, units: dict) -> bool:
    """True when two cards hold different repositories or project folders (`card_units`): two projects."""
    ua, ub = units.get(a["id"], set()), units.get(b["id"], set())
    return bool(ua and ub and not ua & ub)


def unrelated(a: dict, b: dict, units: dict) -> bool:
    """True when one card holds a repository or project folder (`card_units`), the other holds none, and the
    names share no distinctive word (`projects.distinctive_words`), counting the unit's folder name: two
    projects unless they share a project link (see the module notes)."""
    from jobhunt.ingest import projects

    ua, ub = units.get(a["id"], set()), units.get(b["id"], set())
    if bool(ua) == bool(ub):
        return False
    held, held_units, other = (a, ua, b) if ua else (b, ub, a)
    names = [_name(held), *(PurePath(u).name for u in held_units)]
    words = set().union(*(projects.distinctive_words(n) for n in names))
    return not words & projects.distinctive_words(_name(other))


def _name(item: dict) -> str:
    return str(item["fields"].get("name") or item["title"] or "")


def never(a: dict, b: dict, listings: frozenset[str] = frozenset()) -> bool:
    """True when two cards are surely two: dates `YEARS_APART` or more years apart and no file shared."""
    sa, sb = _span(a), _span(b)
    if sa is None or sb is None or _files(a, listings) & _files(b, listings):
        return False
    return max(sa[0], sb[0]) - min(sa[1], sb[1]) >= YEARS_APART * MONTHS


def _short(value):
    if isinstance(value, list):
        return [_short(v) for v in value[:LIST_ITEMS]]
    return str(value)[:FIELD_CHARS] if isinstance(value, str) else value


def card(item: dict, listings: frozenset[str] = frozenset()) -> dict:
    """What the AI reads about one card: its name, summary, tools, dates, links and files (not the files that
    list projects, `listings`, which every card they list shares)."""
    f = item["fields"]
    return {"id": item["id"], "kind": item["kind"], "name": _short(item["title"]),
            "summary": _short(f.get("summary") or f.get("detail") or item["sub"] or ""),
            "tools": _short(list(f.get("tools") or [])), "when": item["when"],
            "links": [v for v in (f.get("link"),) if v],
            "files": sorted({e["source_rel"] for e in item["evidence"] if e["source_rel"] not in listings})[:FILES]}


def _ask(con: sqlite3.Connection, cards: list[dict]) -> list[list[int]]:
    """The AI's groups for one batch, as lists of the ids it was sent; raises `LLMUnavailable`."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    user = "Cards, one per line:\n" + "\n".join(json.dumps(c, ensure_ascii=True) for c in cards)
    reply = ai.chat_json(prompts.text(PURPOSE, con=con), user, role="checker", timeout=TIMEOUT_S, purpose=PURPOSE)
    asked = {c["id"] for c in cards}
    out = []
    for group in reply.get("groups") if isinstance(reply.get("groups"), list) else []:
        ids = []
        for said in group if isinstance(group, list) else []:
            try:
                ids.append(int(said))
            except (TypeError, ValueError):
                log.info("left out a card the AI named %r", said)
        ids = sorted(set(ids) & asked)
        if len(ids) > 1:
            out.append(ids)
    return out


def groups(found: list[dict], said: list[list[int]], listings: frozenset[str] = frozenset(),
           units: dict | None = None) -> list[list[dict]]:
    """The groups of cards to merge: the certain pairs and the AI's groups (`said`), without the pairs that
    are never one (`never`), hold two units (`apart`) or are a unit's card and an `unrelated` one. Each card
    in a group is one its target (`target`) is certainly one with or the AI put with it, so a chain of pairs
    never joins two cards nothing tied; and no two cards in a group are refused that way."""
    by_id = {i["id"]: i for i in found}
    root = {i: i for i in by_id}
    units = units or {}
    agreed: set[frozenset[int]] = set()

    def refused(a: dict, b: dict) -> bool:
        return never(a, b, listings) or apart(a, b, units) or (unrelated(a, b, units) and not certain(a, b))

    def top(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    def join(a: int, b: int) -> None:
        root[top(a)] = top(b)

    for n, a in enumerate(found):
        for b in found[n + 1:]:
            if certain(a, b):
                agreed.add(frozenset((a["id"], b["id"])))
    for ids in said:
        agreed.update(frozenset((a, b)) for n, a in enumerate(ids) for b in ids[n + 1:]
                      if a != b and a in by_id and b in by_id)
    for pair in agreed:
        a, b = sorted(pair)
        if not refused(by_id[a], by_id[b]):
            join(a, b)
    parts: dict[int, list[dict]] = {}
    for i in by_id:
        parts.setdefault(top(i), []).append(by_id[i])
    out = []
    for part in parts.values():
        left = sorted(part, key=_order)
        while len(left) > 1:          # a chain of pairs joined cards nothing tied: each must agree with the target
            kept = [left[0]]
            for c in left[1:]:
                if frozenset((c["id"], kept[0]["id"])) in agreed and not any(refused(c, k) for k in kept):
                    kept.append(c)
            if len(kept) > 1:
                out.append(kept)
            left = [c for c in left if c not in kept]
    return out


def _from_cv(item: dict) -> bool:
    return any(merge.doc_group(e["doc_type"]) == "cv" for e in item["evidence"])


def _order(item: dict) -> tuple:
    """Strongest first: the card the CV names, then the best shown (`check.strength`), then the oldest."""
    return not _from_cv(item), _negated(check.strength(item)), item["id"]


def _negated(strength: tuple) -> tuple:
    """`check.strength` turned so that sorting ascending puts the strongest first."""
    return tuple(_negated(x) if isinstance(x, tuple) else -int(x) for x in strength)


def target(group: list[dict]) -> dict:
    """The card a group is merged into (`_order`)."""
    return min(group, key=_order)


def _ready() -> bool:
    from jobhunt import ai
    from jobhunt.core import runner

    why = ai.ready(priority=runner.current_priority(), purpose=PURPOSE)
    if why is not None:
        log.info("merged duplicate experiences by rules only: %s", why)
    return why is None


def _said(con: sqlite3.Connection, found: list[dict],
          listings: frozenset[str] = frozenset()) -> tuple[list[list[int]], int]:
    """The AI's groups for `found`, `BATCH` cards a call, and how many cards it could not look at."""
    from jobhunt import ai
    from jobhunt.ai.base import PrivateLeak

    said, missed = [], 0
    ordered = sorted(found, key=lambda i: merge.project_key(i["title"]))   # likely twins in one call
    for start in range(0, len(ordered), BATCH):
        batch = ordered[start:start + BATCH]
        try:
            said += _ask(con, [card(i, listings) for i in batch])
        except ai.LLMUnavailable as e:
            log.info("stopped looking for duplicate experiences: %s", e)
            missed += len(ordered) - start
            break
        except PrivateLeak:   # a card holds a private answer: this batch waits, the rest go on
            log.info("left %d cards out of the duplicate check: a card holds a private answer", len(batch))
            missed += len(batch)
    return said, missed


def refresh(con: sqlite3.Connection, *, judge: bool = True) -> dict[str, int]:
    """Merges the duplicate experiences (see the module notes); returns {"groups", "merged", "unjudged"}: the
    groups merged, the cards hidden into them, and the cards the AI could not look at."""
    counts = {"groups": 0, "merged": 0, "unjudged": 0}
    use_ai = judge and _ready()
    listings = frozenset(listing_files(con))
    for kind in KINDS:
        found = candidates(con, kind)
        if len(found) < 2:
            continue
        said, missed = _said(con, found, listings) if use_ai else ([], 0)
        counts["unjudged"] += missed
        units = card_units(con, found, listings) if kind == "project" else {}
        for group in groups(found, said, listings, units):
            into = target(group)
            why = MERGED + into["title"]
            for other in group:
                if other["id"] != into["id"]:
                    store.merge_into(con, other["id"], into["id"], why)
                    counts["merged"] += 1
            counts["groups"] += 1
    return counts


def retake(con: sqlite3.Connection) -> int:
    """Takes apart each card Hal merged into a card nobody checked, unless the two share a project link
    (`certain`), so the pass after a tidy (`refresh`) merges again only what today's rules allow; returns how
    many cards came back. Older merges did not ask the AI and chained, and which ones the AI agreed to was not
    kept (2026-11-08). They come back free to merge (`store.unfold` with `apart=False`)."""
    back = 0
    for kind in KINDS:
        for r in con.execute("""SELECT i.id, i.fields, t.fields AS into_fields FROM profile_items i
                                JOIN profile_items t ON t.id = i.merged_into WHERE i.kind = ? AND i.state = 'hidden'
                                AND substr(i.merged_why, 1, ?) = ? AND t.state = 'hal'""",
                             (kind, len(MERGED), MERGED)).fetchall():
            if certain({"kind": kind, "fields": json.loads(r["fields"])},
                       {"kind": kind, "fields": json.loads(r["into_fields"])}):
                continue
            store.unfold(con, r["id"], apart=False)
            back += 1
    return back


def listing(con: sqlite3.Connection) -> list[dict]:
    """Each card Hal merged others into, shown on Check your record: [{"item": ITEM, "merged": [ITEM]}]."""
    out = []
    for kind in KINDS:
        found = store.items(con, kind)
        merged: dict[int, list[dict]] = {}
        for i in found:
            if i["merged_into"] is not None and i["merged_why"].startswith(MERGED):
                merged.setdefault(i["merged_into"], []).append(i)
        out += [{"item": i, "merged": merged[i["id"]]} for i in found if i["id"] in merged and i["state"] != "hidden"]
    return out


def undo(con: sqlite3.Connection, item_id) -> dict:
    """Takes the cards Hal merged into this one back out, as they were, and keeps them all apart from then on;
    returns the card. LookupError for an unknown card, ValueError for one Hal merged nothing into."""
    item = store.item(con, item_id)
    merged = [i for i in store.items(con, item["kind"]) if i["merged_into"] == item["id"]
              and i["merged_why"].startswith(MERGED)]
    if not merged:
        raise ValueError("Hal merged nothing into this card.")
    for i in merged:
        store.unfold(con, i["id"])
    store.keep_apart(con, item["id"])
    return store.item(con, item["id"])
