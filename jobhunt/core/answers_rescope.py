"""The data task `answers.rescope`: decides where each saved answer copied by migration 0007 may be reused.

Migration 0007 copies today's global saved answers into `answers2` with scope `review`, because which ones
are safe to reuse depends on the person's own profile, which is only in settings once an importer has run.
This task classifies those rows against the live `you.*` values: answers that are the same for every job
become `everywhere`, answers worked out per job or per site stay `review` as `derived`, essays and "why us"
answers become `never` (kept only as voice examples), and anything that names the employer or disagrees with
the profile waits for the person's choice.

Design notes:
- The label rules are a frozen copy of the old form filler's label patterns (RULES, ABOUT_THIS_JOB,
  WHY, FIT) and essay length, so a later change to the live form filler
  never changes what this one-off task did.
- Only rows still in scope `review` with source `import` are touched, so a choice the person already made,
  or a row Hal learned later, is never overwritten, and running the task twice changes nothing.
- Profile values are read from the connection the task was given, not through `settings.get`, because the
  importer runs this against its new database before that database is the live one.
- Values are compared, never logged: the task's step says only how many rows went where.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from typing import Any

from jobhunt.core.db import tasks

log = logging.getLogger(__name__)

NAME = "answers.rescope"
AFTER = 7                  # migration 0007 makes `answers2`
TITLE = "Sorted your saved answers by where they apply"
ESSAY_CHARS = 400          # a longer answer is an essay for one employer

# (label pattern, profile key) in L RULES order; the first match names the key, as the old filler decided.
RULES: tuple[tuple[str, str], ...] = (
    (r"\bpreferred (first )?name\b", "preferred_name"),
    (r"\bmiddle name", "middle_name"),
    (r"\bfirst name|given name|forename", "first_name"),
    (r"\blast name|surname|family name", "last_name"),
    (r"^(full )?(legal )?name\b|^your name$|full name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|telephone", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal (site|url|page)|other links?", "website"),
    (r"sponsor", "sponsorship"),
    (r"right to work|legally (entitled|authori[sz]ed|eligible|able)|eligib\w+ to work|authori[sz]ed to work|"
     r"work permit|immigration status|visa status|work status", "right_to_work"),
    (r"notice period", "notice"),
    (r"start date|when (can|could) you start|earliest (start|availability)|available to start", "start"),
    (r"salary|compensation|pay expectation|remuneration", "salary"),
    (r"how did you (hear|find|learn)|where did you (hear|find|see|learn)|referr?al source|^source$", "how_heard"),
    (r"\boffice\b|on-?site|in person|hybrid|commut", "office"),
    (r"relocat", "relocate"),
    (r"post ?code|\bzip\b", "postcode"),
    (r"address( line)? ?1|street address|^address:?$", "address1"),
    (r"^country|country of residence|country/region", "country"),
    (r"^(town|city)\b|town ?/ ?city", "city"),
    (r"\bcity\b|where are you (based|located)|current location|^location", "location"),
    (r"nationality|citizenship", "nationality"),
    (r"universit|institution|college|\bschool\b", "university"),
    (r"degree|discipline|\bmajor\b|field of study|subject", "degree"),
    (r"grade|classification|\bgpa\b|\bresults?\b", "grade"),
    (r"graduat", "grad_year"),
)
DERIVED = ("salary", "how_heard")                        # worked out for each job or site, never reused as is
PROFILE = {"start": "you.start", "notice": "you.notice", "office": "you.office", "relocate": "you.relocate"}
WHY = re.compile(r"why (do you want|are you (interested|applying)|this (role|company|team)|us\b|join)|"
                 r"what (interests|excites|attracts) you|motivat", re.I)
FIT = re.compile(r"why (are you|would you be) (a good|the right|suited)|what makes you|tell us about yourself|"
                 r"relevant experience|why should we", re.I)
INTEREST = re.compile(r"\binterest(ed)? in\b", re.I)    # as the old classifier's `why|interest(ed)? in|motivat`
EMPLOYER = re.compile(r"\bthe employer\b", re.I)          # the old form filler put this for an employer's name
SOON = re.compile(r"immediate|asap|\bnow\b|straight away|right away|at once|no notice", re.I)
YES = ("yes", "y", "true")
NO = ("no", "n", "false")


def label_key(label: str) -> str | None:
    """The profile key the old form filler would have answered this label with, or None."""
    for pattern, key in RULES:
        if re.search(pattern, label or "", re.I):
            return key
    return None


def _text(answer: Any) -> str:
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    if isinstance(answer, list):
        return ", ".join(str(a) for a in answer)
    return str(answer or "")


def _yes_no(answer: Any) -> bool | None:
    words = _text(answer).strip().lower().rstrip(".")
    if words in YES:
        return True
    if words in NO:
        return False
    return None


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def matches_profile(key: str, answer: Any, profile: dict[str, Any]) -> bool:
    """True when a saved answer says the same as the person's profile value for `key`."""
    value = profile.get(PROFILE[key])
    if key in ("office", "relocate"):
        return _yes_no(answer) is not None and _yes_no(answer) == bool(value)
    if key == "start":
        return value == "now" and bool(SOON.search(_text(answer)))
    said, held = _words(_text(answer)), _words(_text(value))
    if said == held:
        return True
    # "None", "No notice" and "available immediately" all mean the same notice period
    return bool(SOON.search(said) or said in ("none", "0")) and bool(SOON.search(held) or held.startswith("none"))


