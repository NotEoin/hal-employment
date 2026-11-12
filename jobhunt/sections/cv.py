"""The CV reader: the jobs, education, skills, projects, awards, basics and links a CV of the person's lists.

The person put the file under CVs, so it is their CV: the AI is asked only what the CV says (the `record_cv`
prompt), never what kind of document it is or whose it is. Its facts are checked as the bulk read checks
them (`ingest.understand.facts`): each quote against the CV's words, and an employer, institution or issuer
the CV never names is left out. Each fact keeps the file and the line its quote is on.

Design decisions, each with its reason:

- A CV is read by the AI only. When the AI is not set up or does not answer, `LLMUnavailable` reaches the
  pipeline and the CV waits to be read again: the CV studio's rules read CVs without the AI into junk cards
  (a project's result as a degree, a skills line as a university) when Hal's model was stopped (2026-11-11).
- Dates, grades and marks are copied as written; the profile turns dates into `YYYY-MM` (`profile.store.clean`).
- A CV's date (`Reading.date`, kept as `record_sources.dated`) is the one in its heading ("CV, March 2025"),
  else the latest date of its entries, else the date of its file: the newest CV leads the others
  (`profile.cvs`), and a CV is newest by what it covers, not by when it was copied to Hal.
- A CV headed by another person's name, that never names the person (`you.name`, `you.other_names`, in its
  words or its email address), is theirs: a classmate's CV sorted under CVs gave the person three of the
  classmate's projects (2026-11-12). It is kept out with whose it is, before the AI is asked, until the person
  says it is theirs (`meta.theirs`, "This is mine"). A heading that only names a role ("Software Engineer") is
  no name, and with no name set every CV is the person's.
- Which CV is current is `profile.cvs`'s answer, so the card, the letters and the list of CVs agree; reading,
  removing or choosing a CV works out again every card the CVs give (`settle`).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jobhunt.ingest import convert, rank, understand
from jobhunt.profile import cvs, merge
from jobhunt.profile import store as profile_store
from jobhunt.sections import readers

log = logging.getLogger(__name__)

SECTION = cvs.SECTION
PURPOSE = "record_cv"
PRIORITY = 1               # the person waits on the page, as for the `record.sources` action
HEADING_LINES = 3          # a CV's own date ("CV, March 2025") is in its first lines
YEAR = re.compile(r"\b((?:19|20)\d\d)\b")
LOCATE_WORDS = 6           # a quote's first words find its line; a CV line is rarely shorter
HEAD_LINES = 5             # the lines a CV's owner's name heads: after "Curriculum Vitae" and a title at most
NAME_WORDS = (2, 4)        # "Ben Smith" to "Mary Anne de Vere Smith" written short
NAME_WORD = re.compile(r"[^\W\d_][\w'\-]*")   # letters of any alphabet, so "Siobh\u00e1n" is a name word
NOT_NAME = {"curriculum", "vitae", "cv", "resume", "profile", "personal", "statement", "contact", "details",
            "engineer", "developer", "student", "graduate", "scientist", "analyst", "manager", "assistant",
            "officer", "designer", "teacher", "intern", "consultant", "researcher", "volunteer", "software",
            "data", "web", "junior", "senior", "summary"}


def file_texts(files: tuple[readers.SourceFile, ...]) -> list[tuple[str, str]]:
    """Each file's words as (rel, text), the files with none left out."""
    out = []
    for f in files:
        words = convert.text_of(f.path, rank.rule_for(f.rel, f.path)).strip()
        if words:
            out.append((f.rel, words))
    return out


def joined(texts: list[tuple[str, str]]) -> str:
    """The files' words as `readers.text_of` joins them: each under its own heading when there are several."""
    if len(texts) == 1:
        return texts[0][1]
    return "\n\n".join(f"### {rel}\n\n{words}" for rel, words in texts)


def locate(texts: list[tuple[str, str]], quote: str) -> tuple[str, int | None]:
    """The file and the line (from 1) a quote starts on: the first line holding its first words, compared as
    `understand.quote_found` compares (a quote may run on to the next line); the first file and no line when
    none holds them."""
    head = " ".join(re.findall(r"\w+", quote)[:LOCATE_WORDS])
    if head:
        for rel, words in texts:
            for n, line in enumerate(words.splitlines(), 1):
                if understand.quote_found(head, line):
                    return rel, n
    return (texts[0][0] if texts else ""), None


def _fact(texts: list[tuple[str, str]], kind: str, fields: dict, field: str, quote: str) -> readers.Fact:
    place, line = locate(texts, quote)
    return readers.Fact(kind, fields, readers.Quote(field, quote, place, line))


# Whose CV ----------------------------------------------------------------------------------------------------

