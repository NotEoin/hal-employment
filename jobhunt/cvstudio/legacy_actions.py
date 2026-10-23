"""The legacy interface's CV actions: the base CV preview, its review, matching to a job and rebuilding.

Moved from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface. `BASE_CV`
and `REVIEW` are resolved when used, so the folders follow the data folder and the test's home.
Adding a source for the CV builder is the action kind `cv.source`, so Classic's request never
holds a slow file read. Importing this module also registers the data task `cv.import_v1`, whose module
name the data-task loader does not look for.
"""
import base64
import binascii
import json
from pathlib import Path

from .. import cv, cvbuild, cvscore, db, documents
from ..apply.queries import _job
from ..core import naming, paths, runner
from ..core.runner import _say
from . import import_v1  # noqa: F401 - registers the data task cv.import_v1


def base_cv_dir() -> Path:
    """Where the base CV's preview is drawn: the cache, which the interface may show."""
    return paths.cache_dir() / "base-cv"


def review_path() -> Path:
    """The model's last read of the base CV, beside the other CV files."""
    return cv._live("DATA") / ("cv-review.json" if paths.is_legacy_layout() else "review.json")


_LIVE = {"BASE_CV": base_cv_dir, "REVIEW": review_path}


def __getattr__(name):  # old constants, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    raise AttributeError(name)


def base_cv() -> dict:
    """The base CV for the builder: as Hal has it, as it starts, and its preview."""
    folder = base_cv_dir()
    pdf = folder / naming.file_name("cv")
    if not pdf.exists():
        pdf = cv.render(cv.load(), folder)
    built = json.loads(cv.BUILT.read_text()) if cv.BUILT.exists() else None
    review = json.loads(review_path().read_text()) if review_path().exists() else None
    if review:
        review["stale"] = review.get("of") != cvscore.fingerprint(cv.load())
    return {"score": cvscore.score(cv.load(), pdf), "review": review, "cv": cv.for_editing(),
            "site": cv.origin(), "origin": cv.origin_name(),
            "changes": len(cv.overrides()), "pdf": str(pdf), "sources": cvbuild.sources(),
            "options": {**cvbuild.OPTIONS, **((built or {}).get("options") or {})},
            "built": built and {"at": built["built"], "flags": built["flags"],
                                "pdf": str(cvbuild.PREVIEW / naming.file_name("cv"))}}


def act_review_cv(con, _job_id: str) -> str:
    """Has the local model read the base CV as a recruiter would."""
    base = cv.load()
    out = cvscore.review(cv.facts_text_of(base))
    if out["note"]:
        raise RuntimeError(out["note"])
    review_path().parent.mkdir(parents=True, exist_ok=True)
    review_path().write_text(json.dumps({**out, "at": db.now(), "of": cvscore.fingerprint(base)}) + "\n")
    return f"reviewed: {out['average']} out of 5 on average"


def _match_path(job) -> Path:
    return documents.pack_dir(job) / "match.json"


def act_match(con, job_id: str) -> str:
    """Checks this application's CV against its advert: requirements met, and skills named."""
    job = _job(con, job_id)
    docs = documents.load(con, job)
    mine = documents.current_cv(job, docs)
    out = cvscore.match(job, cv.facts_text_of(mine), docs.get("letter") or "")
    if out["note"] and out["coverage"] is None:
        raise RuntimeError(out["note"])
    path = _match_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**out, "at": db.now()}) + "\n")
    return f"{out['label']}: {out['coverage']}% of what the advert asks for"


def act_build_cv(con, _job_id: str, options: dict) -> str:
    """Builds a draft CV from the applicant's material, for them to read before using it."""
    out = cvbuild.build(options, lambda message: _say("_cvbuild", message))
    n = len(out["flags"])
    return f"your CV is built; {n} thing{'s' if n != 1 else ''} to check" if n else "your CV is built"


def act_rerender_cvs(con, _job_id: str) -> str:
    """Draws again the CVs of applications still to be sent, from the base CV as it now stands."""
    rows = con.execute("SELECT * FROM jobs WHERE status IN ('ready', 'applying')").fetchall()
    made = 0
    for job in rows:
        docs = documents.load(con, job)
        if docs.get("cv"):
            continue  # an application's own edit of its CV stands
        cv.render(documents.tailored(job, docs), documents.pack_dir(job))
        made += 1
    return f"{made} application CVs made again from your base CV"


def _source_example(world) -> dict:
    return {"name": "volunteering notes", "text": "Made-up notes: ran a weekly reading club for six months."}


@runner.action("cv.source", area="cv", label="Adding a source for your CV",
               why="You added {name} for Hal to build your CV from", priority=0, exclusive="none",
               example=_source_example)
def add_source(ctx, name: str = "notes", text: str = "", data: str = "") -> dict:
    """Keeps a pasted note or a file (base64) as a source for the CV builder; returns today's Classic shape."""
    try:
        raw = base64.b64decode(data, validate=True) if data else None
    except (binascii.Error, ValueError) as e:
        raise ValueError("That file did not arrive whole. Add it again.") from e
    ctx.step(f"Reading {name}")
    added = cvbuild.add(name or "notes", text or "", raw)
    return {"name": added, "sources": cvbuild.sources()}
