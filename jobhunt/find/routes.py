"""The Jobs routes: Matches and Saved, Hidden grouped by reason, one job's page, and
what the person does with a job.

A job is in Matches while it is open (`new`, `shortlisted` or `ready`), active, not hidden by a rule, not
saved, and scores at least `scoring.min_show`; a lower score shows as the Hidden group "Scored under N". The
person's own "Not for me" jobs are listed apart from the rule groups, each with the reason they chose.

Every Hidden group carries a fix: a route to the setting that hid it and, where one value would change it
(add a kind of role, show unpaid roles), that value, so the page can change it in one press and offer a
re-check. Students-only schemes get "Check when your course ends", because the person's course end is what
decides them.

Lists read the stored score and reasons only, so each request stays well under a second on a large
database; scoring happens in `find.rules` when jobs arrive or are re-checked.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from jobhunt.core import clock, runner, settings
from jobhunt.find import feedback, job_actions, kinds, rules
from jobhunt.find import goal as goal_mod
from jobhunt.web.router import BadRequest, NotFound, Request, route

PAGE = 50                   # jobs per page of a list; the page asks for more as the person scrolls
EXAMPLES = 2                # example jobs named under each Hidden group
NEW_DAYS = 2                # a job found in the last two days carries "New"
TOP_POINTS = 3              # point chips on a card
BAND_STRONG, BAND_GOOD = 80, 65   # ui/controls.js Score uses the same bands
OPEN = feedback.OPEN_STATUSES
# each ends in the id, so jobs with equal scores keep one order from page to page
SORTS = {"best": "j.score DESC, j.first_seen DESC, j.id",
         "closing": "j.closes_at IS NULL, j.closes_at ASC, j.score DESC, j.id",
         "newest": "j.first_seen DESC, j.score DESC, j.id",
         "pay": "COALESCE(j.pay_max, j.pay_min) IS NULL, COALESCE(j.pay_max, j.pay_min) DESC, j.score DESC, j.id"}
PATTERN_WORDS = {"hybrid": "Hybrid", "remote": "Remote", "on-site": "On site"}
PATTERNS = {"Hybrid": "hybrid", "Remote": "remote", "On site": "on-site"}
SOURCE_WORDS = {"advert": "an advert you added", "email": "your email", "adzuna": "Adzuna", "reed": "Reed",
                "jobsacuk": "jobs.ac.uk", "hn": "Hacker News", "ktp": "KTP", "greenhouse": "Greenhouse",
                "lever": "Lever", "ashby": "Ashby", "workable": "Workable", "smartrecruiters": "SmartRecruiters",
                "recruitee": "Recruitee", "personio": "Personio"}
ATS = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "personio", "workday",
       "teamtailor", "bamboohr", "pinpoint")
GOAL = "settings-goal"
# Hidden groups whose rule ids vary by job ("avoid_role" says which role) get one heading.
GROUP_WORDS = {"avoid_role": "Roles you avoid", "role_title": "Not one of the roles you want",
               "years_required": "Asks for more experience than you have",
               "pay_far_under": "Pay well under your lowest", "screen_out": "Hal's AI check said it does not fit",
               "pattern": "A working pattern you did not choose"}
FIXES = {"excluded_employer": ("Edit employers to avoid", GOAL), "training_scheme": ("Change this", GOAL),
         "clearance": ("Change this", GOAL), "too_far": ("Change your places", GOAL),
         "pattern": ("Change your working pattern", GOAL), "avoid_role": ("Edit roles to avoid", GOAL),
         "role_title": ("Change the roles you want", GOAL),
         "students_only": ("Check when your course ends", GOAL), "grad_year": ("Check your finishing year", GOAL),
         "years_required": ("Change your career stage", GOAL),
         "experience_required": ("Change your career stage", GOAL),
         "postgrad_required": ("Change your career stage", GOAL), "pay_far_under": ("Change your lowest pay", GOAL),
         "agency": ("Change how Hal treats agencies", GOAL), "too_old": ("Change \"Ignore adverts older than\"",
                                                                         "settings-sites"),
         "below_min": ("Change the lowest score", "settings-scoring")}


def _int(value: str | None, name: str, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as e:
        raise BadRequest(f"{name} must be a whole number.", field=name) from e


def min_show() -> int:
    """The lowest score Matches shows."""
    return int(settings.get("scoring.min_show") or 0)


def band(score: float) -> str:
    """The score's band words for the card: strong, good or fair."""
    return "strong" if score >= BAND_STRONG else "good" if score >= BAND_GOOD else "fair"


