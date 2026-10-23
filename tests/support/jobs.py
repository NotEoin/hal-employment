"""Made-up goals and adverts for the scoring tests.

`TODAYS_GOAL` stands in for the owner's imported goal in the compatibility test: a technical new graduate
whose first place is the invented town of Northby and whose second is London, who wants an office, avoids
every built-in sector and has the old skill weights. `TODAYS_SCORES` are the scores and notes the code at
ef53088 gave the world's made-up jobs (tests/goldens/score.json), with the old first
place written as Northby, so no real home area is named here.

`advert()` makes one made-up advert with sensible defaults, so each test states only what it is about.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

# The old skill weights, as `scoring.skills` lines.
OLD_SKILLS = [
    r"re:\bpython\b = 3", r"re:\bpytorch\b = 3", r"re:\bnumpy\b = 2", r"re:\bmachine learning\b = 2",
    r"re:\bcomputer vision\b = 3", r"re:\b(llm|rag|retrieval)\b = 3", r"re:\bmcp\b = 2",
    r"re:\bdistributed systems?\b = 3", r"re:\bconcurren(cy|t)\b = 2", r"re:\bcryptograph = 3",
    r"re:\b(security|secure)\b = 2", r"re:\bnetwork(ing)?\b = 1", r"re:\bjava\b = 2", r"re:\btypescript\b = 2",
    r"re:\breact\b = 1", r"re:\bfastapi|flask\b = 2", r"re:\bsql(ite)?\b|\bpostgres = 1", r"re:\blinux\b = 2",
    r"re:\bc\+\+\b|\b(in|and|or) c\b = 2", r"re:\bgo(lang)?\b = 1", r"re:\brest(ful)? api = 1",
    r"re:\bembedded|real[- ]time\b = 2", r"re:\brobotic|autonomous = 2", r"re:\bresearch\b = 1",
    r"re:\bpytest|testing\b = 1", r"re:\bgit\b = 1", r"re:\bmedical imaging|healthcare\b = 2",
]

TODAYS_GOAL = {
    "goal.stage": "new_grad",
    "goal.study_status": "finished",
    "goal.study_end": "2026",
    "goal.kinds": ["graduate_job", "graduate_scheme", "internship", "fixed_term"],
    "goal.places": ["Northby", "London"],
    "goal.place_aliases": ["Northby: Northby Quay, Eastholm, Westholm", "London: City of London, Canary Wharf, "
                           "Shoreditch, Kings Cross, King Cross"],
    "goal.pattern": "office",
    "goal.min_pay": 30000,
    "goal.avoid_sectors": ["gambling", "tobacco", "arms and defence", "adult", "payday loans",
                           "multi-level marketing", "firearms"],
    "goal.avoid_employers": ["# Always", "Brightwater Bets Ltd"],
    "goal.training_ok_places": ["Northby"],
    "scoring.preset": "technical_grad",
    "scoring.skills": OLD_SKILLS,
}

INTERN_NOTES = "London, graduate internship, entry-level title, skills +3, small (12 roles open)"
GRAD_NOTES = "London, hybrid, entry-level title, skills +5, small (12 roles open)"
TODAYS_SCORES = {
    "greenhouse:cobalt-interview": [80, GRAD_NOTES],
    "greenhouse:fernhill-grad": [80, GRAD_NOTES],
    "greenhouse:gilt-closed": [80, GRAD_NOTES],
    "greenhouse:larchmont-grad": [0, "location: Edinburgh"],
    "greenhouse:ostrava-senior": [0, "too senior"],
    "greenhouse:quillon-intern": [71, INTERN_NOTES],
    "greenhouse:wexford-ready": [0, "location: Cambridge"],
    "hn:brackenridge-data": [0, "location: Remote (UK)"],
    "lever:ambleside-applied": [80, GRAD_NOTES],
    "lever:dunmore-rejected": [0, "location: Manchester"],
    "lever:halberd-defence": [0, "needs existing security clearance"],
    "lever:marrowby-placement": [0, "internship/placement/apprenticeship/PhD"],
    "lever:sorrel-ready": [80, GRAD_NOTES],
    "lever:tidewater-grad": [0, "location: Leeds"],
    "workable:heron-assessment": [80, GRAD_NOTES],
    "workable:kestrel-bond": [0, "train-and-place outside Northby"],
    "workable:pennant-sales": [0, "not an engineering role: Graduate Sales Executive"],
}
# The one intended change: a paid internship the goal includes no longer loses 10 points.
INTERNSHIP_PENALTY = 10


def use(values: dict) -> None:
    """Stores `values` as the person's settings, as setup or the importer would."""
    from jobhunt.core import settings

    settings.set_many(values, by="setup")


@contextlib.contextmanager
def goal(values: dict) -> Iterator[None]:
    """Stores `values` for the block, then puts every key back to its default."""
    from jobhunt.core import settings
    from jobhunt.core.settings import store

    store.drop_cache()      # the cache is per process; another world's values may still be in it
    use(values)
    try:
        yield
    finally:
        for key in values:
            settings.reset(key, by="setup")
        store.drop_cache()


def advert(title: str, text: str = "", **fields) -> dict:
    """One made-up advert in the shape a source returns."""
    job = {"id": f"test:{abs(hash((title, text))) % 10**8}", "source": "greenhouse", "company": "Lanternfield Ltd",
           "title": title, "location": "London", "url": "https://jobs.example.org/a", "description": text,
           "salary": None, "posted_at": None, "board_size": 30}
    job.update(fields)
    return job
