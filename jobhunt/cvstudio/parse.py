"""Reads an uploaded or pasted CV into sections: the file's lines, Hal's rules, then the AI.

The order is fixed: the rules always run and their reading is what Hal keeps when there is no AI. The AI
(`cv_parse` prompt, role checker, priority 0) then maps the same lines to schema 2 with a confidence per
field. Design decisions, each with its reason:

- Nothing is taken on trust. A value the AI gives that is not in the file's text (whitespace and case
  normalised) is kept but marked "Check this", and so is any field it was unsure of. Two-digit years
  ("Sept 24") are always marked, by rules or AI, because only the person knows the century they meant.
- PDFs are read through `pdfread.chars` (positions and type sizes), Word files with `zipfile` and
  ElementTree over `word/document.xml` (paragraph styles, Word numbering, bold runs, tables flattened a row
  per line), so no extra library is needed on Windows. Each line keeps its paragraph index
  and a hash of its text, for an in-place Word editor later.
- A PDF printed by a browser marks little: list markers are shapes, not text, and long lines wrap with no
  sign. `pdf_lines` therefore finds bold from font names, list items from marker shapes or indents, and
  joins a row to the one above when its first word would not have fitted there (see the constants below).
- Markdown is read line by line with regular expressions (`md_lines`), not a Markdown library: a CV uses
  headings, lists and bold, and the text Hal keeps holds no markup (formatting inside a line is not kept
  yet).
- A PDF with no text layer is refused with words that say what to do instead; there is no OCR for CVs.
- Headings are the common ones (`HEADINGS`) plus every built-in CV type's own section titles, so a CV
  written to a type's outline is read back into the same sections.
- Ids are made the way schema 2 makes them (`<prefix>-` + sha1(kind + heading)[:8]), so reading the same
  file twice gives the same ids and later edits find their entries.
- Rows of the "Check what Hal read" report have a path that names one field
  (`contact/email`, `s:<section>/text`, `s:<section>/e:<entry>/<field>`, `.../b:<bullet>`), and
  `correct()` changes exactly that field.
"""
from __future__ import annotations

import bisect
import copy
import functools
import hashlib
import io
import itertools
import logging
import re
import statistics
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

from jobhunt.ai import prompts
from jobhunt.cvstudio import pdfread
from jobhunt.cvstudio import types as cvtypes
from jobhunt.cvstudio.model import KINDS, Bullet, CVDoc, Entry, Module, Section

log = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024    # a CV is a few pages; a bigger file is not one
MAX_PAGES = 6                   # a CV is a few pages; a longer file is not one
MAX_UNZIPPED = 50 * 1024 * 1024  # a Word file's text part above this is not a CV (and may be a zip bomb)
LOW_CONFIDENCE = 0.6            # an AI confidence below this marks the field "Check this"
LARGE_TYPE = 1.15               # a line this much larger than the body text may be a heading
HEADING_WORDS = 5               # headings are short
HEADLINE_WORDS = 12             # a line under the name longer than this is a profile, not a headline
PARAGRAPH_WORDS = 14            # a non-bullet line this long inside an entry is its paragraph, not a header
LINE_TOLERANCE = 0.45           # share of the type size two baselines may differ by on one line (as pdfread)
WORD_GAP = 0.25                 # a gap wider than this share of the type size parts two words (as pdfread)
COLUMN_GAP = 2.0                # a gap wider than this many type sizes is a tab (dates on the right)
# Joining wrapped rows and finding list items, in shares of the type size (checked on CVs printed by Chrome
# from HTML and on the made-up fixtures, 2026-11-18):
ALIGN_SLACK = 0.2               # two rows start in the same place when their pens are this close
SIZE_SLACK = 0.05               # the same type: a bold entry heading (9.6 pt) beside body text (9.2 pt) is not
WRAP_STEP = 1.6                 # a wrapped row sits at most one line below; 1.5 line spacing is the widest common
PITCH_SLACK = 0.1               # Chrome puts baselines on whole pixels (0.75 pt), so a paragraph's line steps vary
                                # by one pixel; the 2 pt between paragraphs is more than this over the usual step
MARKER_REACH = 2.5              # a list marker sits within this many type sizes left of its text
MARKER_DROP = 0.2               # ... its middle no lower than this below the baseline
MARKER_RISE = 0.8               # ... and no higher than this above it (about the height of a capital)
SPACE_WIDTH = 0.25              # a space is about a quarter of the type size in Arial, Helvetica and Calibri
WRAP_SLACK = 0.3                # ink stops short of the advance a wrapping browser measures, by under a third
INDENT_MIN = 0.5                # an indent under half the type size is ink overhang ("f", "T"), not a list
INDENT_MAX = 5.0                # a list indents about 1.5 type sizes (CSS 13 pt at 9.2 pt, Word 0.25 in at
                                # 11 pt); five allows deep ones while a centred line is usually further in

PICTURE_WORDS = "This PDF is a picture of a CV, so Hal cannot read it. Upload the Word file, or paste the text."
TOO_BIG_WORDS = ("This file is bigger than 10 MB, so Hal cannot read it. Save a smaller PDF or Word file, "
                 "or paste the text.")
TOO_LONG_WORDS = "This CV has {n} pages. Hal reads CVs of up to 6 pages. Upload a shorter one, or paste the text."
KIND_WORDS = ("Hal reads PDF, Word (.docx) and Markdown (.md) files. Save your CV as one of those, or paste "
              "the text.")
MARKDOWN_ENDINGS = (".md", ".markdown")
OLD_WORD_WORDS = "This is an older Word file (.doc). Save it as a .docx file in Word, or paste the text."
BAD_PDF_WORDS = ("Hal could not open this PDF. It may be damaged or locked with a password. Upload the Word "
                 "file, or paste the text.")
BAD_DOCX_WORDS = "Hal could not open this Word file. Save it again as a .docx file, or paste the text."
EMPTY_WORDS = "Paste the text of your CV first."
NOT_FOUND_WHY = "Hal could not find these words in your file."
UNSURE_WHY = "Hal was not sure about this."
TWO_DIGIT_WHY = "The year is written with two digits."
MISSING_WHY = "Hal did not find this in your file."

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
BULLET_GLYPHS = ("\u2022", "\u25cf", "\u25aa", "\u25e6", "\u2023", "\u2043", "\u00b7", "\uf0b7", "\u27a2", "\u25ba")
DASHES = "-\u2010\u2011\u2012\u2013\u2014"

SECTION_KINDS = ("summary", "education", "experience", "volunteering", "projects", "research", "skills",
                 "achievements", "publications", "certifications", "languages", "interests", "teaching",
                 "talks", "portfolio", "additional", "custom")
ENTRY_KINDS = ("education", "experience", "volunteering", "projects", "research", "teaching", "portfolio")
LIST_KINDS = ("certifications", "achievements", "publications", "talks")
ITEM_KINDS = ("skills", "languages")
ORG_KINDS = ("education", "experience", "volunteering", "research", "teaching")
DATED_KINDS = ("education", "experience", "volunteering")
PREFIX = {"education": "ed", "experience": "ex", "volunteering": "vo", "projects": "pr", "research": "re",
          "skills": "sk", "achievements": "ac", "publications": "pu", "certifications": "ce", "languages": "la",
          "teaching": "te", "talks": "ta", "portfolio": "po", "additional": "ad", "custom": "cu",
          "summary": "su", "interests": "in"}

# Exact headings (lower case, no colon) and the kind each one is.
HEADINGS = {
    "profile": "summary", "personal profile": "summary", "personal statement": "summary", "summary": "summary",
    "professional summary": "summary", "about me": "summary", "career summary": "summary", "objective": "summary",
    "education": "education", "education and qualifications": "education", "qualifications": "education",
    "academic background": "education", "academic qualifications": "education",
    "experience": "experience", "work experience": "experience", "employment": "experience",
    "employment history": "experience", "work history": "experience", "professional experience": "experience",
    "career history": "experience", "relevant experience": "experience",
    "volunteering": "volunteering", "volunteer experience": "volunteering", "voluntary work": "volunteering",
    "volunteering experience": "volunteering",
    "skills": "skills", "key skills": "skills", "technical skills": "skills", "core skills": "skills",
    "skills and abilities": "skills",
    "projects": "projects", "personal projects": "projects", "selected projects": "projects",
    "research": "research", "research experience": "research", "publications": "publications",
    "interests": "interests", "hobbies": "interests", "hobbies and interests": "interests",
    "interests and hobbies": "interests",
    "certifications": "certifications", "certificates": "certifications", "training": "certifications",
    "courses": "certifications", "licences and certifications": "certifications",
    "languages": "languages", "achievements": "achievements", "awards": "achievements",
    "awards and achievements": "achievements", "teaching": "teaching", "teaching experience": "teaching",
    "talks": "talks", "presentations": "talks", "portfolio": "portfolio", "references": "additional",
    "additional information": "additional", "additional": "additional", "other information": "additional",
}


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("&", "and")).lower()


