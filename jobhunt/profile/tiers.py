"""Which experiences are worth telling an employer: a tier for each job, course, project, achievement and
certificate in the profile.

A tier is `core` (what the person would lead with for the jobs they want), `supporting` (true and useful when
a job asks for it), `archive` (never used for writing: hobby tools, generic online courses, trivia) or ''
(not judged yet). `refresh(con)` works the tiers out after an ingest and when the person asks; `set_tier`
is the person's own choice; `shown(item)` is the tier a shaped item carries (`store.item`, `store.items`).

Design decisions, each with its reason:

- Rules come first, in code (`rule`): a hidden card is archive; a found card nobody has checked with no
  evidence from the person's own documents (`store.OWN_ROLE`) is archive; a school-age qualification (GCSE,
  O level, a school-leaving certificate, secondary school) is supporting. They are cheap and certain, so
  the AI is asked only about the rest. The own-documents rule leaves out cards the person confirmed,
  edited or added: their check is the evidence.
- Rules are applied again whenever a tier is shown, so a card archived for having no evidence from the
  person's own documents leaves the archive the moment they confirm it, before any refresh.
- The judge is the `profile_tier` prompt (checker role, JSON), one call per `BATCH` cards, given the
  person's aim (`aim`) and each card's fields, quotes and the kinds of document they came from. Without
  an AI the rules alone are used and the other cards stay unjudged ('').
- A tier the person set (`tier_by` `person`) is never changed by Hal. Hal judges a card again only when
  its evidence changes: `tier_basis` keeps a fingerprint of the evidence it was judged from (`RULE_BASIS`
  for a rule), so a second refresh with nothing new asks the AI nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3

from jobhunt import db
from jobhunt.profile import store

log = logging.getLogger(__name__)

CORE, SUPPORTING, ARCHIVE = "core", "supporting", "archive"
TIERS = (CORE, SUPPORTING, ARCHIVE)
TIERED_KINDS = ("job", "education", "project", "achievement", "certificate")
BY_HAL, BY_PERSON = "hal", "person"
RULE_BASIS = "rule"
TIER_WORDS = {CORE: "Lead with this", SUPPORTING: "Useful when a job asks", ARCHIVE: "Archived", "": "Not sorted yet"}
WHY_HIDDEN = "You hid it"
WHY_NOT_OWN = "Only in files that are not your own"
WHY_SCHOOL = "A school qualification"
WHY_PERSON = "You chose this"
# A school-age qualification: useful when a job asks for it, never the lead (2026-11-07).
SCHOOL = re.compile(r"\b(?:i?gcses?|o[ -]?levels?|school[ -]leaving|leaving cert(?:ificate)?|secondary school)\b",
                    re.I)

PURPOSE = "profile_tier"
# The prompt calls the archive "leave_out": its words reach every person's prompts, and "archive" is a word of
# one kind of career (tests/test_prompts_neutral.py). A reply that says "archive" is taken too.
REPLY_TIERS = {CORE: CORE, SUPPORTING: SUPPORTING, "leave_out": ARCHIVE, ARCHIVE: ARCHIVE}
BATCH = 20                 # cards per call: about 4K tokens of fields and quotes, well inside a 64K context
TIMEOUT_S = 180
WHY_MAX = 80               # "a few plain words"; a longer reply is cut, never refused
QUOTES = 3                 # the quotes of a card the AI reads, best first
QUOTE_CHARS = 200
FIELD_CHARS = 300
LIST_ITEMS = 6             # duties, tools or modules of a card the AI reads
FINGERPRINT_CHARS = 16


def rule(item: dict) -> tuple[str, str] | None:
    """(tier, why) when a rule decides the shaped `item`'s tier, else None (see the module notes)."""
    if item["kind"] not in TIERED_KINDS:
        return None
    if item["state"] == "hidden":
        return ARCHIVE, WHY_HIDDEN
    if item["origin"] == "found" and item["state"] == "hal" and \
            not any(e.get("role", "") == store.OWN_ROLE for e in item["evidence"]):
        return ARCHIVE, WHY_NOT_OWN
    if item["kind"] in ("education", "certificate"):
        f = item["fields"]
        if SCHOOL.search(" ".join(str(f.get(k) or "") for k in ("qualification", "subject", "institution", "name"))):
            return SUPPORTING, WHY_SCHOOL
    return None


