"""The CV reader: the jobs, education, skills, projects, awards, basics and links a CV of the person's lists.

The person put the file under CVs, so it is their CV: the AI is asked only what the CV says (the `record_cv`
prompt), never what kind of document it is or whose it is. Its facts are checked as the bulk read checks
them (`ingest.understand.facts`): each quote against the CV's words, and an employer, institution or issuer
the CV never names is left out. Each fact keeps the file and the line its quote is on.

Design decisions, each with its reason:

- When the AI is not set up or does not answer, the CV is read by the CV studio's own rules
  (`cvstudio.parse.read_rules`), the same rules that read an uploaded CV into sections, so a CV is never left
  unread. Their facts quote the CV's own lines.
- Dates, grades and marks are copied as written; the profile turns dates into `YYYY-MM` (`profile.store.clean`).
- A CV's date (`Reading.date`, kept as `record_sources.dated`) is the one in its heading ("CV, March 2025"),
  else the latest date of its entries, else the date of its file: the newest CV leads the others
  (`profile.cvs`), and a CV is newest by what it covers, not by when it was copied to Hal.
- Which CV is current is `profile.cvs`'s answer, so the card, the letters and the list of CVs agree; reading,
  removing or choosing a CV works out again every card the CVs give (`settle`).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from jobhunt.ingest import convert, rank, understand
from jobhunt.profile import cvs, merge
from jobhunt.profile import store as profile_store
from jobhunt.sections import readers

log = logging.getLogger(__name__)

SECTION = cvs.SECTION
PURPOSE = "record_cv"
PRIORITY = 1               # the person waits on the page, as for the `record.sources` action
HEADING_LINES = 3          # a CV's own date ("CV, March 2025") is in its first lines
MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
YEAR = re.compile(r"\b((?:19|20)\d\d)\b")
EMPHASIS = re.compile(r"\*\*|__")
BULLET = "\u2022"            # a bullet a converted Word file keeps at a line's start
LOCATE_WORDS = 6           # a quote's first words find its line; a CV line is rarely shorter
# CV sections whose entries are jobs, as the CV studio names its kinds of section.
JOB_SECTIONS = ("experience", "volunteering", "research", "teaching")
PROJECT_SECTIONS = ("projects", "portfolio")
SKILL_SECTIONS = ("skills", "languages")
CONTACT_BASICS = (("name", "name"), ("email", "email"), ("phone", "phone"), ("city", "location"))


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


# Rules --------------------------------------------------------------------------------------------------

def _lines(text: str) -> list:
    """The CV's lines as the CV studio's rules take them: a Markdown heading below the first one is a heading,
    the first (the person's name) plain text, and the lines naming who saved the file are left out."""
    from jobhunt.cvstudio import parse

    out, first = [], True
    for n, raw in enumerate(text.splitlines()):
        if raw.startswith((convert.AUTHOR_LINE, convert.SAVED_LINE)):
            continue
        style = ""
        if m := MD_HEADING.match(raw):
            raw = m.group(2)
            style = "" if first else "heading"
        raw = EMPHASIS.sub("", re.sub(r" {3,}", "\t", raw.rstrip()))
        body, bullet = parse._bulleted(raw)
        if body:
            first = False
            out.append(parse.Line(body, bullet=bullet, style=style, index=n))
    return out


def _quote(texts: list[tuple[str, str]], *parts: str) -> str:
    """The CV's own line that holds the first of `parts` found in it, as written; else the parts joined."""
    for part in (p for p in parts if p and p.strip()):
        rel, line = locate(texts, part)
        if line is not None:
            words = dict(texts)[rel].splitlines()[line - 1]
            return EMPHASIS.sub("", words.lstrip("#-*" + BULLET + " \t")).strip()
    return ", ".join(p for p in parts if p)


def _education(entry) -> dict:
    from jobhunt.cvstudio import parse

    split = parse.DEGREE_SPLIT.match(entry.heading)
    return {"qualification": split.group("degree") if split else entry.heading,
            "subject": split.group("subject") if split else "", "institution": entry.org, "start": entry.start,
            "end": entry.end, "grade": entry.grade,
            "modules": [{"name": m.name, "mark": m.mark} for m in entry.modules]}


def _entry_facts(kind: str, entry) -> list[tuple[str, dict, str]]:
    """(kind, fields, field the quote names) for one entry of a CV section of `kind`."""
    lines = [b.text for b in entry.bullets] or ([entry.text] if entry.text else [])
    if kind in JOB_SECTIONS:
        return [("job", {"title": entry.heading, "employer": entry.org, "location": entry.location,
                         "start": entry.start, "end": entry.end, "duties": lines}, "title")]
    if kind == "education":
        return [("education", _education(entry), "qualification")]
    if kind in PROJECT_SECTIONS:
        summary = entry.text or (lines[0] if lines else "") or entry.meta
        return [("project", {"name": entry.heading, "summary": summary, "link": entry.link}, "name")]
    if kind in SKILL_SECTIONS:
        return [("skill", {"name": item, "category": entry.heading}, "name") for item in entry.items]
    if kind == "certifications":
        return [("certificate", {"name": entry.heading, "date": entry.start}, "name")]
    if kind == "achievements":
        return [("achievement", {"title": entry.heading, "date": entry.start}, "title")]
    return []


def by_rules(texts: list[tuple[str, str]]) -> list[readers.Fact]:
    """The CV's facts as the CV studio's rules read it, each quoting the CV's own line."""
    from jobhunt.cvstudio import parse

    doc = parse.read_rules(_lines(joined(texts))).doc
    out = []
    contact = doc.contact_snapshot
    for key, basic in CONTACT_BASICS:
        if value := str(contact.get(key) or "").strip():
            out.append(_fact(texts, "basic", {"key": basic, "value": value}, "value", value))
    for url in contact.get("links") or []:
        out.append(_fact(texts, "link", {"label": _site(url), "url": url}, "url", url))
    for sec in doc.sections:
        for entry in sec.entries:
            for kind, fields, field in _entry_facts(sec.kind, entry):
                kept = {k: v for k, v in fields.items() if v not in (None, "", [])}
                if kept.get(field) or (kind == "education" and kept):
                    said = kept.get(field) or next(iter(kept.values()))
                    out.append(_fact(texts, kind, kept, field, _quote(texts, str(said), entry.org)))
    return out


def _site(url: str) -> str:
    """A link's site name ("github.com"), for its label."""
    host = urlsplit(url if "://" in url else f"https://{url}").hostname or ""
    return host.removeprefix("www.") or url


def _fact(texts: list[tuple[str, str]], kind: str, fields: dict, field: str, quote: str) -> readers.Fact:
    place, line = locate(texts, quote)
    return readers.Fact(kind, fields, readers.Quote(field, quote, place, line))


# The AI ------------------------------------------------------------------------------------------------------

def by_ai(texts: list[tuple[str, str]], name: str) -> list[readers.Fact] | None:
    """The CV's facts as the AI reads them with the `record_cv` prompt; None when the AI does not answer or
    gives no facts, so the rules read it instead."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    text = joined(texts)
    system = prompts.text(PURPOSE, name=understand.owner() or "the person whose CV this is")
    try:
        reply = ai.chat_json(system, f"File: {name}\n\nText:\n{text[:understand.PROMPT_CHARS]}", role="checker",
                             timeout=understand.TIMEOUT_S, purpose=PURPOSE, priority=PRIORITY)
    except (ai.LLMUnavailable, ai.PrivateLeak) as e:
        log.info("read the CV %s by rules: %s", name, e)
        return None
    found = understand.facts(reply.get("facts") if isinstance(reply, dict) else None, text, name)
    if not found:
        return None
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
    """Reads one of the person's CVs into facts; the AI when it answers, else the CV studio's rules."""

    name = "cv"

    def read(self, source: readers.Source) -> readers.Reading:
        """The CV's text, its file's name as its title, its own date and its facts."""
        if not source.files:
            raise ValueError("This CV has no files to read.")
        texts = file_texts(source.files)
        if not texts:
            raise ValueError("Hal found no words in this CV. Upload it as a Word file or a PDF with text in it.")
        name = source.files[0].rel
        facts = by_ai(texts, name)
        if facts is None:
            facts = by_rules(texts)
        title = source.title or readers.title_in("", Path(name).stem if len(source.files) == 1 else source.locator)
        return readers.Reading(title=title, date=cv_date(joined(texts), facts, source.files), text=joined(texts),
                               facts=facts)


readers.register(CvReader())
