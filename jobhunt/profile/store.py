"""The profile store: facts about the person, where each came from, and what they changed.

The ingest calls `add_found(con, kind, fields, evidence)` for each fact it reads in a document; the Your record
page reads items with `items` and `item`, and changes them with `edit`, `confirm`, `hide`, `confirm_many`,
`hide_many`, `add_by_hand` and `delete`. Every write ends with `conflicts.refresh`, so the list of what does
not add up always matches the facts.

Design decisions, each with its reason:

- An item's state is `hal` (found, not checked), `confirmed`, `edited` or `hidden`. Only a `hal` item's fields
  are worked out again from its sources when a new one arrives; a confirmed or edited item keeps the
  person's values, so a later ingest never overwrites what they checked or typed. New sources still attach
  to it, so "From:" stays complete.
- A note rewritten in place from a newer copy (`replace_source`) replaces that note's evidence. A confirmed
  item whose facts from it changed goes back to `hal` to be checked again: the person checked what the old
  copy said, not the new one. An edited item still keeps their values.
- A hidden item still matches new facts, so reading the same CV again, or another file that names the same
  fact, never brings back what the person hid.
- A card Hal found under the wrong name, such as a note's own name for the project it is part of, can be
  folded into the card it belongs to (`refile`, `fold_away`, migration 0027): its evidence moves there and
  remembers where it was (`moved_from`), and the card is hidden with `merged_into` and a few words on why
  (`merged_why`). A new fact that matches a folded card goes to the card it was folded into. Only cards
  nobody checked are folded. A duplicate merged whole (`merge_into`) can be taken out again (`unfold`): its
  evidence comes back and Hal never merges it on its own again (`APART`). Nothing is merged into a card
  that was merged away itself, so merges never chain. A tidy takes apart what older rules folded or merged
  (`unfold` with `apart=False`, and `take_back` for facts filed away from a card that stayed), and
  `rebuild_found` keeps where each moved fact came from, so all of it can be taken apart.
- The person may merge any two cards of one kind themselves (`same_as`, "Same as..." on the Check your
  record page), checked or not: one project was two cards, one with the repository's notes and one resting
  only on a list of projects, and hiding either lost its facts (2026-11-08). The card they keep keeps its
  name, as the facts moved to it are filed under its name, and the merged card's `merged_why` says the person
  did it and which state it had (`SAME_AS`), so taking it back out (`unfold`) gives that state back and
  never works out again what they checked or typed. Hal's own merges (`merge_into`) still never take a card
  the person checked.
- A project or job card can carry the person's star (`set_star`, migration 0028): a starred project is one to
  tell an employer about, a starred job one to lead with. Only the person sets it, so no read changes it.
- Evidence may name the source of the record it came from (`source_id`, with `place` and `line` inside it),
  for facts read from a section's sources (`jobhunt.sections`). Reading a source again replaces its facts in
  one transaction (`swap_source`), so a failed read leaves the old ones whole. A card Hal found that no
  source names any more is hidden, not deleted, remembering its state (`orphaned_from`): adding the source
  again brings it back as the person left it.
- A card's sources rank by document type (`merge.RANKS`), except a CV of the CVs section that is not the
  current one (`profile.cvs`), which ranks below the current CV and documents. When the current CV changes,
  `refold_sources` works out again every card the CVs give.
- A skill is weak (`is_weak`) when only one file names it and that file is not the person's own: a list of
  topics in course material is not a claim the person made, so the check page asks about it last.
- A skill is studied (`is_studied`) when every file naming it is course material: the check page shows it
  apart, under "Studied in your course", and no CV or letter uses it unless the person confirms it.
- A skill is usable (`is_usable`) when the person can stand behind it: on a CV, checked by them, or a tool
  of a project worth telling an employer about. The check page asks only about usable skills and folds the
  rest away as "Mentioned in your files".
- Fields are checked against the kind's list (`KINDS`): an unknown kind or field raises `ValueError` naming
  it, so an ingest that misreads a document fails loudly instead of storing a field nothing shows.
- Only items the person added by hand can be deleted; a found item is hidden instead, because deleting it
  would let the next ingest add it again.
- A new fact finds its item through `index`, which keeps every item and source in memory by match key, so
  an ingest of thousands of facts does not read the whole profile for each one. The ingest adds a
  document's facts with `refresh=False` and refreshes the conflicts once for the document.
- The store does not publish events: an ingest adds hundreds of facts at once. The routes publish `profile`
  after each change the person makes.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable

from jobhunt import db
from jobhunt.profile import conflicts, cvs, index, merge

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
EVIDENCE_KEYS = {"source_rel", "quote", "field", "doc_type", "verified", "role", "source_id", "place", "line"}
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
    if out.get("current") and merge.ended(out):
        del out["current"]          # a past end date is a fact; "current" beside it is a slip
    elif out.get("current"):
        out.pop("end", None)
    return out


# A module, project or dissertation the model filed under education is not a qualification (2026-11-05): it
# would show as a second degree. Projects are found as projects anyway.
BASIC_KEYS = ("name", "email", "phone", "location")
PATH_SLASHES = 2           # "London / Newcastle" is a place; "Computer Science BSc/Year 3/ideas" is a folder
SKILL_LIST = re.compile(r"\s*[,;]\s*")
NOT_QUALIFICATION = re.compile(r"\b(?:project|dissertation|module|coursework|assignment|practical|lecture|"
                               r"student)\b", re.I)
# Lecture notes gave a job {"title": "supervisor", "employer": "bar"} and a module code as a course
# (2026-11-05): a generic word with nothing else is no employer, and a code alone is a module.
GENERIC_EMPLOYERS = {"bar", "pub", "shop", "store", "cafe", "coffee shop", "restaurant", "hotel", "company",
                     "business", "firm", "employer", "workplace", "work", "job", "office", "factory",
                     "warehouse", "supermarket", "agency", "charity", "organisation", "organization"}
EMPLOYER_SKIP = {"the", "a", "an", "local", "my", "our", "small"}
JOB_NAMES = {"title", "employer"}
# A CV's "Projects: GitHub" line and a practical's "quadratic-solver.c" were filed as projects (2026-11-06).
SITE_NAMES = {"github", "gitlab", "bitbucket", "linkedin", "kaggle", "hugging face", "huggingface"}
CODE_FILE = re.compile(r"[\w.-]+\.(?:c|cc|cpp|h|hpp|py|java|js|ts|go|rs|lua|cs|rb|php|sh|kt|swift)", re.I)
# A training course from a certificate provider ("CITI Program Training") is found as a certificate from
# the certificate itself; filed as education it showed as a second course of study (2026-11-05).
TRAINING = re.compile(r"\b(?:training|course|certification|workshop|webinar|e-?learning|induction)\b", re.I)
PLACE_OF_STUDY = re.compile(r"\b(?:universit(?:y|ies)|college|school|academy|polytechnic|conservatoire|"
                            r"sixth form)\b", re.I)


def _generic_employer(name: str) -> bool:
    """True when an employer is only a kind of place ("the bar", "a local shop"), not a name."""
    words = [w for w in merge.text_key(name).split() if w not in EMPLOYER_SKIP]
    return " ".join(words) in GENERIC_EMPLOYERS


def _named(kind: str, fields: dict) -> None:
    if not any(fields.get(f) for f in NAMED_BY[kind]):
        raise ValueError(f"A {kind} needs {' or '.join(NAMED_BY[kind])}.")
    if kind == "job" and _generic_employer(str(fields.get("employer") or "")) and set(fields) <= JOB_NAMES:
        raise ValueError(f"{fields['employer']} is a kind of place, not an employer.")
    name = str(fields.get("name") or "").strip()
    if kind == "project" and merge.text_key(name) in SITE_NAMES:
        raise ValueError(f"{name} is a site, not a project.")
    if kind == "project" and CODE_FILE.fullmatch(name):
        raise ValueError(f"{name} is one file, not a project.")
    qualification = str(fields.get("qualification") or "")
    if kind == "education" and not merge.level(qualification) and NOT_QUALIFICATION.search(qualification):
        raise ValueError(f"{qualification} is a piece of work or a module, not a qualification.")
    subject = str(fields.get("subject") or "")
    if kind == "education" and not qualification and subject and \
            merge.text_key(subject).replace(" ", "") == merge.module_code(subject).lower():
        raise ValueError(f"{subject} is a module code, not a qualification.")
    course = f"{qualification} {subject}"
    if kind == "education" and not merge.level(course) and TRAINING.search(course) and \
            not PLACE_OF_STUDY.search(str(fields.get("institution") or "")):
        raise ValueError(f"{course.strip()} is a training course, found as a certificate instead.")
    if kind == "education" and not any(fields.get(f) for f in ("institution", "start", "end")):
        # an "MSc" in a dissertation's topic, from nowhere and no time, was the model's guess (2026-11-05)
        raise ValueError("A qualification needs where or when it was studied.")
    if kind == "basic" and fields.get("key") not in BASIC_KEYS:
        raise ValueError(f"A basic is one of {', '.join(BASIC_KEYS)}, not {fields.get('key')}.")
    if kind == "basic" and fields["key"] == "location" and str(fields.get("value") or "").count("/") >= PATH_SLASHES:
        raise ValueError("A folder path is not where the person lives.")
    if kind == "basic" and not fields.get("value"):
        raise ValueError("A basic needs a value.")


def _evidence(evidence: dict) -> dict:
    if not isinstance(evidence, dict) or not str(evidence.get("source_rel") or "").strip():
        raise ValueError("Evidence needs source_rel, the file inside the record the fact came from.")
    unknown = sorted(set(evidence) - EVIDENCE_KEYS)
    if unknown:
        raise ValueError(f"Evidence has no field {', '.join(unknown)}; "
                         f"its fields are {', '.join(sorted(EVIDENCE_KEYS))}.")
    line = evidence.get("line")
    if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
        raise ValueError("Evidence's line is a line number from 1.")
    source_id = evidence.get("source_id")
    if source_id is not None and (isinstance(source_id, bool) or not isinstance(source_id, int)):
        raise ValueError("Evidence's source_id is the id of a source of the record.")
    return {"source_rel": str(evidence["source_rel"]).strip(),
            "quote": " ".join(str(evidence.get("quote") or "").split())[:QUOTE_MAX],
            "field": str(evidence.get("field") or "").strip(),
            "doc_type": merge.doc_group(str(evidence.get("doc_type") or "")),
            "verified": bool(evidence.get("verified", True)), "role": str(evidence.get("role") or ""),
            "source_id": source_id, "place": str(evidence.get("place") or "").strip(), "line": line}


def _row_fields(row: sqlite3.Row) -> dict:
    return json.loads(row["fields"])


def refold(con: sqlite3.Connection, item_id: int) -> None:
    """Works out a found item's fields again from its sources; an item the person checked is left alone.

    Among sources of one rank its own come before those moved from cards merged into it, so a merged card
    keeps its own name unless a better source, such as the person's CV, names it otherwise.
    """
    row = con.execute("SELECT kind, state FROM profile_items WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["state"] != "hal":
        return
    older = cvs.older_sources(con)
    sources = [(cvs.rank(e["doc_type"], e["source_id"], older), json.loads(e["said"]))
               for e in con.execute("SELECT doc_type, said, source_id FROM profile_evidence WHERE item_id = ? "
                                    "ORDER BY moved_from IS NOT NULL, id", (item_id,))]
    if not sources:
        return
    fields = merge.fold(sources)
    if row["kind"] == "skill":
        ranked = sorted(enumerate(sources), key=lambda p: (-p[1][0], p[0]))
        fields["name"] = merge.skill_name([said.get("name", "") for _, (_, said) in ranked])
    con.execute("UPDATE profile_items SET fields = ?, updated = ? WHERE id = ?",
                (json.dumps(fields), db.now(), item_id))


def refold_sources(con: sqlite3.Connection, source_ids: list[int]) -> list[int]:
    """Works out again every card these sources of the record give, and the conflicts, in one transaction: after
    the current CV changed, an older CV's facts rank lower and the current one's higher (`profile.cvs`).
    Returns the ids of the cards."""
    if not source_ids:
        return []
    marks = ",".join("?" * len(source_ids))
    with con:
        ids = [r[0] for r in con.execute(f"SELECT DISTINCT item_id FROM profile_evidence WHERE source_id IN ({marks}) "
                                         "ORDER BY item_id", [int(i) for i in source_ids])]
        for item_id in ids:
            refold(con, item_id)
        conflicts.refresh(con)
    index.forget(con)       # the index keeps each card's fields, which may have changed
    return ids


def add_found(con: sqlite3.Connection, kind: str, fields: dict, evidence: dict, *, refresh: bool = True) -> int:
    """Adds one fact the ingest found, merged into the item it belongs to; returns that item's id.

    `evidence` is {source_rel, quote, field, doc_type}: the file inside the record, the verbatim quote, the
    field the quote supports and the document type (transcript, certificate, cv, letter, notes, ...).
    Raises ValueError for an unknown kind or field, a fact with nothing to name it, or evidence with no file.
    `refresh=False` leaves the conflicts to the caller, who refreshes them once after many facts. A skill
    named as a list ("Python, Java, C") is one skill each; the id of the last is returned.
    """
    names = SKILL_LIST.split(str(fields.get("name") or "")) if kind == "skill" else []
    if len([n for n in names if n]) > 1:
        ids = [add_found(con, kind, {**fields, "name": n}, evidence, refresh=refresh) for n in names if n]
        return ids[-1]
    said = clean(kind, fields)
    _named(kind, said)
    ev = _evidence(evidence)
    with con:
        item_id, fields_now = _put(con, kind, said, ev)
        if refresh:
            conflicts.refresh(con)
    index.added(con, item_id, kind, said, fields_now)
    return item_id


def _put(con: sqlite3.Connection, kind: str, said: dict, ev: dict) -> tuple[int, dict]:
    """Files one checked fact on its item (a new one when none matches) inside the caller's transaction; returns
    the item's id and its fields now."""
    now = db.now()
    # the item's own fields or any of its sources count, so a fact the person corrected (a misspelt
    # employer) still matches when the same file is read again, instead of coming back as a second item
    item_id = index.match(con, kind, said)
    moved_from = None
    if item_id is not None and (into := _folded_into(con, item_id)) is not None:
        item_id, moved_from = into, item_id       # a card folded into another: its new facts go there too
    if item_id is None:
        item_id = con.execute("INSERT INTO profile_items (kind, fields, state, origin, created, updated) "
                              "VALUES (?, ?, 'hal', 'found', ?, ?)",
                              (kind, json.dumps(said), now, now)).lastrowid
    con.execute("INSERT INTO profile_evidence (item_id, source_rel, quote, field, doc_type, said, verified, "
                "role, found_at, moved_from, source_id, place, line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, ev["source_rel"], ev["quote"], ev["field"], ev["doc_type"], json.dumps(said),
                 int(ev["verified"]), ev["role"], now, moved_from, ev["source_id"], ev["place"], ev["line"]))
    refold(con, item_id)
    return item_id, _row_fields(con.execute("SELECT fields FROM profile_items WHERE id = ?", (item_id,)).fetchone())


