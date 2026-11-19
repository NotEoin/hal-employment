"""The checks every pack draft goes through: one check of every draft at once and at most one redraft each
(`settle`), the "Check before sending" lines and the refusals taken out.

Design decisions, each with its reason:

- One check, not one per paragraph (owner, 2026-11-19): `settle` makes one `verify` call on every draft of a
  job at once (the letter, the message and the answers in a pack), against the facts their writer was given,
  then redrafts each draft at most once with everything wrong with it stated: the claims the check found, the
  claims the checks with no AI found (`code_claims`, `lifted_claims`), the writing rules' high-severity breaks
  (`writing.check`), wording that says how the draft was made, such as a stock tie to the job ("aligns with",
  `writing.plain.problems`), sentences restating the advert (`drafts.echoes`), and what its `Draft.extra` adds (an
  answer repeating an earlier one, a letter short of its length or opening its paragraphs alike). No guard
  against invented facts is dropped: the checks with no AI run again on the redraft, and a claim the check
  found that the redraft still makes (`still_says`) is listed under "Check before sending". The redraft is
  not checked by the AI again: one redraft, never a loop.
- The advert and the company brief go to the check as the employer's own words, never flagged for being
  restated: most of the "unsupported" claims in real packs were the employer's own facts (2026-11-06). A
  claim one line of the facts the writer had states (`drafts.stated_in`) is supported, whatever the AI said.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jobhunt import ai, record
from jobhunt.pack.fitmap import known_tools
from jobhunt.pack.reading import SOMEONE, Brief, _record_part, prompt
from jobhunt.profile import writing as profile_writing
from jobhunt.writing import check as writing_check
from jobhunt.writing import drafts, plain, stage, strengths, taught

CUT_FLAG = "the {what} stopped mid-sentence twice; Hal kept it to its last full sentence, so check its ending"
CUT_WHAT = {"letter": "cover letter", "message": "message to the person hiring"}
ECHO_FLAG = "restates the advert rather than your own reason"   # a sentence still echoing after its redraft
DANGLING_FLAG = "opens a paragraph by pointing back at something the paragraph before does not say"
KEPT_SHARE = 0.6          # share of a claim's own words a sentence of the redraft holds for it to still make it
LABELS = {"letter": "Letter", "message": "Message", "answers": "Answers"}
REDRAFT_NOTE = ("Write the whole draft again from the facts above. Your first draft has the problems below: fix "
                "every one of them, and keep what was right.")
RULES_NOTE = "These break Hal's writing rules:\n"
PLAIN_NOTE = "These sentences say how the draft was made rather than what the applicant did; rewrite each:\n"


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


def code_claims(written: str, *, facts: str, kept: str) -> list[str]:
    """The claims about the person in `written` found with no AI: a sentence that speaks of finishing a course
    that has ended (`stage.ended_course`), a tool given to a project whose own facts never name it
    (`drafts.misplaced_tools`), a project placed in a degree or in the person's own time against its card's
    context (`drafts.wrong_context`), one project's facts given to another of a like name (`drafts.crossed`), a
    skill only studied said to be used (`writing.strengths`), and what a named module covered or which tool it
    used when no source naming the module says so (`taught.unsupported`, `taught.tooled`), a degree the person's
    education does not hold (`drafts.unheld_degrees`) and a team's work said to be the person's alone
    (`drafts.solo_claims`)."""
    now = stage.current()
    cards = profile_writing.cards()
    known = known_tools(cards)
    return [f"{s} ({stage.ended_said(now)})" for s in stage.ended_course(written, now)] \
        + drafts.misplaced_tools(written, cards, known) + drafts.wrong_context(written, cards) \
        + drafts.crossed(written, cards) + drafts.unheld_degrees(written, profile_writing.degrees()) \
        + drafts.solo_claims(written, cards) \
        + strengths.studied_claims(written, profile_writing.studied_skills()) \
        + taught.unsupported(written, profile_writing.module_names(), (facts, kept)) \
        + taught.tooled(written, profile_writing.module_names(), (facts, kept), known)


LIFTED_SAID = 'takes "{phrase}" from the advert as a fact of your work'


def lifted_claims(written: str, employer: tuple[str, ...], names: tuple[str, ...], own: str) -> list[str]:
    """The sentences about the person's work that take a phrase of the advert or the brief as their fact when
    the person's facts (`own`) do not hold its words (`drafts.lifted`): "low-latency systems" and "AI agents for
    customer support" became claims in sentences mostly the person's own (2026-11-08)."""
    advert, brief = (*employer, "", "")[:2]
    out = []
    for sentence in writing_check.sentences(written):
        if drafts.about_employer(sentence, names):
            continue
        phrase = drafts.lifted(sentence, advert, brief, names, own)
        if phrase:
            out.append(f"{sentence} ({LIFTED_SAID.format(phrase=phrase)})")
    return out


