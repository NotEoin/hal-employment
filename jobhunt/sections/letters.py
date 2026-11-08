"""The letter reader: cover letters and personal statements, kept only as samples of how the person writes.

A letter says what the person wanted an employer to think, so nothing in it becomes a fact about them (sections
plan, 2026-11-09): the reader keeps its text, title and date, and gives no facts. `voice_samples` hands the
newest few letters' words to a writer that wants to match the person's voice, and `voice_excerpts` cuts them to
what a letter's frame and paragraph requests carry (`pack.paragraph_scope`, `writing.paragraphs.voice_block`).

Design decisions, each with its reason:

- A sample is the text of the file Hal keeps (`sections.store.files_dir`), not of the note, so the record
  note's own heading and properties never read as the person's words.
- Only letters that were read count: one kept out as private, or waiting, is never a sample.
- An excerpt is the letter's own prose, at most `EXCERPT_WORDS` words cut at a sentence's end: headings,
  the date, the greeting and the sign-off say nothing of how the person writes, and a whole letter in every
  request would crowd out the facts. The writer is told to match its tone and sentence length and never to
  take a fact from it, and the checks never count its words as facts, so a fact found only there is flagged.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from jobhunt import db
from jobhunt.profile import merge
from jobhunt.sections import readers, store

SECTION = "letter"
NO_DATE = -1               # a letter with no date of its own is older than every dated one
EXCERPTS = 2               # samples a letter's requests carry: two show a voice, more crowd out the facts
EXCERPT_WORDS = 150        # words of one excerpt: a paragraph or two
# Lines of a letter that are not its prose: a greeting, a sign-off, a date or a name on its own.
NOT_PROSE = re.compile(r"^(?:dear\b|to whom\b|yours\b|kind regards|best wishes|many thanks|regards\b|sincerely\b)"
                       r"|^[\w ,.]{0,40}$", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class LetterReader:
    """Reads a cover letter or personal statement: its text, title and date, and never a fact."""

    name = "letter"

    def read(self, source: readers.Source) -> readers.Reading:
        """The letter's text, its title (a heading, else its file's name) and its first date; no facts."""
        if not source.files:
            raise ValueError("This letter has no files to read.")
        text = readers.text_of(source.files)
        if not text.strip():
            raise ValueError("Hal found no words in this letter.")
        name = source.title or (Path(source.files[0].rel).stem if len(source.files) == 1 else source.locator)
        return readers.Reading(title=readers.title_in(text, name), date=readers.date_in(text), text=text)


def voice_samples(con: sqlite3.Connection, n: int) -> list[str]:
    """The words of the person's `n` newest letters that were read, newest first (by their own date, then when
    they were added)."""
    if n < 1:
        return []
    rows = con.execute("SELECT id, dated, added FROM record_sources WHERE section = ? AND state = 'read'",
                       (SECTION,)).fetchall()
    rows.sort(key=lambda r: (merge.month_index(r["dated"] or "", "end") or NO_DATE, r["added"], r["id"]),
              reverse=True)
    out = []
    for r in rows:
        folder = store.files_dir(r["id"])
        files = [readers.SourceFile(rel, folder / rel) for rel in store.file_rels(con, r["id"])]
        if words := readers.text_of(f for f in files if f.path.is_file()).strip():
            out.append(words)
        if len(out) == n:
            break
    return out


def excerpt(text: str, words: int = EXCERPT_WORDS) -> str:
    """A letter's own prose (`NOT_PROSE` and headings left out), whole sentences up to `words` words; "" when it
    has none."""
    prose = " ".join(line.strip() for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("#") and not NOT_PROSE.match(line.strip()))
    out: list[str] = []
    for sentence in SENTENCE_END.split(prose):
        if out and len(" ".join([*out, sentence]).split()) > words:
            break
        out.append(sentence)
    said = " ".join(out)
    return said if len(said.split()) <= words else " ".join(said.split()[:words])


def voice_excerpts(con: sqlite3.Connection | None = None, n: int = EXCERPTS) -> list[str]:
    """Short excerpts (`excerpt`) of the person's `n` newest letters, newest first; [] with no letter read. Hal's
    own database unless `con`."""
    if con is None:
        with closing(db.connect()) as own:
            return voice_excerpts(own, n)
    return [e for e in (excerpt(s) for s in voice_samples(con, n)) if e]


readers.register(LetterReader())
