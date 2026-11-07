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
- Every draft the AI writes is respelt to the person's spelling (`spelt`, `writing.spelling`) before it is
  checked, as dashes are taken out (`llm.undash`): a spelling has one right form, so it needs no revision.
- Every draft goes through the writing checker (`writing.check`). A high-severity break (an invented number,
  a machine-made word, a staged contrast) makes the writer revise once with the problems quoted; what is
  left is listed under "Check before sending", never fixed in a loop.
- The claim check (`unsupported_claims`) works the same way (`fix_claims`): the letter, the message and the
  answers are each rewritten once without the claims about the person their facts do not support, checked
  again, and only what is left is listed. The advert and the brief are the employer's own words, never
  flagged for being restated.
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
from .profile import writing as profile_writing
from .writing import check as writing_check
from .writing import fields, spelling

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
REVISED = "Hal revised it once for its writing rules. "   # before the Checked line when the first draft broke one
SOFTENED = "Hal rewrote it once to take out claims your facts do not support. "   # ... when it made such claims
EVIDENCE_SHOWN = 3        # pieces of evidence named on the letter's Evidence line
PASSAGE = re.compile(r"^\[(.+?)\]$", re.M)   # the "[file > heading]" line `record.context` puts on each passage
QUESTION_SHARE = 0.8      # share of a question's words the advert must hold for it to count as written out
TITLED = re.compile(r"^Dear (Mr|Mrs|Ms|Miss|Mx|Dr)\.? ([^\s,]+),", re.M)   # a greeting with a title
NOTE_PARA = re.compile(r"\(.*\)\Z|(notes?|word count)\b", re.S | re.I)   # a closing paragraph about the draft

def slug(text: str) -> str:
    """Returns a file-name-safe form of a text."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _job_text(job) -> str:
    return f"{job['title']}\n{job['company']} | {job['location'] or ''} | {job['salary'] or ''}\n\n{job['description'] or ''}"


def unsupported_numbers(draft: str, *sources: str) -> list[str]:
    """Returns numbers in a draft that appear in none of the sources."""
    return writing_check.unsupported_numbers(draft, *sources)


def _research(company: str) -> tuple[str, list[str]]:
    try:
        pages = research.company_pages(company)
    except Exception:  # noqa: BLE001 - offline or blocked search still yields a pack
        pages = []
    sources = "\n\n".join(f"[{url}]\n{text}" for url, text in pages)
    return sources, [url for url, _ in pages]


def _analyse(job, who: dict) -> dict:
    text = _job_text(job)[:12000]
    analysis = llm.chat_json(prompt("analyse", who), text, role="main")
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


def _fit(who: dict, analysis: dict, facts: str, kept: str) -> dict:
    conditions = "\n".join(f"- {c}" for c in analysis.get("eligibility", []))
    return llm.chat_json(prompt("fit", who), "Requirements:\n" + "\n".join(f"- {r}" for r in _requirements(analysis))
                         + f"\n\nConditions:\n{conditions}\n\nToday: {date.today().isoformat()}\n\nCV:\n{facts}"
                         + _record_part("Employment record", kept),
                         role="main")


def _context(job, who: dict, analysis: dict, about: str, fit: dict, facts: str, kept: str, history: str) -> str:
    """The request text every drafted document shares: the job, the evidence and the person's dates."""
    return (f"Job: {job['title']} at {job['company']}\nRole summary: {analysis.get('summary', '')}\n"
            + (f"Earlier applications to this employer: {history}. Do not mention them unless asked.\n"
               if history else "") +
            f"{who['status_line']}. Mention availability only if the advert's start date is "
            f"flexible; for a fixed intake, refer to that intake instead.\n"
            f"Company: {about}\nEvidence map: {fit.get('matches', [])}\n\nCV facts:\n{facts}"
            + _record_part(f"Employment record ({record.OWN_DOCUMENTS}; use it where the CV is silent)", kept))


def spelt(text: str, who: dict) -> str:
    """An AI draft in the person's spelling (`who["spelling_code"]`; UK English when it is unset)."""
    return spelling.respell(text, who.get("spelling_code"))


