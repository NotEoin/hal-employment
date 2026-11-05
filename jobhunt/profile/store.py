"""The profile store: facts about the person, where each came from, and what they changed.

The ingest calls `add_found(con, kind, fields, evidence)` for each fact it reads in a document; the Your record
page reads items with `items` and `item`, and changes them with `edit`, `confirm`, `hide`, `add_by_hand` and
`delete`. Every write ends with `conflicts.refresh`, so the list of what does not add up always matches the
facts.

Design decisions, each with its reason:

- An item's state is `hal` (found, not checked), `confirmed`, `edited` or `hidden`. Only a `hal` item's fields
  are worked out again from its sources when a new one arrives; a confirmed or edited item keeps the
  person's values, so a later ingest never overwrites what they checked or typed. New sources still attach
  to it, so "From:" stays complete.
- A hidden item still matches new facts, so reading the same CV again never brings back what the person hid.
- Fields are checked against the kind's list (`KINDS`): an unknown kind or field raises `ValueError` naming
  it, so an ingest that misreads a document fails loudly instead of storing a field nothing shows.
- Only items the person added by hand can be deleted; a found item is hidden instead, because deleting it
  would let the next ingest add it again.
- The store does not publish events: an ingest adds hundreds of facts at once. The routes publish `profile`
  after each change the person makes.
"""
from __future__ import annotations

import json
import re
import sqlite3

from jobhunt import db
from jobhunt.profile import conflicts, merge

TEXT, DATE, FLAG, LIST, MODULES = "text", "date", "bool", "list", "modules"
# Each kind's fields and their types. `period` is also accepted for jobs and education, and split into
# start, end and current.
KINDS: dict[str, dict[str, str]] = {
    "job": {"title": TEXT, "employer": TEXT, "location": TEXT, "start": DATE, "end": DATE, "current": FLAG,
            "hours": TEXT, "duties": LIST, "achievements": LIST},
    "education": {"qualification": TEXT, "subject": TEXT, "institution": TEXT, "start": DATE, "end": DATE,
                  "current": FLAG, "grade": TEXT, "modules": MODULES},
    "skill": {"name": TEXT, "level": TEXT, "category": TEXT},
    "project": {"name": TEXT, "summary": TEXT, "role": TEXT, "tools": LIST, "link": TEXT, "period": TEXT},
    "achievement": {"title": TEXT, "date": DATE, "detail": TEXT, "issuer": TEXT},
    "certificate": {"name": TEXT, "issuer": TEXT, "date": DATE, "expires": DATE, "grade": TEXT},
    "basic": {"key": TEXT, "value": TEXT},
    "link": {"label": TEXT, "url": TEXT},
}
# A fact of each kind needs at least one of these, or it says nothing that can be shown.
NAMED_BY = {"job": ("title", "employer"), "education": ("qualification", "institution", "subject"),
            "skill": ("name",), "project": ("name",), "achievement": ("title",), "certificate": ("name",),
            "basic": ("key",), "link": ("url",)}
STATES = ("hal", "confirmed", "edited", "hidden")
STATE_WORDS = {"hal": "Found by Hal", "confirmed": "Checked by you", "edited": "Edited by you",
               "hidden": "Hidden"}
HOURS = {"full time": "full_time", "fulltime": "full_time", "full_time": "full_time",
         "part time": "part_time", "parttime": "part_time", "part_time": "part_time"}
EVIDENCE_KEYS = {"source_rel", "quote", "field", "doc_type", "verified"}
QUOTE_MAX = 600            # a quote is a sentence or two; a longer one is cut, never refused


def _text(value, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f"{field} should be text, not {type(value).__name__}.")
    return " ".join(value.split())


def _list(value, field: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field} should be a list.")
    return [t for t in (_text(v, field) for v in value) if t]


