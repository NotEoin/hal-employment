"""Application packs: everything needed to apply for one job, drafted locally.

A pack holds a company brief, a requirements-to-evidence map with honest gaps,
a tailored CV, a cover letter, a short message for the person hiring, and
answers to common form questions. It is written to the job's folder (`documents.pack_dir`), and a
`docs.prepared` event says one is ready. With the Hal add-on on, a copy also goes into its Applications
folder (`addon.applications_mirror`). Nothing is sent or submitted.

Design notes:
- The prompts live in `ai/prompts/*.txt` and name no one: `cvstudio.context.build` fills them from the
  `you.*`, `goal.*` and `cvdocs.*` settings and the CV type. The old constants
  (`pack.LETTER` and the rest) are rendered when read, so callers that pass them to the model, and tests
  that match on them, see the text the person's edits and settings give.
- One letter path: `write_letter` reads the advert, the record and the fit map, writes the letter and checks
  it, for a pack and for a letter a form asks for (`documents.draft_letter`), so every letter is checked
  the same way.
- The pack's checklist names no command: every action is a button on the Documents page.
"""
import re
import textwrap
from datetime import date
from pathlib import Path

from . import cv, db, llm, record, reputation, research
from .ai import prompts
from .core import events, paths
from .cvstudio import context as cv_context

# PACKS, the old application packs, is resolved when used until application folders replace it.
_LIVE = {"PACKS": paths.legacy_packs_dir}
# Old constant name -> prompt id; each is the prompt's text filled in for the person (see `prompt`).
PROMPTS = {"ANALYSE": "analyse", "BRIEF": "brief", "FIT": "fit", "LETTER": "letter", "MESSAGE": "message",
           "ANSWERS": "answers", "VERIFY": "verify"}
# The vault mirror moved to the Hal add-on; these answer None only so the made-up world's patches
# (tests/support/world.py) still apply until its owner drops them.
RETIRED = ("VAULT", "APPLICATIONS")
SOMEONE = cv_context.SOMEONE  # what a prompt calls the person before they have given a name
PREPARED = "docs.prepared"    # the event the Hal add-on's Applications mirror listens for


def __getattr__(name):  # old path constants and prompts, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    if name in PROMPTS:
        return prompt(PROMPTS[name])
    if name in RETIRED:
        return None
    raise AttributeError(name)


def person(job=None) -> dict:
    """The placeholders every document prompt may use; never a private answer."""
    return cv_context.build(job)


def prompt(prompt_id: str, values: dict | None = None) -> str:
    """A document prompt filled in for the person; `values` saves reading the settings again."""
    return prompts.text(prompt_id, **(values or person()))


COMMON_QUESTIONS = ["Why do you want to work here?", "Why are you a good fit for this role?"]
# The pack's last section. It names no command: the Documents page has a button for each.
CHECKLIST = ("Read every draft and correct anything that is not true", "Attach the CV made for this job",
             "Press Submit on the employer's site yourself, then press \"I sent it myself\" if Hal has not seen it",
             "Send the message to the person hiring the same day")
FIND_PREFIX = "find the person hiring"   # the start of the To line when the advert names nobody
NOTES_HEADING = "Letter notes"
NOTE_KEYS = {"Tone": "tone", "Length": "length", "Evidence": "evidence", "Checked": "checked"}
TONE_LABELS = {"plain": "Plain", "formal": "Formal", "warm": "Warm"}   # the setting's choices (catalog/cvdocs.py)
CHECKED_CLEAN = "Every fact is in your CV, record or the advert. No numbers were invented."
EVIDENCE_SHOWN = 3        # pieces of evidence named on the letter's Evidence line
PASSAGE = re.compile(r"^\[(.+?)\]$", re.M)   # the "[file > heading]" line `record.context` puts on each passage

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


def _analyse(job, who: dict) -> dict:
    return llm.chat_json(prompt("analyse", who), _job_text(job)[:12000], role="main")


def _requirements(analysis: dict) -> list[str]:
    return analysis.get("must_have", []) + analysis.get("nice_to_have", [])


def _kept(analysis: dict) -> str:
    """What the CV leaves out (certificates, the detail of each job), looked up in the employment record."""
    return record.context(_requirements(analysis) + (analysis.get("questions") or [])) or "none"


def passages(kept: str) -> list[str]:
    """The record passages a prompt was given, as "file > heading" labels."""
    return PASSAGE.findall(kept or "")


