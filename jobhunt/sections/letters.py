"""The letter reader: cover letters and personal statements, kept only as samples of how the person writes.

A letter says what the person wanted an employer to think, so nothing in it becomes a fact about them (sections
plan, 2026-11-09): the reader keeps its text, title and date, and gives no facts. `voice_samples` hands the
newest few letters' words to a writer that wants to match the person's voice; no writer reads them yet.

Design decisions, each with its reason:

- A sample is the text of the file Hal keeps (`sections.store.files_dir`), not of the note, so the record
  note's own heading and properties never read as the person's words.
- Only letters that were read count: one kept out as private, or waiting, is never a sample.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from jobhunt.profile import merge
from jobhunt.sections import readers, store

SECTION = "letter"
NO_DATE = -1               # a letter with no date of its own is older than every dated one


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


readers.register(LetterReader())
