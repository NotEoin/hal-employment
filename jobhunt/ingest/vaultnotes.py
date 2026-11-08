"""Notes from a person's own note-taking vault that say nothing of their own: empty stubs, and notes made
from a document.

A vault in a real dump held thousands of notes a tool wrote for it: concept stubs (a heading, one line saying
a tool made it, an empty section and a query block) and notes made from lecture slides, whose frontmatter
names the slide file they came from (2026-11-07). Each became a note of the record and they buried the rest.

- An empty note (`is_empty`): a text or Markdown file with fewer than `EMPTY_WORDS` words of its own once
  headings, lines of links, embeds, code and query blocks, placeholder lines and template lines are taken
  out, or a note whose frontmatter says `status: stub`. It is skipped: no note, no AI call, no facts.
- A template line (`Templates`) is a line found, once the note's own title is replaced by a mark and spacing
  and bold are evened out, in `TEMPLATE_NOTES` or more of one read's Markdown notes and at least
  `TEMPLATE_SHARE` of them. About 4,200 concept stubs of a real vault each held one tool's quoted line naming
  the note's own concept in bold, too long for a placeholder, so each was sent to the AI: most of a 4-hour
  read (2026-11-08). The counts are made once per read (`Templates.of`) and only decide emptiness: a note
  with words of its own keeps its template lines.
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

import math
import re
from collections import Counter
from collections.abc import Iterable
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
# A line the person typed is rarely word for word in ten of their notes, while a tool's line is in every note
# it made (4,200 of them in one vault, 2026-11-08). In a vault of tens of thousands of notes the share, one in
# two hundred, keeps a phrase the person reuses in a dozen notes from being taken for a tool's.
TEMPLATE_NOTES = 10
TEMPLATE_SHARE = 0.005
TEMPLATE_CHARS = 20_000      # a note's template lines sit at its top; a long note is its own words anyway
TITLE_MARK = "{title}"
MARKUP = re.compile(r"[*_`]+")
MARKDOWN = (".md", ".markdown")
STUB_STATUS = "stub"


def _lines(text: str) -> list[str]:
    """A note's lines that can hold words of its own: no frontmatter, code or query blocks, or headings."""
    return [line.strip() for line in CODE_BLOCK.sub("", frontmatter.parse(text)[1]).splitlines()
            if line.strip() and not line.strip().startswith("#")]     # a heading, or a line of tags


def template_key(line: str, title: str) -> str:
    """A line as template lines are compared: the note's own title as a mark, without bold, spacing evened out,
    in lower case; "" for a line with no words."""
    for name in sorted({title, re.sub(r"[-_]+", " ", title)}, key=len, reverse=True):
        if len(name.strip()) >= 2:
            line = re.sub(re.escape(name.strip()), TITLE_MARK, line, flags=re.I)
    line = " ".join(MARKUP.sub("", line).split()).lower()
    return line if WORD.search(line.replace(TITLE_MARK, "")) else ""


class Templates:
    """The lines found in many of one read's Markdown notes (see the module notes), counted once per read."""

    def __init__(self, counts: Counter | None = None, notes: int = 0) -> None:
        self.counts = counts or Counter()
        self.notes = notes
        self.at_least = max(TEMPLATE_NOTES, math.ceil(TEMPLATE_SHARE * notes))

    @classmethod
    def of(cls, files: Iterable[tuple[str, str]]) -> Templates:
        """The template lines of the Markdown notes among `files`, as (path, rel) pairs; unreadable ones are left
        out."""
        counts: Counter = Counter()
        notes = 0
        for path, rel in files:
            if PurePosixPath(rel).suffix.lower() not in MARKDOWN:
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read(TEMPLATE_CHARS)
            except OSError:
                continue
            notes += 1
            title = PurePosixPath(rel).stem
            counts.update({k for line in _lines(text) if (k := template_key(line, title))})
        return cls(counts, notes)

    def common(self, line: str, title: str) -> bool:
        """True when `line`, in the note titled `title`, is a template line."""
        key = template_key(line, title)
        return bool(key) and self.counts[key] >= self.at_least


def own_words(text: str, title: str = "", templates: Templates | None = None) -> int:
    """How many words of its own a note's text has, without headings, links, embeds, code, placeholders and
    the template lines of `templates` (in a note titled `title`)."""
    words = 0
    for line in _lines(text):
        if PLACEHOLDER.search(line) and len(line.split()) <= PLACEHOLDER_WORDS_MAX:
            continue
        if templates is not None and templates.common(line, title):
            continue
        words += len(WORD.findall(LINK.sub("", line)))
    return words


def is_empty(rel: str, text: str, templates: Templates | None = None, fm: dict | None = None) -> bool:
    """True for a text or Markdown file with fewer than `EMPTY_WORDS` words of its own, or whose frontmatter
    (`fm`, or the text's own) says `status: stub` (see the header)."""
    if PurePosixPath(rel).suffix.lower() not in rank.TEXT_SUFFIXES:
        return False
    fm = fm if fm is not None else frontmatter.parse(text)[0]
    if str(fm.get("status") or "").strip().lower() == STUB_STATUS:
        return True
    return own_words(text, PurePosixPath(rel).stem, templates) < EMPTY_WORDS


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
