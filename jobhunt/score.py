"""Rank jobs 0-100 with the reasons kept alongside: the old interface over `jobhunt.find.rules`.

`score(job) -> (score, score_notes)` and `prefilter(job) -> bool` keep their signatures, so discovery,
rescoring, alert emails and Classic work unchanged. A score of 0 means hidden; the note says which rule hid
it. Every preference now comes from the person's goal and scoring settings instead of constants here.
"""

from __future__ import annotations

from jobhunt.find import goal as goal_mod
from jobhunt.find import rules
from jobhunt.find.goal import company_key  # noqa: F401 - re-exported; adopt.py and others import it here


def score(job: dict) -> tuple[float, str]:
    """The job's score and its comma-joined notes, under the live goal."""
    return rules.as_legacy(rules.score(job))


def prefilter(job: dict) -> bool:
    """The quick check on a listing before its details are fetched.

    It applies the goal's places, avoided roles and, unless the goal includes jobs that ask for experience,
    seniority in the title. A listing with no location passes, because its advert may name one.
    """
    goal = goal_mod.current()
    w = rules.current_weights()
    c = rules.compiled(goal, w)
    title = job.get("title") or ""
    if c.avoid is not None and c.avoid.search(title):
        return False
    if w.role_filter and c.roles is not None and not c.roles.search(title):
        return False
    if "experienced" not in goal.kinds and rules.SENIORITY.search(title):
        return False
    if not goal.places or not job.get("location"):
        return True
    where, _points, _note = rules.place(c, w, job.get("location"), "")
    return where is not None


def __getattr__(name: str):
    # old constants, read live
    if name == "SALARY_FLOOR":
        return goal_mod.current().min_pay
    raise AttributeError(name)
