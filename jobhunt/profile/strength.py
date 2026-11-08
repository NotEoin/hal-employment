"""How strongly a skill is shown: in the person's own work, done in coursework, on a CV, or only studied.

Each piece of a skill's evidence has a strength (`of_evidence`), and a skill the strongest of its evidence's
(`of_skill`). In order, strongest first, as the sections plan ranks a skill's sources (2026-11-09):

- `shown`: the person's own work outside a course (a repository, a project's files);
- `coursework`: a piece of their coursework (the `coursework` section, or a coursework, dissertation, essay or
  report file of their own);
- `cv`: one of their CVs;
- `studied`: course material only (the `lecture` section, or any evidence of role `taught`).

`words(evidence)` says it on the skill card: "Done in your coursework: <title>", "Studied in <module>".

Design decisions, each with its reason:

- A strength is worked out from what the evidence already says (its section through `source_id`, its role
  and document type), not stored: a source moved to another section, or a module chosen later, changes it
  with nothing to update, and evidence from the bulk read gets one too.
- `studied` is exactly the evidence `profile.store.is_studied` counts, so a skill is studied when every piece
  of its evidence is: the check page's "Studied in your course" and the writer agree.
- The writer reads it through `profile.writing.skill_strengths`, so it can tell a studied skill only as
  knowledge, never as something the person did. A prompt's skill line says it in `PROMPT_WORDS`: shown and
  coursework skills may be told as experience ("I used", "I applied"), a CV skill may be listed, and a studied
  one is told only as knowledge ("I studied", "covered in my degree"; sections plan, 2026-11-09).
"""
from __future__ import annotations

from jobhunt.profile import merge

SHOWN, COURSEWORK, ON_CV, STUDIED = "shown", "coursework", "cv", "studied"
ORDER = (SHOWN, COURSEWORK, ON_CV, STUDIED)        # strongest first
SECTION_STRENGTH = {"coursework": COURSEWORK, "lecture": STUDIED}
OWN, TAUGHT = "own", "taught"                      # evidence roles (`ingest.roles`)
OWN_WORK_TYPES = ("repository", "project")
COURSEWORK_TYPES = ("coursework", "dissertation", "essay", "report")
EXPERIENCE = (SHOWN, COURSEWORK)                   # strengths a draft may tell as something the person did
# How a prompt's skill line says each strength (`profile.writing.line`).
PROMPT_WORDS = {SHOWN: "Shown in own work: may be told as experience",
                COURSEWORK: "Done in coursework: may be told as experience",
                ON_CV: "On a CV: may be listed",
                STUDIED: 'Studied only: tell it as knowledge ("I studied", "covered in my degree"), never as done'}


def of_evidence(evidence: dict) -> str:
    """The strength one piece of evidence gives its skill, "" when it says none (a note of unknown role).

    `evidence` is as `profile.store.evidence_of` gives it; `section` is the section of its source, if any."""
    section = evidence.get("section") or ""
    if section in SECTION_STRENGTH:
        return SECTION_STRENGTH[section]
    role = evidence.get("role") or ""
    if role == TAUGHT:
        return STUDIED
    group = merge.doc_group(str(evidence.get("doc_type") or ""))
    if group == "cv":
        return ON_CV
    if role == OWN and group in OWN_WORK_TYPES:
        return SHOWN
    if role == OWN and group in COURSEWORK_TYPES:
        return COURSEWORK
    return ""


def of_skill(item: dict) -> str:
    """The strongest strength of a shaped skill's evidence; "" when none gives one."""
    found = {e.get("strength") or of_evidence(e) for e in item.get("evidence") or []}
    return next((s for s in ORDER if s in found), "")


def studied_only(item: dict) -> bool:
    """True when every piece of a skill's evidence is studied, and there is some."""
    evidence = item.get("evidence") or []
    return bool(evidence) and all(of_evidence(e) == STUDIED for e in evidence)


def words(strength: str, *, title: str = "", module: str = "", course: str = "") -> str:
    """The skill card's words for one source's strength: "Done in your coursework: <title>", "Studied in
    <module>" (else the course); "" for no strength."""
    if strength == COURSEWORK:
        return f"Done in your coursework: {title}" if title else "Done in your coursework"
    if strength == STUDIED:
        where = module or course
        return f"Studied in {where}" if where else "Studied in your course"
    return {SHOWN: "Shown in your own work", ON_CV: "On your CV"}.get(strength, "")
