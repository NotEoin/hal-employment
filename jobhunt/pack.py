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
- The claim check (`unsupported_claims`) works the same way (`fix_claims`) for a draft with no evidence
  markers: the letter, the message and the answers are each rewritten once without the claims about the
  person their facts do not support, checked again, and only what is left is listed. The advert and the
  brief are the employer's own words, never flagged for being restated.
- The letter's first paragraph and the "why do you want to work here" answers are checked, with no AI, for
  sentences that restate the advert or the brief (`drafts.echoes`) and rewritten once to tie something of
  the applicant's own to the employer (`fix_echoes`), before the claim check reads them: 26 generic
  sentences in five real packs were the advert's words given back as a reason (2026-11-07).
- An offer to relocate to the town the person lives in is taken out of the letter, the message and the
  answers with no AI (`relocation.without_own_move`): a letter offered to relocate to the London office of a
  person living in London (2026-11-07).
- A form answer that repeats an earlier one nearly word for word is rewritten once, with the others, to
  use other evidence (`fix_repeats`, `repeats.repeated`), and the fit map lists each gap once
  (`repeats.unique_gaps`): two answers of a real pack gave the same details and a gap was listed twice
  (2026-11-07). After the sentence checks, a sentence of the letter, the message or an answer that says
  what an earlier one said, in its own draft or an earlier one, is rewritten once from unused evidence,
  else taken out or listed (`_unrepeated`, `grounded.unrepeat`).
- Each draft rests on an evidence plan picked before it is written (`evidence_plan`, `writing.plan`): quotes
  of the CV, the Profile block and the record, each with its card's context and the person's part, as a
  local model framed projects in the wrong place and wrote team work as solo when it wrote freely
  (2026-11-07). The letter, the message and the answers mark each sentence with the items it rests on
  (`writing.cites`), and each sentence is checked against only what it cites (`settle`, `writing.grounded`)
  in place of the whole-draft claim check, whose single call over every draft listed about 20 false alarms
  for 10 real problems in five packs (2026-11-07). The markers are taken out before a draft is returned.
  The letter's reason for the employer comes from a pairing of one employer fact with one plan item
  (`writing.motive`), or it gives none, as letters gave generic motivation that echoed the advert.
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
from .writing import (
    cites,
    drafts,
    eligibility,
    fields,
    flow,
    frame,
    grounded,
    motive,
    opening,
    paragraphs,
    plain,
    relocation,
    repeats,
    spelling,
    stage,
    taught,
)
from .writing import plan as planning

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
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# A shared mailbox for applications, never a person hiring: careers@, recruitment@, jobs@, hr@ and the like.
PROCESS_MAILBOX = re.compile(r"^(careers?|recruit\w*|jobs?|talent\w*|hr|hiring|apply|applications?|vacanc\w*|"
                             r"resourcing|people|graduates?|early[-_.]?careers|internships?|enquiries|info)"
                             r"([-_.][\w.-]*)?@", re.I)
# Words an advert uses for a contact who answers questions about applying, not about the work.
PROCESS_WORDS = re.compile(r"\b(application process|the process|questions about applying|how to apply|questions "
                           r"about (the|your|this|an) application|application (queries|questions|enquiries)|"
                           r"recruitment|recruiter|careers? team|talent (team|acquisition|partner)|human resources|"
                           r"hr|technical (issues|problems|difficulties)|reasonable adjustments)\b", re.I)
NOTES_HEADING = "Letter notes"
NOTE_KEYS = {"Tone": "tone", "Length": "length", "Evidence": "evidence", "Checked": "checked"}
TONE_LABELS = {"plain": "Plain", "formal": "Formal", "warm": "Warm"}   # the setting's choices (catalog/cvdocs.py)
CHECKED_CLEAN = "Every fact is in your CV, record or the advert. No numbers were invented."
REVISED = "Hal revised it once for its writing rules. "   # before the Checked line when the first draft broke one
SOFTENED = "Hal rewrote it once to take out claims your facts do not support. "   # ... when it made such claims
CUT_NOTE = "Your last reply stopped mid-sentence. Write the whole draft again, and end it with a full sentence."
ROOM_CTX = 16384          # tokens for the second ask after a cut-off reply, as for the CV score and critique
# The letter and the message are drafted cooler than other prose, as each paragraph is (`writing.paragraphs`).
DRAFT_TEMPERATURE = paragraphs.DRAFT_TEMPERATURE
# Letters drafted independently, the one with the fewest sentences still failing kept (`write_letter`); the
# second is skipped when the first needed no rewrite, as it could only tie. Two keep a pack within a few
# minutes on a local model: each draft adds one call and a call per sentence checked (`tests/test_pack.py`
# counts them).
LETTER_DRAFTS = 2
CUT_FLAG = "the {what} stopped mid-sentence twice; Hal kept it to its last full sentence, so check its ending"
CUT_WHAT = {"letter": "cover letter", "message": "message to the person hiring"}
ECHO_FLAG = "restates the advert rather than your own reason"   # a sentence still echoing after its rewrite
NOTHING_TO_CHECK = "Nothing to check."   # the "Check before sending" section when it lists nothing
CHECK_HEADING = "## Check before sending"
# A letter left short after its sentence checks and the one added paragraph (`flow.short`).
SHORT_FLAG = ("the letter is {n} words, well under the {words} you prefer; add a paragraph of your own evidence "
              "before you send it")
DANGLING_FLAG = "opens a paragraph by pointing back at something the paragraph before does not say"
REPEAT_FLAG = "says what an earlier sentence or draft already says"   # still repeating after its rewrite
EVIDENCE_SHOWN = 3        # pieces of evidence named on the letter's Evidence line
PASSAGE = re.compile(r"^\[(.+?)\]$", re.M)   # the "[file > heading]" line `record.context` puts on each passage
QUESTION_SHARE = 0.8      # share of a question's words the advert must hold for it to count as written out
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


