"""Graduate-eligibility screen: the local model reads each advert in full.

Keyword rules catch stated years and seniority; this catches the rest, such as
requirements that only make sense for someone who has held the job before.
A failed screen sets the job's score to 0 with the model's reason.
"""
from . import llm

SCREEN = """Decide whether a new graduate could realistically be hired for this job. The
candidate has a recent degree, strong personal and
academic projects, and no professional software experience. Reply with JSON only:
{"eligible": true | false, "reason": one short sentence}
Answer false if the advert requires professional experience, a postgraduate degree,
seniority, or skills only gained in industry. Answer true for graduate, junior and
entry-level roles, and for roles that state that projects or academic work count."""


def screen(con, min_score: float = 50, limit: int = 60) -> list[tuple[str, bool, str]]:
    """Screens unreviewed jobs at or above `min_score`; returns (id, eligible, reason)."""
    rows = con.execute("""SELECT id, company, title, description, score_notes FROM jobs
                          WHERE status = 'new' AND active = 1 AND score >= ?
                          AND COALESCE(score_notes, '') NOT LIKE '%screened%'
                          ORDER BY score DESC LIMIT ?""", (min_score, limit)).fetchall()
    results = []
    for r in rows:
        eligible, reason = screen_one(con, r)
        results.append((r["id"], eligible, reason))
    return results


def screen_one(con, job) -> tuple[bool, str]:
    """Screens one job row, records the verdict, and returns (eligible, reason)."""
    verdict = llm.chat_json(SCREEN, f"{job['title']} at {job['company']}\n\n{(job['description'] or '')[:8000]}",
                            role="main", timeout=180)
    eligible = verdict.get("eligible") is True
    reason = str(verdict.get("reason", ""))[:160]
    if eligible:
        con.execute("UPDATE jobs SET score_notes = COALESCE(score_notes, '') || ', screened eligible' WHERE id = ?",
                    (job["id"],))
    else:
        con.execute("UPDATE jobs SET score = 0, score_notes = ? WHERE id = ?", (f"screened out: {reason}", job["id"]))
    con.commit()
    return eligible, reason