@functools.cache
def headings() -> dict[str, str]:
    """`HEADINGS` plus every built-in CV type's own section titles, as heading words -> kind."""
    found = dict(HEADINGS)
    for type_id in cvtypes.builtin_ids():
        for kind, part in cvtypes.load(type_id).sections.items():
            title = part.get("title") if isinstance(part, dict) else None
            if kind in KINDS and title:
                found.setdefault(_key(title), kind)
    return found


# For headings Hal does not know word for word: the first keyword found decides the kind.
KEYWORDS = (("volunteer", "volunteering"), ("voluntary", "volunteering"), ("educat", "education"),
            ("qualification", "education"), ("employ", "experience"), ("experience", "experience"),
            ("work", "experience"), ("career", "experience"), ("skill", "skills"), ("project", "projects"),
            ("publication", "publications"), ("research", "research"), ("certific", "certifications"),
            ("training", "certifications"), ("language", "languages"), ("award", "achievements"),
            ("achievement", "achievements"), ("interest", "interests"), ("hobb", "interests"),
            ("profile", "summary"), ("summary", "summary"), ("statement", "summary"), ("teaching", "teaching"),
            ("talk", "talks"), ("portfolio", "portfolio"), ("reference", "additional"))

MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
POINT = rf"(?:{MONTH}\s+(?:\d{{4}}|'?\d{{2}})|\d{{1,2}}/\d{{4}}|(?:19|20)\d{{2}})"
OPEN_END = r"(?:Present|Current|Now|Today|Ongoing|date)"
RANGE = re.compile(rf"(?P<start>{POINT})\s*(?:[{DASHES}]|to|until)\s*(?P<end>{POINT}|{OPEN_END})", re.I)
# "Mar - May 2025": the year is written once, after the second month
SHORT_RANGE = re.compile(rf"\b(?P<start>{MONTH})\s*[{DASHES}]\s*(?P<end>{MONTH}\s+(?:19|20)\d{{2}})", re.I)
SINGLE = re.compile(rf"(?P<start>{MONTH}\s+(?:19|20)\d{{2}}|(?:19|20)\d{{2}})\s*$", re.I)
TWO_DIGIT = re.compile(rf"{MONTH}\s+'?\d{{2}}\b(?!\d)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+44\s?\(?0?\)?\s?|\b0)\d(?:[\s-]?\d){8,10}\b")
URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:[\w-]+\.)?(?:linkedin\.com|github\.com|gitlab\.com)/\S+", re.I)
GRADE = re.compile(r"(?:,\s*|\s+\()(?P<grade>First[- ]class(?: honours)?|First|2:1|2:2|Upper second|Lower second|"
                   r"Third|Distinction\*?|Merit|Pass|A\*?[A-E]*)\)?\s*$", re.I)
DEGREE = re.compile(r"\b(BA|BSc|MA|MSc|MEng|BEng|LLB|LLM|PhD|DPhil|MPhil|MRes|BEd|BMus|MBA|Bachelor|Master|"
                    r"Doctor|Foundation degree)\b")
DEGREE_SPLIT = re.compile(r"^(?P<degree>(?:BA|BSc|MA|MSc|MEng|BEng|LLB|LLM|PhD|DPhil|MPhil|MRes|BEd|BMus|MBA)"
                          r"(?:\s*\(Hons\))?)\s+(?:in\s+)?(?P<subject>.+)$")
MODULES = re.compile(r"^(?:Key |Selected |Relevant )?Modules\s*(?:include|studied)?\s*[:\-]\s*(?P<rest>.+)$", re.I)
MODULE_MARK = re.compile(r"^(?P<name>.+?)\s*\((?P<mark>[^()]+)\)\s*$")
NAME_LINE = re.compile(r"^[A-Z][\w'\u00c0-\u024f.-]*(?:\s+[A-Z][\w'\u00c0-\u024f.-]*){1,4}$")
ITEM_SPLIT = re.compile(r"\s*[,;\u00b7|]\s*(?![^()]*\))")
SEPARATORS = re.compile(r"\s*(?:\||\u2022|\u00b7|\t|\s{3,})\s*")


class CVReadError(ValueError):
    """The file cannot be read as a CV; the message says why and what to do instead."""


@dataclass
class Line:
    """One line of the file: its text and what the file said about it."""

    text: str
    size: float = 0.0
    bold: bool = False
    bullet: bool = False
    style: str = ""
    page: int = 1
    index: int = 0          # the paragraph's place in a Word file, or the line's place in the text
    hash: str = ""          # sha1 of the text, for finding the paragraph again
    left: float = 0.0       # a PDF line's left edge in points
    lead: str = ""          # the bold words the line starts with ("Security:"), or all of it when it is bold
    italic: bool = False


@dataclass
class Reading:
    """A CV read into schema 2, with the fields that need a check (path -> why)."""

    doc: CVDoc
    marks: dict[str, str] = field(default_factory=dict)
    how: str = "rules"      # rules | ai


# ------------------------------------------------------------------------------------------- the file

def kind_of(name: str, data: bytes) -> str:
    """"pdf", "docx", "md" or "txt", from the bytes first and the name second; CVReadError for anything else."""
    lower = name.lower()
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK") and lower.endswith(".docx"):
        return "docx"
    if data.startswith(b"\xd0\xcf\x11\xe0") or lower.endswith(".doc"):
        raise CVReadError(OLD_WORD_WORDS)
    if lower.endswith(MARKDOWN_ENDINGS):
        return "md"
    if lower.endswith(".txt"):
        return "txt"
    if data.startswith(b"PK"):
        return "docx"
    raise CVReadError(KIND_WORDS)


def check(data: bytes, name: str) -> tuple[str, int | None]:
    """The file's kind and page count, or CVReadError saying why Hal cannot read it."""
    if len(data) > MAX_BYTES:
        raise CVReadError(TOO_BIG_WORDS)
    kind = kind_of(name, data)
    if kind in ("txt", "md"):
        if not data.decode("utf-8", "replace").strip():
            raise CVReadError(EMPTY_WORDS)
        return kind, None
    if kind == "docx":
        lines = docx_lines(data)
        if not lines:
            raise CVReadError(BAD_DOCX_WORDS)
        pages = docx_pages(data)
        if pages and pages > MAX_PAGES:
            raise CVReadError(TOO_LONG_WORDS.format(n=pages))
        return kind, pages
    pages = pdfread.pages(data)
    if pages == 0:
        raise CVReadError(BAD_PDF_WORDS)
    if pages > MAX_PAGES:
        raise CVReadError(TOO_LONG_WORDS.format(n=pages))
    if not pdfread.text(data).strip():
        raise CVReadError(PICTURE_WORDS)
    return kind, pages


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _bulleted(text: str) -> tuple[str, bool]:
    """The text without a leading bullet glyph or list hyphen, and whether it had one."""
    stripped = text.lstrip()
    for glyph in BULLET_GLYPHS:
        if stripped.startswith(glyph):
            return stripped[len(glyph):].strip(), True
    if len(stripped) > 2 and stripped[0] in "-*\u2013" and stripped[1] == " ":
        return stripped[2:].strip(), True
    return text.strip(), False


@dataclass
class _Row:
    """One printed row of a PDF page before wrapped rows are joined."""

    text: str
    page: int
    y: float
    size: float
    left: float             # pen position of the first character after any bullet glyph
    right: float            # right edge of the last character's ink
    first_word: float       # width of the first word, to tell whether it would have fitted on the row above
    bold: bool
    lead: str
    italic: bool
    bullet: bool
    tab: bool


