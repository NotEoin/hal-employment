"""The `coursework` reader: one piece of the person's own work for a course, and the skills and methods it shows.

Coursework is added on an education card (`sections.registry`, section `coursework`, role own), so the card
says which course it is for; the person may say which of the card's modules (`store.MODULE_SECTIONS`). Any
field's work fits: an essay, a lab report, a portfolio, a presentation, code, a design, a dissertation.

What the reader gives:

- in `details` (about the piece, not the person): its summary, its mark when the work states one ("Mark:
  68"), Hal's suggested module (`suggest_module`), and the AI's reading, kept so a read again asks nothing new;
- as facts: each skill or method the work shows ("archival research", "thematic analysis", "regression in
  R", "close reading", "lab titration"), with its quote and line, and each programming language of its code
  files. The pipeline files them with the section's role, so they count as done in coursework;
- and, only when the person put the piece in their showcase (its star), a project fact: its name, a summary
  saying what it was for (its module, or the course; "Dissertation for ..." for the dissertation) and its
  mark, its methods and languages as tools, and its date as the period.

Design decisions, each with its reason:

- The title, date, mark, module and languages are computed (sections plan, 2026-11-09: anything that can be
  computed never comes from the AI). The AI writes only the summary and names the methods, each with a
  quote; a method whose quote is not in the work is left out, so nothing rests on words the work does not
  have.
- Without the AI the summary is the work's own abstract or first paragraph, and only languages are found:
  the work is still read, and reading it again later finds the rest.
- Coursework never makes a project card on its own: a course's every essay as a project crowded out the
  person's real projects. A star makes one, evidenced by the piece alone, and `settle` gives that card the
  star and the context "degree" (`STAR_BY`); taking the star off reads the piece again with no project fact,
  so the card no source names any more is hidden (`profile.store.swap_source`) and its star goes, while the
  piece's skills stay. A card the person starred or unstarred themselves (`profile.store.STAR_BY_YOU`) keeps
  their choice.
- The dissertation is a kind of document (`dissertation`), set by the "This is my dissertation" toggle
  (`store.change`) and at most one per education card (`store.check_dissertation`), so its evidence carries
  the type the writer frames as a dissertation (`writing.stage.DEGREE_TYPES`) and its card is named by the
  dissertation's own title.
- Hal suggests a module, never sets one: a module code in the file's name or opening lines first, then the
  share of a module's own words found there (`SHARE_MIN`, as `ingest.lectures.same_lecture`). The person
  confirms or changes it on the page, and their choice is kept apart from Hal's (`store.MODULE_SECTIONS`).
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from pathlib import PurePosixPath

from jobhunt import db
from jobhunt.ingest import convert, lectures, rank
from jobhunt.profile import merge
from jobhunt.profile import store as profile_store
from jobhunt.sections import readers

log = logging.getLogger(__name__)

PURPOSE = "coursework_read"
PROMPT_CHARS = 60_000      # as `ingest.understand.PROMPT_CHARS`: a long report's first chapters in a 64K context
TIMEOUT_S = 300
SUMMARY_CHARS = 400        # "a short summary": two or three sentences
SUMMARY_MIN_WORDS = 12     # a shorter paragraph is a title page line ("COMP2040 report, March 2024"), not a summary
METHODS_MAX = 12           # past this a list of methods is a list of every word, not what the work shows
METHOD_CHARS = 60          # a method is a few words; a longer one is a sentence
HEAD_CHARS = readers.DATE_CHARS    # where a piece names its module: its title page and first lines
SHARE_MIN = lectures.SHARE_MIN
# Code files, whose language is a computed skill; prose and page formats (.md, .tex, .html) are not code.
CODE_LANGUAGES = {s: convert.LANGUAGES[s] for s in rank.CODE_SUFFIXES - {".html", ".css"} if s in convert.LANGUAGES}
# A mark as a piece of work states it: "Mark: 68", "Final grade: 72%", "Score - 65/100", "Grade: 2:1".
MARK = re.compile(r"^\W*(?:final |overall )?(?:mark|grade|score)(?: awarded| given)?\s*[:=-]\s*"
                  r"(?P<mark>\d{1,3}(?:\.\d)?\s*%?(?:\s*/\s*100)?|[A-F][+-]?|1st|2:1|2:2|3rd|first|distinction|merit|"
                  r"pass)(?![\w:])", re.I | re.M)
ABSTRACT = re.compile(r"^#{1,4}\s*(?:abstract|summary|overview|introduction)\s*$", re.I | re.M)
SENTENCE_END = re.compile(r"(?<=[.!?])\s")
DISSERTATION = "dissertation"      # the kind of document of the dissertation (`registry.DOC_KINDS`)
STAR_BY = "coursework"             # `profile_items.star_by` of a card starred through its coursework
DEGREE = "degree"                  # the context of a showcase piece (`writing.stage.DEGREE`)
FILE_HEADING = re.compile(r"^### (.+)$")     # `readers.text_of`'s heading over each file of a source
LOCATE_CHARS = 30          # a quote's start, enough to find its line; a quote may run on past the line's end


def mark_in(text: str) -> str:
    """The mark a piece of work states ("68", "72%", "2:1"), or ""."""
    m = MARK.search(text)
    return " ".join(m.group("mark").split()) if m else ""


def _paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip() and not p.lstrip().startswith("#")]


def first_summary(text: str) -> str:
    """The work's own abstract or first full paragraph, cut at a sentence near `SUMMARY_CHARS`; "" for none."""
    m = ABSTRACT.search(text)
    for part in (_paragraphs(text[m.end():]) if m else []) + _paragraphs(text):
        if len(part.split()) < SUMMARY_MIN_WORDS:
            continue
        if len(part) <= SUMMARY_CHARS:
            return part
        cut = [s.start() for s in SENTENCE_END.finditer(part) if s.start() <= SUMMARY_CHARS]
        return part[:cut[-1]] if cut else part[:SUMMARY_CHARS].rsplit(" ", 1)[0]
    return ""


