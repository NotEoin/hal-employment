"""The Saved answers tab of What Hal remembers: what Hal reuses on forms and where.

Each row of `answers2` is listed with where it may be used, in the words "Where may Hal use this?" offers:
everywhere, only on one site, only for one job, or never (kept only as an example of the person's
writing). A row waiting in `review` says "Choose where to use it" and is never used until the person picks.
Three answers Hal works out for each form are listed too, read-only, so the person sees why a salary or a
"why us" answer never lands on the wrong form.

Design notes:
- A row whose question `autofill.private_question` matches is never listed, changed or deleted here.
  `remember()` never writes one, and migration 0007 never copied one; this is the backstop.
- An answer keeps its JSON shape when changed: a list stays a list (comma-separated words), a yes or no
  stays a boolean, so `autofill._fit` reads it as before.
"""
from __future__ import annotations

import sqlite3

from .. import autofill, db

SCOPES = ("everywhere", "site", "job", "never")
NEEDS_CHOICE = "Choose where to use it"
EVERYWHERE, NEVER = "Everywhere", "Never reused"
# Read-only answers Hal works out each time; their ids are not row ids.
WORKED_OUT = [
    {"id": "auto:how_heard", "q": "How did you hear about this job?", "a": "Worked out from where Hal found the job",
     "scope": "site", "scope_label": "Each job site"},
    {"id": "auto:salary", "q": "What salary are you expecting?",
     "a": "Researched for each job, unless you set one answer in Settings, You", "scope": "job",
     "scope_label": "This job only"},
    {"id": "auto:why_us", "q": "Why do you want to work for us?", "a": "Written fresh for each job. Never reused.",
     "scope": "never", "scope_label": NEVER},
]
WORKED_OUT_WORDS = "Hal works this answer out for each form, so it cannot be changed here."


class AnswerError(ValueError):
    """A change Hal cannot make, in words for the person."""


class AnswerConflict(AnswerError):
    """The question already has an answer where this one would move to."""


def _job(con: sqlite3.Connection, job_id: str):
    if not job_id:
        return None
    return con.execute("SELECT id, title, company, url FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _scope_label(con: sqlite3.Connection, scope: str, key: str) -> str:
    """The words for where an answer may be used."""
    if scope == "everywhere":
        return EVERYWHERE
    if scope == "never":
        return NEVER
    if scope == "site":
        return f"Only on {key}"
    if scope == "job":
        job = _job(con, key)
        return f"Only for {job['title']} at {job['company']}" if job is not None else "This job only"
    return NEEDS_CHOICE


def _choices(con: sqlite3.Connection, row) -> list[dict]:
    """The places this answer may be moved to: everywhere, its site and its job when known, or never."""
    out = [{"scope": "everywhere", "scope_key": "", "label": EVERYWHERE}]
    site = row["scope_key"] if row["scope"] == "site" else ""
    job = _job(con, row["scope_key"]) if row["scope"] == "job" else None
    if job is not None and not site:
        site = autofill.site_key(job)
    if site:
        out.append({"scope": "site", "scope_key": site, "label": f"Only on {site}"})
    if row["scope"] == "job":
        out.append({"scope": "job", "scope_key": row["scope_key"], "label": _scope_label(con, "job", row["scope_key"])})
    out.append({"scope": "never", "scope_key": "", "label": NEVER})
    return out


def _shown(value) -> str:
    """An answer's JSON value as the words in the box."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return "" if value is None else str(value)


def _private(row) -> bool:
    return autofill.private_question({"label": row["label"]}) is not None


def _item(con: sqlite3.Connection, row) -> dict:
    review = row["scope"] == "review"
    return {"id": str(row["id"]), "q": row["label"], "a": _shown(db.json_value(row["answer"])), "scope": row["scope"],
            "scope_key": row["scope_key"], "scope_label": _scope_label(con, row["scope"], row["scope_key"]),
            "uses": row["uses"], "kind": row["kind"], "needs_choice": review, "auto": False,
            "choices": _choices(con, row)}


def listing(con: sqlite3.Connection) -> list[dict]:
    """Every saved answer, those waiting for a choice first, then the answers Hal works out each time."""
    rows = con.execute("""SELECT * FROM answers2 ORDER BY scope = 'review' DESC, uses DESC, updated DESC""").fetchall()
    items = [_item(con, r) for r in rows if not _private(r)]
    worked = [{**w, "scope_key": "", "uses": 0, "kind": "derived", "needs_choice": False, "auto": True, "choices": []}
              for w in WORKED_OUT]
    return items + worked


def _row(con: sqlite3.Connection, answer_id: str):
    """The row, or None when there is none the tab shows."""
    if answer_id.startswith("auto:"):
        raise AnswerError(WORKED_OUT_WORDS)
    if not answer_id.isdigit():
        return None
    row = con.execute("SELECT * FROM answers2 WHERE id = ?", (int(answer_id),)).fetchone()
    return None if row is None or _private(row) else row


def _parsed(old, text: str):
    """The new answer in the old one's JSON shape."""
    if isinstance(old, bool):
        word = text.strip().lower()
        if word not in ("yes", "no"):
            raise AnswerError("This answer is Yes or No.")
        return word == "yes"
    if isinstance(old, list):
        return [part.strip() for part in text.split(",") if part.strip()]
    return text.strip()


def change(con: sqlite3.Connection, answer_id: str, body: dict) -> dict | None:
    """Changes an answer's words or where it may be used; returns the listed answer, or None when there is none."""
    row = _row(con, answer_id)
    if row is None:
        return None
    answer = row["answer"]
    if "a" in body:
        text = body.get("a")
        if not isinstance(text, str) or not text.strip():
            raise AnswerError("Write an answer, or delete this one.")
        answer = db.json_text(_parsed(db.json_value(row["answer"]), text))
    scope, key = row["scope"], row["scope_key"]
    if "scope" in body:
        scope, key = body.get("scope"), str(body.get("scope_key") or "").strip().lower()
        if scope not in SCOPES:
            raise AnswerError("Choose where Hal may use this answer.")
        if scope in ("everywhere", "never"):
            key = ""
        elif not key:
            raise AnswerError("Choose which site or job this answer is for.")
        elif scope == "job":
            key = str(body.get("scope_key")).strip()
            if _job(con, key) is None:
                raise AnswerError("That job is no longer in Hal's list.")
    try:
        con.execute("UPDATE answers2 SET answer = ?, scope = ?, scope_key = ?, updated = ? WHERE id = ?",
                    (answer, scope, key, db.now(), row["id"]))
    except sqlite3.IntegrityError as e:
        con.rollback()
        raise AnswerConflict("You already have an answer to this question there. Delete one of them first.") from e
    con.commit()
    return _item(con, con.execute("SELECT * FROM answers2 WHERE id = ?", (row["id"],)).fetchone())


def delete(con: sqlite3.Connection, answer_id: str) -> bool:
    """Deletes a saved answer; False when there is none the tab shows."""
    row = _row(con, answer_id)
    if row is None:
        return False
    con.execute("DELETE FROM answers2 WHERE id = ?", (row["id"],))
    con.commit()
    return True
