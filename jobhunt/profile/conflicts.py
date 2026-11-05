"""What does not add up in the profile: the Inconsistencies list.

`detect(con)` finds, from the items and their sources, four kinds of conflict:

- `field`: two sources give one item different values for a field (a start of "May 2024" in one CV and
  "June 2024" in another, a grade on the transcript that is not the one on the CV). Each value is listed
  with the files that gave it; the item shows the best-ranked source's value (see `merge.RANKS`).
- `dates`: an item ends before it starts.
- `overlap`: two jobs that are not marked part-time share more than a month.
- `gap`: more than `GAP_MONTHS` months between one job or course ending and the next starting.

`refresh(con)` keeps the table in step: a conflict found again keeps its row (the `key` is its
fingerprint), one no longer found is removed while open, and a resolved one stays resolved. Edited items
have no field conflicts, because the person's values win; hidden items take part in none.
"""
from __future__ import annotations

import json
import sqlite3

from jobhunt import db
from jobhunt.profile import merge

GAP_MONTHS = 6             # employers ask about a gap longer than six months
OVERLAP_MIN_MONTHS = 2     # one shared month is a job change, not two jobs at once
CHECKED_FIELDS = {          # the fields whose disagreement is worth the person's time
    "job": ("title", "employer", "start", "end", "location"),
    "education": ("qualification", "subject", "institution", "start", "end", "grade"),
    "certificate": ("issuer", "date", "grade"),
    "achievement": ("date",),
    "basic": ("value",),
    "project": ("role",),
}
FIELD_WORDS = {"start": "start date", "end": "end date", "date": "date", "value": "value"}
GROUP_WORDS = {"transcript": "transcript", "certificate": "certificate", "cv": "CV", "letter": "letter",
               "notes": "notes"}
CHOICES = {"field": ["value", "keep_all"], "dates": ["value", "keep_all"], "overlap": ["keep_item", "keep_all"],
           "gap": ["keep_all"]}
TIMELINE = ("job", "education")
DATE_FIELDS = ("start", "end", "date", "expires")


def _words(field: str, value: str) -> str:
    return merge.date_words(value) if field in DATE_FIELDS else str(value)


def _source_words(doc_type: str, source_rel: str) -> str:
    group = GROUP_WORDS.get(doc_type)
    return f"your {group} {source_rel}" if group else source_rel


def _items(con: sqlite3.Connection) -> list[dict]:
    return [{"id": r["id"], "kind": r["kind"], "state": r["state"], "fields": json.loads(r["fields"])}
            for r in con.execute("SELECT id, kind, state, fields FROM profile_items WHERE state != 'hidden' "
                                 "ORDER BY id")]


def _field_conflicts(con: sqlite3.Connection, item: dict) -> list[dict]:
    if item["state"] == "edited" or item["kind"] not in CHECKED_FIELDS:
        return []
    sources = con.execute("SELECT source_rel, quote, doc_type, said FROM profile_evidence WHERE item_id = ? "
                          "ORDER BY id", (item["id"],)).fetchall()
    out = []
    for field in CHECKED_FIELDS[item["kind"]]:
        groups: dict[str, dict] = {}
        for s in sources:
            value = json.loads(s["said"]).get(field)
            if value in (None, ""):
                continue
            key = merge.value_key(field, value)
            group = groups.setdefault(key, {"value": value, "words": _words(field, value), "sources": [],
                                            "rank": merge.rank(s["doc_type"])})
            group["rank"] = max(group["rank"], merge.rank(s["doc_type"]))
            if s["source_rel"] not in [x["source_rel"] for x in group["sources"]]:
                group["sources"].append({"source_rel": s["source_rel"], "doc_type": s["doc_type"],
                                         "quote": s["quote"]})
        if field in DATE_FIELDS:
            _fold_years(groups)
        if len(groups) < 2:
            continue
        values = sorted(groups.values(), key=lambda g: -g["rank"])
        out.append({"key": f"field:{item['id']}:{field}:" + "|".join(sorted(groups)), "kind": "field",
                    "item_ids": [item["id"]], "field": field,
                    "values": [{k: g[k] for k in ("value", "words", "sources")} for g in values],
                    "why": _field_why(item, field, values)})
    return out


