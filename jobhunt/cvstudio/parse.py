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
- A PDF with no text layer is refused with words that say what to do instead; there is no OCR for CVs.
- Ids are made the way schema 2 makes them (`<prefix>-` + sha1(kind + heading)[:8]), so reading the same
  file twice gives the same ids and later edits find their entries.
- Rows of the "Check what Hal read" report have a path that names one field
  (`contact/email`, `s:<section>/text`, `s:<section>/e:<entry>/<field>`, `.../b:<bullet>`), and
  `correct()` changes exactly that field.
"""
from __future__ import annotations

import copy
import hashlib
import io
import logging
import re
import statistics
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

from ..ai import prompts
from . import pdfread
from .model import Bullet, CVDoc, Entry, Module, Section

log = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024    # 10 MB
MAX_PAGES = 6                   # 6 pages
MAX_UNZIPPED = 50 * 1024 * 1024  # a Word file's text part above this is not a CV (and may be a zip bomb)
LOW_CONFIDENCE = 0.6            # an AI confidence below this marks the field "Check this"
LARGE_TYPE = 1.15               # a line this much larger than the body text may be a heading
HEADING_WORDS = 5               # headings are short
HEADLINE_WORDS = 12             # a line under the name longer than this is a profile, not a headline
PARAGRAPH_WORDS = 14            # a non-bullet line this long inside an entry is its paragraph, not a header
LINE_TOLERANCE = 0.45           # share of the type size two baselines may differ by on one line (as pdfread)
WORD_GAP = 0.25                 # a gap wider than this share of the type size parts two words (as pdfread)
COLUMN_GAP = 2.0                # a gap wider than this many type sizes is a tab (dates on the right)

PICTURE_WORDS = "This PDF is a picture of a CV, so Hal cannot read it. Upload the Word file, or paste the text."
TOO_BIG_WORDS = ("This file is bigger than 10 MB, so Hal cannot read it. Save a smaller PDF or Word file, "
                 "or paste the text.")
TOO_LONG_WORDS = "This CV has {n} pages. Hal reads CVs of up to 6 pages. Upload a shorter one, or paste the text."
KIND_WORDS = "Hal reads PDF and Word (.docx) files. Save your CV as one of those, or paste the text."
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


@dataclass
class Reading:
    """A CV read into schema 2, with the fields that need a check (path -> why)."""

    doc: CVDoc
    marks: dict[str, str] = field(default_factory=dict)
    how: str = "rules"      # rules | ai


# ------------------------------------------------------------------------------------------- the file

def kind_of(name: str, data: bytes) -> str:
    """"pdf", "docx" or "txt", from the bytes first and the name second; CVReadError for anything else."""
    lower = name.lower()
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK") and lower.endswith(".docx"):
        return "docx"
    if data.startswith(b"\xd0\xcf\x11\xe0") or lower.endswith(".doc"):
        raise CVReadError(OLD_WORD_WORDS)
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
    if kind == "txt":
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


def pdf_lines(data: bytes) -> list[Line]:
    """The PDF's lines in reading order, with the type size each is set in."""
    rows: list[list[pdfread.Char]] = []
    for c in sorted(pdfread.chars(data), key=lambda c: (c.page, -c.y, c.x)):
        if rows and rows[-1][0].page == c.page and abs(rows[-1][0].y - c.y) <= LINE_TOLERANCE * max(c.size, 1.0):
            rows[-1].append(c)
        else:
            rows.append([c])
    out = []
    for row in rows:
        text, prev, gap_pending = "", None, False
        for c in sorted(row, key=lambda c: c.x):
            if c.char.isspace():
                gap_pending = True
                continue
            if prev is not None:
                gap = c.left - prev.right
                if gap > COLUMN_GAP * max(c.size, 1.0):
                    text += "\t"
                elif gap_pending or gap > WORD_GAP * max(c.size, 1.0):
                    text += " "
            text += c.char
            prev, gap_pending = c, False
        sizes = [c.size for c in row if not c.char.isspace()]
        body, bullet = _bulleted(text)
        if body:
            out.append(Line(body, size=max(sizes) if sizes else 0.0, bullet=bullet, page=row[0].page,
                            index=len(out), hash=_hash(body)))
    return out


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


def lines_of(data: bytes, kind: str) -> list[Line]:
    """The lines of a file of `kind` ("pdf", "docx" or "txt")."""
    if kind == "pdf":
        return pdf_lines(data)
    if kind == "docx":
        return docx_lines(data)
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


def heading_kind(line: Line, body_size: float | None) -> str | None:
    """The section kind when `line` is a heading, else None."""
    if line.bullet:
        return None
    text = line.text.strip().rstrip(":").strip()
    words = text.split()
    if not words or len(words) > HEADING_WORDS or "\t" in text or any(ch.isdigit() for ch in text):
        return None
    key = re.sub(r"\s+", " ", text.replace("&", "and")).lower()
    if key in HEADINGS:
        return HEADINGS[key]
    letters = [ch for ch in text if ch.isalpha()]
    caps = bool(letters) and all(ch.isupper() for ch in letters) and len(letters) > 2
    styled = line.style.startswith("heading")
    large = bool(body_size) and line.size >= body_size * LARGE_TYPE
    if caps or styled or large or (line.bold and len(words) <= 3):
        for word, kind in KEYWORDS:
            if word in key:
                return kind
        return "custom" if (caps or styled) else None
    return None


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


def _drafts(lines: list[Line]) -> list[_Draft]:
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
    rest_lines = draft.header[1:]
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
            meta = f"{meta}; {extra}" if meta else extra
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
    first_heading = next((i for i, ln in enumerate(lines) if heading_kind(ln, body_size)), len(lines))
    contact, headline, leftover = _contact(lines[:first_heading], body_size, marks)
    doc = CVDoc(headline=headline, contact_snapshot=contact)
    used: set[str] = set()
    counts: Counter = Counter()
    groups: list[tuple[str, str, list[Line]]] = []
    if leftover:
        groups.append(("summary", "Profile", leftover))
    for line in lines[first_heading:]:
        kind = heading_kind(line, body_size)
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
    for line in lines:
        tag = "H" if heading_kind(line, body_size) else "B" if line.bullet else "T"
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
            heading = _s(e.get("heading"))
            eid = _entry_id(kind, heading, used)
            entry = Entry(id=eid, heading=heading, org=_s(e.get("org")), location=_s(e.get("location")),
                          start=_s(e.get("start")), end=_s(e.get("end")), grade=_s(e.get("grade")),
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
    from .. import ai
    from ..ai.base import LLMUnavailable, PrivateLeak

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