def _modules(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("modules should be a list of {name, mark}.")
    out = []
    for mod in value:
        if isinstance(mod, str):
            mod = {"name": mod}
        if not isinstance(mod, dict) or set(mod) - {"name", "mark"}:
            raise ValueError("Each module is {name, mark}.")
        name = _text(mod.get("name") or "", "module name")
        if name:
            out.append({"name": name, "mark": _text(mod.get("mark") or "", "module mark")})
    return out


def clean(kind: str, fields: dict) -> dict:
    """`fields` checked against `kind` and put in stored form: dates as YYYY-MM, empty values left out.

    Raises ValueError for an unknown kind or field, or a value of the wrong type.
    """
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a kind of profile item; use one of {', '.join(KINDS)}.")
    if not isinstance(fields, dict):
        raise ValueError("The fields should be an object.")
    spec = KINDS[kind]
    raw = dict(fields)
    if "period" in raw and kind in ("job", "education"):
        start, end, current = merge.parse_period(_text(raw.pop("period") or "", "period"))
        raw.setdefault("start", start)
        raw.setdefault("end", end)
        raw.setdefault("current", current)
    unknown = sorted(set(raw) - set(spec))
    if unknown:
        raise ValueError(f"A {kind} has no field {', '.join(unknown)}; its fields are {', '.join(spec)}.")
    out: dict = {}
    for field, value in raw.items():
        if value is None:
            continue
        kind_of = spec[field]
        if kind_of == FLAG:
            if not isinstance(value, bool):
                raise ValueError(f"{field} should be true or false.")
            if value:
                out[field] = True
            continue
        if kind_of == LIST:
            value = _list(value, field)
        elif kind_of == MODULES:
            value = _modules(value)
        elif kind_of == DATE:
            text = _text(value, field)
            if merge.is_now(text) and field == "end" and "current" in spec:
                out["current"] = True
                continue
            value = merge.clean_date(text, "end" if field in ("end", "expires") else "start")
        else:
            value = _text(value, field)
            if field == "hours":
                value = HOURS.get(value.lower().replace("-", " "), value)
        if value not in ("", []):
            out[field] = value
    if out.get("current"):
        out.pop("end", None)
    return out


# A module, project or dissertation the model filed under education is not a qualification (2026-11-05): it
# would show as a second degree. Projects are found as projects anyway.
NOT_QUALIFICATION = re.compile(r"\b(?:project|dissertation|module|coursework|assignment|practical|lecture|"
                               r"student)\b", re.I)


def _named(kind: str, fields: dict) -> None:
    if not any(fields.get(f) for f in NAMED_BY[kind]):
        raise ValueError(f"A {kind} needs {' or '.join(NAMED_BY[kind])}.")
    qualification = str(fields.get("qualification") or "")
    if kind == "education" and not merge.level(qualification) and NOT_QUALIFICATION.search(qualification):
        raise ValueError(f"{qualification} is a piece of work or a module, not a qualification.")
    if kind == "basic" and not fields.get("value"):
        raise ValueError("A basic needs a value.")


def _evidence(evidence: dict) -> dict:
    if not isinstance(evidence, dict) or not str(evidence.get("source_rel") or "").strip():
        raise ValueError("Evidence needs source_rel, the file inside the record the fact came from.")
    unknown = sorted(set(evidence) - EVIDENCE_KEYS)
    if unknown:
        raise ValueError(f"Evidence has no field {', '.join(unknown)}; "
                         f"its fields are {', '.join(sorted(EVIDENCE_KEYS))}.")
    return {"source_rel": str(evidence["source_rel"]).strip(),
            "quote": " ".join(str(evidence.get("quote") or "").split())[:QUOTE_MAX],
            "field": str(evidence.get("field") or "").strip(),
            "doc_type": merge.doc_group(str(evidence.get("doc_type") or "")),
            "verified": bool(evidence.get("verified", True))}


def _row_fields(row: sqlite3.Row) -> dict:
    return json.loads(row["fields"])


def _match(con: sqlite3.Connection, kind: str, fields: dict) -> sqlite3.Row | None:
    """The first item `fields` belongs to, by its own fields or by what any of its sources said.

    The sources count too, so a fact the person corrected (a misspelt employer) still matches when the same
    file is read again, instead of coming back as a second item.
    """
    said: dict[int, list[dict]] = {}
    for e in con.execute("SELECT e.item_id, e.said FROM profile_evidence e JOIN profile_items i "
                         "ON i.id = e.item_id WHERE i.kind = ? ORDER BY e.id", (kind,)):
        said.setdefault(e["item_id"], []).append(json.loads(e["said"]))
    for row in con.execute("SELECT * FROM profile_items WHERE kind = ? ORDER BY id", (kind,)):
        if any(merge.same_item(kind, known, fields) for known in [_row_fields(row), *said.get(row["id"], [])]):
            return row
    return None


def refold(con: sqlite3.Connection, item_id: int) -> None:
    """Works out a found item's fields again from its sources; an item the person checked is left alone."""
    row = con.execute("SELECT state FROM profile_items WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["state"] != "hal":
        return
    sources = [(merge.rank(e["doc_type"]), json.loads(e["said"]))
               for e in con.execute("SELECT doc_type, said FROM profile_evidence WHERE item_id = ? ORDER BY id",
                                    (item_id,))]
    if sources:
        con.execute("UPDATE profile_items SET fields = ?, updated = ? WHERE id = ?",
                    (json.dumps(merge.fold(sources)), db.now(), item_id))


def add_found(con: sqlite3.Connection, kind: str, fields: dict, evidence: dict, *, refresh: bool = True) -> int:
    """Adds one fact the ingest found, merged into the item it belongs to; returns that item's id.

    `evidence` is {source_rel, quote, field, doc_type}: the file inside the record, the verbatim quote, the
    field the quote supports and the document type (transcript, certificate, cv, letter, notes, ...).
    Raises ValueError for an unknown kind or field, a fact with nothing to name it, or evidence with no file.
    `refresh=False` leaves the conflicts to the caller, who refreshes them once after many facts.
    """
    said = clean(kind, fields)
    _named(kind, said)
    ev = _evidence(evidence)
    now = db.now()
    with con:
        row = _match(con, kind, said)
        if row is None:
            item_id = con.execute("INSERT INTO profile_items (kind, fields, state, origin, created, updated) "
                                  "VALUES (?, ?, 'hal', 'found', ?, ?)",
                                  (kind, json.dumps(said), now, now)).lastrowid
        else:
            item_id = row["id"]
        con.execute("INSERT INTO profile_evidence (item_id, source_rel, quote, field, doc_type, said, verified, "
                    "found_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (item_id, ev["source_rel"], ev["quote"], ev["field"], ev["doc_type"], json.dumps(said),
                     int(ev["verified"]), now))
        refold(con, item_id)
        if refresh:
            conflicts.refresh(con)
    return item_id


def rebuild_found(con: sqlite3.Connection) -> int:
    """Merges every item Hal found and nobody checked again from its sources; returns how many there are now.

    For after the merge rules change: an item the person confirmed, edited, hid or added is kept as it is,
    and new facts still merge into it. Conflicts are worked out again; ones the person settled about rebuilt
    items are found afresh.
    """
    rows = con.execute("""SELECT i.kind, e.source_rel, e.quote, e.field, e.doc_type, e.said, e.verified
                          FROM profile_evidence e JOIN profile_items i ON i.id = e.item_id
                          WHERE i.state = 'hal' AND i.origin = 'found' ORDER BY e.id""").fetchall()
    with con:
        con.execute("DELETE FROM profile_items WHERE state = 'hal' AND origin = 'found'")
    for r in rows:
        try:
            add_found(con, r["kind"], json.loads(r["said"]),
                      {"source_rel": r["source_rel"], "quote": r["quote"], "field": r["field"],
                       "doc_type": r["doc_type"], "verified": bool(r["verified"])}, refresh=False)
        except ValueError:  # a fact the rules now refuse, such as a module filed as a qualification, stays out
            continue
    with con:
        conflicts.refresh(con)
    return con.execute("SELECT COUNT(*) FROM profile_items").fetchone()[0]


def _row(con: sqlite3.Connection, item_id) -> sqlite3.Row:
    try:
        key = int(item_id)
    except (TypeError, ValueError):
        raise LookupError("That item is not in your profile.") from None
    row = con.execute("SELECT * FROM profile_items WHERE id = ?", (key,)).fetchone()
    if row is None:
        raise LookupError("That item is not in your profile.")
    return row


def _set_state(con: sqlite3.Connection, item_id: int, state: str, fields: dict | None = None) -> None:
    if fields is None:
        con.execute("UPDATE profile_items SET state = ?, updated = ? WHERE id = ?", (state, db.now(), item_id))
    else:
        con.execute("UPDATE profile_items SET state = ?, fields = ?, updated = ? WHERE id = ?",
                    (state, json.dumps(fields), db.now(), item_id))


def edit(con: sqlite3.Connection, item_id, fields: dict) -> dict:
    """Changes some of an item's fields (a field given as "" or [] is cleared); the item becomes edited.

    From now on no ingest changes it. Returns the item.
    """
    row = _row(con, item_id)
    kind = row["kind"]
    cleared = {f for f, v in (fields or {}).items() if v in ("", [], False)}
    merged = {k: v for k, v in _row_fields(row).items() if k not in cleared}
    merged.update(clean(kind, fields))
    if merged.get("current"):
        merged.pop("end", None)
    _named(kind, merged)
    with con:
        _set_state(con, row["id"], "edited", merged)
        conflicts.refresh(con)
    return item(con, row["id"])


def confirm(con: sqlite3.Connection, item_id) -> dict:
    """Marks an item as checked by the person (a hidden one comes back); no ingest changes it after this."""
    row = _row(con, item_id)
    with con:
        _set_state(con, row["id"], "confirmed")
        conflicts.refresh(con)
    return item(con, row["id"])


def hide(con: sqlite3.Connection, item_id) -> dict:
    """Hides an item: it stays out of every document and every check, and an ingest never brings it back."""
    row = _row(con, item_id)
    with con:
        _set_state(con, row["id"], "hidden")
        conflicts.refresh(con)
    return item(con, row["id"])


def add_by_hand(con: sqlite3.Connection, kind: str, fields: dict) -> dict:
    """Adds an item the person typed in; it starts as edited, so their words are used as they are."""
    clean_fields = clean(kind, fields)
    _named(kind, clean_fields)
    now = db.now()
    with con:
        item_id = con.execute("INSERT INTO profile_items (kind, fields, state, origin, created, updated) "
                              "VALUES (?, ?, 'edited', 'you', ?, ?)",
                              (kind, json.dumps(clean_fields), now, now)).lastrowid
        conflicts.refresh(con)
    return item(con, item_id)


def delete(con: sqlite3.Connection, item_id) -> None:
    """Deletes an item the person added by hand; a found item raises PermissionError (hide it instead)."""
    row = _row(con, item_id)
    if row["origin"] != "you":
        raise PermissionError("Hal found this in your files, so it would come back. Hide it instead.")
    with con:
        con.execute("DELETE FROM profile_items WHERE id = ?", (row["id"],))
        conflicts.refresh(con)


def evidence_of(con: sqlite3.Connection, item_ids: list[int]) -> dict[int, list[dict]]:
    """Each item's sources, oldest first: {item_id: [{id, source_rel, quote, field, doc_type, verified,
    found_at}]}."""
    out: dict[int, list[dict]] = {i: [] for i in item_ids}
    if not item_ids:
        return out
    marks = ",".join("?" * len(item_ids))
    for e in con.execute(f"SELECT id, item_id, source_rel, quote, field, doc_type, verified, found_at "
                         f"FROM profile_evidence "
                         f"WHERE item_id IN ({marks}) ORDER BY id", item_ids):
        out[e["item_id"]].append({"id": e["id"], "source_rel": e["source_rel"], "quote": e["quote"],
                                  "field": e["field"], "doc_type": e["doc_type"], "verified": bool(e["verified"]),
                                  "found_at": e["found_at"]})
    return out


def _shape(row: sqlite3.Row, evidence: list[dict], open_conflicts: list[dict]) -> dict:
    fields = _row_fields(row)
    return {"id": row["id"], "kind": row["kind"], "state": row["state"], "state_words": STATE_WORDS[row["state"]],
            "origin": row["origin"], "fields": fields, "title": title(row["kind"], fields),
            "sub": subtitle(row["kind"], fields), "when": when(fields), "evidence": evidence,
            "conflicts": [c for c in open_conflicts if row["id"] in c["item_ids"]],
            "can_delete": row["origin"] == "you", "created": row["created"], "updated": row["updated"]}


def item(con: sqlite3.Connection, item_id) -> dict:
    """One item with its sources and open conflicts; LookupError when there is none."""
    row = _row(con, item_id)
    return _shape(row, evidence_of(con, [row["id"]])[row["id"]], conflicts.listing(con))


def _order(kind: str, fields: dict, item_id: int):
    if kind in ("job", "education"):
        span = merge.span(fields)
        return (0 if fields.get("current") else 1, -(span[1] if span else -1), -(span[0] if span else -1), item_id)
    if kind == "skill":
        return (merge.skill_key(fields.get("name", "")), item_id)
    return (item_id,)


def items(con: sqlite3.Connection, kind: str, *, hidden: bool = True) -> list[dict]:
    """Every item of `kind`, newest first for jobs and education, by name for skills; hidden ones too unless
    `hidden` is False."""
    if kind not in KINDS:
        raise LookupError(f"{kind!r} is not a kind of profile item.")
    rows = [r for r in con.execute("SELECT * FROM profile_items WHERE kind = ?", (kind,))
            if hidden or r["state"] != "hidden"]
    rows.sort(key=lambda r: _order(kind, _row_fields(r), r["id"]))
    found = evidence_of(con, [r["id"] for r in rows])
    open_conflicts = conflicts.listing(con)
    return [_shape(r, found[r["id"]], open_conflicts) for r in rows]


def counts(con: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """For each kind, how many items there are in all and in each state."""
    out = {k: {"total": 0, **dict.fromkeys(STATES, 0)} for k in KINDS}
    for r in con.execute("SELECT kind, state, count(*) AS n FROM profile_items GROUP BY kind, state"):
        if r["kind"] in out and r["state"] in out[r["kind"]]:
            out[r["kind"]][r["state"]] = r["n"]
            out[r["kind"]]["total"] += r["n"]
    return out


# Words for people ----------------------------------------------------------------------------------------

def title(kind: str, fields: dict) -> str:
    """An item's main line: a job's title, a qualification, a skill's name."""
    if kind == "job":
        return fields.get("title") or fields.get("employer") or ""
    if kind == "education":
        return " ".join(p for p in (fields.get("qualification"), fields.get("subject")) if p) or \
            fields.get("institution", "")
    if kind == "basic":
        return fields.get("key", "")
    if kind == "link":
        return fields.get("label") or fields.get("url", "")
    return fields.get("title" if kind == "achievement" else "name", "")


def subtitle(kind: str, fields: dict) -> str:
    """An item's second line: where (employer, institution, issuer) or what (a skill's level)."""
    if kind == "job":
        return ", ".join(p for p in (fields.get("employer") if fields.get("title") else "", fields.get("location"))
                         if p)
    if kind == "education":
        return ", ".join(p for p in (fields.get("institution") if fields.get("qualification") or
                                     fields.get("subject") else "", fields.get("grade")) if p)
    if kind == "skill":
        return ", ".join(p for p in (fields.get("level"), fields.get("category")) if p)
    if kind == "basic":
        return fields.get("value", "")
    if kind == "link":
        return fields.get("url", "") if fields.get("label") else ""
    if kind == "project":
        return fields.get("role", "")
    return fields.get("issuer", "")


def when(fields: dict) -> str:
    """An item's dates in words: "September 2023 to now", "May 2024", or ""."""
    start = merge.date_words(fields.get("start", ""))
    end = "now" if fields.get("current") else merge.date_words(fields.get("end", ""))
    if start and end:
        return f"{start} to {end}"
    return start or (f"Until {end}" if end and end != "now" else "") or merge.date_words(
        fields.get("date", "")) or fields.get("period", "")