def _fold_years(groups: dict[str, dict]) -> None:
    """A bare year agrees with a month in that year ("2024" and "May 2024"), so the two are one value."""
    for key in [k for k in groups if len(k) == len("2024") and k.isdigit()]:
        month = next((k for k in groups if k.startswith(f"{key}-")), None)
        if month is not None:
            year = groups.pop(key)
            groups[month]["sources"] += [x for x in year["sources"] if x not in groups[month]["sources"]]
            groups[month]["rank"] = max(groups[month]["rank"], year["rank"])


def _field_why(item: dict, field: str, values: list[dict]) -> str:
    said = "; ".join(f"{g['words']} in " + " and ".join(_source_words(s["doc_type"], s["source_rel"])
                                                        for s in g["sources"]) for g in values)
    label = FIELD_WORDS.get(field, field)
    name = _name(item)
    best = values[0]
    if field == "grade" and any(s["doc_type"] == "transcript" for s in best["sources"]):
        return (f"The grade for {name} on your transcript is not the one elsewhere: {said}. Hal uses the "
                "transcript's until you choose.")
    if best["rank"] > values[1]["rank"]:
        return f"Your files give {name} different {label}s: {said}. Hal uses {best['words']} until you choose."
    return f"Your files give {name} different {label}s: {said}. Choose the right one."


def _name(item: dict) -> str:
    f = item["fields"]
    if item["kind"] == "job":
        return " at ".join(p for p in (f.get("title"), f.get("employer")) if p) or "a job"
    if item["kind"] == "education":
        return " at ".join(p for p in (" ".join(x for x in (f.get("qualification"), f.get("subject")) if x),
                                       f.get("institution")) if p) or "a course"
    return f.get("name") or f.get("title") or f.get("key") or f"a {item['kind']}"


def _dates_conflicts(item: dict) -> list[dict]:
    f = item["fields"]
    start, end = merge.month_index(f.get("start", ""), "start"), merge.month_index(f.get("end", ""), "end")
    if start is None or end is None or f.get("current") or end >= start:
        return []
    why = (f"{_name(item)} ends ({merge.date_words(f['end'])}) before it starts ({merge.date_words(f['start'])}). "
           "One of the dates is wrong.")
    return [{"key": f"dates:{item['id']}:{f['start']}:{f['end']}", "kind": "dates", "item_ids": [item["id"]],
             "field": "end", "values": [{"value": f["end"], "words": merge.date_words(f["end"]), "sources": []}],
             "why": why}]


def _overlaps(jobs: list[dict]) -> list[dict]:
    full = [j for j in jobs if j["fields"].get("hours") != "part_time" and merge.span(j["fields"])]
    out = []
    for i, a in enumerate(full):
        for b in full[i + 1:]:
            shared = merge.shared_months(a["fields"], b["fields"])
            if shared < OVERLAP_MIN_MONTHS:
                continue
            why = (f"{_name(a)} and {_name(b)} overlap by {shared} months, and neither is marked part-time. "
                   "If one was part-time, say so; if they are the same job, keep one.")
            out.append({"key": f"overlap:{a['id']}:{b['id']}", "kind": "overlap", "item_ids": [a["id"], b["id"]],
                        "field": "", "values": [], "why": why})
    return out


def _ends_known(fields: dict) -> bool:
    return bool(fields.get("current")) or merge.month_index(fields.get("end", ""), "end") is not None


def _gaps(timeline: list[dict]) -> list[dict]:
    """Gaps between one item ending and the next starting; never after an item whose end is not known."""
    dated = sorted(((span, it) for it in timeline if (span := merge.span(it["fields"]))),
                   key=lambda p: (p[0][0], p[0][1], p[1]["id"]))
    out = []
    covered_to, last = None, None
    for (start, end), it in dated:
        if covered_to is not None and _ends_known(last["fields"]) and start - covered_to - 1 > GAP_MONTHS:
            months = start - covered_to - 1
            why = (f"There are {months} months between {_name(last)} ending and {_name(it)} starting. "
                   "Employers often ask about a gap of more than six months, so the CV or letter may need a line "
                   "about it.")
            out.append({"key": f"gap:{covered_to}:{start}", "kind": "gap", "item_ids": [last["id"], it["id"]],
                        "field": "", "values": [], "why": why})
        if covered_to is None or end > covered_to:
            covered_to, last = end, it
    return out