def _fit(who: dict, analysis: dict, facts: str, kept: str, job=None) -> dict:
    """The fit map: each requirement matched to evidence from the CV, every checked card and the record, then
    made complete and consistent with no AI (`complete_fit`, `consistent_fit`); a must-have the reply left out
    is asked for once more."""
    conditions = "\n".join(f"- {c}" for c in analysis.get("eligibility", []))
    cards = profile_writing.cards()
    shown = profile_writing.cards_block(cards)

    def ask(requirements: list[str]) -> dict:
        return with_card_tools(llm.chat_json(
            prompt("fit", who), "Requirements:\n" + "\n".join(f"- {r}" for r in requirements)
            + f"\n\nConditions:\n{conditions}\n\n{stage.today_lines()}\n\nCV:\n{facts}"
            + (f"\n\n{shown}" if shown else "") + _record_part("Employment record", kept), role="main"), cards)

    fit = complete_fit(ask(_requirements(analysis)), analysis.get("must_have") or [], ask)
    fit = consistent_fit(fit, eligibility.person(), reputation.towns(reputation.advert_of(job)), facts)
    fit = checked_fit_tools(fit, cards, known_tools(cards), facts)
    return {**fit, "gaps": repeats.unique_gaps(fit.get("gaps") or [])}


def known_tools(cards: list[dict]) -> list[str]:
    """Every tool a checked card or skill names, and every tool a card's plan names (`planned`): what a draft or
    fit row may wrongly give to a project."""
    return [t for c in cards for t in (*c["tools"], *c.get("planned", ()))] + profile_writing.skill_names()


NO_EVIDENCE = re.compile(r"^\s*(no|none|not)\b|\bno (evidence|mention)\b", re.I)   # a fit row that found nothing
ROW_SHARE = 0.6           # share of a requirement's words a fit row must hold to be that requirement's row
MISSING_GAP = "{requirement} (no evidence found)"   # a must-have still missing after the second ask


def _covers(said: str, requirement: str) -> bool:
    wanted = set(_words(requirement))
    return bool(wanted) and len(wanted & set(_words(said))) >= ROW_SHARE * len(wanted)


def missing_rows(fit: dict, must_haves: list[str]) -> list[str]:
    """The must-haves no row or gap of the fit map speaks for."""
    said = [str(m.get("requirement") or "") for m in fit.get("matches") or [] if isinstance(m, dict)]
    said += [str(g) for g in fit.get("gaps") or []]
    return [r for r in (str(r).strip() for r in must_haves) if r and not any(_covers(s, r) for s in said)]


def complete_fit(fit: dict, must_haves: list[str], ask) -> dict:
    """The fit map with a row or gap for every must-have: one more `ask(missing)` for those the reply left out,
    and a gap for any still missing (a requirement went missing from a real fit table, 2026-11-07)."""
    missing = missing_rows(fit, must_haves)
    if not missing:
        return fit
    more = ask(missing)
    fit = {**fit, "matches": [*(fit.get("matches") or []), *(more.get("matches") or [])],
           "gaps": [*(fit.get("gaps") or []), *(more.get("gaps") or [])]}
    left = missing_rows(fit, missing)
    return {**fit, "gaps": [*fit["gaps"], *(MISSING_GAP.format(requirement=r) for r in left)]}


def consistent_fit(fit: dict, you, towns: list[str], facts: str) -> dict:
    """The fit map with rows that agree with their evidence and the person's settings: a row or gap about
    where the job is that where the person lives settles is met, with the reason
    (`eligibility.where_you_live_reason`); a row whose evidence says there is none is a gap, never partial
    or strong. A real table rated "London office three days a week" partial, "no evidence of refusal", for a
    person living in London (2026-11-07)."""
    matches, gaps = [], []
    for m in (dict(m) for m in fit.get("matches") or [] if isinstance(m, dict)):
        requirement = str(m.get("requirement") or "")
        reason = eligibility.where_you_live_reason(requirement, you, towns, facts)
        if reason:
            matches.append({**m, "evidence": reason, "strength": "strong"})
        elif NO_EVIDENCE.search(str(m.get("evidence") or "")):
            gaps.append(requirement)
        else:
            matches.append(m)
    for gap in fit.get("gaps") or []:
        reason = eligibility.where_you_live_reason(str(gap), you, towns, facts)
        if reason:
            matches.append({"requirement": str(gap), "evidence": reason, "strength": "strong"})
        else:
            gaps.append(gap)
    return {**fit, "matches": matches, "gaps": gaps}


def checked_fit_tools(fit: dict, cards: list[dict], known: list[str], facts: str) -> dict:
    """The fit map with every tool a row gives to a project card that never used it taken out of the row, and
    each card of a similar name named with its tag (`drafts.evidence_checked`).

    A part of a row's evidence left with no tool is dropped; a row that lost evidence is partial at best, and
    one left with none is a gap. A fit row said two projects used Pandas, Scikit-learn and Matplotlib when no
    source had either using them (2026-11-07), and rows gave a local assistant its namesake's stack
    (2026-11-08).
    """
    matches, gaps = [], list(fit.get("gaps") or [])
    for m in (dict(m) for m in fit.get("matches") or [] if isinstance(m, dict)):
        evidence, dropped = drafts.evidence_checked(str(m.get("evidence") or ""), cards, known, facts)
        if not dropped:
            matches.append({**m, "evidence": evidence} if m.get("evidence") else m)
        elif evidence:
            matches.append({**m, "evidence": evidence,
                            "strength": "partial" if m.get("strength") == "strong" else m.get("strength")})
        else:
            gaps.append(str(m.get("requirement") or ""))
    return {**fit, "matches": matches, "gaps": gaps}


