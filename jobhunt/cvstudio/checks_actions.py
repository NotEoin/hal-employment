"""The `cv.recruiter` action: a recruiter's read of the CV by the AI.

The route passes the CV's text with the version, type and look, so the action needs only the AI and the
`cv_checks` table to keep the answer in. Without an AI the action still ends, saying why there is no read;
the page keeps showing the last read of that version, if any.
"""
from __future__ import annotations

import json

from jobhunt import ai, db
from jobhunt.core import runner
from jobhunt.cvstudio import checks
from jobhunt.cvstudio import types as cvtypes

EXAMPLE_TEXT = ("Wren Aldersey\nwren.aldersey@example.org | 07700 900477\nPROFILE\nHistory graduate who catalogued "
                "a parish archive of 400 letters.\nEDUCATION\nBA (Hons) History | University of Nowhere Fen | "
                "2023 - 2026\nDissertation on canal trade in the 1840s.")


def _example(world) -> dict:
    return {"version_id": "v-example", "type_id": "humanities_grad", "look_id": "classic", "text": EXAMPLE_TEXT}


def save_read(version_id: str, look_id: str, read: dict) -> None:
    """Keeps a recruiter's read with the version and look it was made for."""
    con = db.connect()
    try:
        now = db.now()
        con.execute("INSERT INTO cv_checks (version_id, look_id, recruiter, recruiter_at, at) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT (version_id, look_id) DO UPDATE SET recruiter = excluded.recruiter,"
                    " recruiter_at = excluded.recruiter_at",
                    (version_id, look_id, json.dumps(read), now, now))
        con.commit()
    finally:
        con.close()


@runner.action("cv.recruiter", area="cv", label="Reading your CV as a recruiter would",
               why="You asked for a recruiter's read", priority=0, exclusive="kind", example=_example)
def recruiter(ctx, version_id: str, type_id: str, look_id: str, text: str) -> dict:
    """Asks the AI for its opinion of the CV as a recruiter for the people the type fits."""
    t = cvtypes.load(type_id)
    ctx.step(f"Reading your CV as a recruiter for {t.fits[:1].lower()}{t.fits[1:]}")
    ctx.used("cv", "Your CV as it is now", version_id)
    try:
        read = checks.recruiter_read(text, t)
    except ai.LLMUnavailable as e:
        ctx.step(f"No recruiter's read this time: {e}")
        return {"ok": False, "why": str(e)}
    save_read(version_id, look_id, read)
    ctx.changed("Saved a recruiter's read of your CV. It is Hal's opinion, not part of any score.")
    return {"ok": True}
