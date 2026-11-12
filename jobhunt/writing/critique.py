"""Critique as a recruiter: the CV and the cover letter of one application read against its advert.

`critique(con, job)` returns per-criterion grades for each document, line comments that quote the words
they are about, the advert's requirements mapped to evidence, the six-second screen of the CV, and the top
three fixes. The writing checker (`writing.check`) gives its line comments with no AI; the recruiter's read
(the `cv_judge` and `letter_judge` prompts) adds the grades when the AI answers. Without an AI the critique
still ends, with the checker's comments and a note saying why there are no grades.

Design decisions, each with its reason:

- The six-second screen sees only the top of the CV (`checks.TOP_CHARS`), in its own block, so the full
  read's judgement does not leak into the skim.
- Requirements are mapped to evidence before any grade, and every grade has anchors, so a small model
  does not drift toward 4 and the person gets a gap list rather than an impression.
- Bias guardrails: the person's name, email, phone and town are replaced before the AI reads anything,
  and the prompts forbid comments on age, gaps, nationality or names except as "a reader may ask".
- A quote the AI gives that is not in the document is dropped, as the CV matcher drops one, so a line
  comment always points at real words.
- The AI never grades what the checker measures (length, dashes, spelling); the checker's findings are
  listed as they are, apart from the opinion.
"""
from __future__ import annotations

import logging
import re

from jobhunt.writing import check, fields

log = logging.getLogger(__name__)

GRADE_MIN, GRADE_MAX = 1, 5
TOP_FIXES = 3                 # the fixes shown first
LINES_MAX = 12                # line comments per document
TIMEOUT_S = 600               # a local model reading a CV and an advert can take minutes
CTX = 16384                   # tokens: a two-page CV, a letter and an advert fit
ADVERT_CHARS = 6000           # of the advert, which can run long
IDENTITY = ("name", "email", "phone", "city")
DOCUMENTS = {"cv": "Your CV", "letter": "Your cover letter"}
NO_AI = "Hal's AI could not read your documents just now, so there are no grades: {why}"


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def anonymised(text: str, profile: dict) -> str:
    """`text` with the person's name, first name, email, phone and town replaced, before an AI reads it."""
    swaps = [(str(profile.get(k) or "").strip(), f"[{k}]") for k in IDENTITY]
    name = str(profile.get("name") or "").strip()
    swaps += [(part, "[name]") for part in name.split() if len(part) > 2]
    for value, label in sorted(swaps, key=lambda s: -len(s[0])):
        if value:
            text = re.sub(re.escape(value), label, text, flags=re.I)
    return text


def _grade(value) -> int | None:
    try:
        return max(GRADE_MIN, min(GRADE_MAX, int(value)))
    except (TypeError, ValueError):
        return None


def parse(reply: dict, text: str) -> dict:
    """A recruiter's read from the AI's JSON, keeping only quotes that are in `text`."""
    held = _norm(text)
    grades = []
    for g in reply.get("grades") or []:
        grade = _grade(g.get("grade")) if isinstance(g, dict) else None
        if grade is not None:
            grades.append({"criterion": str(g.get("criterion") or ""), "grade": grade, "why": str(g.get("why") or ""),
                           "fix": str(g.get("fix") or ""), "needs_fact": bool(g.get("needs_fact"))})
    lines = [{"quote": str(x.get("quote") or "").strip(), "comment": str(x.get("comment") or "").strip(), "by": "ai"}
             for x in reply.get("lines") or [] if isinstance(x, dict)]
    lines = [x for x in lines if x["quote"] and x["comment"] and _norm(x["quote"]) in held][:LINES_MAX]
    requirements = []
    for r in reply.get("requirements") or []:
        if not isinstance(r, dict) or not str(r.get("requirement") or "").strip():
            continue
        evidence = str(r.get("evidence") or "").strip()
        shown = evidence and evidence.lower() != "none" and _norm(evidence.strip("\"'.")) in held
        requirements.append({"requirement": str(r["requirement"]).strip(), "evidence": evidence if shown else "",
                             "strength": (_grade(r.get("strength")) or GRADE_MIN) if shown else GRADE_MIN})
    screen = reply.get("screen") if isinstance(reply.get("screen"), dict) else {}
    return {"summary": str(reply.get("summary") or "").strip(), "grades": grades, "lines": lines,
            "requirements": requirements,
            "screen": {"who": str(screen.get("who") or ""), "wants": str(screen.get("wants") or ""),
                       "read_on": bool(screen.get("read_on")), "why": str(screen.get("why") or "")} if screen else {},
            "top_fixes": [str(f).strip() for f in reply.get("top_fixes") or [] if str(f).strip()]}


