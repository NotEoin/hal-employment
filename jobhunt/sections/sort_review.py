"""The review of a sort beyond its rows: the courses its folders name, proposed as education cards.

Coursework and lecture material wait for their course's card (`sorter.needs_card`). The folders already name
the courses (`sort_courses`), so the review shows them first as "Courses Hal found": the person accepts one,
renaming it if they like, and its card is made at once; every row in its folders takes it, and the rest are
matched again (`sorts.rematch`).

Design decisions, each with its reason:

- Courses are worked out from the rows each time the review asks, never stored: a course disappears from the
  list once its rows have a card, whatever gave it (this list, an accepted CV, a transcript, or the person
  choosing a card by hand), so nothing can go stale.
- The card is the person's own (`profile.store.add_by_hand`): they accepted its name, so it is not a guess
  waiting for a source. Its dates are the months of the course's oldest and newest files, and its modules the
  module folders found.
"""
from __future__ import annotations

import sqlite3

from jobhunt.profile import store as profile_store
from jobhunt.sections import sort_courses, sorts

COURSE_WORDS = ("qualification", "subject", "institution")


def courses_found(con: sqlite3.Connection, sort_id: int) -> list[dict]:
    """The courses a sort's waiting coursework and lecture rows name (`sort_courses.courses_found`)."""
    rows = con.execute("SELECT rel, mtime FROM record_sort_rows WHERE sort_id = ? AND state = 'proposed' AND "
                       "section IN ('lecture', 'coursework') AND target_id IS NULL ORDER BY rel",
                       (int(sort_id),)).fetchall()
    return sort_courses.courses_found((r["rel"], r["mtime"]) for r in rows)


def accept_course(con: sqlite3.Connection, sort_id, key: str, words: dict | None = None) -> dict:
    """Makes the education card for the course found at folder `key`, with the person's `words` for its
    qualification, subject or institution; gives it to every waiting row in its folders and matches the rest
    again. Returns {"card": the card, "matched": rows that found a card}.

    LookupError when the sort has no such course (any more); ValueError when the words do not make a course.
    """
    s = sorts.row_of(con, sort_id)
    course = next((c for c in courses_found(con, s["id"]) if c["key"] == key), None)
    if course is None:
        raise LookupError("Hal found no such course in this sort. It may have its card already.")
    words = words or {}
    fields = {k: " ".join(str(words.get(k, course[k]) or "").split()) for k in COURSE_WORDS}
    fields = {k: v for k, v in fields.items() if v}
    if course["start"]:
        fields.update(start=course["start"], end=course["end"])
    if course["modules"]:
        fields["modules"] = [{"name": m} for m in course["modules"]]
    card = profile_store.add_by_hand(con, "education", fields)
    given = 0
    with con:
        for folder in course["keys"]:
            given += con.execute("UPDATE record_sort_rows SET target_id = ? WHERE sort_id = ? AND state = "
                                 "'proposed' AND target_id IS NULL AND section IN ('lecture', 'coursework') AND "
                                 "substr(rel, 1, ?) = ?", (card["id"], s["id"], len(folder) + 1,
                                                          folder + "/")).rowcount
    return {"card": card, "matched": given + sorts.rematch(con, s["id"])}
