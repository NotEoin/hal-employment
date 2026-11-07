"""Notes from a person's own note-taking vault that say nothing of their own: empty stubs, and notes made
from a document.

A vault in a real dump held thousands of notes a tool wrote for it: concept stubs (a heading, one line saying
a tool made it, an empty section and a query block) and notes made from lecture slides, whose frontmatter
names the slide file they came from (2026-11-07). Each became a note of the record and they buried the rest.

- An empty note (`is_empty`): a text or Markdown file with fewer than `EMPTY_WORDS` words of its own once
  headings, lines of links, embeds, code and query blocks and placeholder lines are taken out. It is
  skipped: no note, no AI call, no facts.
- A note made from a document (`source_of`): its frontmatter names a slide deck, PDF or Word file under a key
  such as `source_file`. When that document is course material by its name or folder (`rank.rule_for` says
  slides or notes), or the note's own `role` or `type` says lecture, exam, handout, solution, exercise or
  tutorial, the note is course material by rules alone (`taught_from_source`): taught, with no AI call.
  When the deck itself is in the read too, nothing new joins them: the deck is covered by the note as by
  any of the person's typed lecture notes (`lectures.Covered`, same module code and title words), else it is
  read as course material as before.

Hal's own notes (frontmatter `hal`) also name a source; they are never taken for a note made from one.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from jobhunt.ingest import frontmatter, rank

EMPTY = "an empty note"
# One word beside a note's headings and links is a placeholder ("TODO", "Draft"), and a stub a vault tool
# writes has none once its tool's line is gone; two already make a line of the person's ("Completed 2025").
EMPTY_WORDS = 2
FRONT_CHARS = 8000           # frontmatter sits at the top; a note's first few thousand characters hold it
SOURCE_KEYS = ("source_file", "source_path", "source", "original", "original_file", "from_file")
SOURCE_SUFFIXES = {".pdf", ".ppt", ".pptx", ".odp", ".key", ".doc", ".docx", ".odt", ".rtf"}
MATERIAL_ROLES = re.compile(r"^(?:lectures?|exams?|handouts?|solutions?|exercises?|tutorials?)$", re.I)
MATERIAL_KINDS = ("slides", "notes")          # course material by the source document's name or folder
CODE_BLOCK = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.S | re.M)
LINK = re.compile(r"!?\[\[[^\]]*\]\]|!?\[[^\]]*\]\([^)]*\)")
PLACEHOLDER = re.compile(r"^\s*[_*]+\(.*\)[_*]+\s*$|\b(?:stub|placeholder|auto-?(?:created|generated)|"
                         r"automatically (?:created|generated))\b", re.I)
PLACEHOLDER_WORDS_MAX = 12   # a longer line naming a stub is a sentence of the person's, not a tool's line
WORD = re.compile(r"[^\W_]{2,}")      # letters or digits: "2025" says something, "a" and "-" do not


def own_words(text: str) -> int:
    """How many words of its own a note's text has, without headings, links, embeds, code and placeholders."""
    words = 0
    for line in CODE_BLOCK.sub("", frontmatter.parse(text)[1]).splitlines():
        line = line.strip()
        if line.startswith("#"):          # a heading, or a line of tags
            continue
        if PLACEHOLDER.search(line) and len(line.split()) <= PLACEHOLDER_WORDS_MAX:
            continue
        words += len(WORD.findall(LINK.sub("", line)))
    return words


def is_empty(rel: str, text: str) -> bool:
    """True for a text or Markdown file with fewer than `EMPTY_WORDS` words of its own (see the header)."""
    return PurePosixPath(rel).suffix.lower() in rank.TEXT_SUFFIXES and own_words(text) < EMPTY_WORDS


def front(path: Path) -> dict:
    """A Markdown file's frontmatter; {} for any other file or one that cannot be read."""
    if path.suffix.lower() not in (".md", ".markdown"):
        return {}
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            return frontmatter.parse(f.read(FRONT_CHARS))[0]
    except OSError:
        return {}


def source_of(fm: dict) -> str:
    """The slide deck, PDF or Word file a note's frontmatter says it was made from; "" for none."""
    if fm.get("hal"):
        return ""
    for key in SOURCE_KEYS:
        value = fm.get(key)
        value = value[0] if isinstance(value, list) and value else value
        name = str(value or "").strip().strip("\"'").removeprefix("[[").removesuffix("]]").split("|")[0]
        if PurePosixPath(name.replace("\\", "/")).suffix.lower() in SOURCE_SUFFIXES:
            return name.replace("\\", "/")
    return ""


def taught_from_source(fm: dict) -> bool:
    """True for a note made from course material: its source is slides or lecture notes by name or folder, or
    its own role or type says lecture, exam, handout, solution, exercise or tutorial."""
    source = source_of(fm)
    if not source:
        return False
    if any(MATERIAL_ROLES.match(str(fm.get(k) or "").strip()) for k in ("role", "type")):
        return True
    return rank.rule_for(source.lstrip("/")).doc_type in MATERIAL_KINDS
