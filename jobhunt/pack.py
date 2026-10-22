"""Application packs: everything needed to apply for one job, drafted locally.

A pack holds a company brief, a requirements-to-evidence map with honest gaps,
a tailored CV, a cover letter, a short message for the person hiring, and
answers to common form questions. It is written to the job's folder (`documents.pack_dir`), and a
`docs.prepared` event says one is ready. With the Hal add-on on, a copy also goes into its Applications
folder (`addon.applications_mirror`). Nothing is sent or submitted.

Design notes:
- The prompts live in `ai/prompts/*.txt` and name no one: `person()` fills them from the `you.*` and
  `goal.*` settings. The old constants (`pack.LETTER` and the rest) are rendered when read, so
  callers that pass them to the model, and tests that match on them, see the text the person's edits and
  settings give.
"""
import re
import textwrap
from datetime import date
from pathlib import Path

from . import coursework, cv, db, llm, record, reputation, research
from .ai import prompts
from .core import events, paths

# PACKS, the old application packs, is resolved when used until application folders replace it.
_LIVE = {"PACKS": paths.legacy_packs_dir}
# Old constant name -> prompt id; each is the prompt's text filled in for the person (see `prompt`).
PROMPTS = {"ANALYSE": "analyse", "BRIEF": "brief", "FIT": "fit", "LETTER": "letter", "MESSAGE": "message",
           "ANSWERS": "answers", "VERIFY": "verify"}
# The vault mirror moved to the Hal add-on; these answer None only so the made-up world's patches
# (tests/support/world.py) still apply until its owner drops them.
RETIRED = ("VAULT", "APPLICATIONS")
SOMEONE = "the applicant"     # what a prompt calls the person before they have given a name
PREPARED = "docs.prepared"    # the event the Hal add-on's Applications mirror listens for


def __getattr__(name):  # old path constants and prompts, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    if name in PROMPTS:
        return prompt(PROMPTS[name])
    if name in RETIRED:
        return None
    raise AttributeError(name)


def person() -> dict:
    """The placeholders every document prompt may use, from the person's settings (never a private answer)."""
    from . import screen
    from .core import settings
    from .find import goal as goal_mod

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


def prompt(prompt_id: str, values: dict | None = None) -> str:
    """A document prompt filled in for the person; `values` saves reading the settings again."""
    return prompts.text(prompt_id, **(values or person()))


COMMON_QUESTIONS = ["Why do you want to work here?", "Why are you a good fit for this role?"]

# Phrases that mark generic or machine-written prose; drafts containing them are flagged.
CLICHES = re.compile(
    r"\b(excited|thrilled|passion(ate)?|thrive|leverage|fast-paced|perfectly align|"
    r"aligns? perfectly|dynamic|synergy|eager to|I am writing to|look forward to hearing)\b"
    r"|\u2014|\u2013", re.I)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _job_text(job) -> str:
    return f"{job['title']}\n{job['company']} | {job['location'] or ''} | {job['salary'] or ''}\n\n{job['description'] or ''}"


def unsupported_numbers(draft: str, *sources: str) -> list[str]:
    """Returns numbers in a draft that appear in none of the sources."""
    # A number ending a sentence ("2023-2024.") is collected with its full stop; it is the same number.
    known = {n.rstrip(".,") for n in re.findall(r"\d[\d,.]*", " ".join(sources))}
    return sorted({n for n in re.findall(r"\d[\d,.]*", draft) if n.rstrip(".,") not in known and len(n) > 1})


def _research(company: str) -> tuple[str, list[str]]:
    try:
        pages = research.company_pages(company)
    except Exception:  # noqa: BLE001 - offline or blocked search still yields a pack
        pages = []
    sources = "\n\n".join(f"[{url}]\n{text}" for url, text in pages)
    return sources, [url for url, _ in pages]