def _letter(who: dict, context: str) -> str:
    return spelt(drop_notes(llm.undash(llm.chat(prompt("letter", who), context, role="writer").strip())), who)


def _hits(who: dict, text: str, kind: str, *, facts: str, kept: str, sources: tuple[str, ...]) -> list:
    """What the writing checker finds in a draft, against every fact it may draw on."""
    return writing_check.check(text, kind, sources=(facts, kept, *sources), spelling=who.get("spelling_code", ""),
                               cv_text=facts if kind == "letter" else "",
                               target_words=who.get("letter_words") if kind == "letter" else None,
                               allowed=who.get("own_words") or ())


def revise_once(draft, hits: list, rewrite):
    """The draft revised once by `rewrite(note)` when the checker found a high-severity problem.

    One revision, never a loop: what is still wrong after it is listed for the person to check, so a draft
    is never held up by the checker. Returns (text, whether it was revised).
    """
    serious = writing_check.high(hits)
    if not serious:
        return draft, False
    again = rewrite(writing_check.revision_note(serious))
    return (again or draft), bool(again)


def drop_notes(text: str) -> str:
    """A draft without the model's closing notes about it, and without Markdown bold."""
    # Claude ended a message with "(About 75 words. The advert names no individual, ...)" and began one with
    # "**Subject:** ..." (2026-10-23); neither belongs in what the person sends
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    while len(paras) > 1 and NOTE_PARA.match(paras[-1].strip()):
        paras.pop()
    return re.sub(r"\*\*(.+?)\*\*", r"\1", "\n\n".join(paras))


def unsupported_claims(who: dict, drafts: str, *, facts: str, kept: str, employer: tuple[str, ...]) -> list[str]:
    """The claims about the person in `drafts` that their facts do not support, as the verify prompt quotes them.

    The facts are the CV, the checked profile and the passages of their own documents (`kept`); the fit map
    is not one, since the AI wrote it from the same facts. The advert and the company brief (`employer`) are
    given as the employer's own words, never to be flagged for being restated: most of the "unsupported"
    claims in real packs were the employer's own facts (2026-11-06).
    """
    words = "\n\n".join(e.strip() for e in employer if e and e.strip())
    request = (f"The employer's own words (the advert and the company brief): never flag a sentence for restating "
               f"them.\n<<<EMPLOYER\n{words}\nEMPLOYER>>>\n\nFacts about the applicant:\nCV:\n{facts}"
               + _record_part(f"Employment record ({record.OWN_DOCUMENTS})", kept) + f"\n\nDrafts:\n{drafts}")
    found = llm.chat_json(prompt("verify", who), request, role="main").get("unsupported", [])
    return [str(c).strip() for c in found if str(c).strip()] if isinstance(found, list) else []


def claims_note(claims: list[str]) -> str:
    """The request to rewrite a draft without the claims its facts do not support."""
    listed = "\n".join(f"- {c}" for c in claims)
    return (f"These claims about the applicant are not in their facts:\n{listed}\nRewrite the draft: take each "
            "one out, or soften it to what the facts do say. Keep the rest as it is.")


def fix_claims(who: dict, draft, rewrite, *, facts: str, kept: str, employer: tuple[str, ...], as_text=str):
    """The draft rewritten once by `rewrite(note)` when it makes claims about the person their facts do not
    support, then checked again; returns (draft, the claims still unsupported, whether it was rewritten).

    One rewrite, never a loop, as with `revise_once`. `as_text` turns a draft (the answers are a list) into
    the text that is checked.
    """
    claims = unsupported_claims(who, as_text(draft), facts=facts, kept=kept, employer=employer)
    if not claims:
        return draft, [], False
    again = rewrite(claims_note(claims))
    if not again:
        return draft, claims, False
    return again, unsupported_claims(who, as_text(again), facts=facts, kept=kept, employer=employer), True