def suggest_module(name: str, title: str, text: str, choices: list[str]) -> str:
    """The module of `choices` a piece named `name` with this title and text seems to be for, or ""."""
    head = f"{name}\n{title}\n{text[:HEAD_CHARS]}"
    named = [merge.module_code(m.group(0)) for m in merge.MODULE_CODE.finditer(head)]
    by_code = [(named.index(merge.module_code(c)), c) for c in choices if merge.module_code(c) in named]
    if by_code:
        return min(by_code)[1]
    words = lectures.subject_words(head)
    best, share = "", 0.0
    for choice in choices:
        own = lectures.subject_words(merge.MODULE_CODE.sub(" ", choice))
        if own and (got := len(own & words) / len(own)) >= SHARE_MIN and got > share:
            best, share = choice, got
    return best


def _normal(words: str) -> str:
    return " ".join(words.split()).lower()


def locate(quote: str, text: str, files: tuple[readers.SourceFile, ...]) -> tuple[str, int | None]:
    """(file, line) of `quote` in the note's text: the file it is under when there are several, and the line its
    start is on; the only file and no line when it is not there."""
    one = files[0].rel if len(files) == 1 else ""
    start = _normal(quote)[:LOCATE_CHARS]
    under = one
    for n, line in enumerate(text.splitlines(), 1):
        if not one and (heading := FILE_HEADING.match(line)):
            under = heading.group(1)
        if start and start in _normal(line):
            return under, n
    return one, None


def own_words(text: str, files: tuple[readers.SourceFile, ...]) -> str:
    """The note's text without the heading `readers.text_of` puts over each file, so a title is the work's own."""
    rels = {f.rel for f in files}
    return "\n".join(line for line in text.splitlines()
                     if not ((m := FILE_HEADING.match(line)) and m.group(1) in rels))


def _languages(files: tuple[readers.SourceFile, ...], text: str) -> list[readers.Fact]:
    """A skill for each programming language of the source's code files, quoting the file's first line."""
    out, seen = [], set()
    for f in files:
        language = CODE_LANGUAGES.get(PurePosixPath(f.rel).suffix.lower())
        if not language or language in seen:
            continue
        seen.add(language)
        try:
            first = next((x.strip() for x in f.path.read_text(encoding="utf-8", errors="replace").splitlines()
                          if x.strip()), f.rel)
        except OSError:
            first = f.rel
        place, line = locate(first, text, files)
        out.append(readers.Fact("skill", {"name": language}, readers.Quote("name", first, place or f.rel, line)))
    return out


