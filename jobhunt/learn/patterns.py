"""The patterns Hal looks for in the person's choices, each turned into a suggestion.

Three patterns are built:
- "Too far" chosen for `TOO_FAR_MIN` or more jobs in `WINDOW_DAYS`, every one beyond X miles of home ->
  "Lower your distance from <now> to X miles?", with those jobs as the evidence.
- `ROLE_MIN` or more saved or applied-to jobs whose titles share a word that is not in the roles you want ->
  "Add '<word>' to the roles you want?".
- Two "Keep my wording" answers on the same CV line -> "Keep this line exactly as written?" (a lock).
- The person's edits of Hal's drafts (`writing.edits`) -> "Never use <word>?" for a word they took out of
  three drafts, and "Write letters of about N words?" when they cut Hal's letters.

A pattern only ever calls `items.offer`: it never changes a setting, a note or the CV (tested for
every pattern). A waiting suggestion whose pattern no longer holds, because the person changed the setting by
hand or put the jobs back, is withdrawn on the next run.

Not built here (cut 7): site notes and outcomes. Voice examples come from the same edits, in
`writing.voice`, with no suggestion to answer.
"""
from __future__ import annotations

import dataclasses
import logging
import re
import sqlite3
from datetime import timedelta

from jobhunt.core import clock, settings
from jobhunt.learn import items, places

log = logging.getLogger(__name__)

WINDOW_DAYS = 7             # "four or more times in 7 days"
TOO_FAR_MIN = 4
DISTANCE_STEP = 5           # goal.max_distance moves in whole miles; suggestions use round numbers
ROLE_MIN = 5                # "saved or applied to five or more jobs"
KEEP_MIN = 2                # two "Keep my wording" on the same line
EVIDENCE_SHOWN = 6          # evidence lines on a card before "and N more"
LINE_SHOWN = 80             # characters of a CV line quoted on a card
ROLE_WORD_MIN = 4           # shorter title words ("data" is the shortest useful one) say nothing
KEEP_REASON = "Keep my wording"
SENT = ("applied", "assessment", "interview", "offer", "rejected")
OPEN = ("new", "shortlisted", "ready")
# The area of `learn.enabled` (learn/settings.py) each pattern belongs to.
SOURCES = {"too_far": "finding", "roles": "finding", "keep_wording": "cv", "writing_edits": "writing"}
# Words in job titles that name a level, a contract or a job type, never a kind of work.
NOT_ROLE_WORDS = frozenset("""
    officer assistant manager coordinator co-ordinator administrator executive associate specialist advisor
    adviser consultant graduate junior senior trainee entry level scheme programme program intern internship
    internships placement placements apprentice apprenticeship part time full fixed term contract temporary
    permanent remote hybrid team lead head deputy principal chief worker support staff role roles position
    based with from into year years month months week weeks paid unpaid summer spring winter autumn
    the and for job jobs maternity cover london uk england scotland wales
""".split())


def enabled(area: str) -> bool:
    """True when the person lets Hal learn in `area` (`learn.enabled`)."""
    try:
        return area in (settings.get("learn.enabled") or [])
    except KeyError:
        return True     # the setting module is not loaded (a bare library use); learning is on by default


def _since(days: int = WINDOW_DAYS) -> str:
    return clock.iso(clock.now_utc() - timedelta(days=days))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _more(lines: list[dict]) -> list[dict]:
    if len(lines) <= EVIDENCE_SHOWN:
        return lines
    return [*lines[:EVIDENCE_SHOWN], {"label": f"and {len(lines) - EVIDENCE_SHOWN} more"}]


def _home() -> str | None:
    """The person's home town for distances: "Where you live", else their first place."""
    city = (settings.get("you.city") or "").strip()
    if places.town_in(city):
        return city
    first = next(iter(settings.get("goal.places") or []), "")
    return first if places.town_in(first) else None


# ------------------------------------------------------------------------------------------- too far

