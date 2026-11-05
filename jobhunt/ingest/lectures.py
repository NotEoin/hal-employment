"""Course material as clean lecture notes: the subject of a lecture, without the people, rooms and admin.

A deck of slides or a handout converts to its raw words: speaker notes, image descriptions, the lecturer's
email, a room number, the week's deadlines. The record wants what was taught, so:

- Course material the AI read (`roles.TAUGHT`) from a converted file (`REWRITE_SUFFIXES`: slides, PDFs,
  Word files, saved pages) is written again by the AI (`lecture_note` prompt) as study notes: headings per
  topic, the ideas, definitions, worked examples, formulas and code. Notes the person typed themselves
  (Markdown, text) are already notes and stay as they are.
- Every piece of course material loses email addresses and phone numbers (`scrub`), whoever wrote it, as a
  lecturer's email on a slide was filed as the person's own (2026-11-05).
- Slides or a handout of a lecture the person already has notes on (`Covered`) get no note of their own:
  the person's notes, which they typed (`is_typed`), are the better account, and the deck's path is listed
  under "Also at" in them. A lecture matches
  when both name the same module code (in the path or the title) and their titles share their words
  (`same_lecture`).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import PurePosixPath

from jobhunt.ingest import roles
from jobhunt.profile.merge import module_code

log = logging.getLogger(__name__)

PURPOSE = "lecture_note"
PROMPT_CHARS = 60_000        # as `understand.PROMPT_CHARS`: a long deck's words inside a 64K context
TIMEOUT_S = 300
NOTE_MIN_CHARS = 200         # a shorter reply is a refusal or a heading alone, not notes
REWRITE_SUFFIXES = {".pptx", ".ppt", ".odp", ".key", ".pdf", ".docx", ".doc", ".odt", ".rtf", ".html", ".htm"}
# Course material by its rank: "Lecture 3.pdf" ranks as notes by its name, "Rivers deck.pptx" as slides.
MATERIAL_TYPES = ("slides", "notes")
NOTE_TYPES = ("notes",)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A phone number as people write one: international (+44 ...) or national (0...), 10 to 13 digits in groups.
PHONE = re.compile(r"(?<![\w.+-])(?:\+\d{1,3}[\s-]?(?:\(0\)\s?)?|0)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\w|[.-]\d)")
TITLE_WORD = re.compile(r"[a-z][a-z]+")
# Words that name the kind of material or its place in a course, not its subject.
NOT_SUBJECT = {"lecture", "lectures", "lec", "slides", "slide", "notes", "note", "week", "part", "handout",
               "handouts", "topic", "session", "intro", "and", "the", "of", "to", "in", "for", "with", "on",
               "new", "copy", "final", "pdf", "pptx", "ppt"}
SHARE_MIN = 0.6              # of the smaller title's subject words, found in the other


def scrub(text: str) -> str:
    """`text` without email addresses and phone numbers."""
    return PHONE.sub("", EMAIL.sub("", text))


def subject_words(title: str) -> set[str]:
    """The words of a title that say what it is about."""
    return {w for w in TITLE_WORD.findall(title.lower()) if w not in NOT_SUBJECT}


def same_lecture(a: str, b: str) -> bool:
    """True when two titles name the same lecture: most of the shorter one's subject words are in the other."""
    wa, wb = subject_words(a), subject_words(b)
    if not wa or not wb:
        return False
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(small & big) / len(small) >= SHARE_MIN


def is_typed(rel: str) -> bool:
    """True for notes a person typed (Markdown, text), not a converted deck, PDF or Word file."""
    return PurePosixPath(rel).suffix.lower() not in REWRITE_SUFFIXES


def is_material(rel: str, doc_type: str) -> bool:
    """True for a converted deck or handout of a lecture, which a person's typed notes may cover."""
    return doc_type in MATERIAL_TYPES and not is_typed(rel)


def wants_note(rel: str, said) -> bool:
    """True when a document the AI read should be written again as a lecture note."""
    return roles.said_role(said) == roles.TAUGHT and PurePosixPath(rel).suffix.lower() in REWRITE_SUFFIXES


def _tidy(reply: str) -> str:
    text = reply.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", text, re.S)
    return scrub(fenced.group(1) if fenced else text).strip()


def write(rel: str, title: str, text: str) -> str:
    """The AI's lecture note for one piece of course material; "" when the AI cannot write one now."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    system = prompts.text(PURPOSE)
    user = f"File: {rel}\nTitle: {title}\n\nText:\n{text[:PROMPT_CHARS]}"
    try:
        reply = ai.chat(system, user, role="checker", timeout=TIMEOUT_S, purpose=PURPOSE, priority=2)
    except ai.LLMUnavailable as e:
        log.info("kept the words of %s as they are: %s", rel, e)
        return ""
    note = _tidy(reply)
    return note if len(note) >= NOTE_MIN_CHARS else ""


def _key(rel: str, title: str) -> str:
    return module_code(rel) or module_code(title)


class Covered:
    """The lecture notes already in the record, by module code, so slides they cover need no note.

    Built once per ingest from the items read before, and kept up to date as notes are written.
    """

    def __init__(self, con: sqlite3.Connection) -> None:
        self.notes: dict[str, list[tuple[str, int, str]]] = {}
        for r in con.execute(f"""SELECT id, rel, note FROM ingest_items WHERE state = 'done' AND note IS NOT NULL
                                 AND kind = 'file' AND doc_type IN ({','.join('?' * len(NOTE_TYPES))})
                                 AND role IN (?, ?)""", (*NOTE_TYPES, roles.OWN, roles.TAUGHT)):
            self.add(r["id"], r["rel"], PurePosixPath(r["rel"]).stem, r["note"])

    def add(self, item_id: int, rel: str, title: str, note: str) -> None:
        """Remembers a note just written, when it is typed notes on a module's lecture."""
        if is_typed(rel) and (code := _key(rel, title)):
            self.notes.setdefault(code, []).append((title, item_id, note))

    def find(self, rel: str, title: str) -> tuple[int, str] | None:
        """(item id, note path) of the person's notes on the lecture these slides are for, or None."""
        for known, item_id, note in self.notes.get(_key(rel, title), []):
            if same_lecture(known, title):
                return item_id, note
        return None