def ask(prompt_id: str, user: str, text: str) -> dict:
    """One recruiter's read by the AI; raises `ai.LLMUnavailable` when it cannot answer."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    reply = ai.chat_json(prompts.text(prompt_id), user, role="checker", timeout=TIMEOUT_S, ctx=CTX,
                         purpose=prompt_id, priority=0)
    return parse(reply if isinstance(reply, dict) else {}, text)


def cv_request(persona: str, cv_text: str, advert: str = "") -> str:
    """The `cv_judge` request: who reads, the top of the CV for the screen, the whole CV and the advert."""
    from jobhunt.cvstudio import checks as cv_checks

    out = f"{persona}\n\n<<<TOP\n{cv_text[:cv_checks.TOP_CHARS]}\nTOP>>>\n\n<<<CV\n{cv_text}\nCV>>>"
    return out + (f"\n\n<<<ADVERT\n{advert[:ADVERT_CHARS]}\nADVERT>>>" if advert else "")


def _checker_lines(text: str, kind: str, **kw) -> list[dict]:
    hits = check.check(text, kind, **kw)
    return [{"quote": h.quote, "comment": h.message, "by": "rules", "severity": h.severity} for h in hits]


def _cv_bullets(cv_text: str) -> str:
    return "\n".join(line for line in cv_text.splitlines() if line.strip().startswith("- "))


def _top_fixes(reads: list[dict], rule_lines: list[dict]) -> list[str]:
    out: list[str] = []
    for i in range(max((len(r.get("top_fixes") or []) for r in reads), default=0)):
        out += [r["top_fixes"][i] for r in reads if i < len(r.get("top_fixes") or [])]
    weakest = sorted((g for r in reads for g in r.get("grades") or [] if g["fix"]), key=lambda g: g["grade"])
    out += [g["fix"] for g in weakest]
    out += [x["comment"] for x in rule_lines if x.get("severity") in ("high", "medium")]
    seen: list[str] = []
    for fix in out:
        if fix not in seen:
            seen.append(fix)
    return seen[:TOP_FIXES]


def critique(con, job) -> dict:
    """A recruiter's read of one application's CV and letter against its advert (see the module notes)."""
    from jobhunt import ai as ai_mod
    from jobhunt import cv, documents, pack
    from jobhunt.cvstudio import checks as cv_checks

    docs = documents.load(con, job)
    who = pack.person(job)
    resolved = fields.for_job(job)
    profile = cv_checks.profile_now()
    cv_text = cv.facts_text_of(documents.current_cv(job, docs))
    letter = (docs.get("letter") or "").strip()
    advert = pack._job_text(job)
    spelling = who.get("spelling_code", "")
    own = who.get("own_words") or ()
    rule_lines = {"cv": _checker_lines(_cv_bullets(cv_text), "cv", spelling=spelling, allowed=own),
                  "letter": _checker_lines(letter, "letter", sources=(cv_text, advert), spelling=spelling,
                                           cv_text=cv_text, target_words=who.get("letter_words"), allowed=own)
                  if letter else []}
    persona = (f"Read it as an experienced UK hiring manager for this job: {job['title']} at {job['company']} "
               f"({resolved.name}). The candidate is {who.get('career_stage') or 'an applicant'}.")
    reads, note = {}, ""
    try:
        reads["cv"] = ask("cv_judge", anonymised(cv_request(persona, cv_text, advert), profile), cv_text)
        if letter:
            reads["letter"] = ask("letter_judge", anonymised(
                f"{persona}\n\n<<<LETTER\n{letter}\nLETTER>>>\n\n<<<CV\n{cv_text}\nCV>>>\n\n"
                f"<<<ADVERT\n{advert[:ADVERT_CHARS]}\nADVERT>>>", profile), letter)
    except ai_mod.LLMUnavailable as e:
        note = NO_AI.format(why=e)
    out_docs = []
    for key, title in DOCUMENTS.items():
        if key == "letter" and not letter:
            continue
        read = reads.get(key, {})
        out_docs.append({"document": key, "title": title, "summary": read.get("summary", ""),
                         "grades": read.get("grades", []),
                         "lines": [*read.get("lines", []), *rule_lines[key]][:LINES_MAX]})
    return {"field": resolved.name, "screen": reads.get("cv", {}).get("screen", {}),
            "requirements": reads.get("cv", {}).get("requirements", []), "documents": out_docs,
            "top_fixes": _top_fixes(list(reads.values()), [x for lines in rule_lines.values() for x in lines]),
            "ai": bool(reads), "note": note}
