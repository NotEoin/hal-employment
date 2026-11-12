"""Claims about what a module taught: a draft may say what a named module covered only when a source says so.

`joined(draft, modules, sources, cards)` finds a sentence that ties a named module and a mark to a project or
activity ("I achieved 86 in Ferry Studies through a team project") that no source naming the module names.

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
- A module and its mark are a fact of the module; tying them to a project or an activity ("through a team
  project", "where I contributed to a team project", "by building Ferry planner") claims the project was the
  module's work. A local model wrote that from a CV line of every module and its mark (2026-11-08), so the
  tie needs the module's own entry (`writing.atoms`: one module and its mark, its list's heading kept) to name
  the project (a card's name) or the activity's own words (`ACTIVITY`, compared as `content` words by their
  first `STEM` letters). The modules of a list in the sources count as named modules too, as a CV's modules
  are often not among the person's checked ones.
- A module named with a tool or a language (`tooled`) claims the module used it: "Algorithm Design & Analysis
  gave me the specific C++ knowledge" of a module whose own entry never names C++ (2026-11-08). It needs no
  "covered" verb, so it is a check of its own; the tool must be in the module's own entry (`_module_text`)
  or its name.
- Course material is never given to the writer (`record.for_writing`), so the module's own notes cannot
  support a claim: the person checks it under "Check before sending" when the rewrite keeps it.
"""
from __future__ import annotations

import re

from jobhunt.profile import merge
from jobhunt.writing import atoms, check, drafts

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
MARK = re.compile(r"\b\d{1,3}(?:\.\d)?\s*(?:%|per ?cent|marks?\b)?|\b(?:first|distinction|merit)\b", re.I)
# How a sentence ties a module's mark to a piece of work: "through a team project", "by building ...".
ACTIVITY = re.compile(r"\b(?:through|by|via|from|for|during|on|in|to)\s+(?:(?:building|making|writing|creating|"
                      r"developing|designing)\s+)?(?P<said>(?:a|an|the|my|our|one)\s+(?P<what>(?:[\w-]+\s+){0,3}?"
                      r"(?:project|assignment|coursework|hackathon|placement|competition|prototype|app|tool|game|"
                      r"website|study)))\b", re.I)


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
    """The passages and lines of the sources, each a unit a module's name must share with what it covered; a
    list-shaped line is one unit per entry (`atoms.split`)."""
    out = []
    for text in sources:
        for para in re.split(r"\n\s*\n", text or ""):
            out += [para, *(e for line in para.splitlines() for e in atoms.split(line))]
    return [u for u in out if u.strip()]


def modules_listed(sources: tuple[str, ...]) -> list[str]:
    """The names of the modules a module list in the sources gives (`atoms.module_entries`)."""
    return list(dict.fromkeys(name for text in sources for line in (text or "").splitlines()
                              for name, _ in atoms.module_entries(line)))


def _module_in(sentence: str, known: list[list[str]]) -> list[str] | None:
    """The names of the first module a sentence names as a module (`_as_module`), or None."""
    codes = [[c] for c in dict.fromkeys(CODE.findall(sentence)) if not any(c in k for k in known)]
    named = [(n, _find(sentence, n)) for n in [*known, *codes] if n]
    named = sorted(((n, m) for n, m in named if m and _as_module(sentence, m)), key=lambda p: p[1].start())
    return named[0][0] if named else None


def _module_text(units: list[str], names: list[str], others: list[list[str]]) -> str:
    """What the sources say of one module: the lines naming it (a module's own entry of a CV's list), or the
    passages naming it when no line does, each cut to the module's part (`_segment`). A whole CV is one
    paragraph, so its other lines would otherwise speak for every module it names."""
    lines = [u for u in units if "\n" not in u.strip() and _find(u, names)]
    return " ".join(_segment(u, names, others) for u in (lines or units))


def joined(draft: str, modules: list[str], sources: tuple[str, ...], cards: list[dict] = ()) -> list[str]:
    """The sentences of `draft` that tie a named module (checked, or in a module list of `sources`) and its mark
    to a project card or an activity (`ACTIVITY`) the module's own entry never names, each with the module:
    "... (nothing in your facts ties Ferry Studies to a team project)"."""
    names_of = {m.lower(): m for m in [*modules, *modules_listed(sources)] if m.strip()}
    known = [_names(m) for m in names_of.values()]
    units = _units(sources)
    out = []
    for sentence in check.sentences(draft):
        names = _module_in(sentence, known)
        if names is None or not MARK.search(sentence):
            continue
        held = _module_text(units, names, [n for n in known if n is not names])
        projects = [c["name"] for c in cards if c["name"].strip() and drafts.names_card(sentence, c, list(cards))
                    and not drafts.names_card(held, c, list(cards))]
        activity = ACTIVITY.search(sentence)
        wanted = {w[:STEM] for w in content(activity.group("what"))} if activity else set()
        if projects or (wanted and not wanted <= _stems(held)):
            tied = projects[0] if projects else activity.group("said")
            out.append(f"{sentence} (nothing in your facts ties {names[0]} to {tied})")
    return out


def tooled(draft: str, modules: list[str], sources: tuple[str, ...], known: list[str]) -> list[str]:
    """The sentences of `draft` that name a module (checked, or in a module list of `sources`) with a tool or
    language of `known` the module's own entry never names, each with the module and the tool: "...
    (nothing in your facts ties Algorithm Design to C++)". A listed entry that is a tool's name ("SQL" of
    "Databases module: SQL, query plans") is what a module covered, never a module of its own."""
    tools = {t.strip().lower() for t in known}
    names_of = {m.lower(): m for m in [*modules, *modules_listed(sources)] if m.strip() and m.lower() not in tools}
    known_names = [_names(m) for m in names_of.values()]
    units = _units(sources)
    out = []
    for sentence in check.sentences(draft):
        names = _module_in(sentence, known_names)
        if names is None:
            continue
        held = _module_text(units, names, [n for n in known_names if n is not names])
        tools = [t for t in dict.fromkeys(known) if t.strip() and drafts.names_tool(sentence, t)
                 and not drafts.names_tool(held, t) and not drafts.names_tool(" ".join(names), t)]
        if tools:
            out.append(f"{sentence} (nothing in your facts ties {names[0]} to {tools[0]})")
    return out


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