def with_card_tools(fit: dict, cards: list[dict]) -> dict:
    """The fit map with every requirement that names a checked card's tool given that card as evidence.

    A gap or a "no evidence" row naming a tool a card lists becomes a partial match citing the card: the
    fit table said "No evidence of AWS" although a project card lists AWS (2026-11-06).
    """
    def card_for(requirement: str) -> str:
        for c in cards:
            tool = next((t for t in c["tools"] if drafts.names_tool(requirement, t)), None)
            if tool:
                return f"{c['kind']}: {c['name']} (tools: {tool})"
        return ""

    matches = [dict(m) for m in fit.get("matches") or [] if isinstance(m, dict)]
    for m in matches:
        found = card_for(str(m.get("requirement") or "")) if NO_EVIDENCE.search(str(m.get("evidence") or "")) else ""
        if found:
            m.update(evidence=found, strength="partial")
    gaps = []
    for gap in fit.get("gaps") or []:
        found = card_for(str(gap))
        if found:
            matches.append({"requirement": str(gap), "evidence": found, "strength": "partial"})
        else:
            gaps.append(gap)
    return {**fit, "matches": matches, "gaps": gaps}


def evidence_plan(analysis: dict, facts: str, kept: str, questions: tuple[str, ...] = ()) -> list[planning.Item]:
    """The evidence the drafts for one job may rest on (`writing.plan`): picked once from the CV, the Profile
    block and the record passages for the advert's requirements and `questions`."""
    return planning.make(_requirements(analysis), [*(analysis.get("questions") or []), *questions], facts, kept,
                         profile_writing.cards())


def _plan_part(plan: list[planning.Item]) -> str:
    """The evidence plan's section of a writing request; "" for an empty plan, which leaves the draft unmarked."""
    return f"\n\n{planning.block(plan)}" if plan else ""


def _context(job, who: dict, analysis: dict, about: str, fit: dict, facts: str, kept: str, history: str) -> str:
    """The request text every drafted document shares: the job, the evidence and the person's dates."""
    return (f"Job: {job['title']} at {job['company']}\nRole summary: {analysis.get('summary', '')}\n"
            + (f"Earlier applications to this employer: {history}. Do not mention them unless asked.\n"
               if history else "") +
            f"{stage.today_lines()}\nMention availability only if the advert's start date is "
            f"flexible; for a fixed intake, refer to that intake instead.\n"
            f"Company: {about}\nEvidence map: {fit.get('matches', [])}\n\nCV facts:\n{facts}"
            + _record_part(f"Employment record ({record.OWN_DOCUMENTS}; use it where the CV is silent)", kept))


def read_job(job, who: dict, facts: str) -> dict:
    """What one job's documents are written from, as `write_letter` takes it: {analysis, kept, fit}."""
    analysis = _analyse(job, who)
    kept = _kept(analysis)
    return {"analysis": analysis, "kept": kept, "fit": _fit(who, analysis, facts, kept, job)}


def shared_context(con, job, who: dict, read: dict, about: str, facts: str) -> str:
    """The request text the letter, message and answers share (`_context`) for a `read_job` reading."""
    return _context(job, who, read["analysis"], about, read["fit"], facts, read["kept"],
                    previous_applications(con, job))


def spelt(text: str, who: dict) -> str:
    """An AI draft in the person's spelling (`who["spelling_code"]`; UK English when it is unset)."""
    return spelling.respell(text, who.get("spelling_code"))


def _draft(who: dict, prompt_id: str, user: str, trimmed: set[str]) -> str:
    """A text draft (letter or message) at `DRAFT_TEMPERATURE`, asked for again once with more room when the
    reply stops mid-sentence (`drafts.cut_off`); when that one stops too it is kept to its last full sentence
    and added to `trimmed`, so "Check before sending" can say so if it is the draft that is kept."""
    def ask(text: str, ctx: int | None = None) -> str:
        said = llm.chat(prompt(prompt_id, who), text, role="writer", ctx=ctx, temperature=DRAFT_TEMPERATURE).strip()
        return cites.normalise(spelt(drop_notes(llm.undash(said)), who))

    draft = ask(user)
    if not drafts.cut_off(cites.strip(draft)):
        return draft
    again = ask(f"{user}\n\n{CUT_NOTE}", ROOM_CTX)
    if not drafts.cut_off(cites.strip(again)):
        return again
    kept = drafts.last_full_sentence(again)
    trimmed.add(kept)
    return kept


def _letter(who: dict, context: str, trimmed: set[str] | None = None) -> str:
    return _draft(who, "letter", context, set() if trimmed is None else trimmed)