def _folded_into(con: sqlite3.Connection, item_id: int) -> int | None:
    """The card a hidden card was folded into (`fold_away`), when that card is still there; else None."""
    row = con.execute("""SELECT i.merged_into FROM profile_items i JOIN profile_items t ON t.id = i.merged_into
                         WHERE i.id = ? AND i.state = 'hidden'""", (item_id,)).fetchone()
    return row[0] if row else None


def refile(con: sqlite3.Connection, evidence_id: int, said: dict) -> int:
    """Files one piece of evidence again as the fact `said`, on the card that fact belongs to (a new one when
    none); returns that card's id.

    For facts an older read filed under the wrong name, such as a note's own name for the project it is part
    of (`ingest.projects`). The evidence remembers the card it was on (`moved_from`). Raises LookupError for
    an unknown piece of evidence and ValueError for a fact the store refuses, changing nothing.
    """
    row = con.execute("""SELECT e.item_id, e.moved_from, i.kind FROM profile_evidence e
                         JOIN profile_items i ON i.id = e.item_id WHERE e.id = ?""", (evidence_id,)).fetchone()
    if row is None:
        raise LookupError("That piece of evidence is not in your profile.")
    fact = clean(row["kind"], said)
    _named(row["kind"], fact)
    now = db.now()
    with con:
        target = index.match(con, row["kind"], fact)
        if target is not None:
            target = _folded_into(con, target) or target
        else:
            target = con.execute("INSERT INTO profile_items (kind, fields, state, origin, created, updated) "
                                 "VALUES (?, ?, 'hal', 'found', ?, ?)",
                                 (row["kind"], json.dumps(fact), now, now)).lastrowid
        if target != row["item_id"]:
            con.execute("UPDATE profile_evidence SET item_id = ?, said = ?, moved_from = ? WHERE id = ?",
                        (target, json.dumps(fact), row["moved_from"] or row["item_id"], evidence_id))
        else:
            con.execute("UPDATE profile_evidence SET said = ? WHERE id = ?", (json.dumps(fact), evidence_id))
        refold(con, row["item_id"])
        refold(con, target)
    return target