def _fit(who: dict, analysis: dict, facts: str, kept: str) -> dict:
    conditions = "\n".join(f"- {c}" for c in analysis.get("eligibility", []))
    return llm.chat_json(prompt("fit", who), "Requirements:\n" + "\n".join(f"- {r}" for r in _requirements(analysis))
                         + f"\n\nConditions:\n{conditions}\n\nToday: {date.today().isoformat()}\n\nCV:\n{facts}"
                         + f"\n\nEmployment record:\n{kept}",
                         role="main")


def _context(job, who: dict, analysis: dict, about: str, fit: dict, facts: str, kept: str, history: str) -> str:
    """The request text every drafted document shares: the job, the evidence and the person's dates."""
    return (f"Job: {job['title']} at {job['company']}\nRole summary: {analysis.get('summary', '')}\n"
            + (f"Earlier applications to this employer: {history}. Do not mention them unless asked.\n"
               if history else "") +
            f"{who['status_line']}. Mention availability only if the advert's start date is "
            f"flexible; for a fixed intake, refer to that intake instead.\n"
            f"Company: {about}\nEvidence map: {fit.get('matches', [])}\n\nCV facts:\n{facts}"
            f"\n\nEmployment record (true; use it where the CV is silent):\n{kept}")


def _letter(who: dict, context: str) -> str:
    return llm.undash(llm.chat(prompt("letter", who), context, role="writer").strip())


def check_drafts(who: dict, drafts: str, *, facts: str, kept: str, fit: dict, sources: tuple[str, ...]) -> list[str]:
    """What to check before sending: numbers no source holds, claims the facts do not support, banned words."""
    flagged = [f"number not in the CV or advert: {n}" for n in unsupported_numbers(drafts, facts, kept, *sources)]
    flagged += [f"unsupported claim: {c}" for c in
                llm.chat_json(prompt("verify", who),
                              f"Facts:\n{facts}\n\nEmployment record:\n{kept}"
                              f"\n\nEvidence map:\n{fit.get('matches', [])}\n\nDrafts:\n{drafts}",
                              role="main").get("unsupported", [])]
    flagged += [f"cliche or dash: {m}" for m in sorted({m.group(0).lower() for m in CLICHES.finditer(drafts)})]
    lowered = drafts.lower()
    flagged += [f"a phrase you never use: {a}" for a in avoided(who) if a.lower() in lowered]
    return flagged


def avoided(who: dict) -> list[str]:
    """The never-use phrases of the settings and the CV type, as the prompts list them."""
    return re.findall(r'"([^"]+)"', who.get("avoid_phrases") or "")


def letter_notes(who: dict, letter: str, evidence: list[str], flags: list[str]) -> dict:
    """The Tone, Length, Evidence and Checked lines shown beside a letter."""
    from .core import settings

    tone = TONE_LABELS.get(settings.get("cvdocs.letter_tone"), who["tone"].capitalize())
    letter_flags = [f for f in flags if not f.startswith(("REAPPLICATION", "ELIGIBILITY"))]
    return {"tone": f"{tone} (your setting)",
            "length": f"{len(letter.split())} words (you prefer about {who['letter_words']})",
            "evidence": ", ".join(evidence[:EVIDENCE_SHOWN]) or "None named",
            "checked": (CHECKED_CLEAN if not letter_flags else
                        f"{len(letter_flags)} {'thing' if len(letter_flags) == 1 else 'things'} to check before "
                        "sending: see Check before sending.")}


def write_letter(con, job, *, who: dict | None = None, analysis: dict | None = None, about: str = "",
                 kept: str | None = None, fit: dict | None = None, request: str = "") -> dict:
    """Writes and checks a cover letter for one job: {text, flags, used, notes}.

    The same path for a pack and for a letter drafted at form time: the advert read, the record searched,
    the fit map made (each unless given), then the letter and its checks. `request` holds the person's own
    words when they asked for a rewrite.
    """
    who = who or person(job)
    facts = cv.facts_text()
    analysis = analysis if analysis is not None else _analyse(job, who)
    kept = kept if kept is not None else _kept(analysis)
    fit = fit if fit is not None else _fit(who, analysis, facts, kept)
    text = _context(job, who, analysis, about, fit, facts, kept, previous_applications(con, job))
    if request:
        text += f"\n\nThe applicant asked for this change: {request}"
    letter = _letter(who, text)
    flags = check_drafts(who, letter, facts=facts, kept=kept, fit=fit, sources=(_job_text(job), about))
    used = passages(kept)
    evidence = used or [m.get("evidence", "") for m in fit.get("matches", []) if m.get("strength") == "strong"]
    return {"text": letter, "flags": flags, "used": used, "notes": letter_notes(who, letter, evidence, flags)}


