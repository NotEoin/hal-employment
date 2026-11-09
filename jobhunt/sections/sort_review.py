"""The review of a sort beyond its rows: the courses its folders name, proposed as education cards, and the
module groups that show a course's many files as one row each.

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
- Lecture material and coursework are shown one row a module (`rows`, by `sort_courses.group_key`, the
  section and the card), with a file count, ticked and moved together (`change_group`): a real dump's 6,417
  lecture rows were too many to read (2026-11-09). A group of one is shown as its file. The rows are grouped
  from the sort's proposed rows each time, as a sort holds thousands of rows, not millions, and a stored
  group would go stale when the person moves a file.
- A project's sibling folders numbered in one series ("lab1" to "lab8", `sort_units.series`) are one row the
  same way, named by its first and last folder: a real dump gave nine rows for one module's practicals
  (2026-11-09).
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from jobhunt.profile import store as profile_store
from jobhunt.sections import registry, sort_courses, sort_units, sorter, sorts

COURSE_WORDS = ("qualification", "subject", "institution")
GROUPED = ("lecture", "coursework")
GROUP_MIN = 2              # one file is shown as itself
GROUP_CHANGES = {"ticked", "section", "target_id"}


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


# Module groups --------------------------------------------------------------------------------------------

def _group_of(r: sqlite3.Row) -> str:
    """A row's group in the review: its section, card and module folder; a project's numbered series
    (`sort_units.series`); "" for a row shown on its own."""
    card = "" if r["target_id"] is None else r["target_id"]
    if r["section"] in GROUPED:
        return f"{r['section']}|{card}|{sort_courses.group_key(r['rel'])}"
    if r["section"] == "project" and (numbered := sort_units.series(r["rel"])):
        return f"project|{card}|{numbered}"
    return ""


def _shape_group(key: str, members: list[sqlite3.Row], titles: dict[int, str]) -> dict:
    """A module group or numbered series as the page shows a row: its name, file count and what most of its
    files are."""
    first = members[0]
    folder = key.split("|", 2)[2]
    course = sort_courses.course_of(first["rel"])
    name = course.module if course and course.module_key == folder else PurePosixPath(folder).name
    why = f"{len(members)} files from one module or folder, added together."
    if first["section"] == "project":
        names = sorted((PurePosixPath(m["rel"]).name for m in members), key=_numbered)
        folder, name = str(PurePosixPath(first["rel"]).parent), f"{names[0]} to {names[-1]}"
        why = f"{len(members)} numbered folders of one series, added together."
    section = registry.SECTIONS.get(first["section"])
    kind = Counter(m["doc_kind"] for m in members).most_common(1)[0][0]
    return {"id": None, "group": key, "count": len(members), "rel": folder, "name": name, "held": "group",
            "size": sum(m["size"] for m in members), "mtime": max(m["mtime"] or "" for m in members),
            "section": first["section"], "section_label": section.label if section else "", "doc_kind": kind,
            "doc_kind_label": registry.DOC_KINDS[kind].label if kind in registry.DOC_KINDS else "",
            "target_id": first["target_id"], "target_title": titles.get(first["target_id"], ""),
            "target_kind": section.target if section else "",
            "needs_card": sorter.needs_card(first["section"], first["target_id"]), "star": False, "can_star": False,
            "by": "rules", "why": why,
            "ticked": all(m["ticked"] for m in members), "state": "proposed", "copy_of": "", "source_id": None,
            "note_rel": None}


def _proposed(con: sqlite3.Connection, sort_id: int, section: str | None) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM record_sort_rows WHERE sort_id = ? AND state = 'proposed'", [int(sort_id)]
    if section:
        registry.get(section)
        sql += " AND section = ?"
        args.append(section)
    return con.execute(sql + " ORDER BY rel", args).fetchall()


def _numbered(name: str) -> tuple[int, str]:
    """Sorts "lab2" before "lab10"."""
    digits = re.search(r"\d+$", name)
    return (int(digits.group()) if digits else 0, name)


def _groups(found: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    members: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in found:
        if key := _group_of(r):
            members[key].append(r)
    return members


def rows(con: sqlite3.Connection, sort_id, *, section: str | None = None, group: str | None = None,
         offset: int = 0, limit: int = sorts.PAGE) -> dict:
    """One page of a sort's proposals in path order, lecture material and coursework one row a module group;
    with `group`, that group's own files. {"rows", "total"}, as `sorts.rows`."""
    s = sorts.row_of(con, sort_id)
    found = _proposed(con, s["id"], section)
    members = _groups(found)
    shown: list = list(members.get(group, [])) if group is not None else []
    if group is None:
        seen: set[str] = set()
        for r in found:
            key = _group_of(r)
            if not key or len(members[key]) < GROUP_MIN:
                shown.append(r)
            elif key not in seen:
                seen.add(key)
                shown.append(key)
    start = max(0, int(offset))
    page = shown[start:start + max(1, min(int(limit), sorts.PAGE_MAX))]
    titles = sorts.card_titles(con, {r["target_id"] for item in page
                                     for r in (members[item] if isinstance(item, str) else [item])
                                     if r["target_id"] is not None})
    return {"rows": [_shape_group(item, members[item], titles) if isinstance(item, str) else
                     sorts.shape_row(item, titles) for item in page], "total": len(shown)}


def change_group(con: sqlite3.Connection, sort_id, group: str, changes: dict) -> dict:
    """Ticks, moves or gives a card to every file of a module group at once (`sorts.change_row` for each);
    {"changed": n}. ValueError for a change a group does not take; LookupError for a group not in the sort."""
    s = sorts.row_of(con, sort_id)
    unknown = sorted(set(changes) - GROUP_CHANGES)
    if unknown:
        raise ValueError(f"A group of files has no {', '.join(unknown)} to change.")
    found = _groups(_proposed(con, s["id"], None)).get(group, [])
    if not found:
        raise LookupError("Those files are not waiting in this sort any more.")
    for r in found:
        sorts.change_row(con, r["id"], changes)
    return {"changed": len(found)}