def fold_away(con: sqlite3.Connection, item_id: int, into: int, why: str) -> None:
    """Hides a card Hal found and nobody checked as part of the card `into`, saying `why` in a few words.

    Raises ValueError for a card the person checked, edited, hid or added: Hal never folds those on its own.
    """
    row = _row(con, item_id)
    if row["state"] != "hal" or row["origin"] != "found":
        raise ValueError("Hal only folds cards it found and nobody has checked.")
    _row(con, into)
    with con:
        con.execute("UPDATE profile_items SET state = 'hidden', merged_into = ?, merged_why = ?, updated = ? "
                    "WHERE id = ?", (into, why, db.now(), item_id))
        # a card folded into this one before goes with it, so every fold names a card that is shown
        con.execute("UPDATE profile_items SET merged_into = ? WHERE merged_into = ?", (into, item_id))


APART = "apart"            # `merged_why` of a card the person took back out of a merge (`unfold`)
# `merged_why` of a card the person merged into another (`same_as`), by the state it had then, followed by the
# kept card's name; `unfold` gives the card that state back.
SAME_AS = {"hal": "You said it is the same as ", "confirmed": "You checked it, then said it is the same as ",
           "edited": "You edited it, then said it is the same as ",
           "hidden": "You hid it, then said it is the same as "}


