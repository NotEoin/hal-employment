"""The AI check on a new match: does it fit this person? (prompt `screen`).

Keyword rules catch stated years, seniority and students-only wording; this catches the rest, such as
requirements that only make sense for someone who has held the job before. The prompt is filled from the
goal (stage, kinds, roles, course end), never from a hard-coded person. The verdict goes to `screen_state`
and `screen_why`; a job that does not fit is hidden by the `screen_out` reason and scores 0, as before.
"""
from __future__ import annotations

import json

from . import llm
from .ai import prompts
from .find import goal as goal_mod

ADVERT_CHARS = 8000        # what fits the local model's context with the instructions
REASON_CHARS = 160
SCREEN_TIMEOUT = 180       # seconds; a local model on a busy GPU can be slow
STAGE_LINES = {"student": "a student", "new_grad": "a new graduate", "early": "early in their career",
               "experienced": "an experienced worker", "changing": "changing career",
               "school_leaver": "a school leaver"}


def values(goal: goal_mod.Goal, job) -> dict:
    """The prompt's placeholders for one job under `goal`."""
    end = goal.finished_year()
    if goal.is_student():
        status = f"They are studying now; the course ends in {end}" if end else "They are studying now"
    elif goal.study_status == "none":
        status = "They did not go to university"
    else:
        status = f"They finished their course in {end}" if end else "They have finished studying"
    years = goal.experience_years
    raw = job["eligibility"] if "eligibility" in job.keys() else None
    elig = json.loads(raw) if raw else {}
    return {
        "career_stage": STAGE_LINES.get(goal.stage, goal.stage),
        "status_line": status,
        "kinds": goal_mod.kinds_words(goal),
        "target_roles": ", ".join(goal.roles) or "any",
        "experience_line": f"They have {years} years of work experience" if years else "They have no work experience",
        "eligibility_line": elig.get("quote") or "nothing",
    }


def screen(con, min_score: float = 50, limit: int = 60) -> list[tuple[str, bool, str]]:
    """Screens unchecked shown jobs at or above `min_score`; returns (id, eligible, reason)."""
    rows = con.execute("""SELECT * FROM jobs
                          WHERE status = 'new' AND active = 1 AND score >= ? AND hidden_rule IS NULL
                          AND COALESCE(screen_state, 'unscreened') = 'unscreened'
                          AND COALESCE(score_notes, '') NOT LIKE '%screened%'
                          ORDER BY score DESC LIMIT ?""", (min_score, limit)).fetchall()
    return [(r["id"], *screen_one(con, r)) for r in rows]


def screen_one(con, job) -> tuple[bool, str]:
    """Screens one job row, records the verdict, and returns (eligible, reason)."""
    goal = goal_mod.current()
    system = prompts.text("screen", con=con, **values(goal, job))
    advert = (job["description"] or "")[:ADVERT_CHARS]
    verdict = llm.chat_json(system, f"{job['title']} at {job['company']}\n\n{advert}", role="main",
                            timeout=SCREEN_TIMEOUT)
    eligible = verdict.get("eligible") is True
    reason = str(verdict.get("reason", ""))[:REASON_CHARS]
    if eligible:
        con.execute("""UPDATE jobs SET screen_state = 'eligible', screen_why = ?,
                       score_notes = COALESCE(score_notes, '') || ', screened eligible' WHERE id = ?""",
                    (reason, job["id"]))
    else:
        con.execute("""UPDATE jobs SET score = 0, score_notes = ?, screen_state = 'out', screen_why = ?,
                       hidden_rule = 'screen_out' WHERE id = ?""", (f"screened out: {reason}", reason, job["id"]))
        con.execute("""INSERT OR REPLACE INTO job_reasons (job_id, rule, stage, points, hide, label, note)
                       VALUES (?, 'screen_out', 'screen', 0, 1, ?, ?)""",
                    (job["id"], f"Hal's AI check: {reason}", f"screened out: {reason}"))
    con.commit()
    return eligible, reason