def ai_claims(who: dict, written: str, *, given: str, employer: tuple[str, ...], cv: str) -> list[str]:
    """The claims about the person in `written` that the facts in `given` do not support, as the `verify` prompt
    quotes them, less those one line of `cv` states (`drafts.stated_in`): the CV, or every fact the writer had."""
    words = "\n\n".join(e.strip() for e in employer if e and e.strip())
    request = (f"The employer's own words (the advert and the company brief): never flag a sentence for restating "
               f"them.\n<<<EMPLOYER\n{words}\nEMPLOYER>>>\n\nFacts about the applicant:\n{given}\n\nDrafts:\n{written}")
    found = ai.chat_json(prompt("verify", who), request, role="checker", timeout=ai.LONG_TIMEOUT_S,
                         purpose="verify").get("unsupported", [])
    claims = [str(c).strip() for c in found if str(c).strip()] if isinstance(found, list) else []
    return [c for c in claims if not drafts.stated_in(c, cv)]


def unsupported_claims(who: dict, written: str, *, facts: str, kept: str, employer: tuple[str, ...]) -> list[str]:
    """The claims about the person in `written` their facts do not support: the AI's (`ai_claims`, against the
    CV, every checked card with its own tools and the passages of their own documents) and those found with no
    AI (`code_claims`). For form filling (`autofill.check_written`); a pack's drafts are checked by `settle`."""
    shown = profile_writing.cards_block(profile_writing.cards())
    given = (f"{stage.today_lines()}\nCV:\n{facts}" + (f"\n\n{shown}" if shown else "")
             + _record_part(f"Employment record ({record.OWN_DOCUMENTS})", kept))
    return ai_claims(who, written, given=given, employer=employer, cv=facts) + code_claims(written, facts=facts,
                                                                                         kept=kept)


def claims_note(claims: list[str]) -> str:
    """The part of a redraft request about claims the facts do not support."""
    listed = "\n".join(f"- {c}" for c in claims)
    return (f"These claims about the applicant are not in their facts:\n{listed}\nNone of them may stay in the new "
            "draft: take each sentence out, or say instead only what the facts do say.")


def echo_note(sentences: list[str]) -> str:
    """The part of a redraft request about sentences restating the advert or brief as the applicant's reason."""
    listed = "\n".join(f"- {s}" for s in sentences)
    return (f"These sentences restate the advert or the company brief instead of giving the applicant's own "
            f"reason:\n{listed}\nRewrite each one to tie one specific thing about the applicant (a named project, "
            "module or interest from their facts) to one specific thing about this employer.")


def still_says(claim: str, text: str, given: str = "") -> bool:
    """True when `text` still makes `claim`: holds its words, or one sentence holds `KEPT_SHARE` of its own words
    and, with the writer's facts (`given`), every one of its words those facts lack. A redraft that kept "Hatch was
    built in TypeScript" and dropped "with Electron" was listed as still making the claim (2026-11-19)."""
    if claim.lower() in (text or "").lower():
        return True
    if not drafts.stated_in(claim, text, share=KEPT_SHARE):
        return False
    held = set(re.findall(r"[a-z0-9]+", (given or "").lower()))
    novel = {w for w in re.findall(r"[a-z0-9]+", claim.lower()) if w not in held and w not in drafts.LITTLE_WORDS}
    said = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return not given or novel <= said