def merge_into(con: sqlite3.Connection, item_id: int, into: int, why: str) -> None:
    """Merges a card Hal found and nobody checked into the card `into`: its evidence moves there, remembering
    where it was, and it is hidden as folded, saying `why`. ValueError as `fold_away`, for a card the person
    took out of a merge before, and for a target that was itself merged away."""
    row = _row(con, item_id)
    if row["merged_why"] == APART:
        raise ValueError("You took this card out of a merge, so Hal leaves it on its own.")
    if _row(con, into)["merged_into"] is not None:   # chains made one card of unrelated work (2026-11-08)
        raise ValueError("That card was merged into another, so nothing is merged into it.")
    fold_away(con, item_id, into, why)
    with con:
        con.execute("UPDATE profile_evidence SET item_id = ?, moved_from = coalesce(moved_from, ?) "
                    "WHERE item_id = ?", (into, item_id, item_id))
        refold(con, into)
        conflicts.refresh(con)


def same_as(con: sqlite3.Connection, item_id: int, into: int) -> None:
    """Merges card `item_id` into card `into` because the person said they are one: its evidence moves there,
    remembering where it was and filed under the kept card's name, and it is hidden, saying the person did it
    (`SAME_AS`). Any state may go, as the person asks; a hidden card kept is shown again for them to check.

    LookupError for an unknown card; ValueError for the same card twice, two kinds, a card already merged into
    another, and a target that was merged away itself."""
    row, target = _row(con, item_id), _row(con, into)
    if row["id"] == target["id"]:
        raise ValueError("Pick another card: a card is always the same as itself.")
    if row["kind"] != target["kind"]:
        raise ValueError("Only two cards of the same kind can be one.")
    if row["merged_into"] is not None:
        raise ValueError("That card is already merged into another.")
    if target["merged_into"] is not None:
        raise ValueError("That card was merged into another, so nothing is merged into it.")
    kept = _row_fields(target)
    why = SAME_AS.get(row["state"], SAME_AS["hal"]) + title(target["kind"], kept)
    moved = [(e["id"], json.loads(e["said"])) for e in
             con.execute("SELECT id, said FROM profile_evidence WHERE item_id = ?", (row["id"],))]
    named = {f: kept[f] for f in NAMED_BY.get(target["kind"], ()) if kept.get(f)}
    now = db.now()
    with con:
        if target["state"] == "hidden":
            con.execute("UPDATE profile_items SET state = 'hal', updated = ? WHERE id = ?", (now, target["id"]))
        con.execute("UPDATE profile_items SET state = 'hidden', merged_into = ?, merged_why = ?, updated = ? "
                    "WHERE id = ?", (target["id"], why, now, row["id"]))
        con.execute("UPDATE profile_items SET merged_into = ? WHERE merged_into = ?", (target["id"], row["id"]))
        # filed under the kept card's name, so a better-ranked file of the merged card never renames it
        con.executemany("UPDATE profile_evidence SET item_id = ?, said = ?, moved_from = coalesce(moved_from, ?) "
                        "WHERE id = ?", [(target["id"], json.dumps({**said, **named}), row["id"], evidence_id)
                                         for evidence_id, said in moved])
        refold(con, target["id"])
        conflicts.refresh(con)