def _hits(who: dict, text: str, kind: str, *, facts: str, kept: str, sources: tuple[str, ...]) -> list:
    """What the writing checker finds in a draft, without its evidence markers, against every fact it may draw
    on."""
    return writing_check.check(cites.strip(text), kind, sources=(facts, kept, *sources),
                               spelling=who.get("spelling_code", ""), cv_text=facts if kind == "letter" else "",
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


def unsupported_claims(who: dict, written: str, *, facts: str, kept: str, employer: tuple[str, ...]) -> list[str]:
    """The claims about the person in the drafts (`written`) that their facts do not support, as the verify
    prompt quotes them.

    The facts are the CV, every checked project and job card with its own tools, the checked profile and the
    passages of their own documents (`kept`); the fit map is not one, since the AI wrote it from the same
    facts. The advert and the company brief (`employer`) are given as the employer's own words, never to be
    flagged for being restated: most of the "unsupported" claims in real packs were the employer's own facts
    (2026-11-06). A claim one CV line states (`drafts.stated_in`) is supported, whatever the AI said. Three
    kinds are found with no AI as well: a sentence that speaks of finishing a course that
    has ended (`stage.ended_course`), a tool given to a project whose own facts never name it
    (`drafts.misplaced_tools`), a project placed in a degree or in the person's own time against its
    card's context (`drafts.wrong_context`), and what a named module covered when no source naming the module
    says it (`taught.unsupported`). Each goes into the one rewrite `fix_claims` asks for. A draft that cites an
    evidence plan is checked sentence by sentence instead (`settle`); this whole-draft check is for drafts
    with no markers, and for form filling.
    """
    written = cites.strip(written)
    now = stage.current()
    cards = profile_writing.cards()
    shown = profile_writing.cards_block(cards)
    words = "\n\n".join(e.strip() for e in employer if e and e.strip())
    request = (f"The employer's own words (the advert and the company brief): never flag a sentence for restating "
               f"them.\n<<<EMPLOYER\n{words}\nEMPLOYER>>>\n\nFacts about the applicant:\n{stage.today_lines(now)}\n"
               f"CV:\n{facts}" + (f"\n\n{shown}" if shown else "")
               + _record_part(f"Employment record ({record.OWN_DOCUMENTS})", kept) + f"\n\nDrafts:\n{written}")
    found = llm.chat_json(prompt("verify", who), request, role="main").get("unsupported", [])
    claims = [str(c).strip() for c in found if str(c).strip()] if isinstance(found, list) else []
    claims = [c for c in claims if not drafts.stated_in(c, facts)]
    known = known_tools(cards)
    return claims + [f"{s} ({stage.ended_said(now)})" for s in stage.ended_course(written, now)] \
        + drafts.misplaced_tools(written, cards, known) + drafts.wrong_context(written, cards) \
        + drafts.crossed(written, cards) \
        + taught.unsupported(written, profile_writing.module_names(), (facts, kept))


def claims_note(claims: list[str]) -> str:
    """The request to rewrite a draft without the claims its facts do not support."""
    listed = "\n".join(f"- {c}" for c in claims)
    return (f"These claims about the applicant are not in their facts:\n{listed}\nRewrite the draft: take each "
            "one out, or soften it to what the facts do say. Keep the rest as it is.")


def echo_note(sentences: list[str]) -> str:
    """The request to rewrite the sentences that restate the advert or brief as the applicant's own reason."""
    listed = "\n".join(f"- {s}" for s in sentences)
    return (f"These sentences restate the advert or the company brief instead of giving the applicant's own "
            f"reason:\n{listed}\nRewrite each one to tie one specific thing about the applicant (a named project, "
            "module or interest from their facts) to one specific thing about this employer. Keep the rest as it "
            "is.")


def fix_echoes(draft, echoed_in, rewrite):
    """The draft rewritten once by `rewrite(note)` when `echoed_in(draft)` finds sentences that restate the
    advert or brief (`drafts.echoes`); returns (draft, whether it was rewritten). One rewrite, never a loop:
    what still echoes is listed by the caller."""
    found = echoed_in(draft)
    if not found:
        return draft, False
    again = rewrite(echo_note(found))
    return (again or draft), bool(again)


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


def grounds(who: dict, plan: list[planning.Item], *, facts: str, kept: str, employer: tuple[str, ...],
            pairing: motive.Pairing | None = None) -> grounded.Facts:
    """What each sentence of a marked draft is checked against (`writing.grounded`): the plan, the cards and
    tools, the employer's words and the stage line (numbers there are not claims), modules and sources, and
    the motivation `pairing`'s item for a letter's reason."""
    cards, now = profile_writing.cards(), stage.current()
    words = "\n\n".join(e for e in employer if e)
    return grounded.Facts({i.id: i for i in plan}, cards, known_tools(cards), f"{words}\n{stage.today_lines(now)}",
                          now, profile_writing.module_names(), (facts, kept), who,
                          pairing.item if pairing else None, tuple(applicant_names(who)),
                          tuple(plain.settings_labels()))


def settle(who: dict, draft: str, rewrite, *, plan: list[planning.Item], essential: grounded.Essential, facts: str,
           kept: str, employer: tuple[str, ...], pairing: motive.Pairing | None = None,
           words: int | None = None) -> grounded.Grounded:
    """A letter or message checked for what it says about the person: its text, what is still wrong and what
    was done. One that cites the evidence `plan` is checked sentence by sentence (`grounded.ground`), and a
    letter left short of `words` gets one paragraph more (`grounded.lengthen`); one with no markers is
    checked whole and rewritten once (`fix_claims`)."""
    if plan and cites.has_markers(draft):
        against = grounds(who, plan, facts=facts, kept=kept, employer=employer, pairing=pairing)
        return grounded.lengthen(grounded.ground(draft, against, essential), against, words, essential)
    text, claims, again = fix_claims(who, draft, rewrite, facts=facts, kept=kept, employer=employer)
    return grounded.Grounded(text, claims, int(again), marked=False)


def settle_answers(who: dict, answers: list[dict], rewrite, *, plan: list[planning.Item], facts: str, kept: str,
                   employer: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    """The answers checked as `settle` checks a letter, each answer on its own when they cite the plan (an
    answer's first sentence answers the question, so it is never cut); returns (answers, what is still
    wrong)."""
    if not (plan and any(cites.has_markers(str(a.get("answer") or "")) for a in answers)):
        return fix_claims(who, answers, rewrite, facts=facts, kept=kept, employer=employer, as_text=_answers_text)[:2]
    against = grounds(who, plan, facts=facts, kept=kept, employer=employer)
    out, failed = [], []
    for a in answers:
        done = grounded.ground(str(a.get("answer") or ""), against, grounded.first_of_answer)
        out.append({**a, "answer": done.text})
        failed += done.failed
    return out, failed


def check_drafts(who: dict, drafts: str, *, facts: str, kept: str, sources: tuple[str, ...], kind: str = "letter",
                 claims: list[str] | None = None) -> list[str]:
    """What to check before sending: numbers no source holds, claims the facts do not support (`claims` when
    `fix_claims` already found them, else `unsupported_claims`), the writing rules a draft breaks
    (`writing.check`) and the person's never-use phrases."""
    drafts = cites.strip(drafts)
    hits = _hits(who, drafts, kind, facts=facts, kept=kept, sources=sources)
    flagged = [h.flag for h in hits if h.rule == "T1"]
    if claims is None:
        claims = unsupported_claims(who, drafts, facts=facts, kept=kept, employer=sources)
    flagged += [f"unsupported claim: {c}" for c in claims]
    flagged += [h.flag for h in writing_check.flagged(hits) if h.rule != "T1"]
    names, labels = applicant_names(who), plain.settings_labels()
    flagged += [f"{p}: {s}" for s in writing_check.sentences(drafts) if not any(s in c for c in claims)
                for p in plain.problems(s, names, labels)]
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


def paragraph_scope(who: dict, job, analysis: dict, about: str, *, facts: str, kept: str,
                    found: list[planning.Story], cards: list[dict], request: str = "",
                    kind: str = "letter") -> paragraphs.Scope:
    """What each paragraph of one job's `kind` of draft is drafted with and checked against beside its own story
    (`writing.paragraphs`): the job and its advert, today, the field's conventions, the employer's words, every
    story and card, and the person's names, modules and settings wording."""
    now, advert = stage.current(), _job_text(job)
    context = (f"The job: {job['title']} at {job['company']}\nThe advert (for what the job is; never copy its "
               f"words):\n{advert[:paragraphs.ADVERT_CHARS]}\n\n{stage.today_lines(now)}\n\n"
               + fields.block(field_of(job, who, analysis), kind))
    return paragraphs.Scope(tuple(found), tuple(cards), tuple(known_tools(cards)),
                            f"{advert}\n{about}\n{stage.today_lines(now)}", advert, about,
                            (job["title"], job["company"]), now, tuple(profile_writing.module_names()), (facts, kept),
                            tuple(applicant_names(who)), tuple(plain.settings_labels()), who, context.strip(), request)


def letter_frame(who: dict, job, scope: paragraphs.Scope, pairing: motive.Pairing | None,
                 told: tuple[str, ...]) -> frame.Facts:
    """The fixed facts of a letter's opening and closing: the person's name and course (`opening.described`), the
    role, the reason for this employer and the names of the work the middle tells."""
    name = str(who.get("name") or "")
    now = scope.now or stage.current()
    return frame.Facts("" if name == SOMEONE else name, opening.described(now), job["title"], job["company"],
                            pairing, told, now.institution)


def _story_letter(con, job, who: dict, scope: paragraphs.Scope, *, fit: dict, kept: str, facts: str,
                  plan: list[planning.Item], pairing: motive.Pairing | None) -> dict:
    """A letter told one piece of work a paragraph (`writing.paragraphs`): up to `plan.LETTER_STORIES` stories,
    each drafted from its own facts and checked against them, between an opening and a closing drafted from the
    fixed facts of who is writing, the role and the reason for this employer (`letter_frame`, `writing.frame`)."""
    bodies = [paragraphs.settle(s, paragraphs.LETTER, scope)
              for s in planning.choose(list(scope.stories), planning.LETTER_STORIES, {})]
    told = tuple(b.story.name for b in bodies if b.text and b.story.name)
    facts_said = letter_frame(who, job, scope, pairing, told)
    framed = frame.settle(facts_said, scope)
    cards = list(scope.cards)
    text = "\n\n".join(p for p in (framed.opening, *(b.text for b in bodies), framed.closing) if p)
    letter = plain.tidy(relocation.without_own_move(text, eligibility.person(con)), cards)
    flags = check_drafts(who, letter, facts=facts, kept=kept, sources=(scope.advert, scope.brief, facts_said.words(),
                                                                       scope.allowed), claims=[])
    flags += [b.listed for b in bodies if b.dropped]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(letter)]
    if flow.short(letter, who.get("letter_words")):
        flags.append(SHORT_FLAG.format(n=flow.word_count(letter), words=who["letter_words"]))
    softened = any(b.redrafted or b.cut for b in bodies) or framed.redrafted
    shown = [_item_note(i, cards) for i in plan]
    shown = [e for e in (drafts.evidence_checked(s, cards, known_tools(cards), facts)[0] for s in shown) if e]
    return {"text": letter, "flags": flags, "used": passages(kept), "revised": False, "softened": softened,
            "plan": plan, "pairing": pairing, "told": [b.story.key for b in bodies if b.text],
            "calls": framed.calls + sum(b.calls for b in bodies),
            "notes": letter_notes(who, letter, shown, flags, softened=softened)}


def write_letter(con, job, *, who: dict | None = None, analysis: dict | None = None, about: str = "",
                 kept: str | None = None, fit: dict | None = None, request: str = "",
                 plan: list[planning.Item] | None = None) -> dict:
    """Writes and checks a cover letter for one job: {text, flags, used, notes, plan, pairing}.

    The same path for a pack and for a letter drafted at form time: the advert read, the record searched,
    the fit map made and the evidence plan picked (`evidence_plan`), each unless given, then the letter and its
    checks. `request` holds the person's own words when they asked for a rewrite. Its reason for the employer
    comes from a motivation pairing (`writing.motive`), or it gives none. When the plan tells any piece of work
    (`plan.stories`), the letter is one paragraph per piece of work (`_story_letter`). Otherwise it cites the plan's
    items (`writing.cites`), each sentence is checked against its own (`settle`), and when the first draft
    needed a rewrite a second is drafted and the better kept (`LETTER_DRAFTS`, `grounded.Grounded.key`); its
    markers are taken out before it is returned.
    """
    who = who or person(job)
    facts = cv.facts_text()
    analysis = analysis if analysis is not None else _analyse(job, who)
    kept = kept if kept is not None else _kept(analysis)
    fit = fit if fit is not None else _fit(who, analysis, facts, kept, job)
    plan = plan if plan is not None else evidence_plan(analysis, facts, kept)
    names = (job["title"], job["company"])
    pairing = motive.make(names, _job_text(job), about, plan)
    cards = profile_writing.cards()
    found = planning.stories(plan, cards)
    if found:
        scope = paragraph_scope(who, job, analysis, about, facts=facts, kept=kept, found=found, cards=cards,
                                request=request, kind="letter")
        return _story_letter(con, job, who, scope, fit=fit, kept=kept, facts=facts, plan=plan, pairing=pairing)
    text = _context(job, who, analysis, about, fit, facts, kept, previous_applications(con, job))
    text += _plan_part(plan) + (f"\n\n{motive.block(pairing)}" if plan else "")
    text += "\n\n" + fields.block(field_of(job, who, analysis), "letter")
    if request:
        text += f"\n\nThe applicant asked for this change: {request}"
    trimmed: set[str] = set()
    sources = (_job_text(job), about)

    def echoed_in(draft: str) -> list[str]:
        return drafts.echoed(drafts.first_paragraph(cites.strip(draft)), *sources, names)

    def one_letter() -> tuple[grounded.Grounded, bool]:
        """One letter drafted, revised for the writing rules and the advert's words, and checked: (it, revised)."""
        letter = _letter(who, text, trimmed)
        letter, revised = revise_once(letter, _hits(who, letter, "letter", facts=facts, kept=kept, sources=sources),
                                      lambda note: _letter(who, f"{text}\n\nYour first draft:\n{letter}\n\n{note}",
                                                           trimmed))
        letter, _ = fix_echoes(letter, echoed_in,
                               lambda note: _letter(who, f"{text}\n\nYour draft:\n{letter}\n\n{note}", trimmed))
        return settle(who, letter, lambda note: _letter(who, f"{text}\n\nYour draft:\n{letter}\n\n{note}", trimmed),
                      plan=plan, essential=grounded.only_in_paragraph, facts=facts, kept=kept,
                      employer=sources, pairing=pairing, words=who.get("letter_words")), revised

    best, revised = one_letter()
    for _ in range(LETTER_DRAFTS - 1):
        if not best.marked or (not best.failed and not best.rewrites):
            break
        again, again_revised = one_letter()
        if again.key() < best.key():
            best, revised = again, again_revised
    cut = best.text in trimmed
    unique = grounded.unrepeat(best.text, grounds(who, plan, facts=facts, kept=kept, employer=sources,
                                                  pairing=pairing), grounded.only_in_paragraph)
    claims, softened = best.failed, bool(best.rewrites or best.removed or unique.rewrites or unique.removed)
    letter = plain.tidy(relocation.without_own_move(cites.strip(unique.text), eligibility.person(con)),
                        profile_writing.cards())
    flags = check_drafts(who, letter, facts=facts, kept=kept, sources=sources, claims=claims)
    flags += [f"{ECHO_FLAG}: {s}" for s in echoed_in(letter)]
    flags += [f"{REPEAT_FLAG}: {s}" for s in unique.failed]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(letter)]
    if flow.short(letter, who.get("letter_words")):
        flags.append(SHORT_FLAG.format(n=flow.word_count(letter), words=who["letter_words"]))
    if cut:
        flags.append(CUT_FLAG.format(what=CUT_WHAT["letter"]))
    used = passages(kept)
    cards = profile_writing.cards()
    shown = [_item_note(i, cards) for i in plan] or used or [m.get("evidence", "") for m in fit.get("matches", [])
                                                            if m.get("strength") == "strong"]
    shown = [e for e in (drafts.evidence_checked(s, cards, known_tools(cards), facts)[0] for s in shown) if e]
    return {"text": letter, "flags": flags, "used": used, "revised": revised, "softened": softened, "plan": plan,
            "pairing": pairing,
            "notes": letter_notes(who, letter, shown, flags, revised=revised, softened=softened)}


