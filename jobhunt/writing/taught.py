"""Claims about what a module taught: a draft may say what a named module covered only when a source says so.

`unsupported(draft, modules, sources)` finds, with no AI, the sentences of a draft that say a named module
(one of the person's checked modules, or any module code) covered, taught, included or introduced
something, when no source passage or fact that names the module also names it. `pack.unsupported_claims`
adds them to the claims a draft is rewritten without, as with `drafts.misplaced_tools`.

Design decisions, each with its reason:

- A module's name says nothing of its content: a letter said "the Foundations of Data Science module, which
  covered core statistical methods" while that module's own notes covered databases, SQL and language
  processing (2026-11-07). The model filled in what such a module usually teaches.
- Only a source that names the module can say what it covered, and in a source naming several modules
  (a CV's list of marks) only the words from this module's name up to the next module's count, so another
  module's "Statistics" never supports this one.
- What the sentence says the module covered is compared by its content words (`content`): little words and
  words any module says ("core", "key", "methods", "concepts") are left out, and a word counts when its
  first `STEM` letters match, so "statistical" meets "statistics". The claim is supported when a source
  holds at least `COVERED_SHARE` of them; a claim with no content word is left alone.
- A name counts only where the draft uses it as the module's (a code, a capitalised name of several words,
  or a name followed by "module" or "course"), and "covered" only within `VERB_WINDOW` words of it, so
  "databases" as a plain word and a verb about something else later in the sentence say nothing of a
  module.
- Course material is never given to the writer (`record.for_writing`), so the module's own notes cannot
  support a claim: the person checks it under "Check before sending" when the rewrite keeps it.
"""
from __future__ import annotations

import re

from jobhunt.profile import merge
from jobhunt.writing import check

TAUGHT = re.compile(r"\b(covered|covers|cover|taught|teaches|teach|included|includes|include|introduced|"
                    r"introduces|explored|explores|focus(s)?ed on|focus(es)? on|centred on|centered on|dealt with|"
                    r"deals with|went over|goes over)\b", re.I)
STEM = 6                   # letters of a word compared: "statistical" and "statistics" share "statis"
COVERED_SHARE = 0.5        # share of a claim's content words a source naming the module must hold
# Words that say what any module does, so they say nothing about this one.
VAGUE = frozenset("""a an the and or of to in on for with by as at from my me i us it its this that these those which
who core key main basic basics fundamental fundamentals foundations advanced introductory intro method methods
concept concepts topic topics technique techniques principle principles skill skills such including include
like various range wide variety how use using used area areas aspect aspects practice practical theory module
modules course courses also both all many some well other different important essential""".split())
CODE = re.compile(r"\b[A-Z]{2,5}\d{3,5}[A-Z]?\b")   # a module code in a draft, as `writing.stage.MODULE_CODE`
VERB_WINDOW = 4            # words between a module's name and "covered" ("the X module, which covered")
# A one-word module name ("Databases") names the module only beside a word like "module".
AS_MODULE = re.compile(r"^\W*(module|course|class|unit)\b", re.I)


def _stems(text: str) -> set[str]:
    return {w[:STEM] for w in re.findall(r"[a-z0-9+#]+", (text or "").lower())}


def content(text: str) -> list[str]:
    """The content words of what a sentence says a module covered: little and vague words left out."""
    return [w for w in re.findall(r"[a-z0-9+#]+", (text or "").lower()) if w not in VAGUE and len(w) > 1]


def _names(module: str) -> list[str]:
    """The ways a module is named: its name without its code, and its code."""
    code = merge.module_code(module)
    bare = " ".join(re.sub(r"\(\s*\)", " ", merge.MODULE_CODE.sub(" ", module)).split()).strip(" -:")
    return [n for n in (bare, code) if n]


def _find(text: str, names: list[str]) -> re.Match | None:
    """The first place `text` names a module by one of `names`."""
    found = [m for n in names for m in [re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.I)] if m]
    return min(found, key=lambda m: m.start()) if found else None


def _as_module(sentence: str, m: re.Match) -> bool:
    """True when a name found in a draft's sentence is used as the module's: a code, a capitalised name of
    several words, or a name followed by "module" or "course"."""
    said = m.group(0)
    return bool(CODE.fullmatch(said)) or AS_MODULE.match(sentence[m.end():]) is not None or \
        (said[:1].isupper() and len(said.split()) > 1)


def _segment(unit: str, names: list[str], others: list[list[str]]) -> str:
    """The part of a source unit about the module: all of it when it names no other module, else from the
    module's name to the next other module's; "" when the unit does not name it."""
    m = _find(unit, names)
    if not m:
        return ""
    if not any(_find(unit, o) for o in others):
        return unit
    rest = unit[m.end():]
    nexts = [n.start() for o in others for n in [_find(rest, o)] if n]
    return unit[m.start():m.end() + min(nexts, default=len(rest))]


def _units(sources: tuple[str, ...]) -> list[str]:
    """The passages and lines of the sources, each a unit a module's name must share with what it covered."""
    out = []
    for text in sources:
        for para in re.split(r"\n\s*\n", text or ""):
            out += [para, *para.splitlines()]
    return [u for u in out if u.strip()]


def unsupported(draft: str, modules: list[str], sources: tuple[str, ...]) -> list[str]:
    """The sentences of `draft` that say what a named module covered when no source naming the module says
    it, each with the module: "... (nothing in your facts says what Data Science covered)"."""
    known = [_names(m) for m in modules if m.strip()]
    units = _units(sources)
    out = []
    for sentence in check.sentences(draft):
        codes = [[c] for c in dict.fromkeys(CODE.findall(sentence)) if not any(c in k for k in known)]
        named = [(n, _find(sentence, n)) for n in [*known, *codes] if n]
        named = sorted(((n, m) for n, m in named if m and _as_module(sentence, m)), key=lambda p: p[1].start())
        if not named:
            continue
        names, at = named[0]
        verb = TAUGHT.search(sentence, at.end())
        near = verb and len(sentence[at.end():verb.start()].split()) <= VERB_WINDOW
        said = content(sentence[verb.end():]) if near else []
        if not said:
            continue
        others = [n for n in known if n is not names]
        wanted = {w[:STEM] for w in said}
        if not any(len(wanted & _stems(_segment(u, names, others))) >= COVERED_SHARE * len(wanted) for u in units):
            out.append(f"{sentence} (nothing in your facts says what {names[0]} covered)")
    return out
