"""The Home route: what needs you, most urgent first, composed from every area's `home.py`.

Each area's `home.py` may offer `needs(con)` (items with an `urgency`, lower is more urgent) and `sections(con)`
(`new_matches`, `goal`, `setup`, `week`, `counts`). This module adds the needs that belong to no one area:
actions waiting for you, work Hal could not finish, documents that need making again, emails that need you
and to-dos. A provider that fails is logged and left out, so one broken area never blanks Home.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from .. import stats
from ..web.router import Request, route
from . import activity, clock, plugins, runner, settings

log = logging.getLogger(__name__)

TODAY_ITEMS = 4                       # "What Hal did today" shows the latest four
MORNING_ENDS, AFTERNOON_ENDS = 12, 18
# Urgency of the needs this module adds; areas place theirs on the same scale.
URGENCY = {"waiting": 10, "interview": 15, "email": 20, "redraft": 30, "interrupted": 40, "todo": 50}
TODO_ICONS = {"interview": "calendar", "assessment": "calendar", "offer": "star", "reply": "mail",
              "apply": "file", "message": "mail", "task": "check"}
WAITING_ICON = "warn"


def _greeting(now: datetime) -> str:
    part = "morning" if now.hour < MORNING_ENDS else "afternoon" if now.hour < AFTERNOON_ENDS else "evening"
    name = (settings.get("you.preferred_name") or "").strip()
    return f"Good {part}, {name}" if name else f"Good {part}"


def _date_words(now: datetime) -> str:
    return f"{now.strftime('%A')} {now.day} {now.strftime('%B')}"


def _due_words(due: str | None) -> str:
    if not due:
        return ""
    try:
        when = datetime.fromisoformat(due[:10]).date()
    except ValueError:
        return ""
    days = (when - clock.local_day()).days
    if days < 0:
        return "Overdue"
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    return f"In {days} days"


def own_needs(con: sqlite3.Connection) -> list[dict]:
    """The needs no single area owns: waiting and interrupted actions, redrafts, emails and to-dos."""
    out = []
    for a in runner.listing(con, "waiting_for_you"):
        out.append({"id": f"action:{a['id']}", "icon": WAITING_ICON, "tone": "warn",
                    "title": a.get("message") or a.get("subject") or "Hal is waiting for you",
                    "detail": "Hal stopped here and is waiting for you.", "due": "",
                    "act": {"label": "Open", "route": f"app-{a['job_id']}" if a.get("job_id") else "activity"},
                    "urgency": URGENCY["waiting"]})
    for a in runner.listing(con, "interrupted"):
        out.append({"id": f"action:{a['id']}", "icon": "pause", "tone": "warn",
                    "title": f"Carry on: {a.get('subject') or a.get('kind')}",
                    "detail": "Hal closed before this finished. Nothing was sent.", "due": "",
                    "act": {"label": "Carry on", "action": f"/actions/{a['id']}/carry-on"},
                    "urgency": URGENCY["interrupted"]})
    for r in con.execute("""SELECT j.id, j.title, j.company, a.number FROM applications a JOIN jobs j ON j.id = a.job_id
                            WHERE a.docs_need_redraft = 1 AND j.status IN ('ready', 'applying')"""):
        out.append({"id": f"redraft:{r['id']}", "icon": "file", "tone": "warn",
                    "title": f"Documents for {r['title']} need making again",
                    "detail": f"{r['company']}. An older job used the same folder, so Hal makes them again for this "
                              "advert.", "due": "",
                    "act": {"label": "Make documents again", "action": f"/applications/{r['id']}/documents/redo"},
                    "urgency": URGENCY["redraft"]})
    for t in con.execute("""SELECT * FROM todos WHERE done IS NULL ORDER BY priority, coalesce(due, '9999'), id
                            LIMIT 20"""):
        kind = t["kind"]
        out.append({"id": f"todo:{t['id']}", "icon": TODO_ICONS.get(kind, "check"),
                    "tone": "good" if kind in ("interview", "offer") else "info", "title": t["title"],
                    "detail": t["detail"] or "", "due": _due_words(t["due"]),
                    "act": ({"label": "Open", "url": t["url"]} if t["url"] else
                            {"label": "Open", "route": f"job-{t['job_id']}"} if t["job_id"] else None),
                    "urgency": (URGENCY["interview"] if kind in ("interview", "offer", "assessment")
                                else URGENCY["todo"] + t["priority"])})
    return out


def _providers(con: sqlite3.Connection) -> tuple[list[dict], dict]:
    needs, sections = [], {}
    for mod in plugins.modules("home"):
        try:
            if hasattr(mod, "needs"):
                needs += list(mod.needs(con))
            if hasattr(mod, "sections"):
                for key, value in (mod.sections(con) or {}).items():
                    if key == "counts" and isinstance(value, dict):
                        sections.setdefault("counts", {}).update(value)
                    else:
                        sections[key] = value
        except Exception:
            log.exception("the Home provider %s failed", mod.__name__)
    return needs, sections


def _goal(sections: dict) -> dict | None:
    if "goal" in sections:
        return sections["goal"]
    try:
        from ..find import goal as goal_mod

        g = goal_mod.current()
        return {"rows": [list(r) for r in goal_mod.summary_rows(g)], "mode": settings.display("goal.mode"),
                "version": g.version}
    except Exception:
        log.exception("could not read the goal for Home")
        return None


def _apps_count(con: sqlite3.Connection) -> int:
    return con.execute("""SELECT count(*) FROM applications a JOIN jobs j ON j.id = a.job_id
                          WHERE j.status = 'applying' AND (a.handed_over_at IS NOT NULL
                                OR (a.docs IS NOT NULL AND a.approved_at IS NULL))""").fetchone()[0]


def summary_words(n_needs: int, n_new: int) -> str:
    """The line under the greeting: "3 things need you. Hal found 2 new matches since yesterday."."""
    first = ("Nothing needs you right now." if n_needs == 0 else
             "1 thing needs you." if n_needs == 1 else f"{n_needs} things need you.")
    if n_new:
        first += f" Hal found {n_new} new match{'es' if n_new != 1 else ''} since yesterday."
    return first


def home(con: sqlite3.Connection) -> dict:
    """Everything Home shows, in the shape the Home page reads."""
    from ..mail import messages

    runner.flush()
    now = clock.local()
    area_needs, sections = _providers(con)
    needs = sorted(own_needs(con) + area_needs, key=lambda n: (n.get("urgency", URGENCY["todo"]), n.get("title", "")))
    new_matches = sections.get("new_matches") or []
    page = activity.page(con, limit=TODAY_ITEMS * 3)
    today = [i for d in page["days"] if d["day"] == "Today" for i in d["items"]][:TODAY_ITEMS]
    counts = {"jobs": len(new_matches), "apps": _apps_count(con), "inbox": messages.unread_count(con),
              **sections.get("counts", {})}
    return {"date_words": _date_words(now), "greeting": _greeting(now), "summary": summary_words(len(needs),
                                                                                                 len(new_matches)),
            "needs": needs, "new_matches": new_matches, "first_search": sections.get("first_search"),
            "week": sections.get("week") or stats.week(con), "goal": _goal(sections), "setup": sections.get("setup"),
            "today": today, "counts": counts}


@route("GET", "/api/v2/home")
def home_page(req: Request) -> dict:
    """Home: what needs you, new matches, this week, your goal and what Hal did today."""
    return home(req.con)