def _item_note(item: planning.Item, cards: list[dict]) -> str:
    """A plan item as the letter's Evidence line shows it: its quote, with its card and tag after it when the
    card's name is like another's and the quote does not name it."""
    card = next((c for c in cards if c["name"] == item.card), None)
    if not item.tag or card is None or card in drafts.named_cards(item.text, cards):
        return item.text
    return f"{item.text} ({item.card}, {item.tag})"


def _answers_text(answers: list[dict]) -> str:
    return "\n".join(f"- {a.get('question', '')}: {a.get('answer', '')}" for a in answers)


def echoed_answers(answers: list[dict], sources: tuple[str, ...], names: tuple[str, ...]) -> list[str]:
    """The sentences of the "why do you want to work here" answers that restate the advert or brief."""
    return [s for a in answers if drafts.why_question(str(a.get("question") or ""))
            for s in drafts.echoed(cites.strip(str(a.get("answer") or "")), *sources, names)]


def repeated_answers(answers: list[dict]) -> list[tuple[int, str, int]]:
    """Each sentence of a later answer that repeats an earlier answer (`repeats.repeated`)."""
    return repeats.repeated([cites.strip(str(a.get("answer") or "")) for a in answers])


def repeat_note(answers: list[dict], found: list[tuple[int, str, int]]) -> str:
    """The request to rewrite each later answer that repeats an earlier one, using other evidence."""
    listed = "\n".join(f"- In the answer to \"{answers[i].get('question', '')}\": {s} (already said in the answer "
                       f"to \"{answers[j].get('question', '')}\")" for i, s, j in found)
    return (f"These sentences repeat what an earlier answer already says:\n{listed}\nRewrite only those later "
            "answers so each one uses a different piece of evidence from the applicant's facts (another project, "
            "job, module or result). Keep the other answers as they are.")