def _eligibility_flag(job, who: dict, fit: dict, conditions: list) -> str:
    # with no condition to judge, Claude still answered "eligible": false ("nothing to match", 2026-10-23)
    if fit.get("eligible") is False and conditions:
        return f"ELIGIBILITY: {fit.get('eligibility_notes', 'may not be eligible')}"
    if who.get("not_eligible"):
        return f"ELIGIBILITY: {who.get('eligibility_line') or 'the advert may not be open to you'}"
    return ""


def message_to(job, analysis: dict, who: dict) -> str:
    """Who the message goes to: the people the advert names, else where to find a title the CV type names."""
    contacts = [", ".join(str(v) for v in c.values() if v) if isinstance(c, dict) else str(c)
                for c in analysis.get("contacts") or []]
    if contacts:
        return "; ".join(contacts)
    title = (who.get("hiring_titles") or ["hiring manager"])[0]
    return f"{FIND_PREFIX} on LinkedIn (search \"{job['company']} {title}\")"


def build(con, job_id: str, use_web: bool = True, ctx=None) -> Path:
    """Drafts the pack for one job and returns the pack's Markdown path.

    With `ctx` (a running action) the record passages used are noted under its Activity "Used".
    """
    job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    from . import documents

    job_text = _job_text(job)
    facts = cv.facts_text()
    out_dir = documents.pack_dir(job)
    who = person(job)

    analysis = _analyse(job, who)
    web, urls = _research(job["company"]) if use_web else ("", [])
    about = llm.chat(prompt("brief", who),
                     f"Company: {job['company']}\n\nJob advert:\n{job_text[:4000]}\n\nWeb sources:\n{web[:8000]}",
                     role="main")
    kept = _kept(analysis)
    fit = _fit(who, analysis, facts, kept)
    written = write_letter(con, job, who=who, analysis=analysis, about=about, kept=kept, fit=fit)
    letter = written["text"]
    context = _context(job, who, analysis, about, fit, facts, kept, previous_applications(con, job))
    message = llm.undash(llm.chat(prompt("message", who), context, role="writer").strip())
    questions = analysis.get("questions") or []
    answers = llm.chat_json(prompt("answers", who),
                            "Questions:\n" + "\n".join(f"- {q}" for q in [*questions, *COMMON_QUESTIONS])
                            + f"\n\n{context}", role="writer").get("answers", [])
    for a in answers:
        a["answer"] = llm.undash(a.get("answer", ""))

    variant, track = cv.tailor(job_text)
    pdf = cv.render(variant, out_dir)
    others = "\n\n".join([message, *(a.get("answer", "") for a in answers)])
    flagged = written["flags"] + check_drafts(who, others, facts=facts, kept=kept, fit=fit, sources=(job_text, about))
    # The type's special cases: a KTP advert's employer is the host company, not the scheme.
    ktp = "ktp" in who["special_cases"] and job["company"] == "KTP"
    employer = (analysis.get("employer") or job["company"]) if ktp else job["company"]
    company_flags, company_md = reputation.check(employer)
    flagged += company_flags
    history = previous_applications(con, job)
    if history:
        flagged.insert(0, f"REAPPLICATION: you applied here before: {history}")
    eligibility = _eligibility_flag(job, who, fit, analysis.get("eligibility") or [])
    if eligibility:
        flagged.insert(0, eligibility)
    if ctx is not None:
        for label in written["used"]:
            ctx.used("record", label)

    md = _markdown(job, analysis, about, urls, fit, letter, message, answers, pdf, track, flagged, who,
                   written["notes"])
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


def read_letter_notes(job) -> dict:
    """The letter's Tone, Length, Evidence and Checked lines as the pack recorded them; {} for older packs."""
    from . import documents

    notes = documents.read_pack(job).get(NOTES_HEADING, "")
    return {NOTE_KEYS[k]: v.strip() for k, v in re.findall(r"^- (\w+): (.*)$", notes, re.M) if k in NOTE_KEYS}


def _markdown(job, analysis, about, urls, fit, letter, message, answers, pdf, track, flagged, who, notes) -> str:
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
    lines += ["## Cover letter", "", "Dear Hiring Team,", "", letter, "", f"{who['signoff']},", who["name"], ""]
    lines += [f"## {NOTES_HEADING}", ""] + [f"- {k}: {notes[v]}" for k, v in NOTE_KEYS.items()] + [""]
    lines += ["## Message to the hiring manager", "", "To: " + message_to(job, analysis, who), "", message, ""]
    lines += ["## Form answers", ""]
    for a in answers:
        lines += [f"**{a.get('question', '')}**", "", textwrap.fill(a.get("answer", ""), 100), ""]
    lines += ["## Before submitting", "", *(f"- [ ] {item}" for item in CHECKLIST), ""]
    return "\n".join(lines)