def build(con, job_id: str, use_web: bool = True) -> Path:
    """Drafts the pack for one job and returns the pack's Markdown path."""
    job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    from . import documents

    job_text = _job_text(job)
    facts = cv.facts_text()
    out_dir = documents.pack_dir(job)
    who = person()

    analysis = llm.chat_json(prompt("analyse", who), job_text[:12000], role="main")
    web, urls = _research(job["company"]) if use_web else ("", [])
    about = llm.chat(prompt("brief", who),
                     f"Company: {job['company']}\n\nJob advert:\n{job_text[:4000]}\n\nWeb sources:\n{web[:8000]}",
                     role="main")
    requirements = analysis.get("must_have", []) + analysis.get("nice_to_have", [])
    conditions = "\n".join(f"- {c}" for c in analysis.get("eligibility", []))
    course_notes = coursework.as_text(coursework.evidence(requirements))
    # What the CV leaves out (certificates, the detail of each job) is looked up in the employment record.
    kept = record.context(requirements + (analysis.get("questions") or [])) or "none"
    fit = llm.chat_json(prompt("fit", who), "Requirements:\n" + "\n".join(f"- {r}" for r in requirements)
                        + f"\n\nConditions:\n{conditions}\n\nToday: {date.today().isoformat()}\n\nCV:\n{facts}"
                        + f"\n\nEmployment record:\n{kept}\n\nCourse notes:\n{course_notes or 'none'}",
                        role="main")

    history = previous_applications(con, job)
    context = (f"Job: {job['title']} at {job['company']}\nRole summary: {analysis.get('summary', '')}\n"
               + (f"Earlier applications to this employer: {history}. Do not mention them unless asked.\n"
                  if history else "") +
               f"{who['status_line']}. Mention availability only if the advert's start date is "
               f"flexible; for a fixed intake, refer to that intake instead.\n"
               f"Company: {about}\nEvidence map: {fit.get('matches', [])}\n\nCV facts:\n{facts}"
               f"\n\nEmployment record (true; use it where the CV is silent):\n{kept}")
    letter = llm.undash(llm.chat(prompt("letter", who), context, role="writer").strip())
    message = llm.undash(llm.chat(prompt("message", who), context, role="writer").strip())
    questions = analysis.get("questions") or []
    answers = llm.chat_json(prompt("answers", who),
                            "Questions:\n" + "\n".join(f"- {q}" for q in [*questions, *COMMON_QUESTIONS])
                            + f"\n\n{context}", role="writer").get("answers", [])
    for a in answers:
        a["answer"] = llm.undash(a.get("answer", ""))

    variant, track = cv.tailor(job_text)
    pdf = cv.render(variant, out_dir)
    drafts = "\n\n".join([letter, message, *(a.get("answer", "") for a in answers)])
    flagged = [f"number not in the CV or advert: {n}" for n in unsupported_numbers(drafts, facts, job_text, about, kept)]
    flagged += [f"unsupported claim: {c}" for c in
                llm.chat_json(prompt("verify", who),
                              f"Facts:\n{facts}\n\nEmployment record:\n{kept}\n\nCourse notes:\n{course_notes}"
                              f"\n\nEvidence map:\n{fit.get('matches', [])}\n\nDrafts:\n{drafts}",
                              role="main").get("unsupported", [])]
    flagged += [f"cliche or dash: {m}" for m in sorted({m.group(0).lower() for m in CLICHES.finditer(drafts)})]
    # KTP posts are listed under the scheme; the employer is the host company in the advert.
    employer = (analysis.get("employer") or job["company"]) if job["company"] == "KTP" else job["company"]
    company_flags, company_md = reputation.check(employer)
    flagged += company_flags
    if history:
        flagged.insert(0, f"REAPPLICATION: you applied here before: {history}")
    if fit.get("eligible") is False:
        flagged.insert(0, f"ELIGIBILITY: {fit.get('eligibility_notes', 'may not be eligible')}")
    elif "current students" in (job["score_notes"] or ""):
        flagged.insert(0, "ELIGIBILITY: the advert appears to be for current students; confirm graduates can apply")

    md = _markdown(job, analysis, about, urls, fit, letter, message, answers, pdf, track, flagged, who["name"])
    md = md.replace("## Role\n", company_md + "\n## Role\n", 1)
    pack_md = out_dir / "pack.md"
    pack_md.write_text(md)
    events.publish(PREPARED, job_id=job_id, company=job["company"], title=job["title"], pack=str(pack_md))
    mirror_note(job, pack_md)
    if job["status"] in ("new", "shortlisted"):
        db.set_status(con, job_id, "ready", f"pack drafted in {out_dir.name}")
    con.commit()
    return pack_md


