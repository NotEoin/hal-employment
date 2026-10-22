"""Interview and assessment preparation notes for one application, drafted locally.

Reads the advert, the pack's fit map, the CV facts and the employment record; writes Markdown into the
applications table. The notes prepare the person for the stage; they are not answers
to send on their behalf. The prompt (`ai/prompts/prep.txt`) names no one; `pack.person()` fills it in.
"""
from . import cv, db, documents, llm, pack, record


def build(con, job) -> str:
    """Drafts the preparation notes for a job, stores them, and returns them."""
    s = documents.summary(job)
    fit = "\n".join(f"- {m['requirement']}: {m['evidence']} ({m['strength']})" for m in s["fit"])
    stage = job["status"] if job["status"] in ("assessment", "interview", "offer") else "first interview"
    # Stories come from everything recorded about each job and project, not only the CV's lines.
    kept = record.context([job["title"], *(m["requirement"] for m in s["fit"])], per_query=2)
    notes = llm.chat(pack.prompt("prep"), f"Stage: {stage}\nJob: {job['title']} at {job['company']}\n\n"
                           f"Advert:\n{(job['description'] or '')[:8000]}\n\nFit map:\n{fit}\n"
                           f"Gaps: {s['gaps']}\n\nCV facts:\n{cv.facts_text()}"
                           f"\n\nEmployment record (true; fuller than the CV):\n{kept or 'none'}",
                     role="main", timeout=600).strip()
    con.execute("""INSERT INTO applications (job_id, stage, prep, updated) VALUES (?, 'submitted', ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET prep = excluded.prep, updated = excluded.updated""",
                (job["id"], notes, db.now()))
    con.commit()
    return notes
