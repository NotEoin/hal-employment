"""Build me a fresh CV: a short interview, then a CV shaped by the chosen CV type.

The interview asks one thing at a time (`QUESTIONS`): education, jobs, volunteering and skills, then
interests. `build` turns the answers into a schema 2 document with the type's section order and titles.
Module marks are optional for everyone: a module with no mark is listed by name only when the type shows
all modules, and never makes the build fail. When the AI is ready, it may write the profile line from the
same facts, through the suggestion guards, so it cannot add a number or qualification the answers do not
hold, or speak as I or we, and it is respelt to the person's spelling.

`make` saves the result as a generated version, checks it against its type, and runs the reviewers, so a
failing check arrives as a suggestion rather than as a silent edit. Before saving, Hal's own words (the profile
line) are held to the CV's page limit on the printed page (`within_limit`); the person's answers are never
cut, so when they alone run over, Hal keeps them and says how many pages it is.

Not built yet: reading the record and old documents through `cv_extract` and `cv_compose` before the
interview; today the answers are the only facts.
"""
from __future__ import annotations

import hashlib
import json
import logging

from jobhunt import db
from jobhunt.cvstudio import suggest
from jobhunt.writing import spelling

log = logging.getLogger(__name__)

QUESTIONS = (
    {"id": "education",
     "text": "Where did you study, what, and when? Add your grade and dissertation if you have them."},
    {"id": "jobs", "text": "What jobs have you had, including part-time and holiday work? What did you do in each?"},
    {"id": "volunteering", "text": "Have you volunteered, or helped run a club or society? What did you do?"},
    {"id": "skills", "text": "What are you good at? Tools, languages and ways of working all count."},
    {"id": "interests", "text": "Anything else you do that says something about you? You can skip this."},
)
SECTION_KINDS = {"summary": "summary", "education": "education", "experience": "experience",
                 "volunteering": "volunteering", "skills": "skills", "interests": "interests", "projects": "projects"}
DEFAULT_TITLES = {"summary": "Profile", "education": "Education", "experience": "Work experience",
                  "volunteering": "Volunteering", "skills": "Skills", "interests": "Interests", "projects": "Projects"}
PREFIX = {"education": "ed", "experience": "ex", "volunteering": "vo", "skills": "sk", "projects": "pr"}
ID_CHARS = 8                        # the stable id rule: sha1(kind + heading)[:8]


def _id(kind: str, heading: str) -> str:
    return f"{PREFIX.get(kind, kind[:2])}-" + hashlib.sha1(f"{kind}{heading}".encode()).hexdigest()[:ID_CHARS]


def _bullets(model, entry_id: str, lines) -> list:
    out = []
    for text in [str(x).strip().lstrip("-").strip() for x in lines or [] if str(x).strip()]:
        out.append(model.Bullet(id=f"{entry_id}-" + hashlib.sha1(text.encode()).hexdigest()[:6], text=text))
    return out


def _marked(mark: str, least: int) -> bool:
    try:
        return int(str(mark).strip()) >= least
    except ValueError:
        return bool(str(mark).strip())     # "Distinction", "A": written by the person, shown as written


def _education(model, rows, rules: dict) -> list:
    out = []
    show, least = rules.get("show_modules", "strong"), int(rules.get("min_mark") or 0)
    for row in rows or []:
        heading = str(row.get("qualification") or "").strip()
        eid = _id("education", heading)
        lines = [f"Dissertation: {row['dissertation']}"] if row.get("dissertation") else []
        modules = []
        for m in row.get("modules") or []:
            mark = str(m.get("mark") or "")
            if show == "all" or (show == "strong" and mark and _marked(mark, least)):
                modules.append(model.Module(name=str(m.get("name") or ""), mark=mark, year=str(m.get("year") or "")))
        out.append(model.Entry(id=eid, heading=heading, org=str(row.get("org") or ""),
                               start=str(row.get("start") or ""),
                               end=str(row.get("end") or ""), grade=str(row.get("grade") or ""),
                               bullets=_bullets(model, eid, [*lines, *(row.get("did") or [])]), modules=modules))
    return out