def _spacer(chars: list[pdfread.Char]):
    """A test of whether a space separates two characters on one row.

    PDFium's text order holds the PDF's own spaces and the ones PDFium works out from the gaps, so when the
    file has any, a space is there exactly when one lies between the two characters in that order. Sorting
    by position instead misplaced them: a space drawn 0.01 pt right of the "2" in "27" read as "2 7" (a CV
    printed by Chrome, 2026-11-18). A PDF with no spaces at all falls back to the gap rule.
    """
    spaces: dict[int, list[int]] = {}
    for c in chars:
        if c.char.isspace():
            spaces.setdefault(c.page, []).append(c.index)
    if not spaces:
        return lambda a, b: b.left - a.right > WORD_GAP * max(b.points, 1.0)

    def spaced(a: pdfread.Char, b: pdfread.Char) -> bool:
        if b.index <= a.index:      # drawn out of order: only the gap can tell
            return b.left - a.right > WORD_GAP * max(b.points, 1.0)
        found = spaces.get(a.page, [])
        k = bisect.bisect_right(found, a.index)
        return k < len(found) and found[k] < b.index
    return spaced


def _row(row: list[pdfread.Char], spaced) -> _Row | None:
    """A row of characters as text, with its geometry and how much of it is bold."""
    glyphs = sorted((c for c in row if not c.char.isspace()), key=lambda c: c.x)
    if not glyphs:
        return None
    text, lead, tab, starts = "", None, False, []
    for i, c in enumerate(glyphs):
        if i:
            prev = glyphs[i - 1]
            if c.left - prev.right > COLUMN_GAP * max(c.points, 1.0):
                text, tab = text + "\t", True
            elif spaced(prev, c):
                text += " "
        if lead is None and not c.bold and i:
            lead = text
        starts.append(len(text))
        text += c.char
    body, bullet = _bulleted(text)
    skip = 1 if bullet and len(glyphs) > 1 and glyphs[0].char not in body[:1] else 0
    rest = glyphs[skip:]
    first_word = rest
    for i in range(1, len(rest)):
        if starts[skip + i] > starts[skip + i - 1] + 1:     # a space or tab came before this character
            first_word = rest[:i]
            break
    bold = all(c.bold for c in rest)
    lead_text = "" if bold or not rest[0].bold else _bulleted((lead or "").rstrip())[0]
    return _Row(body, row[0].page, row[0].y, max(c.points for c in glyphs), rest[0].x, max(c.right for c in glyphs),
                first_word[-1].right - first_word[0].x, bold, body if bold else lead_text,
                all(c.italic for c in rest), bullet, tab)


def _pitches(rows: list[_Row]) -> dict[float, float]:
    """The usual distance between baselines for each type size: the step most rows of that size take."""
    steps: dict[float, Counter] = {}
    for a, b in itertools.pairwise(rows):
        if a.page == b.page and abs(a.size - b.size) <= SIZE_SLACK * a.size and a.y > b.y:
            steps.setdefault(round(a.size, 1), Counter())[round(a.y - b.y, 2)] += 1
    return {size: counts.most_common(1)[0][0] for size, counts in steps.items()}


def _continues(a: _Row, b: _Row, right_edge: float, pitch: float | None, marked: bool) -> bool:
    """True when `b` is the wrapped rest of `a`: same column and type, one line below, and `a` was full.

    With list markers drawn as shapes (`marked`), an unmarked row under a list item is its continuation
    whatever its length; elsewhere only a full row above says the text ran on.
    """
    if a.page != b.page or a.tab or b.tab or b.bullet:
        return False
    size = max(a.size, b.size)
    if abs(a.left - b.left) > ALIGN_SLACK * size or abs(a.size - b.size) > SIZE_SLACK * size:
        return False
    step = a.y - b.y
    if not 0 < step <= WRAP_STEP * size or (pitch is not None and step > pitch + PITCH_SLACK * size):
        return False
    if marked and a.bullet:
        return True
    return a.right + SPACE_WIDTH * size + b.first_word >= right_edge - WRAP_SLACK * size


def _has_marker(r: _Row, shapes: list[pdfread.Shape]) -> bool:
    """True when a list marker shape sits just left of the row, level with its letters."""
    return any(s.page == r.page and s.right <= r.left and r.left - s.right <= MARKER_REACH * r.size
               and r.y - MARKER_DROP * r.size <= (s.bottom + s.top) / 2 <= r.y + MARKER_RISE * r.size
               for s in shapes)


def _join(a: _Row, b: _Row) -> _Row:
    """`a` with its wrapped continuation `b` added."""
    glue = "" if len(a.text) > 1 and a.text[-1] in "-/" and a.text[-2].isalnum() and b.text[:1].isalnum() else " "
    lead = (a.text + glue + b.lead).rstrip() if a.bold else a.lead
    return _Row(a.text + glue + b.text, a.page, b.y, a.size, a.left, b.right, a.first_word, a.bold and b.bold,
                a.text + glue + b.text if a.bold and b.bold else lead, a.italic and b.italic, a.bullet, False)


def pdf_lines(data: bytes) -> list[Line]:
    """The PDF's lines in reading order: wrapped rows joined, with type size, left edge, bold and list items.

    A browser draws list markers as shapes, not text, so when no line has a bullet glyph a line indented a
    little from the page's text is a list item. A row is joined to the one above when it starts in the same
    place in the same type and its first word would not have fitted at the end of the row above, which is
    how a browser or word processor wraps text.
    """
    chars = pdfread.chars(data)
    widths = pdfread.page_widths(data)
    spaced = _spacer(chars)
    grouped: list[list[pdfread.Char]] = []
    for c in sorted(chars, key=lambda c: (c.page, -c.y, c.x)):
        if grouped and grouped[-1][0].page == c.page and \
                abs(grouped[-1][0].y - c.y) <= LINE_TOLERANCE * max(c.points, 1.0):
            grouped[-1].append(c)
        else:
            grouped.append([c])
    rows = [r for r in (_row(g, spaced) for g in grouped) if r is not None and r.text]
    edges: dict[int, float] = {}
    for r in rows:
        width = widths[r.page - 1] if r.page <= len(widths) else float("inf")
        if r.right <= width:
            edges[r.page] = max(edges.get(r.page, r.right), r.right)
    marked = False
    if not any(r.bullet for r in rows):
        shapes = pdfread.markers(data)
        for r in rows:
            r.bullet = _has_marker(r, shapes)
        marked = any(r.bullet for r in rows)
    pitches = _pitches(rows)
    joined: list[_Row] = []
    for r in rows:
        if joined and _continues(joined[-1], r, edges.get(r.page, r.right), pitches.get(round(r.size, 1)), marked):
            joined[-1] = _join(joined[-1], r)
        else:
            joined.append(r)
    if not any(r.bullet for r in joined):
        margins: dict[int, float] = {}
        for r in joined:
            margins[r.page] = min(margins.get(r.page, r.left), r.left)
        for r in joined:
            r.bullet = INDENT_MIN * r.size <= r.left - margins[r.page] <= INDENT_MAX * r.size
    return [Line(r.text, size=r.size, bold=r.bold, bullet=r.bullet, page=r.page, index=n, hash=_hash(r.text),
                 left=r.left, lead=r.lead, italic=r.italic) for n, r in enumerate(joined)]


def _docx_styles(z: zipfile.ZipFile) -> dict[str, str]:
    try:
        root = ElementTree.fromstring(z.read("word/styles.xml"))
    except (KeyError, ElementTree.ParseError):
        return {}
    out = {}
    for style in root.iter(f"{W}style"):
        name = style.find(f"{W}name")
        out[style.get(f"{W}styleId", "")] = (name.get(f"{W}val", "") if name is not None else "").lower()
    return out


