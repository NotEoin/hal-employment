"""Application packs: everything needed to apply for one job, drafted locally.

A pack holds a company brief, a requirements-to-evidence map with honest gaps,
a tailored CV, a cover letter, a short message for the hiring manager, and
answers to common form questions. It is written to packs/<slug>/ and mirrored
into the vault's Applications folder. Nothing is sent or submitted.
"""
import re
import textwrap
from datetime import date
from pathlib import Path

from . import coursework, cv, db, llm, reputation, research
from .config import ROOT
from .vault import VAULT

PACKS = ROOT / "packs"
APPLICATIONS = VAULT / "Areas" / "Job Search" / "Applications"

ANALYSE = """You analyse a job advert for a UK graduate software engineer. Reply with JSON only:
{"summary": one sentence on what the role does,
 "must_have": [up to 8 short requirements stated as essential],
 "nice_to_have": [up to 6 short requirements stated as desirable],
 "contacts": [named people or email addresses in the advert, with their role if given],
 "questions": [application questions the advert asks candidates to answer],
 "employer": the company the successful candidate will work for,
 "eligibility": [hard conditions on who may apply: current enrolment, graduation
   year, start date, right to work, location, minimum experience]}
Use only what the advert says."""

BRIEF = """Summarise what this company does for a candidate preparing an application.
Write at most 120 words of plain prose: the product, who uses it, the stage or size
if stated, and anything distinctive about how they work. Use only the sources given.
If the sources say little, say so in one sentence rather than guessing."""

FIT = """Match a candidate's evidence to a job's requirements. Reply with JSON only:
{"matches": [{"requirement": ..., "evidence": the named project, module, degree or
  job that shows it, in at most 20 words, "strength": "strong" | "partial"}],
 "gaps": [requirements the CV gives no evidence for],
 "eligible": true | false, "eligibility_notes": why, in one sentence}
Rules: use only the CV text and the course notes. A course note counts only if its
excerpt shows the requirement; cite it as "<module name> coursework: <topic>", never by code.
Never cite the summary or a skills list as evidence,
except for a requirement that names a language or tool. "strong" means the evidence
directly demonstrates the requirement. A requirement with no evidence goes in gaps.
Judge eligibility against the conditions given and the candidate's dates. Experience
from internships, academic work or personal projects counts where the advert allows it;
a preference ("desirable", "ideally", "a plus") is not a condition."""

LETTER = """Write a cover letter from Firstname Lastname for this job. At most 220 words, in
three short paragraphs: why this company and role specifically; the two or three
strongest pieces of evidence, each tied to a requirement; a one-line close. Name
university modules by title, never by code. No
greeting line or sign-off; those are added separately. Use only the facts given."""

MESSAGE = """Write a short message from Firstname Lastname to the hiring manager or engineering
lead for this role, to send by email or LinkedIn alongside the application. At most
90 words. Name the role, give one concrete reason he fits, and ask one specific
question about the team's work. No flattery and no request for a referral."""

ANSWERS = """Answer these application questions for Firstname Lastname, each in at most 100
words, using only the facts given. Reply with JSON only: {"answers": [{"question": ...,
"answer": ...}]}."""