def pay_words(row) -> str:
    """The pay as the card shows it; the pound sign is an escape, so the source stays ASCII."""
    if row["paid"] == 0:
        return "Unpaid"
    lo, hi = row["pay_min"], row["pay_max"]
    if not lo and not hi:
        return "Pay not stated"
    if lo and hi and lo != hi:
        return f"\u00a3{lo:,} to \u00a3{hi:,}"
    return f"\u00a3{(lo or hi):,}"


def _days(day: str | None, today: date) -> int | None:
    try:
        return (date.fromisoformat(day[:10]) - today).days if day else None
    except ValueError:
        return None


def _in_days(n: int | None) -> str:
    if n is None or n < 0:
        return ""
    return "today" if n == 0 else "1 day" if n == 1 else f"{n} days"


def _ago(n: int | None) -> str:
    if n is None:
        return ""
    n = -n
    return "Today" if n <= 0 else "Yesterday" if n == 1 else f"{n} days ago"


def source_words(con: sqlite3.Connection, source: str) -> str:
    """Where Hal found a job, in words."""
    row = con.execute("SELECT name FROM sources WHERE id = ?", (source,)).fetchone()
    return row["name"] if row else SOURCE_WORDS.get(source, source.replace("_", " ").title())


def _eligibility(row) -> dict:
    try:
        return json.loads(row["eligibility"]) if row["eligibility"] else {}
    except ValueError:
        return {}


def eligibility_line(row) -> str | None:
    """Who may apply, in plain words, when the advert says."""
    e = _eligibility(row)
    if not e:
        return None
    if e.get("eligible") is False and e.get("why"):
        return e["why"]
    if e.get("requires") == "graduates_welcome":
        return "Says graduates are welcome"
    if e.get("requires") == "current_students":
        return "For current students"
    return None


