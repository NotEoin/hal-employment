"""What every document prompt knows about the person, their CV type and the job.

`build(job=None)` is the one source of the placeholders of the analyse, brief, fit, letter, message,
answers, verify, prep and contacts_pick prompts, so no document is written for one fixed kind of career.
It reads the `you.*`, `goal.*` and `cvdocs.*` settings, the `[documents]` and `[writing]` tables of the CV
type of Hal's copy of the CV, and, for the voice, answers the person rewrote. It never reads a private
answer: voice examples skip every question `autofill.private_question` marks.

Design decisions, each with its reason:

- Lists come twice: as a list under the CV type's name (`evidence`, `hiring_titles`) for code, and as words
  under the prompt's name (`type_evidence`, `type_hiring_titles`) for the prompt text.
- Values that depend on the job (`eligibility_line`, `test_kinds`) are never prompt
  placeholders: they go in the request text, so a prompt reads the same for every job of one person and an
  edit of it on the Advanced page applies everywhere.
- With no CV type to read (a copied type that was deleted), the documents use neutral wording that names
  no career, rather than another type's (a log line says why).
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

# Wording when no CV type can be read: it must name no career, so it can never leak one.
NEUTRAL = {"evidence": ["projects", "courses", "qualifications", "jobs", "volunteering"],
           "letter_mentions": "the two or three pieces of evidence that best match the advert",
           "hiring_titles": ["hiring manager", "manager of the team"],
           "prep_topics": ["the work the role does", "competency questions"],
           "special_cases": []}
NEUTRAL_TYPE_NAME = "General"
# What each special case tells the advert reader; the key is the type's `special_cases` id.
SPECIAL_CASES = {
    "ktp": "Knowledge Transfer Partnership (KTP) adverts are listed under the scheme: the employer is the "
           "host company the advert names.",
}
SPELLING_WORDS = {"en-GB": "UK English", "en-US": "US English"}
TONE_WORDS = {"plain": "plain and direct", "formal": "formal", "warm": "warm", "confident": "confident"}
SOMEONE = "the applicant"   # what a prompt calls the person before they have given a name
VOICE_MAX = 4               # rewritten answers used as voice examples; more crowd out the facts
VOICE_CHARS = 400           # each example is cut to this, so the examples never crowd out the facts
# Kinds of test an advert may name, for the prep notes.
TEST_KINDS = {
    "online test": r"online (test|assessment)",
    "numerical reasoning test": r"numerical (reasoning|test)",
    "verbal reasoning test": r"verbal (reasoning|test)",
    "situational judgement test": r"situational judge?ment",
    "written exercise": r"written (exercise|task|test)",
    "presentation": r"\bpresentation\b",
    "assessment centre": r"assessment (centre|center|day)",
    "case study": r"case stud(y|ies)",
    "video interview": r"video interview",
    "technical test": r"technical (test|task|exercise|assessment)|coding (test|challenge)",
    "in-tray exercise": r"in-tray",
}
TEST_RX = {name: re.compile(rx, re.I) for name, rx in TEST_KINDS.items()}


def _words(items: list[str], last: str = "or") -> str:
    items = [i for i in items if i]
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last} {items[-1]}"


def cv_type():
    """The CV type of Hal's copy of the CV, or None when none can be read."""
    from . import store, types

    try:
        return types.load(store.doc()["type_id"])
    except LookupError as e:
        log.warning("the CV type could not be read (%s); documents use neutral wording", e)
        return None


def _person() -> dict:
    from .. import screen
    from ..core import settings
    from ..find import goal as goal_mod

    goal = goal_mod.current()
    name = (settings.get("you.name") or "").strip()
    first = (settings.get("you.preferred_name") or "").strip() or (name.split()[0] if name else "")
    subject = (settings.get("you.education.subject") or "").strip()
    degree = (settings.get("you.education.degree") or "").strip()
    studied = " in ".join(p for p in (degree, subject) if p)
    end = goal.finished_year()
    if goal.is_student():
        status = f"They are studying now; the course ends in {end}" if end else "They are studying now"
    elif goal.study_status == "none":
        status = "They did not go to university"
    else:
        status = f"They finished their course in {end}" if end else "They have finished studying"
    return {
        "name": name or SOMEONE, "first_name": first or SOMEONE,
        "career_stage": screen.STAGE_LINES.get(goal.stage, goal.stage),
        "status_line": status,
        "target_roles": ", ".join(goal.roles) or "roles that suit them",
        "kinds": goal_mod.kinds_words(goal),
        "field_line": f"They studied {studied}." if studied else "",
    }


