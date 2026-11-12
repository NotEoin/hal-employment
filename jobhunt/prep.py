"""Interview and assessment preparation notes for one application, drafted locally.

Reads the advert, the pack's fit map, the CV facts and the employment record; writes Markdown into the
applications table. The notes prepare the person for the stage; they are not answers
to send on their behalf. The prompt (`ai/prompts/prep.txt`) names no one: `cvstudio.context.build` fills it
with the person and the CV type's `prep_topics`, and the tests the advert names go in the request.
"""
from . import cv, db, documents, llm, pack, record
from .writing import stage as person_stage

PREP_TIMEOUT = 600   # seconds; the notes are long, and a local model on a busy graphics card is slow
ADVERT_CHARS = 8000  # what fits a local model's context beside the instructions and the evidence


def request(job, who: dict) -> str:
    """The text the prep prompt is asked about: stage, advert, tests it names, fit map and evidence."""
    s = documents.summary(job)
    fit = "\n".join(f"- {m['requirement']}: {m['evidence']} ({m['strength']})" for m in s["fit"])
    stage = job["status"] if job["status"] in ("assessment", "interview", "offer") else "first interview"
    # Stories come from everything recorded about each job and project, not only the CV's lines.
    kept = record.context([job["title"], *(m["requirement"] for m in s["fit"])], per_query=2)
    # with no record, "Employment record: none" read to Claude as a record that lacked a job on the CV, and
    # the notes warned the person about it (made-up humanities run, 2026-10-23); so an empty record is left out
    return (f"Application stage: {stage}\n{person_stage.today_lines()}\nJob: {job['title']} at {job['company']}\n"
            f"Tests the advert names: {who['test_kinds_line']}\n\n"
            f"Advert:\n{(job['description'] or '')[:ADVERT_CHARS]}\n\nFit map:\n{fit}\n"
            f"Gaps: {s['gaps']}\n\nCV facts:\n{cv.facts_text()}"
            + (f"\n\nEmployment record ({record.OWN_DOCUMENTS}; fuller than the CV):\n{kept}" if kept else ""))


def build(con, job) -> str:
    """Drafts the preparation notes for a job, stores them, and returns them."""
    who = pack.person(job)
    notes = pack.spelt(llm.chat(pack.prompt("prep", who), request(job, who), role="main",
                                timeout=PREP_TIMEOUT, purpose="prep").strip(), who)
    con.execute("""INSERT INTO applications (job_id, stage, prep, updated) VALUES (?, 'submitted', ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET prep = excluded.prep, updated = excluded.updated""",
                (job["id"], notes, db.now()))
    con.commit()
    return notes