def fix_repeats(answers: list[dict], rewrite):
    """The answers rewritten once by `rewrite(note)` when a later one repeats an earlier one; returns (answers,
    whether they were rewritten). One rewrite, never a loop: what still repeats is listed by the caller."""
    found = repeated_answers(answers)
    if not found:
        return answers, False
    again = rewrite(repeat_note(answers, found))
    return (again or answers), bool(again)


def _answers(who: dict, request: str, *, facts: str, kept: str, sources: tuple[str, ...],
             names: tuple[str, ...] = (), plan: list[planning.Item] = ()) -> tuple[list[dict], list[str], list[str]]:
    """Answers to the questions `request` lists, revised once together when one breaks a high-severity rule,
    once when a "why here" answer restates the advert (`echoed_answers`) and once more when they make claims
    the facts do not support, after once more when a later answer repeats an earlier one (`fix_repeats`);
    returns (answers, the claims still unsupported, the sentences still restating the advert), the answers
    still carrying their evidence markers."""
    def ask(text: str) -> list[dict]:
        found = llm.chat_json(prompt("answers", who), text, role="writer").get("answers", [])
        return [{**a, "answer": cites.normalise(spelt(llm.undash(a.get("answer", "")), who))}
                for a in found if isinstance(a, dict)]

    def again(note: str) -> list[dict]:
        return ask(f"{request}\n\nYour answers:\n{_answers_text(answers)}\n\n{note}")

    answers = ask(request)
    hits = [h for a in answers for h in _hits(who, a["answer"], "answer", facts=facts, kept=kept, sources=sources)]
    answers = revise_once(answers, hits,
                          lambda note: ask(f"{request}\n\nYour first answers:\n{_answers_text(answers)}\n\n{note}"))[0]
    answers, _ = fix_echoes(answers, lambda found: echoed_answers(found, sources, names), again)
    answers, _ = fix_repeats(answers, again)
    answers, claims = settle_answers(who, answers, again, plan=plan, facts=facts, kept=kept, employer=sources)
    return answers, claims, echoed_answers(answers, sources, names)