def _roles(model, kind: str, rows) -> list:
    out = []
    for row in rows or []:
        heading = str(row.get("role") or "").strip()
        eid = _id(kind, heading + str(row.get("org") or ""))
        out.append(model.Entry(id=eid, heading=heading, org=str(row.get("org") or ""),
                               start=str(row.get("start") or ""),
                               end=str(row.get("end") or ""), bullets=_bullets(model, eid, row.get("did"))))
    return out


def _skills(model, rows) -> list:
    groups = []
    for row in rows or []:
        if isinstance(row, dict):
            groups.append((str(row.get("group") or "Skills"), [str(x) for x in row.get("items") or []]))
        else:
            groups.append(("Skills", [str(row)]))
    merged: dict[str, list[str]] = {}
    for name, items in groups:
        merged.setdefault(name, []).extend(i for i in items if i.strip())
    return [model.Entry(id=_id("skills", name), heading=name, items=items) for name, items in merged.items() if items]


def profile_line(answers: dict) -> str:
    """A profile made only of the answers' facts: the qualification, then the strongest evidence."""
    ed = (answers.get("education") or [{}])[0]
    qual = " ".join(x for x in (str(ed.get("qualification") or ""),) if x) or "Graduate"
    grade = f" ({ed['grade']})" if ed.get("grade") else ""
    org = f" from {ed['org']}" if ed.get("org") else ""
    parts = [f"{qual} graduate{grade}{org}"]
    if ed.get("dissertation"):
        parts.append(f"with a dissertation on {ed['dissertation']}")
    vol = (answers.get("volunteering") or [{}])[0]
    job = (answers.get("jobs") or [{}])[0]
    more = [f"{x['role'].lower()} experience at {x['org']}" for x in (vol, job) if x.get("role") and x.get("org")]
    if more:
        parts.append(("and " if ed.get("dissertation") else "with ") + " and ".join(more))
    return " ".join(parts) + "."


def build(answers: dict, type_) -> object:
    """A schema 2 document from the interview, in the type's order, with the type's titles."""
    from jobhunt.cvstudio import model

    made = {
        "summary": model.Section(id="summary", kind="summary", title="", text=profile_line(answers)),
        "education": model.Section(id="education", kind="education", title="",
                                   entries=_education(model, answers.get("education"),
                                                      type_.sections.get("education") or {})),
        "experience": model.Section(id="experience", kind="experience", title="",
                                    entries=_roles(model, "experience", answers.get("jobs"))),
        "volunteering": model.Section(id="volunteering", kind="volunteering", title="",
                                      entries=_roles(model, "volunteering", answers.get("volunteering"))),
        "skills": model.Section(id="skills", kind="skills", title="", entries=_skills(model, answers.get("skills"))),
        "interests": model.Section(id="interests", kind="interests", title="",
                                   text=str(answers.get("interests") or "").strip()),
    }
    order = [sid for sid in type_.section_order if sid in made]
    order += [sid for sid in made if sid not in order]
    sections = []
    for sid in order:
        sec = made[sid]
        if not (sec.text or sec.entries):
            continue
        sec.title = (type_.sections.get(sid) or {}).get("title") or DEFAULT_TITLES[sid]
        sections.append(sec)
    return model.CVDoc(headline=str(answers.get("headline") or ""), sections=sections,
                       extras={"from": "fresh interview"})