def _para_text(p: ElementTree.Element) -> tuple[str, bool]:
    """A paragraph's text (tabs kept, breaks as spaces) and whether every run with text is bold."""
    parts, bold_runs, runs = [], 0, 0
    for r in p.iter(f"{W}r"):
        texts = []
        for child in r:
            if child.tag == f"{W}t":
                texts.append(child.text or "")
            elif child.tag == f"{W}tab":
                texts.append("\t")
            elif child.tag in (f"{W}br", f"{W}cr"):
                texts.append(" ")
        run_text = "".join(texts)
        parts.append(run_text)
        if run_text.strip():
            runs += 1
            b = r.find(f"{W}rPr/{W}b")
            if b is not None and b.get(f"{W}val", "true") not in ("0", "false"):
                bold_runs += 1
    return "".join(parts), runs > 0 and bold_runs == runs


def _docx_zip(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        if z.getinfo("word/document.xml").file_size > MAX_UNZIPPED:
            raise CVReadError(BAD_DOCX_WORDS)
        return z
    except (zipfile.BadZipFile, KeyError) as e:
        raise CVReadError(BAD_DOCX_WORDS) from e


def docx_lines(data: bytes) -> list[Line]:
    """The Word file's paragraphs in order: style names, Word numbering as bullets, table rows as one line."""
    z = _docx_zip(data)
    styles = _docx_styles(z)
    try:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    except ElementTree.ParseError as e:
        raise CVReadError(BAD_DOCX_WORDS) from e
    body = root.find(f"{W}body")
    out: list[Line] = []
    if body is None:
        return out

    def paragraph(p: ElementTree.Element, index: int) -> None:
        text, bold = _para_text(p)
        ppr = p.find(f"{W}pPr")
        style_id = ""
        numbered = False
        if ppr is not None:
            ps = ppr.find(f"{W}pStyle")
            style_id = ps.get(f"{W}val", "") if ps is not None else ""
            numbered = ppr.find(f"{W}numPr") is not None
        style = styles.get(style_id, style_id.lower())
        clean, glyph = _bulleted(text)
        if clean:
            out.append(Line(clean, bold=bold, bullet=numbered or glyph or "list" in style, style=style,
                            index=index, hash=_hash(clean)))

    def walk(parent: ElementTree.Element) -> None:
        for n, child in enumerate(parent):
            if child.tag == f"{W}p":
                paragraph(child, n)
            elif child.tag == f"{W}tbl":
                for tr in child.iter(f"{W}tr"):
                    cells = []
                    for tc in tr.findall(f"{W}tc"):
                        cell = " ".join(_para_text(p)[0].strip() for p in tc.iter(f"{W}p"))
                        if cell.strip():
                            cells.append(cell.strip())
                    if cells:
                        text = "\t".join(cells)
                        out.append(Line(text, style="table", index=n, hash=_hash(text)))
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    return out


def docx_pages(data: bytes) -> int | None:
    """The page count Word saved in the file's properties, when it saved one."""
    z = _docx_zip(data)
    try:
        root = ElementTree.fromstring(z.read("docProps/app.xml"))
    except (KeyError, ElementTree.ParseError):
        return None
    for el in root.iter():
        if el.tag.endswith("}Pages") and (el.text or "").strip().isdigit():
            return int(el.text.strip())
    return None


def text_lines(text: str) -> list[Line]:
    """Pasted text as lines; three or more spaces in a row count as a tab, as they line up dates."""
    out = []
    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        raw = re.sub(r" {3,}", "\t", raw.rstrip())
        body, bullet = _bulleted(raw)
        if body:
            out.append(Line(body, bullet=bullet, index=n, hash=_hash(body)))
    return out


MD_HEADING = re.compile(r"^ {0,3}(?P<marks>#{1,6})\s+(?P<text>.*?)(?:\s+#+)?\s*$")
MD_ITEM = re.compile(r"^(?P<indent>\s*)(?:[-*+]|\d{1,3}[.)])\s+(?P<text>.*)$")
MD_RULE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
MD_SETEXT = re.compile(r"^ {0,3}(?P<marks>=+|-+)\s*$")
MD_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
MD_FENCE = re.compile(r"^ {0,3}(```|~~~)")
MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
MD_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\([^)]*\)")
MD_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)\s]*)(?:\s+\"[^\"]*\")?\)")
MD_AUTOLINK = re.compile(r"<(?P<url>(?:https?://|mailto:)[^>\s]+)>")
MD_TAG = re.compile(r"</?[A-Za-z][^>]*>")
MD_CODE = re.compile(r"`+([^`]*)`+")
MD_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
MD_EMPHASIS = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
MD_LEAD = re.compile(r"^(\*\*|__)(?=\S)(?P<lead>.+?)(?<=\S)\1")
ESCAPED = "\ue000"      # a private-use character standing in for each escaped one while markers are stripped
H1, H2 = 1, 2           # "#" is the name and "##" a section; "###" and deeper are entry headings


def _md_plain(text: str) -> str:
    """Markdown inline text as plain words: links as their text, no emphasis markers, escapes undone."""
    kept: list[str] = []

    def hold(m: re.Match) -> str:
        kept.append(m.group(1))
        return ESCAPED
    out = MD_ESCAPE.sub(hold, text)
    out = MD_IMAGE.sub(lambda m: m.group("alt"), out)
    out = MD_LINK.sub(lambda m: m.group("text").strip() or m.group("url"), out)
    out = MD_AUTOLINK.sub(lambda m: m.group("url").removeprefix("mailto:"), out)
    out = MD_TAG.sub(" ", out)
    out = MD_CODE.sub(lambda m: m.group(1), out)
    for _ in range(2):      # bold inside italic and the other way round
        out = MD_STRONG.sub(lambda m: m.group(2), out)
        out = MD_EMPHASIS.sub(lambda m: m.group(1) or m.group(2), out)
    for char in kept:
        out = out.replace(ESCAPED, char, 1)
    return re.sub(r"[ \t]+", " ", out).strip()


def _md_line(raw: str, index: int, *, bullet: bool = False, style: str = "") -> Line | None:
    """One Markdown block as a line, with its bold lead-in kept apart and its markers stripped."""
    text = _md_plain(raw)
    if not text:
        return None
    m = MD_LEAD.match(raw.strip())
    lead = _md_plain(m.group("lead")) if m else ""
    bold = bool(m) and m.end() == len(raw.strip())
    if style in ("title", "entry"):
        bold, lead = True, text
    if "\t" not in text and raw.lstrip().startswith("|"):
        cells = [_md_plain(c) for c in raw.strip().strip("|").split("|")]
        text = "\t".join(c for c in cells if c)
    return Line(text, bold=bold, bullet=bullet, style=style, index=index, hash=_hash(text),
                lead=text if bold else lead)


def md_lines(text: str) -> list[Line]:
    """A Markdown CV as lines: "#" the name, "##" sections, "###" entry headings, list items as bullets.

    Wrapped lines of a paragraph or list item are joined, as Markdown shows them. Emphasis and link markers
    are taken out of the text, keeping a bold lead-in ("**Security:** ...") in `Line.lead`.
    """
    out: list[Line] = []
    block: list[str] = []
    start, item, fenced = 0, False, False

    def flush() -> None:
        nonlocal block, item
        if block:
            line = _md_line(" ".join(block), start, bullet=item)
            if line is not None:
                out.append(line)
        block, item = [], False

    rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if rows and rows[0].strip() == "---":      # front matter, as static site generators write it
        end = next((n for n in range(1, len(rows)) if rows[n].strip() in ("---", "...")), None)
        if end is not None:
            rows = [""] * (end + 1) + rows[end + 1:]
    for n, raw in enumerate(rows):
        if MD_FENCE.match(raw):
            flush()
            fenced = not fenced
            continue
        if fenced:
            line = _md_line(raw, n)
            if line is not None:
                out.append(line)
            continue
        if not raw.strip():
            flush()
            continue
        setext = MD_SETEXT.match(raw)
        if setext and block and not item:
            level = H1 if setext.group("marks")[0] == "=" else H2
            heading, block = " ".join(block), []
            line = _md_line(heading, start, style="title" if level == H1 else "heading 2")
            if line is not None:
                out.append(line)
            continue
        if MD_RULE.match(raw) or (MD_TABLE_RULE.match(raw) and "|" in raw):
            flush()
            continue
        heading = MD_HEADING.match(raw)
        if heading:
            flush()
            level = len(heading.group("marks"))
            style = "title" if level == H1 else "heading 2" if level == H2 else "entry"
            line = _md_line(heading.group("text"), n, style=style)
            if line is not None:
                out.append(line)
            continue
        listed = MD_ITEM.match(raw)
        if listed or raw.lstrip().startswith("|"):
            flush()
            block, start, item = [listed.group("text") if listed else raw], n, bool(listed)
            if not listed:
                flush()
            continue
        hard_break = raw.endswith("  ") or raw.endswith("\\")
        if not block:
            start = n
        block.append(raw.strip().removesuffix("\\").lstrip(">").strip())
        if hard_break:
            flush()
    flush()
    return out


