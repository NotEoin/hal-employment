"""What Hal has learned: the `learned_items` rows behind "Waiting for you" and "Learned".

A pattern offers a suggestion with `offer()`; nothing else happens until the person answers. "Yes, do this"
applies the row's `change` and keeps what was there before, so switching the item off, deleting it or pressing
Undo in Activity puts it back. "Not now" asks again after `ASK_AGAIN_DAYS`; "Never suggest this" keeps the row
(and its fingerprint) so the same suggestion is never offered again.

Design decisions:
- A change is applied from the live value, not from a value worked out when the suggestion was made: adding
  a role adds one word to the roles as they are now, so an edit the person made in between is never lost.
- Putting a change back only happens while the value is still the one Hal set. A person who later changed the
  setting by hand keeps their own value; the item just stops claiming it.
- Accepted setting changes go through `settings.set(..., by="learned")`, which writes its own history row and
  Activity line with Undo, so Settings shows where the value came from.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from jobhunt.core import activity, clock, events, settings

log = logging.getLogger(__name__)

ASK_AGAIN_DAYS = 7          # "Not now" asks again next week ("Hal will ask again next week")
AREA = "learning"           # Activity area of every decision
ON = ("yes", "active")      # an item in use
LEARNED = ("yes", "active", "off")   # what the Learned tab lists
AREA_WORDS = {"finding": "Finding jobs", "letters": "Letters", "writing": "Writing", "cv": "CV",
              "sites": "Job sites", "results": "Results"}
DECISIONS = ("yes", "later", "never")
FROM_PAGE = "in What Hal remembers"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()   # not strftime: it follows the locale
EVENT_SOURCE = "learn"      # marks the `suggestions` events learning publishes, so it never wakes itself


def _now() -> str:
    return clock.iso()


def _row(con: sqlite3.Connection, item_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM learned_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise LookupError("Hal does not remember that any more.")
    return row


def _change(row: sqlite3.Row | dict) -> dict:
    return json.loads(row["change"]) if row["change"] else {}


def changed_event() -> None:
    """Tells open pages that the suggestions or learned items changed."""
    events.publish("suggestions", source=EVENT_SOURCE)


def offer(con: sqlite3.Connection, *, fingerprint: str, area: str, title: str, why: str, effect: str,
          evidence: list[dict], change: dict, source: str) -> tuple[str, bool]:
    """Adds or refreshes a waiting suggestion; returns (id, True when it is newly waiting).

    A fingerprint the person already answered stays as they left it: "Never" and "Yes" for good, "Not now"
    until its week is up, when the suggestion waits again with the latest evidence.
    """
    now = _now()
    row = con.execute("SELECT * FROM learned_items WHERE fingerprint = ?", (fingerprint,)).fetchone()
    fields = (title, why, effect, json.dumps(evidence, ensure_ascii=True), json.dumps(change, ensure_ascii=True))
    if row is None:
        item_id = uuid.uuid4().hex[:12]
        con.execute("""INSERT INTO learned_items (id, area, kind, title, why, effect, evidence, change, fingerprint,
                                                  status, source, created)
                       VALUES (?, ?, 'suggestion', ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)""",
                    (item_id, area, *fields, fingerprint, source, now))
        return item_id, True
    if row["status"] == "waiting":
        con.execute("UPDATE learned_items SET title = ?, why = ?, effect = ?, evidence = ?, change = ? WHERE id = ?",
                    (*fields, row["id"]))
        return row["id"], False
    if row["status"] == "later" and (row["ask_again"] or "") <= now:
        con.execute("""UPDATE learned_items SET title = ?, why = ?, effect = ?, evidence = ?, change = ?,
                       status = 'waiting', ask_again = NULL WHERE id = ?""", (*fields, row["id"]))
        return row["id"], True
    return row["id"], False


def withdraw(con: sqlite3.Connection, keep: set[str], source: str) -> int:
    """Removes waiting suggestions from `source` whose pattern no longer holds; returns how many."""
    rows = con.execute("SELECT id, fingerprint FROM learned_items WHERE status = 'waiting' AND source = ?",
                       (source,)).fetchall()
    gone = [r["id"] for r in rows if r["fingerprint"] not in keep]
    con.executemany("DELETE FROM learned_items WHERE id = ?", [(i,) for i in gone])
    return len(gone)


def _evidence_out(raw: str) -> list[dict]:
    out = []
    for e in json.loads(raw or "[]"):
        item = {"label": e["label"]}
        if e.get("job_id"):
            item["route"] = "job-" + e["job_id"]
        out.append(item)
    return out


def waiting(con: sqlite3.Connection) -> list[dict]:
    """The suggestions waiting for the person, newest first."""
    rows = con.execute("""SELECT * FROM learned_items WHERE status = 'waiting' AND kind = 'suggestion'
                          ORDER BY created DESC, id""").fetchall()
    return [{"id": r["id"], "title": r["title"], "why": r["why"], "evidence": _evidence_out(r["evidence"]),
             "effect": r["effect"] or "", "area": AREA_WORDS.get(r["area"], r["area"])} for r in rows]


def waiting_count(con: sqlite3.Connection) -> int:
    """How many suggestions wait for the person."""
    return con.execute("SELECT count(*) FROM learned_items WHERE status = 'waiting' AND kind = 'suggestion'"
                       ).fetchone()[0]


def _day(at: str):
    return clock.local(datetime.fromisoformat(at.replace("Z", "+00:00"))).date()


def date_words(at: str) -> str:
    """"18 Oct", in local time."""
    day = _day(at)
    return f"{day.day} {MONTHS[day.month - 1]}"


def when_words(at: str | None, today: Any = None) -> str:
    """"Today", "Yesterday" or "18 Oct", in local time."""
    if not at:
        return ""
    day = _day(at)
    today = today or clock.local_day()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return date_words(at)


def _in_effect(row: sqlite3.Row, con: sqlite3.Connection) -> bool:
    """True while the item is switched on and its change still holds."""
    if row["status"] not in ON:
        return False
    change = _change(row)
    if "setting" in change:
        return _setting_holds(change)
    if "cv_lock" in change:
        pref = con.execute("SELECT active FROM cv_preferences WHERE id = ?", (change.get("pref_id"),)).fetchone()
        return bool(pref and pref["active"])
    return True


def learned(con: sqlite3.Connection) -> list[dict]:
    """Everything Hal learned and the person agreed to, with where it came from and a switch."""
    rows = con.execute(f"""SELECT * FROM learned_items WHERE status IN ({",".join("?" * len(LEARNED))})
                           ORDER BY coalesce(decided, created) DESC, id""", LEARNED).fetchall()
    return [{"id": r["id"], "what": _change(r).get("learned") or r["title"], "from": r["why"].rstrip("."),
             "when": when_words(r["decided"] or r["created"]), "area": AREA_WORDS.get(r["area"], r["area"]),
             "on": _in_effect(r, con)} for r in rows]


# ------------------------------------------------------------------------------------- applying changes

def _setting_holds(change: dict) -> bool:
    now = settings.get(change["setting"])
    if "add" in change:
        return change["add"].lower() in [str(v).lower() for v in now or []]
    return now == change["value"]


def _apply(con: sqlite3.Connection, row: sqlite3.Row, *, session_id: str | None) -> dict:
    """Makes the row's change from the live value; returns the change with what it replaced."""
    change = _change(row)
    if "setting" in change:
        key = change["setting"]
        before = settings.get(key)
        if "add" in change:
            have = list(before or [])
            if change["add"].lower() not in [str(v).lower() for v in have]:
                settings.set(key, [*have, change["add"]], by="learned", session_id=session_id)
        else:
            settings.set(key, change["value"], by="learned", session_id=session_id)
        return {**change, "before": before}
    if "cv_lock" in change:
        if change.get("pref_id"):
            con.execute("UPDATE cv_preferences SET active = 1 WHERE id = ?", (change["pref_id"],))
            return change
        cur = con.execute("""INSERT INTO cv_preferences (kind, target, text, from_suggestion, created)
                             VALUES ('lock_line', ?, ?, NULL, ?)""",
                          (change["cv_lock"], change.get("text", ""), _now()))
        return {**change, "pref_id": cur.lastrowid}
    raise ValueError("Hal does not know how to make this change.")