def detect(con: sqlite3.Connection) -> list[dict]:
    """Every conflict in the profile now, each {key, kind, item_ids, field, values, why}."""
    found = _items(con)
    out: list[dict] = []
    for it in found:
        out += _field_conflicts(con, it)
        if it["kind"] in TIMELINE:
            out += _dates_conflicts(it)
    out += _overlaps([it for it in found if it["kind"] == "job"])
    out += _gaps([it for it in found if it["kind"] in TIMELINE])
    return out


def refresh(con: sqlite3.Connection) -> None:
    """Brings the conflicts table in step with the items; the caller commits."""
    now = db.now()
    found = {c["key"]: c for c in detect(con)}
    known = {r["key"]: r for r in con.execute("SELECT id, key, state FROM profile_conflicts")}
    for key, c in found.items():
        args = (c["kind"], json.dumps(c["item_ids"]), c["field"], json.dumps(c["values"]), c["why"], now)
        if key in known:
            con.execute("UPDATE profile_conflicts SET kind = ?, item_ids = ?, field = ?, values_ = ?, why = ?, "
                        "updated = ? WHERE key = ?", (*args, key))
        else:
            con.execute("INSERT INTO profile_conflicts (kind, item_ids, field, values_, why, updated, key, created) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (*args, key, now))
    gone = [r["id"] for key, r in known.items() if key not in found and r["state"] == "open"]
    con.executemany("DELETE FROM profile_conflicts WHERE id = ?", [(i,) for i in gone])


def _shape(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "item_ids": json.loads(row["item_ids"]), "field": row["field"],
            "values": json.loads(row["values_"]), "why": row["why"], "state": row["state"],
            "resolution": json.loads(row["resolution"]) if row["resolution"] else None,
            "choices": CHOICES.get(row["kind"], ["keep_all"])}


def listing(con: sqlite3.Connection, state: str = "open") -> list[dict]:
    """The conflicts in `state` (open or resolved), oldest first."""
    return [_shape(r) for r in con.execute("SELECT * FROM profile_conflicts WHERE state = ? ORDER BY id", (state,))]


def get(con: sqlite3.Connection, conflict_id) -> dict:
    """One conflict; LookupError when there is none."""
    try:
        key = int(conflict_id)
    except (TypeError, ValueError):
        raise LookupError("That conflict is not in your profile.") from None
    row = con.execute("SELECT * FROM profile_conflicts WHERE id = ?", (key,)).fetchone()
    if row is None:
        raise LookupError("That conflict is not in your profile.")
    return _shape(row)


def resolve(con: sqlite3.Connection, conflict_id, *, value=None, keep_item=None, keep_all: bool = False) -> dict:
    """Settles a conflict with the person's choice and returns it.

    - `value`: the right value of the conflict's field; the item is edited to it (so it is the person's).
    - `keep_item`: one of the conflict's items is the right one; the others are hidden.
    - `keep_all`: everything is right as it is (two jobs really overlapped, the gap is real).
    Raises ValueError when the choice does not fit the conflict.
    """
    from jobhunt.profile import store

    c = get(con, conflict_id)
    if value is not None and c["field"]:
        kind = con.execute("SELECT kind FROM profile_items WHERE id = ?", (c["item_ids"][0],)).fetchone()
        if kind is None:
            raise LookupError("The item this conflict was about is not in your profile any more.")
        store.clean(kind["kind"], {c["field"]: value})
    if c["state"] != "open":
        raise ValueError("That conflict is already settled.")
    if sum(x is not None and x is not False for x in (value, keep_item, keep_all or None)) != 1:
        raise ValueError("Choose one: the right value, the item to keep, or keep them all.")
    if value is not None:
        if not str(value).strip():
            raise ValueError("Give the right value.")
        if "value" not in c["choices"] or not c["field"]:
            raise ValueError("This conflict is not about one value; keep an item or keep them all.")
        resolution = {"value": value}
    elif keep_item is not None:
        if "keep_item" not in c["choices"] or keep_item not in c["item_ids"]:
            raise ValueError("Keep one of the items this conflict names.")
        resolution = {"keep_item": keep_item}
    else:
        resolution = {"keep_all": True}
    with con:
        con.execute("UPDATE profile_conflicts SET state = 'resolved', resolution = ?, updated = ? WHERE id = ?",
                    (json.dumps(resolution), db.now(), c["id"]))
    if value is not None:
        store.edit(con, c["item_ids"][0], {c["field"]: value})
    elif keep_item is not None:
        for other in c["item_ids"]:
            if other != keep_item:
                store.hide(con, other)
    return get(con, c["id"])