def lines_of(data: bytes, kind: str) -> list[Line]:
    """The lines of a file of `kind` ("pdf", "docx", "md" or "txt")."""
    if kind == "pdf":
        return pdf_lines(data)
    if kind == "docx":
        return docx_lines(data)
    if kind == "md":
        return md_lines(data.decode("utf-8", "replace"))
    return text_lines(data.decode("utf-8", "replace"))


# -------------------------------------------------------------------------------------------- the rules

def _norm(text: str) -> str:
    out = text
    for glyph in BULLET_GLYPHS:
        out = out.replace(glyph, " ")
    for dash in DASHES[1:]:
        out = out.replace(dash, "-")
    return re.sub(r"\s+", " ", out).strip().casefold()


def source_text(lines: list[Line]) -> str:
    """Every line's text in one normalised string, for checking that a value came from the file."""
    return _norm(" ".join(line.text for line in lines))


def _heading(line: Line, body_size: float | None) -> tuple[str | None, bool]:
    """(section kind or None, whether only a short bold line with a heading word made it one)."""
    if line.bullet or line.style == "entry":
        return None, False
    text = line.text.strip().rstrip(":").strip()
    words = text.split()
    if not words or len(words) > HEADING_WORDS or "\t" in text or any(ch.isdigit() for ch in text):
        return None, False
    key = _key(text)
    known = headings()
    if key in known:
        return known[key], False
    letters = [ch for ch in text if ch.isalpha()]
    caps = bool(letters) and all(ch.isupper() for ch in letters) and len(letters) > 2
    styled = line.style.startswith("heading")
    large = bool(body_size) and line.size >= body_size * LARGE_TYPE
    if caps or styled or large or (line.bold and len(words) <= 3):
        for word, kind in KEYWORDS:
            if word in key:
                return kind, not (caps or styled or large)
        return ("custom" if (caps or styled) else None), False
    return None, False


def heading_kind(line: Line, body_size: float | None) -> str | None:
    """The section kind when `line` is a heading, else None."""
    return _heading(line, body_size)[0]


def _section_kinds(lines: list[Line], body_size: float | None) -> list[str | None]:
    """Each line's section kind when it is a section heading, else None.

    A short bold line with a heading word ("Other projects") is a heading only when it is set as large as
    the file's other headings: in a CV whose section titles are larger, it is an entry's heading.
    """
    found = [_heading(ln, body_size) for ln in lines]
    strong = [ln.size for ln, (kind, weak) in zip(lines, found, strict=True) if kind and not weak and ln.size > 0]
    floor = statistics.median(strong) * (1 - SIZE_SLACK) if strong else 0.0
    return [kind if kind and (not weak or ln.size >= floor) else None
            for ln, (kind, weak) in zip(lines, found, strict=True)]


def _title(text: str) -> str:
    text = text.strip().rstrip(":").strip()
    letters = [ch for ch in text if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        return text[:1] + text[1:].lower()
    return text


def _items(text: str) -> list[str]:
    return [i.strip().rstrip(".").strip() for i in ITEM_SPLIT.split(text) if i.strip().rstrip(".").strip()]


def _dates(text: str) -> tuple[str, str, str]:
    """(text without its dates, start, end) for the date range or single date in `text`."""
    m = RANGE.search(text)
    if m:
        rest = (text[:m.start()] + text[m.end():]).strip()
        return rest, m.group("start").strip(), m.group("end").strip()
    m = SHORT_RANGE.search(text)
    if m:
        rest = (text[:m.start()] + text[m.end():]).strip()
        return rest, m.group("start").strip(), m.group("end").strip()
    m = SINGLE.search(text.replace("\t", " "))
    if m and (m.start() > 0 or "\t" in text):
        flat = text.replace("\t", " ")
        return flat[:m.start()].strip(), m.group("start").strip(), ""
    return text, "", ""


def _clean(part: str) -> str:
    return part.strip().strip(",|").strip().rstrip(f"{DASHES}").strip()


def _entry_id(kind: str, heading: str, used: set[str]) -> str:
    base = f"{PREFIX.get(kind, kind[:2])}-{hashlib.sha1((kind + heading).encode('utf-8')).hexdigest()[:8]}"
    out, n = base, 2
    while out in used:
        out, n = f"{base}-{n}", n + 1
    used.add(out)
    return out


def _bullet_id(entry_id: str, text: str, used: set[str]) -> str:
    base = f"b-{hashlib.sha1((entry_id + text).encode('utf-8')).hexdigest()[:8]}"
    out, n = base, 2
    while out in used:
        out, n = f"{base}-{n}", n + 1
    used.add(out)
    return out


@dataclass
class _Draft:
    header: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)


def _modules(rest: str) -> list[Module]:
    out = []
    for item in _items(rest):
        m = MODULE_MARK.match(item)
        out.append(Module(m.group("name").strip(), m.group("mark").strip()) if m else Module(item))
    return out


def _compact(line: Line) -> _Draft | None:
    """A whole entry on one line ("**GCSEs**, A School, 2019: ten subjects"), or None.

    The bold words are the heading. A lead-in ending in a colon ("Also:") labels a line instead.
    """
    text = line.text.strip()
    lead = line.lead.strip()
    if line.bullet or line.bold or not lead or lead.endswith(":") or not text.startswith(lead):
        return None
    rest = text[len(lead):]
    if not rest.lstrip().startswith(","):
        return None
    head, _, tail = rest.lstrip(" ,").partition(": ")
    return _Draft(header=[lead, head.strip()] if head.strip() else [lead], text=[tail.strip()] if tail.strip() else [])


def _starts_entry(line: Line) -> bool:
    """True when the file marks `line` as an entry's heading: a bold line, or a Markdown "###"."""
    return not line.bullet and (line.style == "entry" or line.bold or _compact(line) is not None)


def _marked_drafts(lines: list[Line]) -> list[_Draft]:
    """Entries of a section whose file marks each heading: every other line stays with the entry above.

    The line just under a heading is its details line (employer, place, dates) when it is short, holds a
    date range or is split with "|"; any other line is the entry's paragraph, so the wrapped end of a
    sentence or a "Label: text" line is never taken for a new entry.
    """
    drafts: list[_Draft] = []
    for line in lines:
        text = line.text.strip()
        m = MODULES.match(text)
        if m and drafts:
            drafts[-1].modules += _modules(m.group("rest"))
            continue
        compact = _compact(line)
        if compact is not None:
            drafts.append(compact)
            continue
        if _starts_entry(line) or not drafts:
            drafts.append(_Draft(header=[text]))
            continue
        cur = drafts[-1]
        if line.bullet:
            cur.bullets.append(text)
        elif (not cur.bullets and not cur.text and len(cur.header) == 1
              and (" | " in text or RANGE.search(text) or len(text.split()) < PARAGRAPH_WORDS)):
            cur.header.append(text)
        else:
            cur.text.append(text)
    return drafts


def _drafts(lines: list[Line]) -> list[_Draft]:
    if any(_starts_entry(ln) for ln in lines):
        return _marked_drafts(lines)
    drafts: list[_Draft] = []
    for line in lines:
        text = line.text.strip()
        m = MODULES.match(text)
        if m and drafts:
            drafts[-1].modules += _modules(m.group("rest"))
            continue
        cur = drafts[-1] if drafts else None
        if line.bullet:
            if cur is None:
                cur = _Draft()
                drafts.append(cur)
            cur.bullets.append(text)
            continue
        if cur is not None and not cur.bullets and not cur.text and cur.header:
            if len(text.split()) >= PARAGRAPH_WORDS:
                cur.text.append(text)
                continue
            dated = any(RANGE.search(h) for h in cur.header)
            if not (dated and RANGE.search(text)) and len(cur.header) < 3:
                cur.header.append(text)
                continue
        if cur is not None and cur.text and len(text.split()) >= PARAGRAPH_WORDS:
            cur.text.append(text)
            continue
        drafts.append(_Draft(header=[text]))
    return drafts