def _message(who: dict, context: str, trimmed: set[str]) -> str:
    """The body of the message to the person hiring, cleaned of the model's notes and of a greeting or an
    introduction it wrote, which the opening says (`writing.opening.body_of`)."""
    draft = _draft(who, "message", context, trimmed)
    body = opening.body_of(draft)
    if draft in trimmed:
        trimmed.add(body)
    return body


def message_opening(who: dict, job, analysis: dict) -> opening.Opening:
    """The message's greeting and first sentence (`writing.opening`): the person's name and stage, the role, and
    the advert's first hiring contact (`hiring_contacts`)."""
    contacts = hiring_contacts(analysis, _job_text(job))
    name = str(who.get("name") or "")
    return opening.Opening.of("" if name == SOMEONE else name, stage.current(), job["title"],
                              contacts[0]["name"] if contacts else "")


def answers_request(job, who: dict, analysis: dict, questions: list[str], context: str) -> str:
    """The request for answers to `questions`: each question, the shared context and the field's conventions."""
    return ("Questions:\n" + "\n".join(f"- {q}" for q in questions)
            + f"\n\n{context}\n\n{fields.block(field_of(job, who, analysis), 'answer')}")


def _unrepeated(against: grounded.Facts, letter: str, message: str,
                answers: list[dict]) -> tuple[str, list[dict], list[str]]:
    """The message and the answers with each sentence that says what the letter, the message or an earlier
    answer already says rewritten or taken out (`grounded.unrepeat`): (message, answers, what still repeats)."""
    done = grounded.unrepeat(message, against, grounded.only_in_paragraph, (letter,))
    message, repeated, out = done.text, list(done.failed), []
    for a in answers:
        earlier = (letter, message, *(str(b.get("answer") or "") for b in out))
        done = grounded.unrepeat(str(a.get("answer") or ""), against, grounded.first_of_answer, earlier)
        out.append({**a, "answer": done.text})
        repeated += done.failed
    return message, out, repeated


def write_others(who: dict, job, analysis: dict, about: str, *, message_request: str, answers_request: str,
                 facts: str, kept: str, plan: list[planning.Item] | None = None, letter: str = "") -> dict:
    """The message to the person hiring and the form answers, written from their requests and checked as a
    pack checks them: {message, answers, flags}. `answers_request` "" writes no answers.

    The message is written after its opening (`message_opening`: the greeting and who is writing, with no AI),
    asked for again when it stops mid-sentence and rewritten once without claims the facts do not support
    (project contexts and misplaced tools included); a greeting or introduction the writer adds is taken out
    (`writing.opening.body_of`), and the message returned starts with the opening. The answers are revised
    for the writing rules, for restating the advert and for claims (`_answers`). The pack and a change the
    person asks for (`docs.change`) both write them here, so neither skips a check. Both
    cite the evidence `plan` (the pack's, else one picked here for the advert's and the common questions);
    their markers are taken out before they are returned. A sentence saying what the `letter`, the message or
    an earlier answer already says is rewritten once from other evidence, else taken out or listed
    (`grounded.unrepeat`).
    """
    job_text = _job_text(job)
    their_words, trimmed = (job_text, about), set()
    plan = plan if plan is not None else evidence_plan(analysis, facts, kept, tuple(COMMON_QUESTIONS))
    first = message_opening(who, job, analysis)
    message_request += f"\n\n{first.note}" + _plan_part(plan)
    answers_request += _plan_part(plan) if answers_request else ""

    def rewrite_message(note: str) -> str:
        return _message(who, f"{message_request}\n\nYour draft:\n{message}\n\n{note}", trimmed)

    message = _message(who, message_request, trimmed)
    settled = settle(who, message, rewrite_message, plan=plan, essential=grounded.only_in_paragraph, facts=facts,
                     kept=kept, employer=their_words)
    message, message_claims = settled.text, settled.failed
    cut = message in trimmed
    answers, answer_claims, answer_echoes = _answers(
        who, answers_request, facts=facts, kept=kept, sources=their_words,
        names=(job["title"], job["company"]), plan=plan) if answers_request else ([], [], [])
    message, answers, repeated = _unrepeated(grounds(who, plan, facts=facts, kept=kept, employer=their_words),
                                             letter, message, answers)
    you, cards = eligibility.person(), profile_writing.cards()
    message = plain.tidy(relocation.without_own_move(cites.strip(message), you), cards)
    answers = [{**a, "answer": plain.tidy(relocation.without_own_move(cites.strip(str(a.get("answer") or "")), you),
                                          cards)} for a in answers]
    written = "\n\n".join([message, *(a.get("answer", "") for a in answers)])
    flags = check_drafts(who, written, facts=facts, kept=kept, sources=their_words, kind="answer",
                         claims=message_claims + answer_claims)
    flags += [f"{ECHO_FLAG}: {s}" for s in answer_echoes]
    flags += [f"{REPEAT_FLAG}: {s}" for s in repeated]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(message)]
    if cut:
        flags.append(CUT_FLAG.format(what=CUT_WHAT["message"]))
    return {"message": first.put(message), "answers": answers, "flags": flags}