def cards(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    """JobCards for job rows, with their top points and requirement counts."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    reasons: dict[str, list[sqlite3.Row]] = {}
    for r in con.execute(f"SELECT * FROM job_reasons WHERE job_id IN ({marks})", ids):
        reasons.setdefault(r["job_id"], []).append(r)
    reqs: dict[str, list[dict]] = {}
    for r in con.execute(f"SELECT job_id, state, essential FROM job_requirements WHERE job_id IN ({marks})", ids):
        reqs.setdefault(r["job_id"], []).append({"s": r["state"],
                                                 "points": job_actions.requirement_points(r["state"], r["essential"])})
    apps = {r[0] for r in con.execute(f"SELECT job_id FROM applications WHERE job_id IN ({marks})", ids)}
    today = clock.local_day()
    out = []
    for row in rows:
        rs = reasons.get(row["id"], [])
        pattern = next((r["note"] for r in rs if r["rule"] == "pattern"), None)
        points = sorted(([r["label"], r["points"]] for r in rs if not r["hide"] and r["points"]),
                        key=lambda p: -abs(p[1]))[:TOP_POINTS]
        rq = reqs.get(row["id"])
        score = job_actions.adjusted(row["score"], rq or [])
        closes_in = _days(row["closes_at"], today)
        first = _days((row["first_seen"] or "")[:10], today)
        out.append({
            "id": row["id"], "title": row["title"], "org": row["company"], "place": row["location"] or "",
            "pattern": PATTERN_WORDS.get(pattern or "", ""), "pay": pay_words(row),
            "pay_min": row["pay_min"], "pay_max": row["pay_max"],
            "closes": row["closes_at"], "closes_in": _in_days(closes_in),
            "posted": _ago(_days(row["posted_at"], today)), "source": source_words(con, row["source"]),
            "platform": row["ats"] or (row["source"] if row["source"] in ATS else None),
            "kind": row["kind"], "kind_label": kinds.LABELS.get(row["kind"] or "", ""),
            "hours": row["hours"], "contract": row["contract"],
            "is_new": row["status"] == "new" and first is not None and -first < NEW_DAYS,
            "saved": bool(row["saved_at"]), "status": row["status"], "score": score, "band": band(score),
            "top_points": points,
            "reqs": {"met": sum(1 for r in rq if r["s"] == "met"), "total": len(rq)} if rq else None,
            "eligibility_line": eligibility_line(row), "application_id": row["id"] if row["id"] in apps else None,
            "hidden_rule": row["hidden_rule"]})
    return out


def _shown_where(tab: str) -> tuple[str, list]:
    base = f"j.active = 1 AND j.hidden_rule IS NULL AND j.status IN ({','.join('?' * len(OPEN))})"
    if tab == "saved":
        return base + " AND j.saved_at IS NOT NULL", list(OPEN)
    return base + " AND j.saved_at IS NULL AND j.score >= ?", [*OPEN, min_show()]


def _filters(where: str, args: list, q: dict) -> tuple[str, list]:
    place = (q.get("place") or "").strip()
    if place:
        if place.lower().startswith("remote"):
            where += " AND (j.location LIKE '%remote%' OR EXISTS (SELECT 1 FROM job_reasons r WHERE r.job_id = j.id"
            where += " AND r.rule = 'pattern' AND r.note = 'remote'))"
        else:
            where += " AND j.location LIKE ?"
            args.append(f"%{place}%")
    pattern = PATTERNS.get(q.get("pattern") or "", q.get("pattern") or "")
    if pattern:
        where += " AND EXISTS (SELECT 1 FROM job_reasons r WHERE r.job_id = j.id AND r.rule = 'pattern' AND r.note = ?)"
        args.append(pattern)
    kind = (q.get("kind") or "").strip()
    if kind:
        where += " AND j.kind = ?"
        args.append(kind)
    return where, args


def kind_filter(con: sqlite3.Connection, goal: goal_mod.Goal) -> list[dict]:
    """The "Kind" filter row: one choice per kind of role in the goal, shown only when there are several."""
    chosen = [k for k in kinds.LABELS if k in goal.kinds and k not in kinds.FLAG_KINDS]
    if len(chosen) < 2:
        return []
    where, args = _shown_where("matches")
    counts = dict(con.execute(f"SELECT j.kind, COUNT(*) FROM jobs j WHERE {where} GROUP BY j.kind", args).fetchall())
    return [{"id": k, "label": kinds.LABELS[k], "n": counts.get(k, 0)} for k in chosen]


def counts(con: sqlite3.Connection) -> dict:
    """The tab counts: Matches, Saved, Hidden and the job sites switched on."""
    out = {}
    for tab in ("matches", "saved"):
        where, args = _shown_where(tab)
        out[tab] = con.execute(f"SELECT COUNT(*) FROM jobs j WHERE {where}", args).fetchone()[0]
    out["hidden"] = sum(g["n"] for g in hidden_groups(con)) + len(yours(con))
    out["sources_on"] = con.execute("SELECT COUNT(*) FROM sources WHERE enabled = 1").fetchone()[0]
    return out


@route("GET", "/api/v2/jobs")
def jobs(req: Request) -> dict:
    """Matches or Saved, sorted and filtered, a page at a time."""
    tab = req.query.get("tab") or "matches"
    if tab not in ("matches", "saved"):
        raise BadRequest("tab is matches or saved.", field="tab")
    sort = req.query.get("sort") or "best"
    if sort not in SORTS:
        raise BadRequest(f"sort is one of {', '.join(SORTS)}.", field="sort")
    offset = _int(req.query.get("cursor"), "cursor")
    where, args = _filters(*_shown_where(tab), req.query)
    rows = req.con.execute(f"SELECT j.* FROM jobs j WHERE {where} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                           [*args, PAGE + 1, offset]).fetchall()
    goal = goal_mod.current()
    places = [p.name for p in goal.places] or sorted({
        (r[0] or "").split(",")[0].strip() for r in req.con.execute(
            f"SELECT j.location FROM jobs j WHERE {_shown_where(tab)[0]} LIMIT 200", _shown_where(tab)[1])} - {""})[:6]
    return {"jobs": cards(req.con, rows[:PAGE]), "counts": counts(req.con),
            "filters": {"places": places, "patterns": list(PATTERNS), "kinds": kind_filter(req.con, goal)},
            "cursor": str(offset + PAGE) if len(rows) > PAGE else None}


def _fix(rule: str) -> dict | None:
    if rule.startswith("kind_not_chosen:"):
        kind = rule.split(":", 1)[1]
        chosen = list(goal_mod.current().kinds)
        return {"label": f"Add {kinds.LABELS.get(kind, kind).lower()} to your goal", "route": GOAL,
                "set": {"key": "goal.kinds", "value": [k for k in kinds.LABELS if k in chosen or k == kind]}}
    if rule == "unpaid":
        return {"label": "Show unpaid roles", "route": GOAL, "set": {"key": "goal.unpaid_ok", "value": True}}
    if rule.startswith("avoid_sector:"):
        return {"label": "Edit sectors to avoid", "route": GOAL}
    found = FIXES.get(rule)
    return {"label": found[0], "route": found[1]} if found else None


def _group_reason(rule: str, labels: list[str]) -> str:
    if len(set(labels)) == 1:
        return labels[0]
    if rule.startswith("kind_not_chosen:") or rule.startswith("avoid_sector:"):
        return labels[0]
    return GROUP_WORDS.get(rule, labels[0])


def _example(row) -> str:
    place = (row["location"] or "").split(",")[0].strip()
    return f"{row['title']}, {place}" if place else row["title"]


def hidden_groups(con: sqlite3.Connection) -> list[dict]:
    """The rule groups of Hidden, largest first: each with its reason, count, examples and fix."""
    rows = con.execute(f"""SELECT j.id, j.title, j.location, j.hidden_rule, r.label, r.data FROM jobs j
                           LEFT JOIN job_reasons r ON r.job_id = j.id AND r.rule = j.hidden_rule
                           WHERE j.active = 1 AND j.hidden_rule IS NOT NULL
                             AND j.status IN ({','.join('?' * len(OPEN))})
                           ORDER BY j.score DESC, j.first_seen DESC""", OPEN).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r["hidden_rule"], {"rule": r["hidden_rule"], "labels": [], "n": 0, "examples": [],
                                                 "why": None})
        g["n"] += 1
        g["labels"].append(r["label"] or r["hidden_rule"])
        if len(g["examples"]) < EXAMPLES:
            g["examples"].append(_example(r))
        if g["why"] is None and r["data"]:
            try:
                g["why"] = json.loads(r["data"]).get("why") or None
            except ValueError:
                pass
    low = min_show()
    below = con.execute(f"""SELECT j.title, j.location FROM jobs j WHERE j.active = 1 AND j.hidden_rule IS NULL
                            AND j.saved_at IS NULL AND j.score < ? AND j.status IN ({','.join('?' * len(OPEN))})
                            ORDER BY j.score DESC""", [low, *OPEN]).fetchall()
    out = [{"rule": g["rule"], "reason": _group_reason(g["rule"], g["labels"]), "why": g["why"], "n": g["n"],
            "examples": g["examples"], "fix": _fix(g["rule"])} for g in groups.values()]
    if below:
        out.append({"rule": "below_min", "reason": f"Scored under {low}, your lowest to show", "why": None,
                    "n": len(below), "examples": [_example(r) for r in below[:EXAMPLES]], "fix": _fix("below_min")})
    return sorted(out, key=lambda g: -g["n"])


def yours(con: sqlite3.Connection) -> list[dict]:
    """Jobs the person hid with "Not for me", newest first, each with the reason they chose."""
    rows = con.execute("""SELECT j.id, j.title, j.company, f.reason, f.words FROM jobs j
                          JOIN job_feedback f ON f.id = (SELECT MAX(id) FROM job_feedback
                                                         WHERE job_id = j.id AND verdict = 'no')
                          WHERE j.status = 'skipped' ORDER BY f.id DESC""").fetchall()
    return [{"id": r["id"], "title": r["title"], "org": r["company"],
             "why": f"You chose: {feedback.reason_words(r['reason'], r['words'])}"} for r in rows]


@route("GET", "/api/v2/jobs/hidden")
def hidden(req: Request) -> dict:
    """Hidden, grouped by the rule that hid each job, and the jobs the person hid themselves."""
    return {"groups": hidden_groups(req.con), "yours": yours(req.con)}


@route("GET", "/api/v2/jobs/hidden/{rule}")
def hidden_group(req: Request) -> dict:
    """One Hidden group's jobs ("Show them"), each with why it is hidden."""
    rule = req.params["rule"]
    offset = _int(req.query.get("cursor"), "cursor")
    marks = ",".join("?" * len(OPEN))
    if rule == "below_min":
        rows = req.con.execute(f"""SELECT j.* FROM jobs j WHERE j.active = 1 AND j.hidden_rule IS NULL
                                   AND j.saved_at IS NULL AND j.score < ? AND j.status IN ({marks})
                                   ORDER BY j.score DESC LIMIT ? OFFSET ?""",
                               [min_show(), *OPEN, PAGE + 1, offset]).fetchall()
        whys = {r["id"]: f"Scored {round(r['score'] or 0)}" for r in rows}
    else:
        rows = req.con.execute(f"""SELECT j.* FROM jobs j WHERE j.active = 1 AND j.hidden_rule = ?
                                   AND j.status IN ({marks}) ORDER BY j.first_seen DESC LIMIT ? OFFSET ?""",
                               [rule, *OPEN, PAGE + 1, offset]).fetchall()
        whys = {r["job_id"]: r["label"] for r in req.con.execute(
            "SELECT job_id, label FROM job_reasons WHERE rule = ? AND hide = 1", (rule,))}
    out = cards(req.con, rows[:PAGE])
    for c in out:
        c["why"] = whys.get(c["id"], "")
    return {"jobs": out, "cursor": str(offset + PAGE) if len(rows) > PAGE else None}


def _row(con: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise NotFound("That job is not in Hal's list.")
    return row


def _flags(row, card: dict) -> list[str]:
    out = []
    if row["closes_at"]:
        out.append(f"Closes on {date.fromisoformat(row['closes_at'][:10]):%A %d %B}")
    if card["platform"]:
        out.append(f"The form is on {SOURCE_WORDS.get(card['platform'], card['platform'].title())}, the "
                   "application system this employer uses")
    if row["hours"] == "part":
        out.append("Part-time")
    if row["contract"] == "fixed":
        out.append("Fixed-term")
    elif row["contract"] == "temp":
        out.append("Temporary")
    if row["paid"] is None:
        out.append("The advert does not say what it pays")
    if row["screen_state"] == "eligible" and row["screen_why"]:
        out.append(f"Hal's AI check: {row['screen_why']}")
    return out


@route("GET", "/api/v2/jobs/{job_id}")
def job(req: Request) -> dict:
    """One job's page: every point and the total, what the advert asks for, and the advert itself."""
    row = _row(req.con, req.params["job_id"])
    card = cards(req.con, [row])[0]
    reasons = req.con.execute("SELECT * FROM job_reasons WHERE job_id = ? ORDER BY rowid", (row["id"],)).fetchall()
    reqs = job_actions.requirements(req.con, row["id"])
    items = [{"rule": r["rule"], "label": r["label"], "points": r["points"], "quote": r["quote"] or ""}
             for r in reasons if not r["hide"] and r["points"]]
    items += [{"rule": f"requirement:{r['n']}", "label": f"{'Missing' if r['s'] == 'miss' else 'Partly met'}: "
               f"{r['text']}", "points": r["points"], "quote": r["quote"]} for r in reqs if r["points"]]
    hidden_by = next((r for r in reasons if r["hide"] and r["rule"] == row["hidden_rule"]), None)
    elig = _eligibility(row)
    mine = feedback.latest(req.con, row["id"]) if row["status"] == "skipped" else None
    card.update({"advert": row["description"] or "", "advert_saved_at": row["first_seen"], "url": row["url"],
                 "apply_url": row["url"], "found_on": card["source"]})
    return {"job": card,
            "points": {"base": rules.current_weights()["base"], "items": items, "total": card["score"]},
            "hidden": {"rule": hidden_by["rule"], "label": hidden_by["label"], "fix": _fix(hidden_by["rule"])}
            if hidden_by else ({"rule": "not_for_me", "label": "You chose: " + feedback.reason_words(
                mine["reason"], mine["words"]), "fix": {"label": "Put it back", "route": None}} if mine else None),
            "reqs": reqs or None, "flags": _flags(row, card), "company": [],
            "eligibility": {"line": eligibility_line(row), "why": elig.get("why") or ""}
            if eligibility_line(row) else None,
            "application_id": card["application_id"]}


def _session(req: Request) -> str | None:
    return req.session.id if req.session else None


def _start(req: Request, kind: str, **kw) -> str:
    """Starts an action for this window; the area modules that define kinds are loaded on first use."""
    runner.load_kinds()
    return runner.start(kind, session_id=_session(req), remote=req.remote, **kw)


@route("POST", "/api/v2/jobs/{job_id}/apply")
def apply(req: Request) -> dict:
    """Apply with Hal: documents come first; no form is opened until the person approves them."""
    row = _row(req.con, req.params["job_id"])
    at = clock.local()
    action_id = _start(req, "docs.prepare", job_id=row["id"], params={"job_id": row["id"]},
                       why=f"You pressed Apply with Hal at {at:%H:%M}. Documents come first.")
    return {"application_id": row["id"], "action_id": action_id}


@route("POST", "/api/v2/jobs/{job_id}/save")
def save(req: Request) -> dict:
    """Saves or unsaves a job."""
    _row(req.con, req.params["job_id"])
    saved = (req.body or {}).get("saved", True)
    return feedback.save(req.con, req.params["job_id"], bool(saved), session_id=_session(req))


@route("POST", "/api/v2/jobs/{job_id}/not-for-me")
def not_for_me(req: Request) -> dict:
    """Hides a job with the person's reason."""
    _row(req.con, req.params["job_id"])
    body = req.body or {}
    return feedback.not_for_me(req.con, req.params["job_id"], body.get("reason"), body.get("words"),
                               session_id=_session(req))


@route("POST", "/api/v2/jobs/{job_id}/put-back")
def put_back(req: Request) -> dict:
    """Brings a hidden job back to Matches."""
    _row(req.con, req.params["job_id"])
    return feedback.put_back(req.con, req.params["job_id"], session_id=_session(req))


@route("POST", "/api/v2/jobs/{job_id}/requirements")
def check_requirements(req: Request) -> dict:
    """Starts checking what the advert asks for against the CV."""
    row = _row(req.con, req.params["job_id"])
    return {"action_id": _start(req, "find.requirements", job_id=row["id"], params={"job_id": row["id"]},
                                subject=f"requirements:{row['id']}")}


@route("POST", "/api/v2/jobs/{job_id}/requirements/{n}/off")
def requirement_off(req: Request) -> dict:
    """"This does not matter to me": the requirement stops counting for this job only."""
    _row(req.con, req.params["job_id"])
    off = (req.body or {}).get("off", True)
    return job_actions.set_requirement_off(req.con, req.params["job_id"], _int(req.params["n"], "n"), bool(off))


@route("POST", "/api/v2/jobs/{job_id}/check")
def check(req: Request) -> dict:
    """Starts checking a job again: still open, and its score under the live goal."""
    row = _row(req.con, req.params["job_id"])
    return {"action_id": _start(req, "find.check_job", job_id=row["id"], params={"job_id": row["id"]},
                                subject=f"check:{row['id']}")}


@route("POST", "/api/v2/jobs/add-advert")
def add_advert(req: Request) -> dict:
    """Starts reading one advert the person pasted; the job id arrives in the action's result."""
    url = ((req.body or {}).get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise BadRequest("Paste the advert's address, starting with https://", field="url")
    return {"action_id": _start(req, "find.add_advert", params={"url": url}, subject=f"advert:{url}")}


def open_count(con: sqlite3.Connection) -> int:
    """How many jobs a re-check covers: every job with an advert."""
    return con.execute("SELECT COUNT(*) FROM jobs WHERE source != 'email'").fetchone()[0]


@route("GET", "/api/v2/jobs/rescore/count")
def rescore_count(req: Request) -> dict:
    """How many jobs "Re-check jobs" would cover."""
    return {"n": open_count(req.con)}


@route("POST", "/api/v2/jobs/rescore")
def rescore(req: Request) -> dict:
    """Re-checks every job against the live goal and points."""
    words = (req.body or {}).get("setting") or "your goal"
    action_id = _start(req, "find.rescore", params={"setting": words}, subject="find.rescore", replace=True,
                       priority=0)
    return {"action_id": action_id, "n": open_count(req.con)}