def shown(item: dict, stored: dict) -> dict:
    """The tier to show for a shaped `item`: {tier, tier_by, tier_why, tier_words}.

    `stored` holds the item's tier columns. The person's choice wins, then a rule, then the AI's judgement;
    a stored rule tier whose rule no longer applies is shown as not judged.
    """
    if item["kind"] not in TIERED_KINDS:
        tier, by, why = "", "", ""
    elif stored["tier_by"] == BY_PERSON and stored["tier"] in TIERS:
        tier, by, why = stored["tier"], BY_PERSON, stored["tier_why"] or WHY_PERSON
    elif (found := rule(item)) is not None:
        tier, by, why = found[0], BY_HAL, found[1]
    elif stored["tier_basis"] == RULE_BASIS or stored["tier"] not in TIERS:
        tier, by, why = "", "", ""
    else:
        tier, by, why = stored["tier"], BY_HAL, stored["tier_why"]
    words = TIER_WORDS[tier] if item["kind"] in TIERED_KINDS else ""
    return {"tier": tier, "tier_by": by, "tier_why": why, "tier_words": words}


def set_tier(con: sqlite3.Connection, item_id, tier: str) -> dict:
    """The person puts an item in `tier`; Hal never changes it after this. Returns the item.

    Raises ValueError for a tier that is not one of `TIERS` or an item of a kind with no tier, and
    LookupError for an unknown item.
    """
    found = store.item(con, item_id)
    if tier not in TIERS:
        raise ValueError(f"{tier!r} is not a tier; use one of {', '.join(TIERS)}.")
    if found["kind"] not in TIERED_KINDS:
        raise ValueError(f"A {found['kind']} has no tier.")
    with con:
        con.execute("UPDATE profile_items SET tier = ?, tier_by = ?, tier_why = ?, tier_basis = '', updated = ? "
                    "WHERE id = ?", (tier, BY_PERSON, WHY_PERSON, db.now(), found["id"]))
    return store.item(con, found["id"])


