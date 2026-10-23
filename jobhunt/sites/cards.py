"""The "Notes about job sites" tab: one card per site.

Cards come in this order: the sites Hal has visited or holds notes for, newest visit first; then one card per
application system with notes of its own ("All Workday sites"); then "Every site" for the global notes.
A site's card lists its own notes and its application system's, so the person sees everything Hal will
do there; global notes are listed once, on their own card, rather than on every site.

Every note says where it came from (`origin_label`) and can be switched off; built-in notes cannot be
deleted. Conflicts are worked out from the active notes for the card's address, as the engines would see it.
"""
from __future__ import annotations

import sqlite3

from jobhunt.sites import notes as N

ACCOUNT_WORDS = {"none": "No account needed", "needed": "Needs an account. You are not signed in.",
                 "signed_in": "Needs an account. You are signed in.", "unknown": ""}
ORIGIN_WORDS = {"you": "You told Hal", "learned": "Hal learned", "hal": "Hal added"}


def origin_label(s: N.Stored) -> str:
    """Where a note came from, in the words the card shows: "Built in (Workable)", "You told Hal"."""
    n = s.note
    if n.origin == "builtin":
        return f"Built in ({N.ATS_NAMES.get(n.scope_key, n.scope_key)})" if n.scope_kind == "ats" else "Built in"
    words = ORIGIN_WORDS.get(n.origin, "You told Hal")
    return words + " (waiting for you)" if s.status == "proposed" else words


def note_dict(s: N.Stored) -> dict:
    """One note as the tab draws it."""
    n = s.note
    return {"id": n.id, "kind": n.kind, "text": n.text, "scope": n.scope_kind, "scope_key": n.scope_key,
            "where": N.scope_words(n), "origin": n.origin, "origin_label": origin_label(s),
            "when": "" if s.seed and not s.said else (n.created or "")[:10], "used": s.uses, "worked": s.wins,
            "on": s.status == "active", "proposed": s.status == "proposed", "may_be_out_of_date": s.stale,
            "built_in": s.seed, "can_share": not s.private, "said": s.said or ""}


def _platform(key: str, ats: str | None, notes_here: list[N.Stored]) -> str:
    if ats:
        return N.ATS_NAMES.get(ats, ats)
    if any(s.note.act.get("job_board") for s in notes_here):
        return "Job board"
    return "Own site"


def listing(con: sqlite3.Connection) -> list[dict]:
    """Every card of the tab, with its notes and any conflicts."""
    every = N.stored(con)
    rows = {r["key"]: dict(r) for r in con.execute("SELECT * FROM sites ORDER BY last_seen DESC, key")}
    keys = list(rows)
    for s in every:
        if s.note.scope_kind in ("site", "tenant") and s.note.scope_key not in rows:
            keys.append(s.note.scope_key)
    cards = []
    for key in dict.fromkeys(keys):
        if not key or key.startswith("ats:"):
            continue
        row = rows.get(key, {})
        url = "https://" + key + ("/" if "/" not in key else "")
        ats = row.get("ats") or N.ats_of(url)
        here = [s for s in every if _on_card(s.note, key, ats, url)]
        cards.append(_card(con, key, key, ats, row, here, url))
    for ats in dict.fromkeys(s.note.scope_key for s in every if s.note.scope_kind == "ats"):
        here = [s for s in every if s.note.scope_kind == "ats" and s.note.scope_key == ats]
        name = N.ATS_NAMES.get(ats, ats)
        row = {"account": "", "last_seen": None}
        card = _card(con, f"ats:{ats}", f"All {name} sites", ats, row, here, None)
        if ats == "workday":
            card["account"] = "Each employer has its own sign-in"
        cards.append(card)
    here = [s for s in every if s.note.scope_kind == "global"]
    cards.append(_card(con, "global", "Every site", None, {}, here, None) | {"platform": "Built in"})
    return cards


def _on_card(n: N.Note, key: str, ats: str | None, url: str) -> bool:
    """True when a note belongs on a site's card: its own, its system's, its jobs' and job-board notes."""
    if n.scope_kind in ("site", "tenant"):
        return n.scope_key in (key, N.registrable(url))
    if n.scope_kind == "ats":
        return bool(ats) and n.scope_key == ats
    if n.scope_kind == "job":
        return n.scope_key.startswith(url.rstrip("/"))
    return n.kind == "note" and bool(n.cond) and N.page_ok(n.cond, url, N.host_of(url), None, None)


def _card(con, key: str, name: str, ats: str | None, row: dict, here: list[N.Stored], url: str | None) -> dict:
    conflicts = N.for_page(url, ats=ats, con=con).conflicts() if url else []
    shown = {s.note.id for s in here}
    return {"key": key, "name": name, "platform": _platform(key, ats, here), "ats": ats,
            "account": ACCOUNT_WORDS.get(row.get("account") or "unknown", ""), "account_state": row.get("account"),
            "seen": row.get("last_seen"), "effort": bool(row.get("effort")),
            "notes": [note_dict(s) for s in here],
            "conflicts": [c for c in conflicts if c["used"] in shown or c["not_used"] in shown]}