def _entry(kind: str, draft: _Draft, used: set[str], bullets_used: set[str]) -> tuple[Entry, dict[str, str]]:
    """One entry from its header lines and bullets, with the fields the rules were unsure of."""
    unsure: dict[str, str] = {}
    first = draft.header[0] if draft.header else ""
    # a details line split with "|" ("University | 2023 - 2026 | Accredited") gives each part its own field
    rest_lines = [part for h in draft.header[1:] for part in h.split(" | ")]
    first, start, end = _dates(first)
    if not (start or end):
        for i, h in enumerate(rest_lines):
            stripped, s, e = _dates(h)
            if s or e:
                start, end = s, e
                rest_lines[i] = stripped
                break
    first = first.replace("\t", " | ")
    grade = ""
    if kind == "education":
        m = GRADE.search(first)
        if m:
            grade, first = m.group("grade"), first[:m.start()]
    if " | " in first:
        parts = [_clean(p) for p in first.split(" | ")]
    elif " at " in first and kind in ORG_KINDS:
        parts = [_clean(p) for p in first.split(" at ", 1)]
    elif kind == "projects" and rest_lines:
        parts = [_clean(first)]     # the details have their own line, so a comma is part of the project's name
    else:
        parts = [_clean(p) for p in first.split(", ")]
        if len(parts) > 3:
            parts = [parts[0], ", ".join(parts[1:-1]), parts[-1]]
            unsure["org"] = UNSURE_WHY
    parts = [p for p in parts if p]
    heading = parts[0] if parts else ""
    org = parts[1] if len(parts) > 1 else ""
    location = parts[2] if len(parts) > 2 else ""
    meta = ""
    if kind == "projects" and org:
        meta, org = ", ".join(parts[1:]), ""
        location = ""
    for extra in (_clean(h) for h in rest_lines):
        if not extra:
            continue
        if kind == "education" and not grade:
            m = re.match(r"^Grade\s*:\s*(.+)$", extra, re.I)
            if m:
                grade = m.group(1).strip()
                continue
        if kind in ORG_KINDS and not org:
            org = extra
        else:
            meta = f"{meta} | {extra}" if meta else extra
    eid = _entry_id(kind, heading, used)
    entry = Entry(id=eid, heading=heading, org=org, location=location, start=start, end=end, grade=grade,
                  meta=meta, modules=list(draft.modules))
    if draft.text and not draft.bullets:
        entry.style, entry.text = "paragraph", " ".join(draft.text)
    else:
        entry.text = " ".join(draft.text)
        entry.bullets = [Bullet(_bullet_id(eid, b, bullets_used), b) for b in draft.bullets]
    if kind in ORG_KINDS and not org:
        unsure["org"] = MISSING_WHY
    if kind in DATED_KINDS and not (start or end):
        unsure["dates"] = MISSING_WHY
    return entry, unsure


def _list_entries(kind: str, lines: list[Line], used: set[str]) -> list[Entry]:
    out = []
    for line in lines:
        text, start, end = _dates(line.text.strip())
        text = _clean(text.replace("\t", " "))
        if text:
            out.append(Entry(id=_entry_id(kind, text, used), heading=text, start=start, end=end))
    return out


def _item_entries(kind: str, lines: list[Line], used: set[str]) -> list[Entry]:
    out: list[Entry] = []
    loose: list[str] = []
    for line in lines:
        text = line.text.strip()
        m = re.match(r"^(?P<group>[^:\t]{1,40})(?::\s*|\t)(?P<rest>.+)$", text)
        if m:
            group = m.group("group").strip()
            out.append(Entry(id=_entry_id(kind, group, used), heading=group, items=_items(m.group("rest"))))
        else:
            loose += _items(text)
    if loose:
        out.append(Entry(id=_entry_id(kind, "", used), items=loose))
    return out


def _section(kind: str, title: str, sid: str, lines: list[Line], marks: dict[str, str],
             used: set[str]) -> Section:
    sec = Section(id=sid, kind=kind, title=title)
    bullets_used: set[str] = set()
    if kind in ENTRY_KINDS:
        for draft in _drafts(lines):
            entry, unsure = _entry(kind, draft, used, bullets_used)
            sec.entries.append(entry)
            for name, why in unsure.items():
                marks[f"s:{sid}/e:{entry.id}/{name}"] = why
    elif kind in LIST_KINDS:
        sec.entries = _list_entries(kind, lines, used)
    elif kind in ITEM_KINDS:
        sec.entries = _item_entries(kind, lines, used)
    elif kind == "additional" and all(":" in ln.text for ln in lines) and lines:
        for line in lines:
            label, text = line.text.split(":", 1)
            sec.entries.append(Entry(id=_entry_id(kind, label.strip(), used), heading=label.strip(),
                                     style="paragraph", text=text.strip()))
    elif kind == "custom" and any(ln.bullet for ln in lines):
        sec.entries = [entry for entry, _ in (_entry(kind, d, used, bullets_used) for d in _drafts(lines))]
    else:
        sec.text = " ".join(ln.text.strip() for ln in lines)
    return sec


def _contact(top: list[Line], body_size: float | None, marks: dict[str, str]) -> tuple[dict, str, list[Line]]:
    """The contact snapshot, the headline and any profile lines from the lines above the first heading."""
    contact: dict[str, Any] = {}
    headline, leftover = "", []
    rest = list(top)
    if rest and not EMAIL.search(rest[0].text) and not PHONE.search(rest[0].text):
        name_line = rest.pop(0)
        contact["name"] = name_line.text.replace("\t", " ").strip()
        biggest = max((ln.size for ln in top), default=0.0)
        sure = (NAME_LINE.match(contact["name"]) is not None
                and (name_line.style == "title" or not body_size or name_line.size >= biggest))
        if not sure:
            marks["contact/name"] = UNSURE_WHY
    else:
        marks["contact/name"] = MISSING_WHY
    places: list[str] = []
    links: list[str] = []
    for line in rest:
        text = line.text
        found = False
        for m in EMAIL.finditer(text):
            contact.setdefault("email", m.group(0))
            found = True
        text = EMAIL.sub(" ", text)
        for m in URL.finditer(text):
            links.append(m.group(0).rstrip(".,"))
            found = True
        text = URL.sub(" ", text)
        for m in PHONE.finditer(text):
            contact.setdefault("phone", m.group(0).strip())
            found = True
        text = PHONE.sub(" ", text)
        parts = [p.strip(" ,") for p in SEPARATORS.split(text) if p.strip(" ,")]
        if found or (parts and all(len(p.split()) <= 4 for p in parts) and len(rest) > 1 and not headline):
            places += [p for p in parts if re.fullmatch(r"[A-Za-z\u00c0-\u024f' .-]+", p) and len(p.split()) <= 4]
            continue
        if not headline and len(line.text.split()) <= HEADLINE_WORDS:
            headline = line.text.strip()
        else:
            leftover.append(line)
    if places:
        contact["city"] = places[0]
        if len(places) > 1:
            marks["contact/city"] = UNSURE_WHY
    if links:
        contact["links"] = links
    return contact, headline, leftover


def read_rules(lines: list[Line]) -> Reading:
    """The CV as Hal's rules read it: headings, entries, dates, bullets and the contact lines at the top."""
    sizes = [round(ln.size, 1) for ln in lines if ln.size > 0]
    body_size = Counter(sizes).most_common(1)[0][0] if sizes else None
    marks: dict[str, str] = {}
    kinds = _section_kinds(lines, body_size)
    first_heading = next((i for i, kind in enumerate(kinds) if kind), len(lines))
    contact, headline, leftover = _contact(lines[:first_heading], body_size, marks)
    doc = CVDoc(headline=headline, contact_snapshot=contact)
    used: set[str] = set()
    counts: Counter = Counter()
    groups: list[tuple[str, str, list[Line]]] = []
    if leftover:
        groups.append(("summary", "Profile", leftover))
    for line, kind in zip(lines[first_heading:], kinds[first_heading:], strict=True):
        if kind:
            groups.append((kind, _title(line.text), []))
        else:
            groups[-1][2].append(line)
    for kind, title, body in groups:
        counts[kind] += 1
        n = counts[kind]
        sid = f"custom-{n}" if kind == "custom" else (kind if n == 1 else f"{kind}-{n}")
        doc.sections.append(_section(kind, title, sid, body, marks, used))
    reading = Reading(doc, marks, "rules")
    mark_values(reading, lines)
    return reading