def _fingerprints(con: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    """A short hash of each item's evidence (file, quote and what it said), to see when it changes."""
    rows: dict[int, list] = {i: [] for i in ids}
    for e in con.execute("SELECT item_id, source_rel, quote, said FROM profile_evidence ORDER BY id"):
        if e["item_id"] in rows:
            rows[e["item_id"]].append([e["source_rel"], e["quote"], e["said"]])
    return {i: hashlib.sha256(json.dumps(sorted(r)).encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]
            for i, r in rows.items()}


def _write(con: sqlite3.Connection, item_id: int, tier: str, why: str, basis: str) -> None:
    con.execute("UPDATE profile_items SET tier = ?, tier_by = ?, tier_why = ?, tier_basis = ? WHERE id = ?",
                (tier, BY_HAL if tier else "", why, basis, item_id))


def aim(con: sqlite3.Connection) -> str:
    """What the person is aiming for, in words for the judge: the roles they want, else their CV's
    headline, else their newest course."""
    from jobhunt.core import settings

    roles = [str(r) for r in settings.get("goal.roles") or [] if str(r).strip()]
    interests = [str(r) for r in settings.get("goal.interests") or [] if str(r).strip()]
    if roles or interests:
        return "; ".join(p for p in (("jobs as " + ", ".join(roles)) if roles else "",
                                      ("interested in " + ", ".join(interests)) if interests else "") if p)
    headline = _headline(con)
    if headline:
        return headline
    course = " ".join(str(settings.get(f"you.education.{k}") or "") for k in ("degree", "subject")).strip()
    if not course:
        newest = store.items(con, "education", hidden=False)
        course = newest[0]["title"] if newest else ""
    return f"jobs that build on {course}" if course else "jobs that fit their experience"


def _headline(con: sqlite3.Connection) -> str:
    """The line under the name on the person's CV, read without making a CV when there is none."""
    from jobhunt.cvstudio import store as cv_store

    row = con.execute("SELECT current_version_id FROM cv_documents WHERE id = ?", (cv_store.DOC_ID,)).fetchone()
    if row is None or not row[0]:
        return ""
    try:
        return " ".join(str(cv_store.get(row[0], con=con).headline or "").split())
    except LookupError:
        return ""


def _short(value):
    if isinstance(value, list):
        return [_short(v) for v in value[:LIST_ITEMS]]
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    return str(value)[:FIELD_CHARS] if isinstance(value, str) else value


def card(item: dict) -> dict:
    """What the judge reads about one shaped item: its fields, its best quotes and where they came from."""
    from jobhunt.profile import check

    ev = sorted(item["evidence"], key=check.source_strength, reverse=True)
    return {"id": item["id"], "kind": item["kind"], "fields": _short(item["fields"]),
            "quotes": [e["quote"][:QUOTE_CHARS] for e in ev if e["quote"]][:QUOTES],
            "from": sorted({e["doc_type"] or "other" for e in ev}),
            "own_document": any(e.get("role", "") == store.OWN_ROLE for e in ev),
            "checked_by_person": item["state"] in ("confirmed", "edited")}


def _ask(con: sqlite3.Connection, cards: list[dict], aim_words: str) -> dict[int, tuple[str, str]]:
    """The judge's tiers for one batch, {item id: (tier, why)}; raises `LLMUnavailable` when it cannot answer."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    user = f"The person is aiming for: {aim_words}\n\nCards, one per line:\n" + \
        "\n".join(json.dumps(c, ensure_ascii=True) for c in cards)
    reply = ai.chat_json(prompts.text(PURPOSE, con=con), user, role="checker", timeout=TIMEOUT_S, purpose=PURPOSE)
    asked = {c["id"] for c in cards}
    out = {}
    for said in reply.get("cards") if isinstance(reply.get("cards"), list) else []:
        if not isinstance(said, dict):
            continue
        try:
            item_id = int(said.get("id"))
        except (TypeError, ValueError):
            log.info("left out a tier for a card the AI named %r", said.get("id"))
            continue
        tier = REPLY_TIERS.get(str(said.get("tier") or "").strip().lower(), "")
        if item_id in asked and tier:
            out[item_id] = (tier, " ".join(str(said.get("why") or "").split())[:WHY_MAX])
    return out


def _ready() -> bool:
    from jobhunt import ai
    from jobhunt.core import runner

    why = ai.ready(priority=runner.current_priority(), purpose=PURPOSE)
    if why is not None:
        log.info("sorted the profile by rules only: %s", why)
    return why is None


def refresh(con: sqlite3.Connection, *, judge: bool = True) -> dict[str, int]:
    """Works out every tier Hal may set: rules first, then the AI for cards whose evidence is new.

    Returns {"rules", "judged", "unjudged"}: cards a rule placed, cards the AI placed now, and cards left
    unjudged (no AI, or it did not answer for them). With `judge` False, or no AI, only the rules run.
    """
    from jobhunt import ai
    from jobhunt.ai.base import PrivateLeak

    marks = ",".join("?" * len(TIERED_KINDS))
    rows = {r["id"]: r for r in con.execute(f"SELECT * FROM profile_items WHERE kind IN ({marks}) ORDER BY id",
                                            TIERED_KINDS)}
    found_ev = store.evidence_of(con, list(rows))
    shaped = {i: {"id": i, "kind": r["kind"], "state": r["state"], "origin": r["origin"],
                  "fields": json.loads(r["fields"]), "evidence": found_ev[i]} for i, r in rows.items()}
    basis = _fingerprints(con, list(rows))
    counts, todo = {"rules": 0, "judged": 0, "unjudged": 0}, []
    with con:
        for item_id, row in rows.items():
            if row["tier_by"] == BY_PERSON:
                continue
            found = rule(shaped[item_id])
            if found is not None:
                _write(con, item_id, found[0], found[1], RULE_BASIS)
                counts["rules"] += 1
            elif row["tier_basis"] == RULE_BASIS:
                _write(con, item_id, "", "", "")
                todo.append(shaped[item_id])
            elif not row["tier"] or row["tier_basis"] != basis[item_id]:
                todo.append(shaped[item_id])
    if not (judge and todo and _ready()):
        counts["unjudged"] = len(todo)
        return counts
    aim_words = aim(con)
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        try:
            said = _ask(con, [card(i) for i in batch], aim_words)
        except ai.LLMUnavailable as e:
            log.info("stopped sorting the profile: %s", e)
            counts["unjudged"] += len(todo) - start
            break
        except PrivateLeak:  # a quote held a private answer: these cards wait, the rest go on
            log.info("left %d cards unjudged: a quote holds a private answer", len(batch))
            counts["unjudged"] += len(batch)
            continue
        with con:
            for item in batch:
                if item["id"] in said:
                    tier, why = said[item["id"]]
                    _write(con, item["id"], tier, why, basis[item["id"]])
        counts["judged"] += len(said)
        counts["unjudged"] += len(batch) - len(said)
    return counts