def classify(label: str, answer: Any, profile: dict[str, Any]) -> tuple[str, str]:
    """(scope, kind) for one saved answer, by the rules in this file."""
    key = label_key(label)
    if key in DERIVED:
        return "review", "derived"
    if key in PROFILE:
        return ("everywhere" if matches_profile(key, answer, profile) else "review"), "fixed"
    if WHY.search(label) or FIT.search(label) or INTEREST.search(label) or len(_text(answer)) > ESSAY_CHARS:
        return "never", "fixed"
    if EMPLOYER.search(label):
        return "review", "fixed"
    return "everywhere", "fixed"


def profile_values(con: sqlite3.Connection) -> dict[str, Any]:
    """The `you.*` values the classification needs, read from this database's settings (or their defaults)."""
    from jobhunt.core import settings
    from jobhunt.core.settings import store

    stored = store.read_all(con)
    out = {}
    for key in PROFILE.values():
        item = settings.setting(key)
        out[key] = stored[key] if key in stored else item.default
    return out


def rescope(con: sqlite3.Connection) -> Counter:
    """Classifies every imported row still waiting in `review`; returns how many went to each scope."""
    profile = profile_values(con)
    moved: Counter = Counter()
    rows = con.execute("SELECT id, label, answer FROM answers2 WHERE scope = 'review' AND source = 'import'"
                       ).fetchall()
    with con:
        for row_id, label, answer in rows:
            try:
                value = json.loads(answer)
            except (TypeError, ValueError):
                value = answer
            scope, kind = classify(label or "", value, profile)
            moved[scope] += 1
            if scope == "review" and kind == "fixed":
                continue
            con.execute("UPDATE answers2 SET scope = ?, kind = ? WHERE id = ?", (scope, kind, row_id))
    log.info("saved answers sorted: %s", dict(moved))
    return moved


def summary(moved: Counter) -> str:
    """One plain line for Activity and the import report: counts only, never a label or an answer."""
    if not sum(moved.values()):
        return "No saved answers needed sorting."
    return (f"Sorted {sum(moved.values())} saved answers: {moved['everywhere']} used everywhere, "
            f"{moved['review']} waiting for you to choose, {moved['never']} kept only as examples of your words.")


@tasks.data_task(NAME, after=AFTER, title=TITLE)
def run(ctx) -> None:
    """The data task body: sorts the rows and says how many went where."""
    moved = rescope(ctx.con)
    step = getattr(ctx, "step", None)
    if callable(step):
        step(summary(moved))


def run_now(con: sqlite3.Connection) -> Counter:
    """Runs the task in-process (the importers, after their settings step) and marks it done."""
    moved = rescope(con)
    with con:
        tasks.mark(con, tasks.TASKS[NAME], "running")
        tasks.mark(con, tasks.TASKS[NAME], tasks.DONE, summary(moved))
    return moved