# ----------------------------------------------------------------------------------------------- the AI

def _blocks(lines: list[Line]) -> str:
    """The file's lines for the AI, each marked as a heading (H), bullet (B) or text (T)."""
    sizes = [round(ln.size, 1) for ln in lines if ln.size > 0]
    body_size = statistics.mode(sizes) if sizes else None
    out = []
    for line, kind in zip(lines, _section_kinds(lines, body_size), strict=True):
        tag = "H" if kind else "B" if line.bullet else "T"
        out.append(f"{tag}| {line.text.replace(chr(9), '    ')}")
    return "\n".join(out)


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _conf(raw: dict, key: str) -> float:
    conf = raw.get("confidence") if isinstance(raw.get("confidence"), dict) else {}
    try:
        return float(conf.get(key, 1.0))
    except (TypeError, ValueError):
        return 0.0


def _known_kind(title: str) -> str | None:
    """The kind Hal's rules give a heading with these words, else None."""
    key = _key(title.strip().rstrip(":").strip())
    known = headings()
    if key in known:
        return known[key]
    return next((kind for word, kind in KEYWORDS if word in key), None)


def _without_grade(heading: str, grade: str) -> str:
    """The qualification without the grade written after it ("BA History, 2:1"), as the rules read it."""
    m = GRADE.search(heading) if grade else None
    if m and _key(m.group("grade")) == _key(grade):
        return heading[:m.start()].rstrip()
    return heading


def from_ai(reply: dict) -> Reading | None:
    """Schema 2 from the AI's JSON, with its unsure fields marked; None when the reply holds no sections."""
    sections = reply.get("sections") if isinstance(reply.get("sections"), list) else []
    if not any(isinstance(s, dict) and (s.get("entries") or _s(s.get("text"))) for s in sections):
        return None
    marks: dict[str, str] = {}
    raw_contact = reply.get("contact") if isinstance(reply.get("contact"), dict) else {}
    contact: dict[str, Any] = {k: _s(raw_contact.get(k)) for k in ("name", "email", "phone", "city")
                               if _s(raw_contact.get(k))}
    links = [_s(x) for x in raw_contact.get("links") or [] if _s(x)]
    if links:
        contact["links"] = links
    for key in ("name", "email", "phone", "city"):
        if key in contact and _conf(raw_contact, key) < LOW_CONFIDENCE:
            marks[f"contact/{key}"] = UNSURE_WHY
    if "name" not in contact:
        marks["contact/name"] = MISSING_WHY
    doc = CVDoc(headline=_s(reply.get("headline")), contact_snapshot=contact)
    used: set[str] = set()
    counts: Counter = Counter()
    for raw in sections:
        if not isinstance(raw, dict):
            continue
        kind = _s(raw.get("kind")).lower()
        kind = kind if kind in SECTION_KINDS else "custom"
        if kind == "custom":
            # Claude called "REFERENCES" custom in one reading and additional in the next (checked 2026-10-23)
            kind = _known_kind(_s(raw.get("title"))) or "custom"
        counts[kind] += 1
        n = counts[kind]
        sid = f"custom-{n}" if kind == "custom" else (kind if n == 1 else f"{kind}-{n}")
        sec = Section(id=sid, kind=kind, title=_s(raw.get("title")) or kind.title(), text=_s(raw.get("text")))
        if sec.text and _conf(raw, "text") < LOW_CONFIDENCE:
            marks[f"s:{sid}/text"] = UNSURE_WHY
        bullets_used: set[str] = set()
        for e in raw.get("entries") or []:
            if not isinstance(e, dict):
                continue
            # The same file read twice by Claude kept the grade on the heading once and gave a lone year as
            # the end date once (checked 2026-10-23), so both are put where the rules put them; the ids match.
            grade, start, end = _s(e.get("grade")), _s(e.get("start")), _s(e.get("end"))
            heading = _without_grade(_s(e.get("heading")), grade) if kind == "education" else _s(e.get("heading"))
            if end and not start and not re.fullmatch(OPEN_END, end, re.I):
                start, end = end, ""
            eid = _entry_id(kind, heading, used)
            entry = Entry(id=eid, heading=heading, org=_s(e.get("org")), location=_s(e.get("location")),
                          start=start, end=end, grade=grade,
                          meta=_s(e.get("meta")), text=_s(e.get("text")),
                          items=[_s(i) for i in e.get("items") or [] if _s(i)])
            entry.bullets = [Bullet(_bullet_id(eid, _s(b), bullets_used), _s(b)) for b in e.get("bullets") or []
                             if _s(b)]
            entry.modules = [Module(_s(m.get("name")), _s(m.get("mark")), _s(m.get("year")))
                             for m in e.get("modules") or [] if isinstance(m, dict) and _s(m.get("name"))]
            if entry.text and not entry.bullets:
                entry.style = "paragraph"
            base = f"s:{sid}/e:{eid}"
            for name in ("heading", "org", "location", "grade", "meta", "text", "items"):
                if getattr(entry, name) and _conf(e, name) < LOW_CONFIDENCE:
                    marks[f"{base}/{name}"] = UNSURE_WHY
            if (entry.start or entry.end) and min(_conf(e, "start"), _conf(e, "end")) < LOW_CONFIDENCE:
                marks[f"{base}/dates"] = UNSURE_WHY
            sec.entries.append(entry)
        doc.sections.append(sec)
    return Reading(doc, marks, "ai")


def read_with_ai(lines: list[Line]) -> tuple[Reading | None, str]:
    """The AI's reading of the lines and "" , or (None, why not) when there is no AI or its reply is unusable."""
    from jobhunt import ai
    from jobhunt.ai.base import LLMUnavailable, PrivateLeak

    try:
        reply = ai.chat_json(prompts.text("cv_parse"), _blocks(lines), role="checker", priority=0,
                             purpose="cv_parse")
    except LLMUnavailable as e:
        return None, str(e) or "Hal's AI is not available."
    except PrivateLeak:
        return None, "Your CV holds one of your private answers, so Hal kept it away from the AI."
    reading = from_ai(reply)
    if reading is None:
        return None, "The AI's reading was empty."
    mark_values(reading, lines)
    return reading, ""


# ------------------------------------------------------------------------------------- checks and rows

def _found(value: str, source: str) -> bool:
    return not value.strip() or _norm(value) in source


def mark_values(reading: Reading, lines: list[Line]) -> None:
    """Marks every value that is not in the file's text, and every date with a two-digit year."""
    source = source_text(lines)
    marks = reading.marks
    contact = reading.doc.contact_snapshot
    for key in ("name", "email", "phone", "city"):
        if contact.get(key) and not _found(str(contact[key]), source):
            marks[f"contact/{key}"] = NOT_FOUND_WHY
    if reading.doc.headline and not _found(reading.doc.headline, source):
        marks["headline"] = NOT_FOUND_WHY
    for sec in reading.doc.sections:
        if sec.text and not _found(sec.text, source):
            marks[f"s:{sec.id}/text"] = NOT_FOUND_WHY
        for e in sec.entries:
            base = f"s:{sec.id}/e:{e.id}"
            for name in ("heading", "org", "location", "grade", "meta", "text"):
                if not _found(getattr(e, name), source):
                    marks[f"{base}/{name}"] = NOT_FOUND_WHY
            if not (_found(e.start, source) and _found(e.end, source)):
                marks[f"{base}/dates"] = NOT_FOUND_WHY
            elif TWO_DIGIT.search(e.start) or TWO_DIGIT.search(e.end):
                marks[f"{base}/dates"] = TWO_DIGIT_WHY
            if not all(_found(i, source) for i in e.items):
                marks[f"{base}/items"] = NOT_FOUND_WHY
            for b in e.bullets:
                if not _found(b.text, source):
                    marks[f"{base}/b:{b.id}"] = NOT_FOUND_WHY


