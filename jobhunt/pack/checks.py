"""The checks every pack draft goes through: the writing checker, the claim check and its one rewrite, the
echo rewrite, the "Check before sending" lines and the refusals taken out."""
import re

from jobhunt import ai, record
from jobhunt.pack.fitmap import known_tools
from jobhunt.pack.reading import SOMEONE, Brief, _record_part, prompt
from jobhunt.profile import writing as profile_writing
from jobhunt.writing import check as writing_check
from jobhunt.writing import drafts, plain, stage, strengths, taught

CUT_FLAG = "the {what} stopped mid-sentence twice; Hal kept it to its last full sentence, so check its ending"
CUT_WHAT = {"letter": "cover letter", "message": "message to the person hiring"}
ECHO_FLAG = "restates the advert rather than your own reason"   # a sentence still echoing after its rewrite
DANGLING_FLAG = "opens a paragraph by pointing back at something the paragraph before does not say"


def unsupported_numbers(draft: str, *sources: str) -> list[str]:
    """Returns numbers in a draft that appear in none of the sources."""
    return writing_check.unsupported_numbers(draft, *sources)


def _hits(brief: Brief, text: str, kind: str, sources: tuple[str, ...]) -> list:
    """What the writing checker finds in a draft against every fact it may draw on."""
    who, facts = brief.who, brief.facts
    return writing_check.check(text, kind, sources=(facts, brief.kept, *sources),
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
    card's context (`drafts.wrong_context`), a skill only studied said to be used (`writing.strengths`), and what
    a named module covered when no source naming the module
    says it (`taught.unsupported`) or which tool a named module used when its own entry never names it
    (`taught.tooled`). Each goes into the one rewrite `fix_claims` asks for. A draft told one piece
    of work a paragraph is checked paragraph by paragraph instead (`writing.paragraphs`); this whole-draft check
    is for drafts with no story to tell, and for form filling.
    """
    now = stage.current()
    cards = profile_writing.cards()
    shown = profile_writing.cards_block(cards)
    words = "\n\n".join(e.strip() for e in employer if e and e.strip())
    request = (f"The employer's own words (the advert and the company brief): never flag a sentence for restating "
               f"them.\n<<<EMPLOYER\n{words}\nEMPLOYER>>>\n\nFacts about the applicant:\n{stage.today_lines(now)}\n"
               f"CV:\n{facts}" + (f"\n\n{shown}" if shown else "")
               + _record_part(f"Employment record ({record.OWN_DOCUMENTS})", kept) + f"\n\nDrafts:\n{written}")
    found = ai.chat_json(prompt("verify", who), request, role="checker", timeout=ai.LONG_TIMEOUT_S,
                        purpose="verify").get("unsupported", [])
    claims = [str(c).strip() for c in found if str(c).strip()] if isinstance(found, list) else []
    claims = [c for c in claims if not drafts.stated_in(c, facts)]
    known = known_tools(cards)
    return claims + [f"{s} ({stage.ended_said(now)})" for s in stage.ended_course(written, now)] \
        + drafts.misplaced_tools(written, cards, known) + drafts.wrong_context(written, cards) \
        + drafts.crossed(written, cards) + strengths.studied_claims(written, profile_writing.studied_skills()) \
        + taught.unsupported(written, profile_writing.module_names(), (facts, kept)) \
        + taught.tooled(written, profile_writing.module_names(), (facts, kept), known)


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


def fix_claims(brief: Brief, draft, rewrite, *, employer: tuple[str, ...], as_text=str):
    """The draft rewritten once by `rewrite(note)` when it makes claims about the person their facts do not
    support, then checked again; returns (draft, the claims still unsupported, whether it was rewritten).

    One rewrite, never a loop, as with `revise_once`. `as_text` turns a draft (the answers are a list) into
    the text that is checked.
    """
    def unsupported(text: str) -> list[str]:
        return unsupported_claims(brief.who, text, facts=brief.facts, kept=brief.kept, employer=employer)

    claims = unsupported(as_text(draft))
    if not claims:
        return draft, [], False
    again = rewrite(claims_note(claims))
    if not again:
        return draft, claims, False
    return again, unsupported(as_text(again)), True


def check_drafts(brief: Brief, drafts: str, *, sources: tuple[str, ...], kind: str = "letter",
                 claims: list[str] | None = None) -> list[str]:
    """What to check before sending: numbers no source holds, claims the facts do not support (`claims` when
    `fix_claims` already found them, else `unsupported_claims`), the writing rules a draft breaks
    (`writing.check`) and the person's never-use phrases."""
    who = brief.who
    hits = _hits(brief, drafts, kind, sources)
    flagged = [h.flag for h in hits if h.rule == "T1"]
    if claims is None:
        claims = unsupported_claims(who, drafts, facts=brief.facts, kept=brief.kept, employer=sources)
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


def applicant_names(who: dict) -> list[str]:
    """The names the applicant goes by in the prompts: full, first and preferred."""
    full = str(who.get("name") or "")
    return [n for n in dict.fromkeys((full, str(who.get("first_name") or ""), full.split()[0] if full else ""))
            if n and n != SOMEONE]


# The AI declining the task rather than drafting: "I cannot complete this request because the Evidence map is
# empty", "I have no facts provided about a novel" (persona evals, 2026-11-12). "I cannot wait to start" is not.
REFUSAL = re.compile(r"\bI (?:cannot|can't|can not|am unable to|am not able to) (?:complete|fulfil+|write|answer|"
                     r"draft|provide|help with) (?:this|that|the|your|a)\b|\bas an AI\b|\bno (?:facts|information|"
                     r"evidence) (?:was |were |is |are )?(?:provided|given|available)\b|\b(?:Evidence map|CV facts)\b"
                     r"[^.]{0,60}\bempty\b", re.I)
REFUSED_LETTER = ("NO LETTER: Hal's AI would not write a cover letter for this job from what it had, so there is "
                  "none. Write one on the Cover letter tab, or check more of your record and ask again.")
REFUSED_ANSWER = "NO ANSWER: Hal's AI would not answer \"{q}\" from what it had. Answer it yourself on the Answers tab."
REFUSED_MESSAGE = "NO MESSAGE: Hal's AI would not write the message from what it had. Write it on the Message tab."


def without_refusals(letter: str, message: str, answers: list[dict]) -> tuple[str, str, list[dict], list[str]]:
    """The drafts with any refusal by the AI left empty, and a "Check before sending" line for each, so a refusal
    never goes to an employer as if it were the person's words."""
    flags = []
    if REFUSAL.search(letter or ""):
        letter, flags = "", [REFUSED_LETTER]
    if REFUSAL.search(message or ""):
        message, flags = "", [*flags, REFUSED_MESSAGE]
    kept = []
    for a in answers:
        if REFUSAL.search(a.get("answer") or ""):
            flags.append(REFUSED_ANSWER.format(q=a.get("question", "")))
            a = {**a, "answer": ""}
        kept.append(a)
    return letter, message, kept, flags