def _revert(con: sqlite3.Connection, row: sqlite3.Row, *, session_id: str | None) -> bool:
    """Puts back what the change replaced, while the change still holds; True when something changed."""
    change = _change(row)
    if "setting" in change:
        if not _setting_holds(change):
            return False
        key = change["setting"]
        if "add" in change:
            left = [v for v in settings.get(key) or [] if str(v).lower() != change["add"].lower()]
            settings.set(key, left, by="you", session_id=session_id)
        else:
            settings.set(key, change["before"], by="you", session_id=session_id)
        return True
    if "cv_lock" in change and change.get("pref_id"):
        cur = con.execute("UPDATE cv_preferences SET active = 0 WHERE id = ? AND active = 1", (change["pref_id"],))
        return bool(cur.rowcount)
    return False


def _publish_cv(change: dict) -> None:
    if "cv_lock" in change:
        events.publish("cv", what="preferences", id=change["cv_lock"])


def decide(con: sqlite3.Connection, item_id: str, decision: str, *, session_id: str | None = None) -> dict:
    """The person's answer to a waiting suggestion: "yes" makes the change, "later" or "never" leave it."""
    if decision not in DECISIONS:
        raise ValueError(f"Pick one of: {', '.join(DECISIONS)}.")
    row = _row(con, item_id)
    if row["status"] != "waiting":
        raise RuntimeError("You already answered this suggestion.")
    now = _now()
    undo = None
    if decision == "yes":
        change = _apply(con, row, session_id=session_id)
        con.execute("UPDATE learned_items SET status = 'yes', decided = ?, change = ? WHERE id = ?",
                    (now, json.dumps(change, ensure_ascii=True), item_id))
        text, undo = f"You said yes: {row['title'].rstrip('?')}", {"type": "learned_item", "id": item_id}
        _publish_cv(change)
    elif decision == "later":
        again = clock.iso(clock.now_utc() + timedelta(days=ASK_AGAIN_DAYS))
        con.execute("UPDATE learned_items SET status = 'later', decided = ?, ask_again = ? WHERE id = ?",
                    (now, again, item_id))
        text = f"Hal will ask again next week: {row['title'].rstrip('?')}"
    else:
        con.execute("UPDATE learned_items SET status = 'never', decided = ? WHERE id = ?", (now, item_id))
        text = f"Hal will not suggest this again: {row['title'].rstrip('?')}"
    pressed = {"yes": "Yes, do this", "later": "Not now", "never": "Never suggest this"}[decision]
    act = activity.record(AREA, f"learn.{decision}", text, f"You pressed {pressed} {FROM_PAGE}. Hal suggested it "
                          f"because: {row['why']}", undo=undo, icon="spark", session_id=session_id, con=con,
                          changed=row["effect"] if decision == "yes" else None)
    con.commit()
    events.publish("activity", id=act)
    changed_event()
    return {"ok": True, "activity_id": act}


