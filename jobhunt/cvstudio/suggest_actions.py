"""The suggestion action kinds: `cv.suggest` (Look again), `cv.another` (Try another way) and `cv.fresh`.

Each may wait on the AI, so the routes start them and return at once; the Suggestions tab
reloads on the `suggestions` event. They are priority 1 (documents for jobs you chose), and only
one review of the CV runs at a time, whoever asked for it. The G9 examples make a small CV for a made-up
person first, because the made-up world starts without one.

The CV store and model are imported at the top on purpose: where they are missing this module does not
load, so its kinds are not offered half-working.
"""
from __future__ import annotations

from jobhunt.core import runner
from jobhunt.cvstudio import fresh, store, suggest

EXAMPLE_ANSWERS = {
    "education": [{"qualification": "BA (Hons) History", "org": "University of Nowhere Fen", "start": "2023",
                   "end": "2026", "grade": "2:1", "dissertation": "Parish records of an invented village"}],
    "volunteering": [{"role": "Archive Volunteer", "org": "Fenmoor Records Office", "start": "2024",
                      "end": "Present", "did": ["Catalogued 120 parish letters for the public search"]}],
    "skills": [{"group": "Research", "items": ["archival research", "Excel"]}],
}


def _example_cv(world) -> None:
    doc = fresh.build(EXAMPLE_ANSWERS, suggest.cv_type())
    doc.sections[0].text += " An organized and hard-working researcher."
    store.new_version(doc, origin="generated", what="A made-up CV for the action examples", by="hal")


def _suggest_example(world) -> dict:
    _example_cv(world)
    return {}


def _another_example(world) -> dict:
    from jobhunt import db

    _example_cv(world)
    con = db.connect()
    try:
        suggest.look_again(con, use_ai=False)
        row = con.execute("SELECT id FROM cv_suggestions WHERE status = 'pending' ORDER BY rowid LIMIT 1").fetchone()
    finally:
        con.close()
    return {"suggestion_id": row["id"]}


def _fresh_example(world) -> dict:
    return {"answers": EXAMPLE_ANSWERS, "type_id": "humanities_grad"}


@runner.action("cv.suggest", area="cv", label="Looking at your CV for suggestions",
               why="Your CV, its type or your goal changed", priority=1, exclusive="kind", example=_suggest_example)
def look_again(ctx) -> dict:
    """Reviews the CV section by section until six suggestions wait or none are left."""
    ctx.step("Reading your CV one section at a time")
    out = suggest.look_again(ctx.con, step=ctx.step)
    if out["found"]:
        ctx.changed(f"Found {out['found']} new suggestions.")
    else:
        ctx.step(suggest.ALL_REVIEWED if out["state"] == "nothing_more" else "Nothing new this time")
    return out


@runner.action("cv.another", area="cv", label="Finding another way to say it", why="You pressed Try another way",
               priority=1, exclusive="none", example=_another_example)
def another(ctx, suggestion_id: str) -> dict:
    """Asks for new wording for one suggestion."""
    ctx.step("Finding another way to say it")
    out = suggest.another(ctx.con, suggestion_id)
    if not out["new"]:
        ctx.step(out["message"])
    return out


@runner.action("cv.fresh", area="cv", label="Building a fresh CV", why="You asked Hal to build a fresh CV",
               priority=1, exclusive="kind", example=_fresh_example)
def build_fresh(ctx, answers: dict, type_id: str) -> dict:
    """Writes a CV from the interview answers, checks it and suggests fixes for what fails."""
    out = fresh.make(answers, type_id, step=ctx.step)
    ctx.changed(f"Saved a fresh CV as a new version; {len(out['failing'])} checks to look at.")
    return out
