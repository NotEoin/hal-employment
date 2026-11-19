"""Personal looks: the looks a person made from their own CV file, kept in their own data (`cv_looks`).

- A personal look is a row of checked measures (`measures.Measures`), never a file in the code folder and never
  CSS: `render.look` builds its page style from the measures each time it is asked for one, so a look that is
  tightened prints again (its page's hash changes) and one that is renamed does not (its page is the same).
- Ids are `my-<words from the name>`, never the id of a built-in look, and stay the same when it is renamed.
- A look that is in use by a CV, archived or not, cannot be deleted: the person is told which CVs use it.
  Moving those CVs to another look without asking would change what employers get from them, so the person
  chooses (2026-11-19).
- A saved look whose measures no longer pass `measures.validate` (a later Hal narrowed a range) is shown with
  the default measures and a warning in the log, as a copied CV type that no longer reads falls back.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

from jobhunt.cvstudio import measures, render
from jobhunt.cvstudio.model import CVError

log = logging.getLogger(__name__)

PREFIX = "my-"
MAX_NAME = 80                   # as a copied CV type's name
SLUG_CHARS = 40
LIKE = "Like {name}"            # a matched look's first name, after the file it came from


class InUse(CVError):
    """A personal look that a CV still uses; the message names the CVs."""


@dataclass(frozen=True)
class Saved:
    """One personal look as stored: its measures, what Hal approximated, and the file it came from."""

    id: str
    name: str
    measures: measures.Measures
    notes: tuple[str, ...]
    steps: int
    file_id: str | None

    def look(self) -> render.Look:
        """The look the page is drawn in."""
        return render.personal_look(self.id, self.name, self.measures)


def _conn(con: sqlite3.Connection | None):
    from jobhunt.cvstudio import store

    return store._conn(con)


def _saved(row: sqlite3.Row) -> Saved:
    try:
        m = measures.validate(json.loads(row["measures"]))
    except (ValueError, TypeError) as e:     # BadMeasure and broken JSON are both ValueErrors
        log.warning("the personal look %s no longer reads (%s); using the default measures", row["id"], e)
        m = measures.Measures()
    try:
        notes = tuple(str(n) for n in json.loads(row["notes"] or "[]"))
    except (ValueError, TypeError):
        notes = ()
    return Saved(row["id"], row["name"], m, notes, int(row["steps"] or 0), row["file_id"])


def get(look_id: str, con: sqlite3.Connection | None = None) -> Saved | None:
    """A personal look by id; None when there is none (or the id is not a personal look's)."""
    if not str(look_id or "").startswith(PREFIX):
        return None
    with _conn(con) as c:
        row = c.execute("SELECT * FROM cv_looks WHERE id = ?", (look_id,)).fetchone()
    return _saved(row) if row else None


def all_saved(con: sqlite3.Connection | None = None) -> list[Saved]:
    """Every personal look, oldest first."""
    with _conn(con) as c:
        rows = c.execute("SELECT * FROM cv_looks ORDER BY created, id").fetchall()
    return [_saved(r) for r in rows]


def _clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:MAX_NAME]
    if not name:
        raise CVError("name: give the look a name")
    return name


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:SLUG_CHARS] or "look"


def create(name: str, m: measures.Measures, *, notes: list[str] | tuple[str, ...] = (), file_id: str | None = None,
           con: sqlite3.Connection | None = None) -> Saved:
    """Saves a new personal look; its id comes from its name."""
    from jobhunt import db
    from jobhunt.cvstudio import store

    name = _clean_name(name)
    m = measures.validate(m.to_dict())
    with _conn(con) as c:
        with store.writing(c):
            taken = {r[0] for r in c.execute("SELECT id FROM cv_looks")} | {x.id for x in render.looks()}
            new_id, n = f"{PREFIX}{_slug(name)}", 2
            while new_id in taken:
                new_id, n = f"{PREFIX}{_slug(name)}-{n}", n + 1
            now = db.now()
            c.execute("INSERT INTO cv_looks (id, name, measures, notes, steps, file_id, created, updated) "
                      "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                      (new_id, name, json.dumps(m.to_dict()), json.dumps(list(notes)), file_id, now, now))
        return get(new_id, c)


def _must(look_id: str, c: sqlite3.Connection) -> Saved:
    found = get(look_id, c)
    if found is None:
        raise LookupError("That look is not one of yours.")
    return found


def rename(look_id: str, name: str, con: sqlite3.Connection | None = None) -> Saved:
    """Gives a personal look a new name; its id, and so every CV using it, stays the same."""
    from jobhunt import db
    from jobhunt.cvstudio import store

    name = _clean_name(name)
    with _conn(con) as c:
        _must(look_id, c)
        with store.writing(c):
            c.execute("UPDATE cv_looks SET name = ?, updated = ? WHERE id = ?", (name, db.now(), look_id))
        return _must(look_id, c)


def set_measures(look_id: str, m: measures.Measures, steps: int, con: sqlite3.Connection | None = None) -> Saved:
    """Saves new measures for a personal look (a "Make it fit" step), with the steps taken so far."""
    from jobhunt import db
    from jobhunt.cvstudio import store

    m = measures.validate(m.to_dict())
    with _conn(con) as c:
        _must(look_id, c)
        with store.writing(c):
            c.execute("UPDATE cv_looks SET measures = ?, steps = ?, updated = ? WHERE id = ?",
                      (json.dumps(m.to_dict()), steps, db.now(), look_id))
        return _must(look_id, c)


def remeasure(look_id: str, m: measures.Measures, notes: list[str] | tuple[str, ...],
              con: sqlite3.Connection | None = None) -> Saved:
    """Saves a new measuring of the file a look came from: new measures and notes, no "Make it fit" steps."""
    from jobhunt import db
    from jobhunt.cvstudio import store

    m = measures.validate(m.to_dict())
    with _conn(con) as c:
        _must(look_id, c)
        with store.writing(c):
            c.execute("UPDATE cv_looks SET measures = ?, notes = ?, steps = 0, updated = ? WHERE id = ?",
                      (json.dumps(m.to_dict()), json.dumps(list(notes)), db.now(), look_id))
        return _must(look_id, c)


def users(look_id: str, con: sqlite3.Connection | None = None) -> list[str]:
    """The names of the CVs printed in this look, archived ones too."""
    with _conn(con) as c:
        rows = c.execute("SELECT name FROM cv_documents WHERE look_id = ? ORDER BY name", (look_id,)).fetchall()
    return [r["name"] or "your CV" for r in rows]


def _and(names: list[str]) -> str:
    quoted = [f'"{n}"' for n in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def delete(look_id: str, con: sqlite3.Connection | None = None) -> None:
    """Deletes a personal look; `InUse` when a CV still uses it, naming those CVs."""
    from jobhunt.cvstudio import store

    with _conn(con) as c:
        found = _must(look_id, c)
        using = users(look_id, c)
        if using:
            one = len(using) == 1
            raise InUse(f"{'Your CV' if one else 'Your CVs'} {_and(using)} {'uses' if one else 'use'} the look "
                        f"{found.name}. Choose another look for {'it' if one else 'them'} first, then delete "
                        "this one.")
        with store.writing(c):
            c.execute("DELETE FROM cv_looks WHERE id = ?", (look_id,))