def switch(con: sqlite3.Connection, item_id: str, on: bool, *, session_id: str | None = None) -> dict:
    """Switches a learned item on (its change again) or off (what was there before)."""
    row = _row(con, item_id)
    if row["status"] not in LEARNED:
        raise RuntimeError("Answer this suggestion first.")
    what = _change(row).get("learned") or row["title"]
    if on:
        change = _apply(con, row, session_id=session_id)
        con.execute("UPDATE learned_items SET status = 'active', change = ? WHERE id = ?",
                    (json.dumps(change, ensure_ascii=True), item_id))
        text, why = f"Hal uses this again: {what}", f"You switched it on {FROM_PAGE}."
    else:
        change = _change(row)
        _revert(con, row, session_id=session_id)
        con.execute("UPDATE learned_items SET status = 'off' WHERE id = ?", (item_id,))
        text, why = f"Hal stopped using: {what}", f"You switched it off {FROM_PAGE}."
    act = activity.record(AREA, "learn.switch", text, why, icon="bulb", session_id=session_id, con=con)
    con.commit()
    _publish_cv(change)
    events.publish("activity", id=act)
    changed_event()
    return {"ok": True}


def delete(con: sqlite3.Connection, item_id: str, *, session_id: str | None = None) -> dict:
    """Forgets a learned item: its change is put back first, while it still holds."""
    row = _row(con, item_id)
    if row["status"] not in LEARNED:
        raise RuntimeError("Answer this suggestion first.")
    what = _change(row).get("learned") or row["title"]
    _revert(con, row, session_id=session_id)
    con.execute("DELETE FROM learned_items WHERE id = ?", (item_id,))
    act = activity.record(AREA, "learn.delete", f"Hal forgot: {what}", f"You pressed Delete {FROM_PAGE}.",
                          icon="trash", session_id=session_id, con=con)
    con.commit()
    _publish_cv(_change(row))
    events.publish("activity", id=act)
    changed_event()
    return {"ok": True}


def undo_yes(con: sqlite3.Connection, spec: dict) -> str:
    """Activity's Undo of a "Yes, do this": puts the change back and switches the item off."""
    row = _row(con, spec["id"])
    what = _change(row).get("learned") or row["title"]
    _revert(con, row, session_id=None)
    con.execute("UPDATE learned_items SET status = 'off' WHERE id = ?", (spec["id"],))
    _publish_cv(_change(row))
    changed_event()
    return f"Put back what was there before: {what}"