CONTACT_LABELS = (("name", "Name"), ("email", "Email"), ("phone", "Phone"), ("city", "Town or city"))
HEADING_LABELS = {"experience": "Job", "volunteering": "Volunteering role", "projects": "Project",
                  "research": "Research", "teaching": "Teaching", "certifications": "Certificate",
                  "achievements": "Achievement", "publications": "Publication", "talks": "Talk"}
ORG_LABELS = {"education": "University or college", "experience": "Employer", "volunteering": "Organisation"}


def dates_words(entry: Entry) -> str:
    """An entry's dates as one phrase: "2024 to 2025", or the one date it has."""
    return f"{entry.start} to {entry.end}" if entry.start and entry.end else entry.start or entry.end


def rows(reading: Reading) -> list[dict]:
    """The rows of "Check what Hal read": one per field worth checking, each Fine or Check this."""
    out: list[dict] = []
    marks = reading.marks

    def add(path: str, label: str, value: str) -> None:
        out.append({"path": path, "label": label, "value": value, "sure": path not in marks,
                    "why": marks.get(path, ""), "corrected": False})

    contact = reading.doc.contact_snapshot
    for key, label in CONTACT_LABELS:
        if contact.get(key) or f"contact/{key}" in marks:
            add(f"contact/{key}", label, str(contact.get(key) or ""))
    if contact.get("links"):
        add("contact/links", "Links", ", ".join(contact["links"]))
    if reading.doc.headline:
        add("headline", "The line under your name", reading.doc.headline)
    for sec in reading.doc.sections:
        if sec.text:
            add(f"s:{sec.id}/text", sec.title, sec.text)
        for e in sec.entries:
            base = f"s:{sec.id}/e:{e.id}"
            if sec.kind in ITEM_KINDS:
                add(f"{base}/items", f"{sec.title}: {e.heading}" if e.heading else sec.title, ", ".join(e.items))
                continue
            if sec.kind == "education":
                label = "Degree" if DEGREE.search(e.heading) else "Qualification"
            else:
                label = HEADING_LABELS.get(sec.kind, sec.title)
            add(f"{base}/heading", label, e.heading)
            if sec.kind in ORG_KINDS and (e.org or f"{base}/org" in marks):
                add(f"{base}/org", ORG_LABELS.get(sec.kind, "Organisation"), e.org)
            if e.start or e.end or f"{base}/dates" in marks:
                add(f"{base}/dates", f"{e.heading or sec.title} dates", dates_words(e))
            if e.grade:
                add(f"{base}/grade", "Grade", e.grade)
            if e.text:
                add(f"{base}/text", e.heading or sec.title, e.text)
            for b in e.bullets:
                if b.text.lower().startswith("dissertation"):
                    add(f"{base}/b:{b.id}", "Dissertation", b.text.split(":", 1)[-1].strip())
                elif f"{base}/b:{b.id}" in marks:
                    add(f"{base}/b:{b.id}", e.heading or sec.title, b.text)
    return out


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def found_words(doc: CVDoc) -> str:
    """What Hal found, for the report: "contact details, 1 degree, 2 jobs, 1 volunteering role, 14 skills"."""
    parts = []
    if any(doc.contact_snapshot.get(k) for k in ("name", "email", "phone")):
        parts.append("contact details")
    count: Counter = Counter()
    for sec in doc.sections:
        for e in sec.entries:
            if sec.kind == "education":
                count["degree" if DEGREE.search(e.heading) else "qualification"] += 1
            elif sec.kind in ITEM_KINDS:
                count[sec.kind] += len(e.items)
            else:
                count[sec.kind] += 1
    words = (("degree", "degree"), ("qualification", "other qualification"), ("experience", "job"),
             ("volunteering", "volunteering role"), ("projects", "project"), ("research", "research role"),
             ("certifications", "certificate"), ("achievements", "achievement"), ("publications", "publication"),
             ("languages", "language"), ("skills", "skill"))
    parts += [_plural(count[key], word) for key, word in words if count[key]]
    return ", ".join(parts) or "no sections"


# ------------------------------------------------------------------------------------------ corrections

PATH = re.compile(r"^s:(?P<sid>[^/]+)(?:/e:(?P<eid>[^/]+))?(?:/(?:b:(?P<bid>[^/]+)|(?P<field>[a-z]+)))?$")
ENTRY_FIELDS = ("heading", "org", "location", "grade", "meta", "text", "dates", "items")
GONE_WORDS = "That part of your CV is not there any more."


def correct(doc: CVDoc, path: str, value: str) -> CVDoc:
    """A new document with the one field `path` names set to `value`; LookupError when it is not there."""
    out = copy.deepcopy(doc)
    value = value.strip()
    if path.startswith("contact/"):
        key = path.split("/", 1)[1]
        if key not in {k for k, _ in CONTACT_LABELS} | {"links"}:
            raise LookupError(GONE_WORDS)
        out.contact_snapshot[key] = _items(value) if key == "links" else value
        return out
    if path == "headline":
        out.headline = value
        return out
    m = PATH.match(path)
    if m is None:
        raise LookupError(GONE_WORDS)
    sec = next((s for s in out.sections if s.id == m.group("sid")), None)
    if sec is None:
        raise LookupError(GONE_WORDS)
    if m.group("eid") is None:
        if m.group("field") != "text":
            raise LookupError(GONE_WORDS)
        sec.text = value
        return out
    entry = next((e for e in sec.entries if e.id == m.group("eid")), None)
    if entry is None:
        raise LookupError(GONE_WORDS)
    if m.group("bid"):
        bullet = next((b for b in entry.bullets if b.id == m.group("bid")), None)
        if bullet is None:
            raise LookupError(GONE_WORDS)
        if bullet.text.lower().startswith("dissertation") and not value.lower().startswith("dissertation"):
            value = f"{bullet.text.split(':', 1)[0]}: {value}"
        bullet.text = value
        return out
    name = m.group("field")
    if name not in ENTRY_FIELDS:
        raise LookupError(GONE_WORDS)
    if name == "dates":
        parts = re.split(rf"\s+(?:to|[{DASHES}])\s+", value, maxsplit=1)
        entry.start, entry.end = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (value, "")
    elif name == "items":
        entry.items = _items(value)
    else:
        setattr(entry, name, value)
    return out


# --------------------------------------------------------------------------------------------- prefill

def prefill(doc: CVDoc) -> dict[str, str]:
    """The `you.*` values the CV gives: its contact details and its first education entry."""
    out: dict[str, str] = {}
    contact = doc.contact_snapshot
    for key in ("name", "email", "phone", "city"):
        if contact.get(key):
            out[f"you.{key}"] = str(contact[key])
    for link in contact.get("links") or []:
        where = "linkedin" if "linkedin." in link else "github" if "github." in link else "website"
        url = link if link.startswith("http") else f"https://{link}"
        out.setdefault(f"you.links.{where}", url)
    edu = next((s for s in doc.sections if s.kind == "education" and s.entries), None)
    if edu is not None:
        first = edu.entries[0]
        m = DEGREE_SPLIT.match(first.heading)
        if m:
            out["you.education.degree"], out["you.education.subject"] = m.group("degree"), m.group("subject")
        elif first.heading:
            out["you.education.degree"] = first.heading
        if first.org:
            out["you.education.university"] = first.org
        if first.grade:
            out["you.education.grade"] = first.grade
        year = re.search(r"(?:19|20)\d{2}", first.start)
        if year:
            out["you.education.start_year"] = year.group(0)
        for b in first.bullets:
            if b.text.lower().startswith("dissertation") and ":" in b.text:
                out["you.education.dissertation"] = b.text.split(":", 1)[1].strip()
                break
    return out


def preview_paragraphs(lines: list[Line]) -> list[dict]:
    """A Word file or pasted text as simple paragraphs for the left side of "Check what Hal read"."""
    sizes = [round(ln.size, 1) for ln in lines if ln.size > 0]
    body_size = statistics.mode(sizes) if sizes else None
    out = []
    for n, line in enumerate(lines):
        kind = ("title" if n == 0 else "heading" if heading_kind(line, body_size) else
                "bullet" if line.bullet else "text")
        out.append({"text": line.text.replace("\t", "    "), "kind": kind})
    return out
