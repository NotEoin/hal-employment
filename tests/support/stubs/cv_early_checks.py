"""Stand-in for `jobhunt.cvstudio.checks`, installed only while the real module is missing.

`for_type(doc, type_)` is the call the suggestions make to check a fresh CV against its type, under
that name. Each result is `{id, name, ok}` for a check the type weighs above 0.
"""
from __future__ import annotations

import re

CLICHES = re.compile(r"\b(passionate|hard[- ]working|team player|go[- ]getter|motivated)\b", re.I)
DUTIES = re.compile(r"^(responsible for|duties (included|include)|in charge of)", re.I)
PRIVATE = re.compile(r"\b(date of birth|marital status|religion)\b", re.I)
AMERICAN = re.compile(r"\b(organiz\w*|analyz\w*|color\w*|behavior\w*)\b", re.I)
NAMES = {"profile_evidence": "Profile shows evidence, not qualities",
         "result_bullets": "Bullets show a result where there is one",
         "dissertation_shown": "Dissertation and strong modules shown", "dates_one_format": "Dates in one format",
         "no_personal_data": "No photo, age or marital status", "pages_within": "Pages within the type's length",
         "one_spelling": "One spelling throughout", "entry_says_what": "Each entry says what you did",
         "contact_at_top": "Contact details at the top", "skills_backed": "Skills backed by an example"}


def _lines(doc) -> list[str]:
    out = []
    for s in doc.sections:
        out.append(s.text)
        for e in s.entries:
            out += [e.text, *(b.text for b in e.bullets)]
    return [x for x in out if x]


def _ok(check: str, doc, type_) -> bool:
    lines = _lines(doc)
    text = "\n".join(lines)
    summary = next((s.text for s in doc.sections if s.kind == "summary"), "")
    bullets = [b.text for s in doc.sections for e in s.entries for b in e.bullets]
    if check == "profile_evidence":
        return bool(summary) and not CLICHES.search(summary)
    if check == "result_bullets":
        return not bullets or any(re.search(r"\d", b) for b in bullets)
    if check == "dissertation_shown":
        return not type_.sections.get("education", {}).get("dissertation") or "dissertation" in text.lower()
    if check == "no_personal_data":
        return not PRIVATE.search(text)
    if check == "one_spelling":
        return not AMERICAN.search(text)
    if check == "entry_says_what":
        return not any(DUTIES.search(b) for b in bullets)
    return True


def for_type(doc, type_) -> list[dict]:
    """The type's checks on this document."""
    return [{"id": c, "name": NAMES.get(c, c), "ok": _ok(c, doc, type_)} for c, w in type_.checks.items() if w]