def mirror_note(job, pack_md: Path) -> Path | None:
    """Copies the pack into the Hal add-on's Applications folder under the old note name; None when it is off."""
    from .addons.hal import mirror

    return mirror.copy(pack_md, f"{slug(job['company'])} - {slug(job['title'])}.md")


def previous_applications(con, job) -> str:
    """Summarises earlier applications to the same employer, from the tracker."""
    from .gmail import norm
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


def add_company_check(pack_md: Path, company: str) -> None:
    """Adds or refreshes the company check in an existing pack, and says the pack changed."""
    flags, section = reputation.check(company)
    text = re.sub(r"## Company check\n.*?(?=\n## )", "", pack_md.read_text(), flags=re.S)
    if flags:
        bullets = "".join(f"- {f}\n" for f in flags)
        if "## Check before sending\n\n" in text:
            text = text.replace("## Check before sending\n\n", "## Check before sending\n\n" + bullets, 1)
        else:
            text = text.replace("\n## Role\n", f"\n## Check before sending\n\n{bullets}\n## Role\n", 1)
    text = text.replace("\n## Role\n", "\n" + section + "\n## Role\n", 1)
    pack_md.write_text(text)
    title = text.splitlines()[0].removeprefix("# ")
    company_name, _, role = title.partition(": ")
    events.publish(PREPARED, company=company_name, title=role, pack=str(pack_md))


def _markdown(job, analysis, about, urls, fit, letter, message, answers, pdf, track, flagged, name) -> str:
    lines = [f"# {job['company']}: {job['title']}", "",
             f"- Apply: {job['url']}",
             f"- Location: {job['location'] or 'not stated'} | Salary: {job['salary'] or 'not stated'}",
             f"- Tailored CV: `{pdf}`" + (f" (track: {track})" if track else ""),
             f"- Drafted: {date.today().isoformat()} | Job id: `{job['id']}`", ""]
    if flagged:
        lines += ["## Check before sending", ""] + [f"- {f}" for f in flagged] + [""]
    lines += ["## Role", "", analysis.get("summary", ""), "", "## Company", "", about.strip(), ""]
    lines += [f"- Source: {u}" for u in urls] + ([""] if urls else [])
    lines += ["## Fit", "", "| Requirement | Evidence | Strength |", "| --- | --- | --- |"]
    lines += [f"| {m.get('requirement', '')} | {m.get('evidence', '')} | {m.get('strength', '')} |"
              for m in fit.get("matches", [])]
    gaps = fit.get("gaps", [])
    lines += ["", "Gaps to address or accept: " + ("; ".join(gaps) if gaps else "none found"), ""]
    contacts = [", ".join(str(v) for v in c.values() if v) if isinstance(c, dict) else str(c)
                for c in analysis.get("contacts") or []]
    lines += ["## Cover letter", "", "Dear Hiring Team,", "", letter, "", "Kind regards,", name, ""]
    lines += ["## Message to the hiring manager", "",
              "To: " + ("; ".join(contacts) if contacts else
                        f"find the person hiring on LinkedIn (search \"{job['company']} hiring manager\")"),
              "", message, ""]
    lines += ["## Form answers", ""]
    for a in answers:
        lines += [f"**{a.get('question', '')}**", "", textwrap.fill(a.get("answer", ""), 100), ""]
    lines += ["## Before submitting", "", "- [ ] Read every draft and correct anything that is not true",
              "- [ ] Attach the tailored CV", "- [ ] Submit, then run `python3 -m jobhunt set "
              f"{job['id']} applied`", "- [ ] Send the hiring-manager message the same day", ""]
    return "\n".join(lines)