def too_far(con: sqlite3.Connection) -> list[dict]:
    """The "Lower your distance" suggestion, when the jobs hidden as too far make one (0 or 1 offers)."""
    home = _home()
    if home is None:
        return []
    now_max = int(settings.get("goal.max_distance"))
    rows = con.execute("""SELECT j.id, j.title, j.location, max(f.at) AS at FROM job_feedback f
                          JOIN jobs j ON j.id = f.job_id
                          WHERE f.verdict = 'no' AND f.reason = 'too_far' AND f.at >= ? AND j.status = 'skipped'
                          GROUP BY j.id ORDER BY at DESC""", (_since(),)).fetchall()
    known = [(r, d) for r in rows if (d := places.distance(home, r["location"])) is not None]
    if len(known) < TOO_FAR_MIN:
        return []
    nearest = min(d for _, d in known)
    to = int(nearest // DISTANCE_STEP * DISTANCE_STEP)
    if to >= nearest:
        to -= DISTANCE_STEP     # "beyond X miles": a job at exactly 20 miles is not beyond 20
    low = int(settings.setting("goal.max_distance").min or DISTANCE_STEP)
    if to < low or to >= now_max:
        return []
    evidence = [{"label": f"{r['title']}, {places.display_name(places.town_in(r['location']))} ({round(d)} miles)",
                 "job_id": r["id"]} for r, d in known]
    further = _further_this_week(con, home, to, now_max)
    count = ("None of the jobs" if further == 0 else "1 job" if further == 1 else f"{further} jobs")
    effect = (f"Searches would look within {to} miles of {places.display_name(places.town_in(home))} instead of "
              f"{now_max}. {count} found this week {'was' if further == 1 else 'were'} further away than that.")
    return [{"fingerprint": f"finding:max_distance:{now_max}:lower", "area": "finding",
             "title": f"Lower your distance from {now_max} to {to} miles?",
             "why": f'You chose "Too far" for {_plural(len(known), "job")} this week.', "effect": effect,
             "evidence": _more(evidence), "source": "too_far",
             "change": {"setting": "goal.max_distance", "value": to,
                        "learned": f"Travel up to {to} miles, not {now_max}"}}]


def _further_this_week(con: sqlite3.Connection, home: str, to: int, now_max: int) -> int:
    """Open jobs found this week between `to` and `now_max` miles away: what the smaller distance leaves out."""
    rows = con.execute(f"""SELECT location FROM jobs WHERE first_seen >= ? AND active = 1
                           AND status IN ({",".join("?" * len(OPEN))})""", (_since(), *OPEN)).fetchall()
    return sum(1 for r in rows if (d := places.distance(home, r["location"])) is not None and to < d <= now_max)


# --------------------------------------------------------------------------------------------- roles

def _title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z-]*[a-z]", (title or "").lower())
            if len(w) >= ROLE_WORD_MIN and w not in NOT_ROLE_WORDS}


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > ROLE_WORD_MIN else word


def _covered(word: str, phrases: list[str]) -> bool:
    """True when `word` is already one of the person's role (or avoided) words, singular or plural."""
    stem = _stem(word)
    return any(stem == _stem(w) for p in phrases for w in re.findall(r"[a-z][a-z-]*", str(p).lower()))


def roles(con: sqlite3.Connection) -> list[dict]:
    """"Add '<word>' to the roles you want?" for each word that five or more chosen jobs share."""
    have = list(settings.get("goal.roles") or [])
    if not have:
        return []       # with no roles of their own the preset's apply; one word would replace all of them
    avoid = list(settings.get("goal.avoid_roles") or [])
    rows = con.execute(f"""SELECT id, title, company, saved_at IS NOT NULL AS saved,
                                  status IN ({",".join("?" * len(SENT))}) AS sent
                           FROM jobs WHERE saved_at IS NOT NULL OR status IN ({",".join("?" * len(SENT))})
                           ORDER BY coalesce(saved_at, first_seen) DESC""", (*SENT, *SENT)).fetchall()
    by_word: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        for w in _title_words(r["title"]):
            by_word.setdefault(w, []).append(r)
    out = []
    for word, jobs in sorted(by_word.items()):
        if len(jobs) < ROLE_MIN or _covered(word, have) or _covered(word, avoid):
            continue
        saved, sent = sum(1 for j in jobs if j["saved"]), sum(1 for j in jobs if j["sent"])
        why = (f"You saved {_plural(saved, word + ' job')} and applied to {sent}." if saved and sent else
               f"You saved {_plural(saved, word + ' job')}." if saved else
               f"You applied to {_plural(sent, word + ' job')}.")
        out.append({"fingerprint": f"finding:roles:add:{word}", "area": "finding",
                    "title": f'Add "{word}" to the roles you want?', "why": why,
                    "effect": _role_effect(con, word, [j["id"] for j in jobs]),
                    "evidence": _more([{"label": f"{j['title']}, {j['company']}", "job_id": j["id"]} for j in jobs]),
                    "source": "roles",
                    "change": {"setting": "goal.roles", "add": word,
                               "learned": f'"{word.capitalize()}" is one of the roles you want'}})
    return out


