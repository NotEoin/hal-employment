"""The setup guide's steps and where the person is in them (table `setup_steps`).

Each of the eleven steps has a row once the person reaches it: `todo`, `done` or `skipped`. The guide's own
place (the step to open at, and whether the person chose "Save and finish later") is the row `guide`, whose
`data` is JSON. Nothing here reads or writes a setting, so going through a step again can never reset a
value changed in Settings; the steps save their own fields through the settings store as they are typed.

Design notes:
- `done()` answers "may the guide stay closed?", which is what `/session` and `/open` ask: true once All set
  is reached, once the person chose "Save and finish later" (Home's card then lists what is left), and for
  a developer's checkout that held jobs before the guide existed (the owner's install), so an update never
  pushes someone who already uses Hal into the guide.
- Welcome and All set are not listed on Home: they have nothing to set up.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..core import clock, events, paths

GUIDE = "guide"            # the pseudo-step row holding the guide's own place
STATES = ("todo", "done", "skipped")


@dataclass(frozen=True)
class Step:
    """One step of the guide: its id (also its file name under `static/app/setup/steps/`) and its words."""

    id: str
    name: str
    sub: str
    optional: bool = False
    home: str = ""          # the line under the step on Home's "Finish setting up" card


STEPS = (
    Step("welcome", "Welcome", ""),
    Step("cv", "Your CV", "Use it, improve it, or start fresh", home="Add your CV so Hal can write for you."),
    Step("about", "About you", "Checked against your CV", home="Your name, contact details and right to work."),
    Step("goal", "What you're looking for", "A short chat or quiz", home="Tell Hal which jobs to look for."),
    Step("record", "Your record", "Certificates and references", home="A folder of proof Hal can quote from."),
    Step("ai", "How Hal thinks", "Your Claude subscription", home="Hal needs an AI to read adverts and write."),
    Step("helper", "Browser helper", "So Hal can fill forms", home="Lets Hal fill in forms in your browser."),
    Step("email", "Email", "Optional", optional=True, home="Turn interview emails into reminders."),
    Step("phone", "Phone alerts", "Optional", optional=True, home="Get interview emails on your phone."),
    Step("updates", "Updates", "Optional", optional=True, home="Let Hal check for new versions."),
    Step("done", "All set", ""),
)
BY_ID = {s.id: s for s in STEPS}
LISTED = tuple(s for s in STEPS if s.home)     # the steps Home can list: all but Welcome and All set


def step(step_id: str) -> Step:
    """The step with this id; `LookupError` with plain words for any other."""
    found = BY_ID.get(step_id)
    if found is None:
        raise LookupError("The setup guide has no step by that name.")
    return found


def _rows(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["id"]: r for r in con.execute("SELECT id, state, updated, data FROM setup_steps")}


def _guide(rows: dict[str, sqlite3.Row]) -> dict:
    row = rows.get(GUIDE)
    if row is None or not row["data"]:
        return {}
    try:
        data = json.loads(row["data"])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(con: sqlite3.Connection, row_id: str, state: str, data: dict | None = None) -> None:
    con.execute("""INSERT INTO setup_steps (id, state, updated, data) VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated = excluded.updated,
                   data = COALESCE(excluded.data, setup_steps.data)""",
                (row_id, state, clock.iso(), None if data is None else json.dumps(data, ensure_ascii=True)))


def states(con: sqlite3.Connection) -> dict[str, str]:
    """Each step's state; a step never reached is `todo`."""
    rows = _rows(con)
    return {s.id: (rows[s.id]["state"] if s.id in rows else "todo") for s in STEPS}


def current(con: sqlite3.Connection) -> str:
    """The step the guide opens at: where the person left it, else the first step not done."""
    rows = _rows(con)
    at = _guide(rows).get("step")
    if at in BY_ID:
        return at
    st = states(con)
    return next((s.id for s in STEPS if st[s.id] == "todo"), STEPS[0].id)


def _used_before_the_guide(con: sqlite3.Connection, rows: dict) -> bool:
    if rows or not paths.is_developer_checkout():
        return False
    return con.execute("SELECT 1 FROM jobs LIMIT 1").fetchone() is not None


def finished(con: sqlite3.Connection) -> bool:
    """True once the person reached All set."""
    return states(con)["done"] == "done"


def done(con: sqlite3.Connection) -> bool:
    """True when the guide need not open by itself (see the module notes)."""
    rows = _rows(con)
    return finished(con) or bool(_guide(rows).get("later")) or _used_before_the_guide(con, rows)


def set_state(con: sqlite3.Connection, step_id: str, state: str) -> None:
    """Marks a step done, skipped or current (the guide opens there next time); commits."""
    found = step(step_id)
    if state == "current":
        rows = _rows(con)
        _write(con, GUIDE, "todo", {**_guide(rows), "step": found.id})
        if found.id not in rows:
            _write(con, found.id, "todo")
    elif state in ("done", "skipped"):
        if state == "skipped" and not found.optional:
            raise ValueError(f"{found.name} cannot be skipped. Press Save and finish later to stop for now.")
        _write(con, found.id, state)
    else:
        raise ValueError("A step is done, skipped or current.")
    con.commit()
    events.publish("setup", step=found.id)


def later(con: sqlite3.Connection) -> None:
    """"Save and finish later": the guide stays closed until the person opens it; commits."""
    rows = _rows(con)
    _write(con, GUIDE, "todo", {**_guide(rows), "later": True})
    con.commit()
    events.publish("setup", step=None)


def overview(con: sqlite3.Connection, found: dict | None = None) -> dict:
    """The `/setup` reply: the step to open at, every step with its state, and what Hal found."""
    st = states(con)
    return {"step": current(con), "done": done(con), "finished": finished(con),
            "steps": [{"id": s.id, "name": s.name, "sub": s.sub, "optional": s.optional, "state": st[s.id]}
                      for s in STEPS],
            "found": found or {"usb_install": None, "old_helper": None}}


def left(con: sqlite3.Connection) -> list[Step]:
    """The steps Home lists: those not done, once the guide was opened or left for later."""
    rows = _rows(con)
    if finished(con) and not any(rows.get(s.id) and rows[s.id]["state"] == "skipped" for s in LISTED):
        return []
    if not rows or _used_before_the_guide(con, rows):
        return []
    st = states(con)
    return [s for s in LISTED if st[s.id] != "done"]
