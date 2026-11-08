"""Duplicate experiences: cards that are one project, course or achievement under different names, merged
after a read, with a way back.

A real read left five cards for one dissertation (its titles over its life), two for one autonomous
navigation project and two for one network sniffer, under names `merge.same_item` cannot tie together
(2026-11-08). `refresh(con)` runs once after each read (`profile.actions.dedupe`):

1. The cards it may merge (`candidates`): projects, courses and achievements Hal found and nobody checked,
   not hidden, and not taken out of a merge by the person before (`store.APART`). A card the person confirmed
   or edited is never merged without their action.
2. Rules that are certain (`certain`): two cards with the same project link, or found in the same file of
   one piece of work (not a CV, letter or transcript, which list every experience), are one.
3. The AI (`profile_same` prompt, checker role, JSON, `BATCH` cards a call, each with its name, summary,
   tools, dates, links and files) groups the rest.
4. A rule that is never broken (`never`): cards whose dates are `YEARS_APART` or more years apart and that
   share no file are two experiences, whatever the AI says.

Each group is merged into its strongest card (`target`: the one the person's CV names, else the best shown,
`check.strength`), which keeps its name unless a better-ranked source names it otherwise (`store.refold`).
The others are hidden as "Merged into <card>" with their evidence moved to it (`store.merge_into`). The Check
your record page lists each merge under "Hal merged these" (`listing`) with **Undo** (`undo`), which puts the
cards back as they were and keeps them apart from then on. Without an AI only the certain rule merges.
"""
from __future__ import annotations

import json
import logging
import sqlite3

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


def _files(item: dict) -> set[str]:
    """The files of one piece of work the card was found in: not a CV, letter or transcript."""
    return {e["source_rel"] for e in item["evidence"] if merge.doc_group(e["doc_type"]) not in LISTS}


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
    """True when two cards are surely one: the same project link, or a shared file of one piece of work."""
    link = _link(a)
    return bool(link and link == _link(b)) or bool(_files(a) & _files(b))


def never(a: dict, b: dict) -> bool:
    """True when two cards are surely two: dates `YEARS_APART` or more years apart and no file shared."""
    sa, sb = _span(a), _span(b)
    if sa is None or sb is None or _files(a) & _files(b):
        return False
    return max(sa[0], sb[0]) - min(sa[1], sb[1]) >= YEARS_APART * MONTHS


def _short(value):
    if isinstance(value, list):
        return [_short(v) for v in value[:LIST_ITEMS]]
    return str(value)[:FIELD_CHARS] if isinstance(value, str) else value


def card(item: dict) -> dict:
    """What the AI reads about one card: its name, summary, tools, dates, links and files."""
    f = item["fields"]
    return {"id": item["id"], "kind": item["kind"], "name": _short(item["title"]),
            "summary": _short(f.get("summary") or f.get("detail") or item["sub"] or ""),
            "tools": _short(list(f.get("tools") or [])), "when": item["when"],
            "links": [v for v in (f.get("link"),) if v],
            "files": sorted({e["source_rel"] for e in item["evidence"]})[:FILES]}


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


def groups(found: list[dict], said: list[list[int]]) -> list[list[dict]]:
    """The groups of cards to merge: the certain pairs, and the AI's groups (`said`) without the pairs that
    are never one; each group holds no two cards that are never one."""
    by_id = {i["id"]: i for i in found}
    root = {i: i for i in by_id}

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
                join(a["id"], b["id"])
    for ids in said:
        for n, a in enumerate(ids):
            for b in ids[n + 1:]:
                if a in by_id and b in by_id and not never(by_id[a], by_id[b]):
                    join(a, b)
    parts: dict[int, list[dict]] = {}
    for i in by_id:
        parts.setdefault(top(i), []).append(by_id[i])
    out = []
    for part in parts.values():
        left = sorted(part, key=_order)
        while len(left) > 1:          # a chain of AI pairs may join two cards that are never one: split them
            kept = [left[0]]
            for c in left[1:]:
                if not any(never(c, k) for k in kept):
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


def _said(con: sqlite3.Connection, found: list[dict]) -> tuple[list[list[int]], int]:
    """The AI's groups for `found`, `BATCH` cards a call, and how many cards it could not look at."""
    from jobhunt import ai
    from jobhunt.ai.base import PrivateLeak

    said, missed = [], 0
    ordered = sorted(found, key=lambda i: merge.project_key(i["title"]))   # likely twins in one call
    for start in range(0, len(ordered), BATCH):
        batch = ordered[start:start + BATCH]
        try:
            said += _ask(con, [card(i) for i in batch])
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
    for kind in KINDS:
        found = candidates(con, kind)
        if len(found) < 2:
            continue
        said, missed = _said(con, found) if use_ai else ([], 0)
        counts["unjudged"] += missed
        for group in groups(found, said):
            into = target(group)
            why = MERGED + into["title"]
            for other in group:
                if other["id"] != into["id"]:
                    store.merge_into(con, other["id"], into["id"], why)
                    counts["merged"] += 1
            counts["groups"] += 1
    return counts


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