def _role_effect(con: sqlite3.Connection, word: str, job_ids: list[str]) -> str:
    """How much those jobs' scores would rise with the word in the roles, worked out by the scoring rules."""
    from jobhunt.find import goal as goal_mod
    from jobhunt.find import rules

    goal = goal_mod.current()
    more = dataclasses.replace(goal, roles=(*goal.roles, word))
    gains, shown = [], 0
    for job_id in job_ids:
        job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            continue
        now, then = rules.score(dict(job), goal), rules.score(dict(job), more)
        if now.hidden is not None and then.hidden is None:
            shown += 1      # a hidden job scores 0, so its "gain" would be its whole score
        elif now.hidden is None and then.hidden is None:
            gains.append(then.score - now.score)
    gain = round(sum(gains) / len(gains)) if gains else 0
    if shown:
        return f"{word.capitalize()} jobs that Hal hides today would show in your matches."
    if gain > 0:
        return f"{word.capitalize()} jobs would score {_plural(gain, 'point')} higher."
    return f'Jobs with "{word}" in the title would count as roles you want.'


# ------------------------------------------------------------------------------------------ CV lines

def keep_wording(con: sqlite3.Connection) -> list[dict]:
    """"Keep this line exactly as written?" for each CV line the person kept twice."""
    rows = con.execute("""SELECT target, before, decided FROM cv_suggestions
                          WHERE status = 'left' AND reason = ? AND job_id IS NULL ORDER BY decided""",
                       (KEEP_REASON,)).fetchall()
    kept = {r["target"] for r in con.execute(
        "SELECT target FROM cv_preferences WHERE active = 1 AND kind IN ('lock_line', 'keep_wording')")}
    by_target: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        if r["target"] and r["target"] not in kept:
            by_target.setdefault(r["target"], []).append(r)
    out = []
    for target, left in by_target.items():
        if len(left) < KEEP_MIN:
            continue
        line = (left[-1]["before"] or "").strip()
        quoted = line if len(line) <= LINE_SHOWN else line[:LINE_SHOWN].rsplit(" ", 1)[0] + " ..."
        evidence = [{"label": f'Your line: "{quoted}"'}] if quoted else []
        evidence += [{"label": f'Rejected on {items.date_words(r["decided"])}: "{KEEP_REASON}"'} for r in left]
        out.append({"fingerprint": f"cv:lock_line:{target}", "area": "cv",
                    "title": "Keep this line exactly as written?",
                    "why": f"You turned down {_plural(len(left), 'rewrite')} of it.",
                    "effect": "Hal will lock this line on My CV and never suggest changes to it.",
                    "evidence": _more(evidence), "source": "keep_wording",
                    "change": {"cv_lock": target, "text": line,
                               "learned": f'Keep this line as written: "{quoted}"' if quoted
                               else "Keep one line of your CV as written"}})
    return out


def writing_edits(con: sqlite3.Connection) -> list[dict]:
    """"Never use <word>?" and "Write letters of about N words?" from the person's edits of Hal's drafts."""
    from jobhunt.writing import edits

    return edits.learned(con)


PATTERNS = {"too_far": too_far, "roles": roles, "keep_wording": keep_wording, "writing_edits": writing_edits}


def run(con: sqlite3.Connection, only: tuple[str, ...] | None = None) -> dict:
    """Runs the patterns and stores their suggestions; commits. Returns {new: [ids], waiting, withdrawn}."""
    new, withdrawn = [], 0
    for source, pattern in PATTERNS.items():
        if only is not None and source not in only:
            continue
        found = pattern(con) if enabled(SOURCES[source]) else []
        for offer in found:
            item_id, fresh = items.offer(con, **offer)
            if fresh:
                new.append(item_id)
        withdrawn += items.withdraw(con, {o["fingerprint"] for o in found}, source)
    con.commit()
    if new or withdrawn:
        items.changed_event()
    return {"new": new, "waiting": items.waiting_count(con), "withdrawn": withdrawn}


def after_feedback(con: sqlite3.Connection) -> str | None:
    """Runs the "Too far" pattern after a job is hidden; the suggestion's id when one now waits, else None.

    For `POST /jobs/{id}/not-for-me`, whose reply carries `suggestion_id`.
    """
    run(con, only=("too_far",))
    row = con.execute("""SELECT id FROM learned_items WHERE status = 'waiting' AND source = 'too_far'
                         ORDER BY created DESC LIMIT 1""").fetchone()
    return row["id"] if row else None