@dataclass
class Draft:
    """One document of a job as written, and as `settle` leaves it.

    `value` is the text, or the answers' list; `as_text` turns it into the text that is checked. `rewrite(note)`
    asks the writer once more with its draft and `note`, returning the new value or a falsy one. `echoed(value)`
    gives the sentences restating the advert and `extra(value)` any other problems, as lines of the note."""

    kind: str                                  # "letter", "message" or "answers"
    value: Any
    rewrite: Callable[[str], Any]
    rule_kind: str = "letter"                  # the writing rules it is read by ("letter" or "answer")
    as_text: Callable[[Any], str] = str
    echoed: Callable[[Any], list[str]] = lambda value: []
    extra: Callable[[Any], list[str]] = lambda value: []
    claims: list[str] = field(default_factory=list)     # still unsupported once settled
    revised: bool = False                      # redrafted once
    softened: bool = False                     # redrafted with claims its facts do not support

    def text(self) -> str:
        """The draft as the checks read it."""
        return self.as_text(self.value) or ""


def _owners(claim: str, written: list[Draft]) -> list[Draft]:
    """The drafts a claim of the one check is about: every draft that makes it (`still_says`), as the check
    quotes a claim once however many drafts make it; else the one sharing the most of its words."""
    making = [d for d in written if still_says(claim, d.text())]
    if making:
        return making
    own = set(re.findall(r"[a-z0-9]+", claim.lower()))
    return [max(written, key=lambda d: len(own & set(re.findall(r"[a-z0-9]+", d.text().lower()))))]


def _note(brief: Brief, d: Draft, claims: list[str], sources: tuple[str, ...]) -> str:
    """Everything wrong with a draft before its one redraft, or "" when nothing is."""
    text = d.text()
    serious = writing_check.high(_hits(brief, text, d.rule_kind, sources))
    names, labels = applicant_names(brief.who), plain.settings_labels()
    wording = [f'- "{s}": {p}' for s in writing_check.sentences(text) for p in plain.problems(s, names, labels)]
    parts = [RULES_NOTE + "\n".join(f'- "{h.quote}": {h.message}' for h in serious) if serious else "",
             PLAIN_NOTE + "\n".join(wording) if wording else "",
             claims_note(claims) if claims else "",
             echo_note(found) if (found := d.echoed(d.value)) else "",
             *d.extra(d.value)]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return "\n\n".join([REDRAFT_NOTE, *parts])


def settle(brief: Brief, written: list[Draft], *, employer: tuple[str, ...], given: str, own: str = "",
           names: tuple[str, ...] = ()) -> None:
    """Checks every draft of one job at once and redrafts each at most once (see the module notes); `given` is the
    facts the writer had (`drafting.Given.checked`), `own` every word of them (`Given.words`) and `names` the job's
    title and employer, for the advert's phrases taken as facts (`lifted_claims`). Each draft's `claims` are what
    is still unsupported."""
    present = [d for d in written if d.text().strip()]
    if not present:
        return
    said = "\n\n".join(f"{LABELS.get(d.kind, d.kind)}:\n{d.text()}" for d in present)
    found: dict[int, list[str]] = {id(d): [] for d in present}
    for claim in ai_claims(brief.who, said, given=given, employer=employer, cv=given):
        for d in _owners(claim, present):
            found[id(d)].append(claim)

    def without_ai(text: str) -> list[str]:
        return code_claims(text, facts=brief.facts, kept=brief.kept) + lifted_claims(text, employer, names,
                                                                                     f"{own}\n{brief.facts}")

    for d in present:
        claims = found[id(d)] + without_ai(d.text())
        note = _note(brief, d, claims, (*employer, own))
        again = d.rewrite(note) if note else None
        if again:
            d.value, d.revised, d.softened = again, True, bool(claims)
        text = d.text()
        d.claims = [c for c in found[id(d)] if not d.revised or still_says(c, text, given)] + without_ai(text)


def check_drafts(brief: Brief, drafts: str, *, sources: tuple[str, ...], kind: str = "letter",
                 claims: list[str] | None = None) -> list[str]:
    """What to check before sending: numbers no source holds, the claims still unsupported (`claims`, as
    `settle` left them; with None the whole check is made here, `unsupported_claims`), the writing rules a draft
    breaks (`writing.check`) and the person's never-use phrases."""
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