VERIFY = """Check drafts written for a job applicant against the facts about him. Reply
with JSON only: {"unsupported": [each claim about the applicant that the facts do not
support, quoted briefly]}. A claim is unsupported if it states a skill, language,
project detail, number, status or experience that the facts do not contain.
Claims about the company are out of scope."""

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
    job_text = _job_text(job)
    facts = cv.facts_text()
    out_dir = PACKS / slug(f"{job['company']}-{job['title']}")

    analysis = llm.chat_json(ANALYSE, job_text[:12000], role="main")
    web, urls = _research(job["company"]) if use_web else ("", [])
    about = llm.chat(BRIEF, f"Company: {job['company']}\n\nJob advert:\n{job_text[:4000]}\n\nWeb sources:\n{web[:8000]}",
                     role="main")
    requirements = analysis.get("must_have", []) + analysis.get("nice_to_have", [])
    conditions = "\n".join(f"- {c}" for c in analysis.get("eligibility", []))
    course_notes = coursework.as_text(coursework.evidence(requirements))
    fit = llm.chat_json(FIT, "Requirements:\n" + "\n".join(f"- {r}" for r in requirements)
                        + f"\n\nConditions:\n{conditions}\n\nToday: {date.today().isoformat()}\n\nCV:\n{facts}"
                        + f"\n\nCourse notes:\n{course_notes or 'none'}",
                        role="main")

    history = previous_applications(con, job)
    context = (f"Job: {job['title']} at {job['company']}\nRole summary: {analysis.get('summary', '')}\n"
               + (f"Earlier applications to this employer: {history}. Do not mention them unless asked.\n"
                  if history else "") +
               f"Firstname graduated recently. Mention availability only if the advert's start date is "
               f"flexible; for a fixed intake, refer to that intake instead.\n"
               f"Company: {about}\nEvidence map: {fit.get('matches', [])}\n\nCV facts:\n{facts}")
    letter = llm.undash(llm.chat(LETTER, context, role="writer").strip())
    message = llm.undash(llm.chat(MESSAGE, context, role="writer").strip())
    questions = analysis.get("questions") or []
    answers = llm.chat_json(ANSWERS, "Questions:\n" + "\n".join(f"- {q}" for q in [*questions, *COMMON_QUESTIONS])
                            + f"\n\n{context}", role="writer").get("answers", [])
    for a in answers:
        a["answer"] = llm.undash(a.get("answer", ""))

    variant, track = cv.tailor(job_text)
    pdf = cv.render(variant, out_dir)
    drafts = "\n\n".join([letter, message, *(a.get("answer", "") for a in answers)])
    flagged = [f"number not in the CV or advert: {n}" for n in unsupported_numbers(drafts, facts, job_text, about)]
    flagged += [f"unsupported claim: {c}" for c in
                llm.chat_json(VERIFY, f"Facts:\n{facts}\n\nCourse notes:\n{course_notes}\n\nEvidence map:\n{fit.get('matches', [])}"
                              f"\n\nDrafts:\n{drafts}", role="main").get("unsupported", [])]
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

    md = _markdown(job, analysis, about, urls, fit, letter, message, answers, pdf, track, flagged)
    md = md.replace("## Role\n", company_md + "\n## Role\n", 1)
    pack_md = out_dir / "pack.md"
    pack_md.write_text(md)
    if VAULT.exists():
        APPLICATIONS.mkdir(parents=True, exist_ok=True)
        (APPLICATIONS / f"{slug(job['company'])} - {slug(job['title'])}.md").write_text(md)
    if job["status"] in ("new", "shortlisted"):
        db.set_status(con, job_id, "ready", f"pack drafted in {out_dir.name}")
    con.commit()
    return pack_md


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
    """Adds or refreshes the company check in an existing pack and its vault copy."""
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
    vault_copy = APPLICATIONS / f"{slug(company_name)} - {slug(role)}.md"
    if vault_copy.exists():
        vault_copy.write_text(text)


def _markdown(job, analysis, about, urls, fit, letter, message, answers, pdf, track, flagged) -> str:
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
    lines += ["## Cover letter", "", "Dear Hiring Team,", "", letter, "", "Kind regards,", "Firstname Lastname", ""]
    lines += ["## Message to the hiring manager", "",
              "To: " + ("; ".join(contacts) if contacts else
                        f"find the engineering lead on LinkedIn (search \"{job['company']} engineering manager\")"),
              "", message, ""]
    lines += ["## Form answers", ""]
    for a in answers:
        lines += [f"**{a.get('question', '')}**", "", textwrap.fill(a.get("answer", ""), 100), ""]
    lines += ["## Before submitting", "", "- [ ] Read every draft and correct anything that is not true",
              "- [ ] Attach the tailored CV", "- [ ] Submit, then run `python3 -m jobhunt set "
              f"{job['id']} applied`", "- [ ] Send the hiring-manager message the same day", ""]
    return "\n".join(lines)