def _ai_profile(doc, type_, answers: dict) -> str | None:
    """The AI's profile line from the same facts, when it passes the guards; else None."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    if not suggest.ai_ready():
        return None
    try:
        system = prompts.text("cv_compose", spelling=suggest.spelling_words())
        got = ai.chat_json(system, json.dumps({"facts": answers, "cv_type": type_.name,
                                               "conventions": suggest.field_conventions()}, ensure_ascii=True),
                           role="writer", purpose="cv_compose", priority=suggest.AI_PRIORITY)
    except ai.LLMUnavailable as e:
        log.info("fresh CV profile by rules: %s", e)
        return None
    text = spelling.respell(str(got.get("summary") or "").strip(), spelling.chosen())
    if not text:
        return None
    gapped = suggest.with_gaps(text, json.dumps(answers))
    phrases = suggest.banned_phrases(type_)
    if (suggest.GAP.search(gapped) or any(p in gapped.lower() for p in phrases)
            or suggest.first_person(gapped, type_)):
        return None
    return gapped


def _without_profile(doc) -> object:
    """`doc` with no profile section: the last of Hal's own words it can drop."""
    import dataclasses

    return dataclasses.replace(doc, sections=[s for s in doc.sections if s.kind != "summary"])


def within_limit(drafts: list, doc_id: str) -> tuple[object, str]:
    """(the draft to save, a note): the first of `drafts` whose printed pages keep to CV `doc_id`'s limit.

    `drafts` hold the same answers with less of Hal's own words each (its AI profile, its plain profile, no
    profile), at most `len(drafts)` prints. With no limit or no count (no browser) the first is used. When
    none fits, the first is kept, the person's answers whole, and the note gives the real numbers.
    """
    from jobhunt.cvstudio import pagelimit

    limit = pagelimit.limit_of(doc_id)[0]
    if not limit:
        return drafts[0], ""
    first = None
    for i, draft in enumerate(drafts):
        pages = pagelimit.count_doc(draft, doc_id)
        if pages is None:
            return drafts[0], ""
        first = pages if first is None else first
        if pages <= limit:
            if i == 0:
                return draft, ""
            profiled = any(s.kind == "summary" for s in draft.sections)
            note = pagelimit.FRESH_SHORTER if profiled else pagelimit.FRESH_NO_PROFILE
            return draft, note.format(limit=pagelimit.pages_words(limit))
    return drafts[0], pagelimit.FRESH_OVER.format(pages=first, limit=limit)


def make(answers: dict, type_id: str, *, step=None, doc_id: str | None = None) -> dict:
    """Saves a fresh CV as a generated version of one CV (the lead CV by default), checks it and starts
    suggestions for what fails.

    Hal's own words are held to the CV's page limit (`within_limit`): the AI's profile gives way to the plain
    one, then to none, until the printed CV fits; `note` says what Hal did, or the real numbers when the
    answers alone are longer.
    """
    from jobhunt.cvstudio import checks, model, pagelimit, store, types

    type_ = types.load(type_id)
    if step:
        step("Writing your CV from your answers")
    doc = build(answers, type_)
    drafts = [doc]
    text = _ai_profile(doc, type_, answers)
    if text:
        drafts.insert(0, model.apply(doc, model.Change("replace", "s:summary/text", text)))
    if any(s.kind == "summary" for s in doc.sections):
        drafts.append(_without_profile(doc))
    if step:
        step("Counting the pages of your CV")
    doc, note = within_limit(drafts, store.doc(doc_id=doc_id)["id"])
    problems = model.validate(doc, type_)
    if problems:
        raise ValueError("Hal could not build a CV from these answers: " + " ".join(problems))
    vid = store.new_version(doc, origin="generated", what=f"Hal built a fresh CV ({type_.name})", by="hal",
                            type_id=type_id, doc_id=doc_id)
    pagelimit.keep(vid)            # the draft's count, printed in `within_limit`, so it is not printed again
    if step:
        step("Checking it against your CV type")
    results = checks.for_type(doc, type_)
    con = db.connect()
    try:
        found = suggest.look_again(con, doc_id=store.version(vid, con=con)["doc_id"])
    finally:
        con.close()
    return {"version_id": vid, "failing": [r["name"] for r in results if r["ok"] is False],
            "checks": len(results), "suggestions": found["pending"], "note": note}