def unfold(con: sqlite3.Connection, item_id: int, *, apart: bool = True) -> None:
    """Takes a merged or folded card back out of the card it was put into: its evidence returns, and it is found
    by Hal again, or has the state it had when the person merged it (`SAME_AS`). Hal never merges it again on
    its own (`APART`) unless `apart` is False, as when a tidy takes apart what today's rules refuse. A fact
    filed again under the other card's name (`refile`, `same_as`) goes back saying what this card was.
    LookupError for an unknown card, ValueError for one that was not merged."""
    row = _row(con, item_id)
    if row["merged_into"] is None:
        raise ValueError("That card was not merged into another.")
    was = _row_fields(row)
    state = next((s for s, words in SAME_AS.items() if row["merged_why"].startswith(words)), "hal")
    with con:
        for e in con.execute("SELECT id, said FROM profile_evidence WHERE moved_from = ?", (item_id,)).fetchall():
            if not merge.same_item(row["kind"], json.loads(e["said"]), was):
                con.execute("UPDATE profile_evidence SET said = ? WHERE id = ?", (json.dumps(was), e["id"]))
        con.execute("UPDATE profile_evidence SET item_id = ?, moved_from = NULL WHERE moved_from = ?",
                    (item_id, item_id))
        con.execute("UPDATE profile_items SET state = ?, merged_into = NULL, merged_why = ?, updated = ? "
                    "WHERE id = ?", (state, APART if apart else "", db.now(), item_id))
        refold(con, item_id)
        refold(con, row["merged_into"])
        conflicts.refresh(con)


def take_back(con: sqlite3.Connection, item_id: int) -> int:
    """Brings back to a shown card the facts filed again on cards nobody checked (`refile`) while it stayed,
    each saying the card's name again; returns how many. For a tidy undoing what today's rules refuse."""
    row = _row(con, item_id)
    name = _row_fields(row).get("name")
    moved = con.execute("""SELECT e.id, e.item_id, e.said FROM profile_evidence e JOIN profile_items t
                           ON t.id = e.item_id WHERE e.moved_from = ? AND e.item_id != ? AND t.state = 'hal'""",
                        (item_id, item_id)).fetchall()
    with con:
        for e in moved:
            said = json.loads(e["said"])
            con.execute("UPDATE profile_evidence SET item_id = ?, moved_from = NULL, said = ? WHERE id = ?",
                        (item_id, json.dumps({**said, "name": name} if name else said), e["id"]))
        for target in {e["item_id"] for e in moved} | {item_id}:
            refold(con, target)
        conflicts.refresh(con)
    return len(moved)


def keep_apart(con: sqlite3.Connection, item_id: int) -> None:
    """Marks a shown card as one the person keeps apart, so Hal never merges it on its own."""
    with con:
        con.execute("UPDATE profile_items SET merged_why = ? WHERE id = ? AND merged_into IS NULL",
                    (APART, _row(con, item_id)["id"]))


def _said_by(con: sqlite3.Connection, source, column: str = "source_rel") -> dict[int, list[str]]:
    """What one file (or one source of the record, by `source_id`) said about each item, as sorted JSON texts,
    so before and after can be compared."""
    out: dict[int, list[str]] = {}
    where = {"source_rel": "source_rel = ?", "source_id": "source_id = ?"}[column]
    for e in con.execute(f"SELECT item_id, said FROM profile_evidence WHERE {where} ORDER BY id", (source,)):
        out.setdefault(e["item_id"], []).append(json.dumps(json.loads(e["said"]), sort_keys=True))
    return {i: sorted(s) for i, s in out.items()}