def applicant_names(who: dict) -> list[str]:
    """The names the applicant goes by in the prompts: full, first and preferred."""
    full = str(who.get("name") or "")
    return [n for n in dict.fromkeys((full, str(who.get("first_name") or ""), full.split()[0] if full else ""))
            if n and n != SOMEONE]


def _eligibility_flag(job, who: dict, fit: dict, conditions: list) -> str:
    # with no condition to judge, Claude still answered "eligible": false ("nothing to match", 2026-10-23)
    note = str(fit.get("eligibility_notes") or "")
    if fit.get("eligible") is False and conditions and not eligibility.where_you_live_settles(
            note, eligibility.person(), reputation.towns(reputation.advert_of(job)), cv.facts_text()) \
            and not eligibility.unfounded_degree(note, _job_text(job)):
        return f"ELIGIBILITY: {fit.get('eligibility_notes', 'may not be eligible')}"
    if who.get("not_eligible"):
        return f"ELIGIBILITY: {who.get('eligibility_line') or 'the advert may not be open to you'}"
    return ""


def _contact(c) -> dict:
    """One contact the advert reading gave, as {name, email, role}; a bare string is a name or an address."""
    if isinstance(c, dict):
        text = {k: str(c.get(k) or "").strip() for k in ("name", "email", "role")}
    else:
        first, _, rest = str(c).partition(",")
        text = {"name": first.strip(), "email": "", "role": rest.strip()}
    found = EMAIL.search(" ".join(text.values()))
    email = found.group(0) if found else ""
    name = "" if "@" in text["name"] else text["name"]
    return {"name": name, "email": email, "role": text["role"]}


def _for_the_process(contact: dict, advert: str) -> bool:
    """True when the advert gives this contact for questions about applying rather than about the work."""
    if PROCESS_WORDS.search(contact["role"]):
        return True
    said = [s for s in writing_check.sentences(advert or "")
            if any(k and k.lower() in s.lower() for k in (contact["email"], contact["name"]))]
    return any(PROCESS_WORDS.search(s) for s in said)


def hiring_contacts(analysis: dict, advert: str = "") -> list[dict]:
    """The named people the advert gives as hiring contacts, each {name, email, role}: never an address or a
    person it gives for questions about the application process, and never a careers or recruitment
    mailbox. A pack's "To:" line was the advert's address for questions about applying (2026-11-07)."""
    out = []
    for c in (_contact(c) for c in analysis.get("contacts") or []):
        if c["name"] and not _for_the_process(c, advert):
            out.append({**c, "email": "" if PROCESS_MAILBOX.match(c["email"]) else c["email"]})
    return out


def message_to(job, analysis: dict, who: dict) -> str:
    """Who the message goes to: the hiring contacts the advert names (`hiring_contacts`), else where to find
    a title the CV type names."""
    advert = str(job["description"] or "") if "description" in job.keys() else ""
    named = [", ".join(p for p in (c["name"], c["role"], c["email"]) if p) for c in hiring_contacts(analysis, advert)]
    if named:
        return "; ".join(named)
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
    fit = _fit(who, analysis, facts, kept, job)
    plan = evidence_plan(analysis, facts, kept, tuple(COMMON_QUESTIONS))
    written = write_letter(con, job, who=who, analysis=analysis, about=about, kept=kept, fit=fit, plan=plan)
    letter = written["text"]
    context = _context(job, who, analysis, about, fit, facts, kept, previous_applications(con, job))
    questions = [*(analysis.get("questions") or []), *COMMON_QUESTIONS]
    others = write_others(who, job, analysis, about, message_request=context,
                          answers_request=answers_request(job, who, analysis, questions, context),
                          facts=facts, kept=kept, plan=plan, letter=letter)
    message, answers = others["message"], others["answers"]

    variant, track = cv.tailor(job_text)
    pdf = cv.render(variant, out_dir)
    flagged = written["flags"] + others["flags"]
    # The type's special cases: a KTP advert's employer is the host company, not the scheme.
    ktp = "ktp" in who["special_cases"] and job["company"] == "KTP"
    employer = (analysis.get("employer") or job["company"]) if ktp else job["company"]
    company_flags, company_md = reputation.check(employer, reputation.advert_of(job))
    flagged += company_flags
    history = previous_applications(con, job)
    if history:
        flagged.insert(0, f"REAPPLICATION: you applied here before: {history}")
    plain = eligibility.blockers([*(analysis.get("must_have") or []), *(analysis.get("eligibility") or [])],
                                 eligibility.person(con), job_text)
    found = _eligibility_flag(job, who, fit, analysis.get("eligibility") or [])
    flagged[:0] = plain + ([found] if found else [])   # first, and never softened by a rewrite
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


def add_company_check(pack_md: Path, company: str, job=None) -> None:
    """Adds or refreshes the company check in an existing pack, and says the pack changed; `job` is the job row
    whose advert ties a lookup's hit to the employer (`reputation.Advert`)."""
    flags, section = reputation.check(company, reputation.advert_of(job))
    text = re.sub(r"## Company check\n.*?(?=\n## )", "", pack_md.read_text(), flags=re.S)
    if flags:
        bullets = "".join(f"- {f}\n" for f in flags)
        text = text.replace(f"{CHECK_HEADING}\n\n{NOTHING_TO_CHECK}\n", f"{CHECK_HEADING}\n\n", 1)
        if f"{CHECK_HEADING}\n\n" in text:
            text = text.replace(f"{CHECK_HEADING}\n\n", f"{CHECK_HEADING}\n\n" + bullets, 1)
        else:
            text = text.replace("\n## Role\n", f"\n{CHECK_HEADING}\n\n{bullets}\n## Role\n", 1)
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
    lines += [CHECK_HEADING, ""] + ([f"- {f}" for f in flagged] or [NOTHING_TO_CHECK]) + [""]
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