def voice_examples(con=None) -> list[str]:
    """Answers the person rewrote in their own words, newest first; none while "Match my own writing" is off."""
    from .. import autofill, db
    from ..core import settings

    if not settings.get("cvdocs.voice"):
        return []
    own = con or db.connect()
    try:
        rows = own.execute("SELECT label, question, answer FROM answers2 WHERE scope = 'never' "
                           "AND source IN ('you', 'learned') ORDER BY updated DESC").fetchall()
    finally:
        if con is None:
            own.close()
    out = []
    for row in rows:
        if autofill.private_question({"label": row["label"]}) or autofill.private_question({"label": row["question"]}):
            continue
        value = db.json_value(row["answer"])
        if isinstance(value, str) and value.strip():
            out.append(" ".join(value.split())[:VOICE_CHARS])
        if len(out) == VOICE_MAX:
            break
    return out


def _type_part(kind) -> dict:
    from ..core import settings

    docs = {**NEUTRAL, **((kind.documents or {}) if kind is not None else {})}
    writing = (kind.writing or {}) if kind is not None else {}
    spelling = settings.get("cvdocs.spelling") or writing.get("spelling") or "en-GB"
    tone = settings.get("cvdocs.letter_tone") or writing.get("tone") or "plain"
    avoid = []
    for phrase in [*(settings.get("cvdocs.never_use") or []), *(writing.get("banned") or [])]:
        if phrase and phrase.lower() not in (a.lower() for a in avoid):
            avoid.append(phrase)
    special = [s for s in docs.get("special_cases") or [] if s]
    return {
        "cv_type": kind.name if kind is not None else NEUTRAL_TYPE_NAME,
        "cv_type_id": kind.id if kind is not None else "",
        "evidence": list(docs["evidence"]), "type_evidence": _words(list(docs["evidence"])),
        "letter_mentions": docs["letter_mentions"], "type_letter_mentions": docs["letter_mentions"],
        "hiring_titles": list(docs["hiring_titles"]), "type_hiring_titles": _words(list(docs["hiring_titles"])),
        "prep_topics": list(docs["prep_topics"]), "type_prep_topics": _words(list(docs["prep_topics"]), "and"),
        "special_cases": special,
        "type_special_cases": " ".join(SPECIAL_CASES[s] for s in special if s in SPECIAL_CASES),
        "spelling": SPELLING_WORDS.get(spelling, spelling),
        "tone": TONE_WORDS.get(tone, tone),
        "letter_words": int(settings.get("cvdocs.letter_words") or 250),
        "signoff": (settings.get("cvdocs.signoff") or "").strip() or "Kind regards",
        "avoid_phrases": ", ".join(f'"{a}"' for a in avoid) or "none",
    }


def _voice_block(examples: list[str]) -> str:
    if not examples:
        return ""
    lines = "\n".join(f"- {e}" for e in examples)
    return f"Examples of the applicant's own writing; match their voice, never copy their facts:\n{lines}"


def _job_part(job) -> dict:
    keys = job.keys() if hasattr(job, "keys") else ()
    raw = job["eligibility"] if "eligibility" in keys else None
    try:
        elig = json.loads(raw) if raw else {}
    except ValueError:
        elig = {}
    text = f"{job['title'] or ''}\n{job['description'] or ''}" if "description" in keys else ""
    tests = [name for name, rx in TEST_RX.items() if rx.search(text)]
    return {
        "eligibility": elig,
        "eligibility_line": elig.get("why") or elig.get("quote") or "",
        "not_eligible": elig.get("eligible") is False,
        "test_kinds": tests,
        "test_kinds_line": _words(tests, "and") or "none named in the advert",
    }


def build(job=None) -> dict:
    """The placeholders of every document prompt for the person, and with `job` that job's own facts."""
    kind = cv_type()
    values = {**_person(), **_type_part(kind)}
    values["voice_examples"] = _voice_block(voice_examples())
    if job is not None:
        values.update(_job_part(job))
    return values