def _name_line(line: str) -> str:
    """`line` as a person's name ("Ben Smith"), or "" when it is not shaped like one."""
    words = line.strip().lstrip("#*_ ").rstrip("*_ ").split()
    if not NAME_WORDS[0] <= len(words) <= NAME_WORDS[1]:
        return ""
    if any(not NAME_WORD.fullmatch(w) or not w[0].isupper() or w.lower() in NOT_NAME for w in words):
        return ""
    return " ".join(words)


def someone_else(text: str, names: str) -> str:
    """The name heading a CV when it is plainly another person's: a name in its first lines, with no word of
    `names` (the person's own, space-separated) anywhere in it; "" when it may be the person's."""
    own = re.findall(r"[^\W\d_]{2,}", names.lower())
    if not own:
        return ""
    plain = text.lower()
    if any(re.search(rf"(?<![^\W\d_]){re.escape(w)}(?![^\W\d_])", plain) for w in own):
        return ""
    head = [line for line in text.splitlines() if line.strip()
            and not line.startswith((convert.AUTHOR_LINE, convert.SAVED_LINE))][:HEAD_LINES]
    return next((found for line in head if (found := _name_line(line))), "")


def own_names() -> str:
    """The person's name and the other names they go by, from their settings."""
    from jobhunt.core import settings

    return f"{understand.owner()} {settings.get('you.other_names') or ''}".strip()


# The AI ------------------------------------------------------------------------------------------------------

def by_ai(texts: list[tuple[str, str]], name: str) -> list[readers.Fact]:
    """The CV's facts as the AI reads them with the `record_cv` prompt.

    Raises `LLMUnavailable` when the AI does not answer, and `PrivateLeak` when the CV may not go to it.
    """
    from jobhunt import ai
    from jobhunt.ai import prompts

    text = joined(texts)
    system = prompts.text(PURPOSE, name=understand.owner() or "the person whose CV this is")
    reply = ai.chat_json(system, f"File: {name}\n\nText:\n{text[:understand.PROMPT_CHARS]}", role="checker",
                         timeout=understand.TIMEOUT_S, purpose=PURPOSE, priority=PRIORITY)
    found = understand.facts(reply.get("facts") if isinstance(reply, dict) else None, text, name)
    return [_fact(texts, f["kind"], f["fields"], f["field"], f["quote"]) for f in found]


# The CV's date ----------------------------------------------------------------------------------------------

def _point(text: str) -> str:
    """The first date in `text` as `YYYY-MM`, else its first year, else ""."""
    if found := readers.date_in(text):
        return found
    m = YEAR.search(text)
    return m.group(1) if m else ""


def _later(a: str, b: str) -> str:
    ia, ib = merge.month_index(a, "end"), merge.month_index(b, "end")
    if ib is None:
        return a
    return b if ia is None or ib > ia else a


def cv_date(text: str, facts: list[readers.Fact], files: tuple[readers.SourceFile, ...]) -> str:
    """The CV's own date: one in its first lines, else its latest dated entry, else its file's date (`YYYY-MM`)."""
    head = "\n".join(line for line in text.splitlines() if line.strip()
                     and not line.startswith((convert.AUTHOR_LINE, convert.SAVED_LINE)))
    if found := _point("\n".join(head.splitlines()[:HEADING_LINES])):
        return found
    latest = ""
    for f in facts:
        for key in ("start", "end", "date"):
            point = merge.clean_date(str(f.fields.get(key) or ""), "end" if key == "end" else "start")
            if merge.month_index(point, "end") is not None:
                latest = _later(latest, point)
    if latest:
        return latest
    stamps = [f.path.stat().st_mtime for f in files if f.path.exists()]
    return datetime.fromtimestamp(max(stamps), UTC).strftime("%Y-%m") if stamps else ""


def settle(con: sqlite3.Connection) -> list[int]:
    """Works out again every card the CVs give, after the current CV may have changed; returns their ids."""
    return profile_store.refold_sources(con, cvs.cv_sources(con))


class CvReader:
    """Reads one of the person's CVs into facts with the AI."""

    name = "cv"

    def read(self, source: readers.Source) -> readers.Reading:
        """The CV's text, its file's name as its title, its own date and its facts."""
        if not source.files:
            raise ValueError("This CV has no files to read.")
        texts = file_texts(source.files)
        if not texts:
            raise ValueError("Hal found no words in this CV. Upload it as a Word file or a PDF with text in it.")
        name = source.files[0].rel
        whose = "" if (source.meta or {}).get("theirs") else someone_else(joined(texts), own_names())
        if whose:
            raise readers.NotTheirs(f"This CV is {whose}'s, not yours, so Hal took nothing from it. Remove it, or "
                                    "press This is mine if it is yours.", whose)
        facts = by_ai(texts, name)
        title = source.title or readers.title_in("", Path(name).stem if len(source.files) == 1 else source.locator)
        return readers.Reading(title=title, date=cv_date(joined(texts), facts, source.files), text=joined(texts),
                               facts=facts)


readers.register(CvReader())
