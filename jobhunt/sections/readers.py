"""Readers: what Hal takes from one source of a section, each fact with the words and the place it came from.

A reader is given a `Source` (its section, kind of document, title, files or link, and the card it belongs
to) and returns a `Reading`: the source's title, a date found in it, its text for the record's note, a short
summary, and its facts. Each `Fact` is a profile kind and fields (`profile.store.KINDS`) with one `Quote`:
the field it supports, the words, and where they are (a file inside the source or a URL, and the line).

Design decisions, each with its reason:

- A reader never decides what a file is or whose it is: the section said so when the person added it
  (`sections.registry`). Readers only take facts out of it.
- `document` is the one reader written so far: it keeps the text and finds only a title and a date, so the
  pipeline runs from upload to note end to end. The others are stubs (`Stub`) that raise `NotReadYet`
  naming the phase that brings them, and the page shows their choices as coming soon (`ready`).
- Later phases put their reader in place with `register`; a test can do the same with a made-up one.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from jobhunt.ingest import convert, rank
from jobhunt.profile import merge

TITLE_CHARS = 120          # a heading longer than this is a sentence, not a title
DATE_CHARS = 4000          # a document's own date is near its start: a letter's date line, a certificate's
# "5 March 2025", "March 2025" and "2025-03-05", the forms a document's own date takes.
DATE = re.compile(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?"
                  r"\s+(?:19|20)\d\d|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d\d"
                  r"|(?:19|20)\d\d-\d\d-\d\d)\b", re.I)
HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*#*\s*$", re.M)


@dataclass(frozen=True)
class Quote:
    """The words a fact rests on: the field they support, the quote, and where it is in the source."""

    field: str
    quote: str
    place: str = ""            # a file inside the source, or a URL
    line: int | None = None    # from 1


@dataclass(frozen=True)
class Fact:
    """One fact for the profile: a kind of `profile.store.KINDS`, its fields, and the quote it rests on.

    `role` is whose words the quote is (`ingest.roles`), "" for the section's own: a referee's words about the
    person are `others`, never the person's own claims."""

    kind: str
    fields: dict
    quote: Quote
    role: str = ""


@dataclass(frozen=True)
class SourceFile:
    """One file of a source: its path inside the source and where Hal keeps it."""

    rel: str
    path: Path


@dataclass(frozen=True)
class Source:
    """What a reader is given: one source of a section, as the person placed it."""

    id: int
    section: str
    doc_kind: str
    title: str
    held: str                                  # `registry.HELD`: upload, files, link, account, folder, manual
    locator: str = ""                          # a URL, an account name or a folder's path
    files: tuple[SourceFile, ...] = ()
    target: dict | None = None                 # the profile card it belongs to (`profile.store.item`), if any


@dataclass
class Reading:
    """What a reader took from a source."""

    title: str = ""
    date: str = ""                             # `YYYY-MM` or `YYYY`, from the source's own words
    text: str = ""                             # what the record's note keeps
    summary: str = ""
    facts: list[Fact] = field(default_factory=list)


class Reader(Protocol):
    """Reads one source; raises `NotReadYet` when it is a stub, OSError or ValueError when it cannot read it."""

    name: str

    def read(self, source: Source) -> Reading:
        """The source's title, date, text, summary and facts."""


class NotReadYet(NotImplementedError):
    """A reader a later phase of the sectioned record writes; the source waits, and nothing else fails."""

    def __init__(self, reader: str, phase: int) -> None:
        super().__init__(f"Hal cannot read these yet (the {reader} reader comes in phase {phase}). Your file is "
                         "kept and Hal reads it once it can.")
        self.reader, self.phase = reader, phase


@dataclass(frozen=True)
class Stub:
    """A reader not written yet: it says which phase brings it."""

    name: str
    phase: int

    def read(self, source: Source) -> Reading:
        """Never reads: raises NotReadYet."""
        raise NotReadYet(self.name, self.phase)


def text_of(files: Iterable[SourceFile]) -> str:
    """The words of a source's files as Markdown, each file under its own heading when there are several."""
    files = list(files)
    parts = []
    for f in files:
        words = convert.text_of(f.path, rank.rule_for(f.rel, f.path)).strip()
        if words:
            parts.append(f"### {f.rel}\n\n{words}" if len(files) > 1 else words)
    return "\n\n".join(parts)


def title_in(text: str, fallback: str) -> str:
    """The first heading of `text` when it reads as a title, else `fallback` with its separators as spaces."""
    for m in HEADING.finditer(text):
        heading = " ".join(m.group(1).split())
        if heading and len(heading) <= TITLE_CHARS:
            return heading
    words = re.sub(r"[_-]+", " ", Path(fallback).stem if "." in fallback else fallback)
    return " ".join(words.split()) or "Untitled"


def date_in(text: str) -> str:
    """The first date near the start of `text` as `YYYY-MM`, or "" when it gives none."""
    m = DATE.search(text[:DATE_CHARS])
    if not m:
        return ""
    found = m.group(1)
    if re.fullmatch(r"\d{4}-\d\d-\d\d", found):
        found = found[:7]
    return merge.parse_point(" ".join(found.replace(",", " ").split()), "start") or ""


class DocumentReader:
    """The generic reader: keeps a document's text and finds its title and date, and no facts."""

    name = "document"

    def read(self, source: Source) -> Reading:
        """The text of the source's files, the first heading as its title (else its name) and its first date."""
        if not source.files:
            raise ValueError("This source has no files to read.")
        text = text_of(source.files)
        name = source.title or (source.files[0].rel if len(source.files) == 1 else source.locator)
        return Reading(title=title_in(text, name), date=date_in(text), text=text)


# The phase of the sectioned record that brings each reader (sections plan, 2026-11-09).
CV_PHASE, PROJECT_PHASE, EDUCATION_PHASE, DOCUMENTS_PHASE = 2, 3, 4, 5
READERS: dict[str, Reader] = {
    "document": DocumentReader(),
    "cv": Stub("cv", CV_PHASE), "letter": Stub("letter", CV_PHASE),
    "repo": Stub("repo", PROJECT_PHASE), "github_account": Stub("github_account", PROJECT_PHASE),
    "project_files": Stub("project_files", PROJECT_PHASE), "portfolio": Stub("portfolio", PROJECT_PHASE),
    "page": Stub("page", PROJECT_PHASE), "manual": Stub("manual", PROJECT_PHASE),
    "coursework": Stub("coursework", EDUCATION_PHASE), "lecture": Stub("lecture", EDUCATION_PHASE),
    "official": Stub("official", EDUCATION_PHASE),
    "job_doc": Stub("job_doc", DOCUMENTS_PHASE),
}


def register(reader: Reader) -> Reader | None:
    """Puts `reader` in place under its name; returns the one it replaced, so a test can put it back."""
    before = READERS.get(reader.name)
    READERS[reader.name] = reader
    return before


def get(name: str) -> Reader:
    """The reader called `name`; LookupError for one that does not exist."""
    try:
        return READERS[name]
    except KeyError:
        raise LookupError(f"There is no reader called {name!r}.") from None


def ready(name: str) -> bool:
    """True when the reader called `name` reads (it is no stub)."""
    return not isinstance(READERS.get(name), Stub)