def replace_source(con: sqlite3.Connection, source_rel: str, add: Callable[[], object]) -> list[int]:
    """Replaces what one rewritten file said: its evidence is deleted, `add()` adds its facts again.

    For a note rewritten in place, such as a project's note from a newer copy. Each item that file spoke
    for is worked out again from its sources; an unchecked found item no source speaks for any more is
    deleted. An item the person confirmed whose fields from this file changed goes back to `hal`, so they check it
    again; an edited or hidden item keeps its state and values. Returns the ids sent back to be checked.
    """
    before = _said_by(con, source_rel)
    with con:
        con.execute("DELETE FROM profile_evidence WHERE source_rel = ?", (source_rel,))
    add()
    after = _said_by(con, source_rel)
    again = []
    with con:
        for item_id in sorted(set(before) | set(after)):
            row = con.execute("SELECT state, origin FROM profile_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                continue
            if row["state"] == "confirmed" and before.get(item_id) != after.get(item_id):
                _set_state(con, item_id, "hal")
                again.append(item_id)
            if row["state"] == "hal" and row["origin"] == "found" and not con.execute(
                    "SELECT 1 FROM profile_evidence WHERE item_id = ? LIMIT 1", (item_id,)).fetchone():
                con.execute("DELETE FROM profile_items WHERE id = ?", (item_id,))
                continue
            refold(con, item_id)
        conflicts.refresh(con)
    return again


def _checked_facts(facts: list[tuple[str, dict, dict]], source_id: int) -> tuple[list, list[str]]:
    """`facts` as (kind, fields, evidence) checked and in stored form, each skill of a list on its own, with the
    source's id on the evidence; and why each refused fact was left out."""
    kept, left_out = [], []
    for kind, fields, evidence in facts:
        names = [n for n in SKILL_LIST.split(str(fields.get("name") or "")) if n] if kind == "skill" else []
        for one in ([{**fields, "name": n} for n in names] if len(names) > 1 else [fields]):
            try:
                said = clean(kind, one)
                _named(kind, said)
                kept.append((kind, said, _evidence({**evidence, "source_id": source_id})))
            except ValueError as e:      # one fact a reader misread is left out, not the whole source
                left_out.append(str(e))
    return kept, left_out


def swap_source(con: sqlite3.Connection, source_id: int, facts: list[tuple[str, dict, dict]]) -> dict:
    """Replaces every fact one source of the record gave (`jobhunt.sections`) with `facts`, in one transaction.

    `facts` are (kind, fields, evidence) as for `add_found`; each evidence gets `source_id`. Returns {"added":
    facts filed, "left_out": [why each refused fact was left out], "hidden": [ids of cards Hal found that no
    source names any more, now hidden], "changed": whether what the source says changed}. A hidden card keeps
    the state it had (`orphaned_from`) and gets it back when a fact names it again. As in `replace_source`, a
    confirmed card whose facts from this source changed goes back to be checked. `facts` empty takes the source
    out of the profile.
    """
    kept, left_out = _checked_facts(facts, source_id)
    before = _said_by(con, source_id, "source_id")
    hidden, now = [], db.now()
    try:
        with con:
            con.execute("DELETE FROM profile_evidence WHERE source_id = ?", (source_id,))
            for kind, said, ev in kept:
                item_id, fields_now = _put(con, kind, said, ev)
                index.added(con, item_id, kind, said, fields_now)
            after = _said_by(con, source_id, "source_id")
            for item_id in sorted(set(before) | set(after)):
                if _settle(con, item_id, before.get(item_id) != after.get(item_id), now):
                    hidden.append(item_id)
            conflicts.refresh(con)
    except BaseException:
        index.forget(con)       # the index learnt facts the rollback took back
        raise
    return {"added": len(kept), "left_out": left_out, "hidden": hidden, "changed": before != after}


def _settle(con: sqlite3.Connection, item_id: int, changed: bool, now: str) -> bool:
    """After a source's facts were swapped: hides a found card no source names any more (True), brings back one
    hidden that way that a source names again, sends a confirmed card whose facts changed back to be checked,
    and works the card's fields out again."""
    r = con.execute("SELECT state, origin, orphaned_from FROM profile_items WHERE id = ?", (item_id,)).fetchone()
    if r is None:
        return False
    named = con.execute("SELECT 1 FROM profile_evidence WHERE item_id = ? LIMIT 1", (item_id,)).fetchone()
    if r["origin"] == "found" and not named:
        if r["state"] == "hidden":
            return False
        con.execute("UPDATE profile_items SET state = 'hidden', orphaned_from = ?, updated = ? WHERE id = ?",
                    (r["state"], now, item_id))
        return True
    if r["orphaned_from"]:
        con.execute("UPDATE profile_items SET state = ?, orphaned_from = '', updated = ? WHERE id = ?",
                    (r["orphaned_from"], now, item_id))
    elif r["state"] == "confirmed" and changed:
        _set_state(con, item_id, "hal")
    refold(con, item_id)
    return False


def rebuild_found(con: sqlite3.Connection) -> int:
    """Merges every item Hal found and nobody checked again from its sources; returns how many there are now.

    For after the merge rules change: an item the person confirmed, edited, hid or added is kept as it is,
    and new facts still merge into it. Conflicts are worked out again; ones the person settled about rebuilt
    items are found afresh. A card folded into a rebuilt one (`fold_away`) is folded into the card rebuilt in
    its place (`_fold_again`).
    """
    folded = {r["id"]: r for r in con.execute(
        """SELECT i.id, t.kind, t.fields FROM profile_items i JOIN profile_items t ON t.id = i.merged_into
           WHERE t.state = 'hal' AND t.origin = 'found'""")}
    rows = con.execute("""SELECT i.kind, e.source_rel, e.quote, e.field, e.doc_type, e.said, e.verified, e.role,
                          e.moved_from, e.source_id, e.place, e.line FROM profile_evidence e
                          JOIN profile_items i ON i.id = e.item_id
                          WHERE i.state = 'hal' AND i.origin = 'found' ORDER BY e.id""").fetchall()
    with con:
        con.execute("DELETE FROM profile_items WHERE state = 'hal' AND origin = 'found'")
        # until the cards they were folded into are rebuilt, no new fact is sent to a card that is gone
        con.executemany("UPDATE profile_items SET merged_into = NULL WHERE id = ?", [(i,) for i in folded])
    for r in rows:
        try:
            add_found(con, r["kind"], json.loads(r["said"]),
                      {"source_rel": r["source_rel"], "quote": r["quote"], "field": r["field"],
                       "doc_type": r["doc_type"], "verified": bool(r["verified"]), "role": r["role"],
                       "source_id": r["source_id"], "place": r["place"], "line": r["line"]},
                      refresh=False)
        except ValueError:  # a fact the rules now refuse, such as a module filed as a qualification, stays out
            continue
        if r["moved_from"] is not None:
            with con:
                con.execute("UPDATE profile_evidence SET moved_from = ? WHERE id = (SELECT max(id) "
                            "FROM profile_evidence) AND item_id != ?", (r["moved_from"], r["moved_from"]))
    with con:
        for item_id, r in folded.items():
            _fold_again(con, item_id, r["kind"], json.loads(r["fields"]))
        conflicts.refresh(con)
    return con.execute("SELECT COUNT(*) FROM profile_items").fetchone()[0]


def _fold_again(con: sqlite3.Connection, item_id: int, kind: str, was: dict) -> None:
    """Folds a card again into the shown card that is now what its old card (`was`) was, with its evidence and
    the evidence that came from it; with none, the card is shown again as found by Hal."""
    target = next((r["id"] for r in con.execute("SELECT id, fields FROM profile_items WHERE kind = ? AND id != ? "
                                                "AND state != 'hidden' ORDER BY id", (kind, item_id))
                   if merge.same_item(kind, json.loads(r["fields"]), was)), None)
    if target is None:
        con.execute("UPDATE profile_evidence SET item_id = ?, moved_from = NULL WHERE moved_from = ?",
                    (item_id, item_id))
        con.execute("UPDATE profile_items SET state = 'hal', merged_why = '' WHERE id = ?", (item_id,))
        refold(con, item_id)
        return
    # evidence moved to this card from another before keeps naming that one, so each can be taken back
    con.execute("UPDATE profile_evidence SET item_id = ?, moved_from = coalesce(moved_from, ?) WHERE item_id = ?",
                (target, item_id, item_id))
    con.execute("UPDATE profile_items SET merged_into = ? WHERE id = ?", (target, item_id))
    refold(con, target)


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
    # a state set by the person or a check replaces the one a card had when its last source went
    if fields is None:
        con.execute("UPDATE profile_items SET state = ?, orphaned_from = '', updated = ? WHERE id = ?",
                    (state, db.now(), item_id))
    else:
        con.execute("UPDATE profile_items SET state = ?, fields = ?, orphaned_from = '', updated = ? WHERE id = ?",
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


def _set_many(con: sqlite3.Connection, item_ids, state: str) -> list[int]:
    if not isinstance(item_ids, list) or not item_ids:
        raise ValueError("Send the ids of the items, as a list.")
    rows = [_row(con, i) for i in item_ids]          # every id is checked before anything changes
    with con:
        for row in rows:
            _set_state(con, row["id"], state)
        conflicts.refresh(con)
    return [row["id"] for row in rows]


def confirm_many(con: sqlite3.Connection, item_ids: list) -> list[int]:
    """Confirms several items at once, with one conflicts refresh; returns their ids.

    Raises ValueError for an empty or missing list and LookupError for an unknown id, changing nothing.
    """
    return _set_many(con, item_ids, "confirmed")


def hide_many(con: sqlite3.Connection, item_ids: list) -> list[int]:
    """Hides several items at once, with one conflicts refresh; returns their ids. Errors as `confirm_many`."""
    return _set_many(con, item_ids, "hidden")


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


# The kinds a star means something for: a showcase project, a job to lead with (sections plan, 2026-11-09).
STAR_KINDS = ("project", "job")
STAR_BY_YOU = "you"


def set_star(con: sqlite3.Connection, item_id, on: bool) -> dict:
    """Stars a project or job card for the person, or takes the star off; returns the item.

    A star is the person's choice and no read changes it. LookupError for an unknown card, ValueError for a
    kind that takes no star or `on` that is not true or false.
    """
    if not isinstance(on, bool):
        raise ValueError("Say whether the card is starred: true or false.")
    row = _row(con, item_id)
    if row["kind"] not in STAR_KINDS:
        raise ValueError("Only projects and jobs can be starred.")
    with con:
        con.execute("UPDATE profile_items SET star = ?, star_by = ?, updated = ? WHERE id = ?",
                    (int(on), STAR_BY_YOU, db.now(), row["id"]))
    return item(con, row["id"])


OWN_ROLE = "own"           # evidence `role`: "own" for the person's own document, "taught" for course material
TAUGHT_ROLE = "taught"
STRONG_FILES = 2           # two files naming a skill is a pattern, not one lecture's list of topics


def is_studied(item: dict) -> bool:
    """True for a found skill whose every source is course material (evidence `role` "taught").

    Such a skill is what the person studied, not what they showed they can do: 262 of a real profile's 504
    skills came only from lectures (2026-11-07). It is never used as a CV skill or in writing. A skill the
    person confirmed, edited or added is theirs, so it is never studied.
    """
    return item["kind"] == "skill" and item.get("origin") != "you" and item["state"] in ("hal", "hidden") and \
        bool(item["evidence"]) and all(e.get("role", "") == TAUGHT_ROLE for e in item["evidence"])


def is_usable(item: dict, tools: set[str]) -> bool:
    """True for a skill the person can stand behind: on one of their CVs, checked by them (confirmed, edited
    or added), or a tool of a project worth telling an employer about (`tools`, as `merge.skill_key` keys).

    A real read gave 2,368 skill cards, most of them words a research note mentioned (2026-11-08); a skill
    that is none of these is only mentioned in the person's files, and the check page folds it away.
    """
    if item["kind"] != "skill":
        return False
    if item.get("origin") == "you" or item["state"] in ("confirmed", "edited"):
        return True
    return any(merge.doc_group(e["doc_type"]) == "cv" for e in item["evidence"]) or \
        merge.skill_key(item["fields"].get("name", "")) in tools


def is_weak(item: dict) -> bool:
    """True for a found skill that fewer than `STRONG_FILES` files name and none of the person's own documents.

    `item` is a shaped item with its evidence (`item`, `items`). An evidence `role` of "" is unknown and does
    not count as the person's own. Every other kind, and an item added by hand, is never weak.
    """
    if item["kind"] != "skill" or item.get("origin") == "you":
        return False
    files = {e["source_rel"] for e in item["evidence"]}
    return len(files) < STRONG_FILES and not any(e.get("role", "") == OWN_ROLE for e in item["evidence"])


def evidence_of(con: sqlite3.Connection, item_ids: list[int]) -> dict[int, list[dict]]:
    """Each item's sources, oldest first: {item_id: [{id, source_rel, quote, field, doc_type, verified, role,
    found_at, source_id, place, line}]}."""
    out: dict[int, list[dict]] = {i: [] for i in item_ids}
    if not item_ids:
        return out
    marks = ",".join("?" * len(item_ids))
    for e in con.execute(f"SELECT id, item_id, source_rel, quote, field, doc_type, verified, role, found_at, "
                         f"source_id, place, line FROM profile_evidence "
                         f"WHERE item_id IN ({marks}) ORDER BY id", item_ids):
        out[e["item_id"]].append({"id": e["id"], "source_rel": e["source_rel"], "quote": e["quote"],
                                  "field": e["field"], "doc_type": e["doc_type"], "verified": bool(e["verified"]),
                                  "role": e["role"], "found_at": e["found_at"], "source_id": e["source_id"],
                                  "place": e["place"], "line": e["line"]})
    return out


def _shape(row: sqlite3.Row, evidence: list[dict], open_conflicts: list[dict]) -> dict:
    # tiers and contexts read the store's items, so they are imported when first needed
    from jobhunt.profile import contexts, tiers

    fields = _row_fields(row)
    out = {"id": row["id"], "kind": row["kind"], "state": row["state"], "state_words": STATE_WORDS[row["state"]],
           "origin": row["origin"], "fields": fields, "title": title(row["kind"], fields),
           "sub": subtitle(row["kind"], fields), "when": when(fields), "evidence": evidence,
           "conflicts": [c for c in open_conflicts if row["id"] in c["item_ids"]],
           "can_delete": row["origin"] == "you", "created": row["created"], "updated": row["updated"],
           "merged_into": row["merged_into"], "merged_why": row["merged_why"],
           "star": bool(row["star"]), "star_by": row["star_by"], "can_star": row["kind"] in STAR_KINDS}
    return {**out, **tiers.shown(out, row), **contexts.shown(out, row)}


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