def check_drafts(who: dict, drafts: str, *, facts: str, kept: str, sources: tuple[str, ...], kind: str = "letter",
                 claims: list[str] | None = None) -> list[str]:
    """What to check before sending: numbers no source holds, claims the facts do not support (`claims` when
    `fix_claims` already found them, else `unsupported_claims`), the writing rules a draft breaks
    (`writing.check`) and the person's never-use phrases."""
    hits = _hits(who, drafts, kind, facts=facts, kept=kept, sources=sources)
    flagged = [h.flag for h in hits if h.rule == "T1"]
    if claims is None:
        claims = unsupported_claims(who, drafts, facts=facts, kept=kept, employer=sources)
    flagged += [f"unsupported claim: {c}" for c in claims]
    flagged += [h.flag for h in writing_check.flagged(hits) if h.rule != "T1"]
    lowered = drafts.lower()
    flagged += [f"a phrase you never use: {a}" for a in avoided(who) if a.lower() in lowered]
    return flagged


def avoided(who: dict) -> list[str]:
    """The never-use phrases of the settings and the CV type, as the prompts list them."""
    return re.findall(r'"([^"]+)"', who.get("avoid_phrases") or "")


def letter_notes(who: dict, letter: str, evidence: list[str], flags: list[str], *, revised: bool = False,
                 softened: bool = False) -> dict:
    """The Tone, Length, Evidence and Checked lines shown beside a letter; `revised` when the writer had to
    revise it once for the writing rules, `softened` when it rewrote claims the facts do not support."""
    from .core import settings

    tone = TONE_LABELS.get(settings.get("cvdocs.letter_tone"), who["tone"].capitalize())
    letter_flags = [f for f in flags if not f.startswith(("REAPPLICATION", "ELIGIBILITY"))]
    # the fit map's evidence is sentences ("... spreadsheet.", 2026-10-23), so the line joins them with ";"
    shown = [e.strip().rstrip(".") for e in evidence[:EVIDENCE_SHOWN] if e.strip()]
    return {"tone": f"{tone} (your setting)",
            "length": f"{len(letter.split())} words (you prefer about {who['letter_words']})",
            "evidence": "; ".join(shown) or "None named",
            "checked": (REVISED if revised else "") + (SOFTENED if softened else "") + (
                CHECKED_CLEAN if not letter_flags else
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
    text += "\n\n" + fields.block(field_of(job, who, analysis), "letter")
    if request:
        text += f"\n\nThe applicant asked for this change: {request}"
    letter = _letter(who, text)
    sources = (_job_text(job), about)
    letter, revised = revise_once(letter, _hits(who, letter, "letter", facts=facts, kept=kept, sources=sources),
                                  lambda note: _letter(who, f"{text}\n\nYour first draft:\n{letter}\n\n{note}"))
    letter, claims, softened = fix_claims(who, letter, lambda note: _letter(who, f"{text}\n\nYour draft:\n{letter}"
                                                                            f"\n\n{note}"),
                                          facts=facts, kept=kept, employer=sources)
    flags = check_drafts(who, letter, facts=facts, kept=kept, sources=sources, claims=claims)
    used = passages(kept)
    evidence = used or [m.get("evidence", "") for m in fit.get("matches", []) if m.get("strength") == "strong"]
    return {"text": letter, "flags": flags, "used": used, "revised": revised, "softened": softened,
            "notes": letter_notes(who, letter, evidence, flags, revised=revised, softened=softened)}


def _answers_text(answers: list[dict]) -> str:
    return "\n".join(f"- {a.get('question', '')}: {a.get('answer', '')}" for a in answers)


def _answers(who: dict, questions: list[str], context: str, *, facts: str, kept: str,
             sources: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    """Answers to the advert's questions, revised once together when one breaks a high-severity rule and once
    more when they make claims the facts do not support; returns (answers, the claims still unsupported)."""
    request = "Questions:\n" + "\n".join(f"- {q}" for q in questions) + f"\n\n{context}"

    def ask(text: str) -> list[dict]:
        found = llm.chat_json(prompt("answers", who), text, role="writer").get("answers", [])
        return [{**a, "answer": spelt(llm.undash(a.get("answer", "")), who)} for a in found if isinstance(a, dict)]

    answers = ask(request)
    hits = [h for a in answers for h in _hits(who, a["answer"], "answer", facts=facts, kept=kept, sources=sources)]
    answers = revise_once(answers, hits,
                          lambda note: ask(f"{request}\n\nYour first answers:\n{_answers_text(answers)}\n\n{note}"))[0]
    answers, claims, _ = fix_claims(who, answers,
                                    lambda note: ask(f"{request}\n\nYour answers:\n{_answers_text(answers)}\n\n{note}"),
                                    facts=facts, kept=kept, employer=sources, as_text=_answers_text)
    return answers, claims


def _message(who: dict, context: str, analysis: dict, job_text: str) -> str:
    """The message to the person hiring, cleaned of the model's notes and of a title the advert never gives."""
    return untitled(spelt(drop_notes(llm.undash(llm.chat(prompt("message", who), context, role="writer").strip())),
                          who), analysis, job_text)


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


def contact_names(analysis: dict) -> list[str]:
    """The names of the people the advert names; an email address is not a name."""
    names = [(str(c.get("name") or "") if isinstance(c, dict) else str(c).split(",")[0]).strip()
             for c in analysis.get("contacts") or []]
    return [n for n in names if n and "@" not in n]


def salutation(analysis: dict) -> str:
    """The letter's greeting: the first person the advert names, else the hiring team."""
    # the letter said "Dear Hiring Team" while the message beside it greeted the person the advert names
    # (made-up humanities advert, 2026-10-23)
    names = contact_names(analysis)
    return f"Dear {names[0]}," if names else "Dear Hiring Team,"


def untitled(message: str, analysis: dict, advert: str) -> str:
    """The message greeting the named person by full name, without a title (Ms, Mr) the advert never gives."""
    # Claude greeted the advert's "Ada Penhallow" as "Dear Ms Penhallow," (2026-10-23), a guess at her title
    m = TITLED.search(message)
    if not m or re.search(rf"\b{m.group(1)}\.? {re.escape(m.group(2))}\b", advert):
        return message
    for name in contact_names(analysis):
        if name.split()[-1] == m.group(2):
            return message[:m.start()] + f"Dear {name}," + message[m.end():]
    return message


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
    their_words = (job_text, about)
    message = _message(who, context, analysis, job_text)
    message, message_claims, _ = fix_claims(
        who, message, lambda note: _message(who, f"{context}\n\nYour draft:\n{message}\n\n{note}", analysis, job_text),
        facts=facts, kept=kept, employer=their_words)
    questions = analysis.get("questions") or []
    answers, answer_claims = _answers(who, [*questions, *COMMON_QUESTIONS],
                                      f"{context}\n\n{fields.block(field_of(job, who, analysis), 'answer')}",
                                      facts=facts, kept=kept, sources=their_words)

    variant, track = cv.tailor(job_text)
    pdf = cv.render(variant, out_dir)
    others = "\n\n".join([message, *(a.get("answer", "") for a in answers)])
    flagged = written["flags"] + check_drafts(who, others, facts=facts, kept=kept, sources=their_words, kind="answer",
                                              claims=message_claims + answer_claims)
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
    lines += ["## Cover letter", "", salutation(analysis), "", letter, "", f"{who['signoff']},", who["name"], ""]
    lines += [f"## {NOTES_HEADING}", ""] + [f"- {k}: {notes[v]}" for k, v in NOTE_KEYS.items()] + [""]
    lines += ["## Message to the hiring manager", "", "To: " + message_to(job, analysis, who), "", message, ""]
    lines += ["## Form answers", ""]
    for a in answers:
        lines += [f"**{a.get('question', '')}**", "", textwrap.fill(a.get("answer", ""), 100), ""]
    lines += ["## Before submitting", "", *(f"- [ ] {item}" for item in CHECKLIST), ""]
    return "\n".join(lines)
