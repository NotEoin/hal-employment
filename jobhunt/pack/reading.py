"""The advert and the person a pack is written for: the prompt values, the advert reading, the record
passages the CV leaves out, the evidence plan and the `Brief` every draft shares."""
import re
from dataclasses import dataclass
from typing import Any

from .. import ai, research
from ..ai import prompts
from ..cvstudio import context as cv_context
from ..profile import writing as profile_writing
from ..writing import fields
from ..writing import plan as planning

SOMEONE = cv_context.SOMEONE  # what a prompt calls the person before they have given a name


def person(job=None) -> dict:
    """The placeholders every document prompt may use; never a private answer."""
    return cv_context.build(job)


def prompt(prompt_id: str, values: dict | None = None) -> str:
    """A document prompt filled in for the person; `values` saves reading the settings again."""
    return prompts.text(prompt_id, **(values or person()))


COMMON_QUESTIONS = ["Why do you want to work here?", "Why are you a good fit for this role?"]
PASSAGE = re.compile(r"^\[(.+?)\]$", re.M)   # the "[file > heading]" line `record.context` puts on each passage
QUESTION_SHARE = 0.8      # share of a question's words the advert must hold for it to count as written out


@dataclass(frozen=True)
class Brief:
    """What every draft for one job is written from and checked against: the job row, the person's prompt
    values (`person`), the advert reading, the company brief, the CV facts and the record passages (`_kept`)."""
    job: Any
    who: dict
    analysis: dict
    about: str
    facts: str
    kept: str


def slug(text: str) -> str:
    """Returns a file-name-safe form of a text."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _job_text(job) -> str:
    return (f"{job['title']}\n{job['company']} | {job['location'] or ''} | {job['salary'] or ''}\n\n"
            f"{job['description'] or ''}")


def _research(company: str) -> tuple[str, list[str]]:
    try:
        pages = research.company_pages(company)
    except Exception:  # offline or blocked search still yields a pack
        pages = []
    sources = "\n\n".join(f"[{url}]\n{text}" for url, text in pages)
    return sources, [url for url, _ in pages]


def _analyse(job, who: dict) -> dict:
    text = _job_text(job)[:12000]
    analysis = ai.chat_json(prompt("analyse", who), text, role="checker", timeout=ai.LONG_TIMEOUT_S, purpose="analyse")
    analysis["questions"] = written_out(analysis.get("questions") or [], text)
    resolved = fields.resolve(job["title"], text, who)
    analysis.update(field=resolved.field, background=resolved.background)
    return analysis


def field_of(job, who: dict, analysis: dict) -> fields.Resolved:
    """The kind of job this is, as the advert reading found it, else read from the advert now."""
    if analysis.get("field") in fields.rules.BY_FIELD:
        return fields.Resolved(analysis["field"], analysis.get("background") or "")
    return fields.resolve(job["title"], _job_text(job), who)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{3,}", text.lower())


def written_out(questions: list, advert: str) -> list[str]:
    """The questions the advert itself writes out; a model's note about questions is not one."""
    # Claude listed "Competency questions (the advert mentions them but doesn't list the actual questions)"
    # for an advert that only said there would be some, and the pack answered it (2026-10-23)
    held = set(_words(advert))
    kept = []
    for q in questions:
        words = _words(str(q))
        if words and sum(w in held for w in words) >= QUESTION_SHARE * len(words):
            kept.append(str(q))
    return kept


def _requirements(analysis: dict) -> list[str]:
    """What the fit map matches the CV against; the role summary when the advert states no requirement."""
    # short adverts state duties but no "essential" list; Claude answered [] for all three made-up adverts,
    # so the fit map, the letter's Evidence line and the prep notes' fit map were empty (2026-10-23)
    stated = (analysis.get("must_have") or []) + (analysis.get("nice_to_have") or [])
    summary = (analysis.get("summary") or "").strip()
    return stated or ([summary] if summary else [])


def _kept(analysis: dict) -> str:
    """What the CV leaves out (certificates, the detail of each job): the profile, then the record's passages."""
    return profile_writing.with_record(_requirements(analysis) + (analysis.get("questions") or [])) or "none"


def _record_part(heading: str, kept: str) -> str:
    """The record section of a request, or nothing when the record holds nothing for this job."""
    # "Employment record: none" read to Claude as a record that lacked a job on the CV (seen 2026-10-23)
    return f"\n\n{heading}:\n{kept}" if kept and kept != "none" else ""


def passages(kept: str) -> list[str]:
    """The record passages a prompt was given, as "file > heading" labels."""
    return PASSAGE.findall(kept or "")


def evidence_plan(analysis: dict, facts: str, kept: str, questions: tuple[str, ...] = ()) -> list[planning.Item]:
    """The evidence the drafts for one job may rest on (`writing.plan`): picked once from the CV, the Profile
    block and the record passages for the advert's requirements and `questions`."""
    return planning.make(_requirements(analysis), [*(analysis.get("questions") or []), *questions], facts, kept,
                         profile_writing.cards(), profile_writing.listings())


def previous_applications(con, job) -> str:
    """Summarises earlier applications to the same employer, from the tracker."""
    from ..mail.gmail import norm
    rows = con.execute("SELECT id, company, title, status FROM jobs WHERE id != ? AND status IN "
                       "('applied','assessment','interview','offer','rejected','withdrawn')", (job["id"],)).fetchall()
    same = [r for r in rows if norm(r["company"]) and norm(r["company"]) == norm(job["company"])]
    parts = []
    for r in same:
        events = con.execute("SELECT at, detail FROM events WHERE job_id = ? AND kind = 'status' ORDER BY at",
                             (r["id"],)).fetchall()
        path = ", ".join(f"{e['detail'].split(':')[0]} {e['at'][:10]}" for e in events)
        parts.append(f"{r['title']} ({path})")
    return "; ".join(parts)