def ask(source: readers.Source, title: str, text: str) -> dict | None:
    """The AI's summary and methods of a piece of work, {"sha", "summary", "methods": [[name, quote]]}; the last
    read's when the text is the same; None when the AI cannot answer now."""
    sha = hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()
    kept = source.details.get("ai")
    if isinstance(kept, dict) and kept.get("sha") == sha:
        return kept
    from jobhunt import ai
    from jobhunt.ai import prompts

    course = (source.target or {}).get("title", "")
    user = f"Course: {course}\nTitle: {title}\n\nText:\n{text[:PROMPT_CHARS]}"
    try:
        reply = ai.chat_json(prompts.text(PURPOSE), user, role="checker", timeout=TIMEOUT_S, purpose=PURPOSE,
                             priority=2)
    except ai.LLMUnavailable as e:
        log.info("read coursework %s without the AI: %s", source.id, e)
        return None
    whole = _normal(text)
    methods, seen = [], set()
    for m in reply.get("methods") or []:
        if not isinstance(m, dict):
            continue
        name, quote = " ".join(str(m.get("name") or "").split()), " ".join(str(m.get("quote") or "").split())
        key = merge.skill_key(name)
        if not name or len(name) > METHOD_CHARS or not quote or _normal(quote) not in whole or key in seen:
            continue
        seen.add(key)
        methods.append([name, quote])
    summary = " ".join(str(reply.get("summary") or "").split())
    return {"sha": sha, "summary": summary[:SUMMARY_CHARS * 2], "methods": methods[:METHODS_MAX]}


def framed(source: readers.Source, summary: str, mark: str) -> str:
    """A showcase piece's summary, opening with what it was for: "Dissertation for the BA History, marked 72%."."""
    what = "Dissertation" if source.doc_kind == DISSERTATION else "Coursework"
    course = (source.target or {}).get("title", "")
    module = source.module or ""
    where = f" for {module}" if module else (f" for the {course}" if course else "")
    opening = f"{what}{where}" + (f", marked {mark}" if mark else "") + "."
    return f"{opening} {summary}".strip()


def showcase(source: readers.Source, title: str, text: str, date: str, summary: str, mark: str,
             tools: list[str]) -> readers.Fact:
    """The project fact of a piece in the person's showcase, resting on its title's line."""
    name = source.title or title
    place, line = locate(name, text, source.files)
    fields = {"name": name, "summary": framed(source, summary, mark), "tools": tools,
              "period": merge.date_words(date)}
    return readers.Fact("project", {k: v for k, v in fields.items() if v},
                        readers.Quote("name", name if line else title, place, line))


def settle(con: sqlite3.Connection) -> None:
    """After coursework was read or removed: the project cards a starred piece rests on carry the star and the
    context "degree", and a card starred through coursework that no starred piece rests on any more loses it.
    A card the person starred or unstarred themselves keeps their choice."""
    showcase_cards = """SELECT e.item_id FROM profile_evidence e JOIN record_sources s ON s.id = e.source_id
                        WHERE s.section = 'coursework' AND s.starred = 1"""
    now = db.now()
    with con:
        con.execute(f"""UPDATE profile_items SET star = 1, star_by = ?, updated = ? WHERE kind = 'project'
                        AND star = 0 AND star_by != ? AND id IN ({showcase_cards})""",
                    (STAR_BY, now, profile_store.STAR_BY_YOU))
        con.execute(f"""UPDATE profile_items SET context = ?, context_by = 'hal' WHERE kind = 'project'
                        AND context_by != 'person' AND id IN ({showcase_cards})""", (DEGREE,))
        con.execute(f"""UPDATE profile_items SET star = 0, star_by = '', updated = ? WHERE star_by = ?
                        AND id NOT IN ({showcase_cards})""", (now, STAR_BY))


class CourseworkReader:
    """Reads one piece of coursework: its title, date, mark, summary, suggested module, and the skills it shows."""

    name = "coursework"

    def read(self, source: readers.Source) -> readers.Reading:
        """The piece's text and facts, with its mark, summary and suggested module in `details`."""
        if not source.files:
            raise ValueError("This source has no files to read.")
        text = readers.text_of(source.files)
        name = source.title or (source.files[0].rel if len(source.files) == 1 else source.locator)
        title = readers.title_in(own_words(text, source.files), name)
        read = ask(source, title, text)
        summary = (read or {}).get("summary") or first_summary(text)
        facts = []
        for method, quote in (read or {}).get("methods", []):
            place, line = locate(quote, text, source.files)
            facts.append(readers.Fact("skill", {"name": method}, readers.Quote("name", quote, place, line)))
        facts += _languages(source.files, text)
        mark, date = mark_in(text), readers.date_in(text)
        if source.starred:
            facts.append(showcase(source, title, text, date, summary, mark, [f.fields["name"] for f in facts]))
        choices = [m["name"] for m in ((source.target or {}).get("fields") or {}).get("modules") or []
                   if m.get("name")]
        details = {"mark": mark, "summary": summary, "module": suggest_module(name, title, text, choices),
                   **({"ai": read} if read else {})}
        return readers.Reading(title=title, date=date, text=text, summary=summary, facts=facts, details=details)

    def settle(self, con: sqlite3.Connection) -> None:
        """Gives the showcase pieces' project cards their star and context (`settle`)."""
        settle(con)


readers.register(CourseworkReader())
