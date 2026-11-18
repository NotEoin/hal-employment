"""Makes the made-up CVs in `tests/fixtures/cv/`: a PDF and a Word file for each of three invented people,
a pasted-text copy, and a PDF that is only a picture.

Run `python tests/support/make_cv_fixtures.py` from the checkout to write them again; `--check` says whether
the files on disk are what this script makes. The output is byte for byte the same on every run (fixed
dates in the zip entries, no timestamps in the PDFs), so the committed files can be checked against it.

The three people are those of `tests/support/people.py`: a humanities graduate, a technical (STEM)
graduate and a career changer. Every name, place, employer and date is invented. Each CV is written as a
list of lines that both writers draw, so the PDF and the Word file say the same things:

- the PDF uses the base-14 Helvetica fonts with WinAnsi text (so no font is embedded), headings in
  capitals and bold, and each entry's dates on the right of its first line;
- the Word file uses the "Title", "Heading 1" and "List Paragraph" styles, Word numbering for bullets, a
  tab before the dates, bold runs for entry headings, and a table for the technical graduate's skills.

A fourth person, Avery Quillon, has a CV printed the way a web browser prints one (see `BROWSER`), with
`BROWSER_EXPECTED` for it.

`EXPECTED` lists what a reader must find in each CV; `tests/test_cv_parse.py` checks against it.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

FOLDER = Path(__file__).resolve().parents[1] / "fixtures" / "cv"
ZIP_DATE = (2026, 1, 1, 0, 0, 0)   # any fixed date keeps the Word files reproducible
UNIX = 3                           # the zip "made by" system of the committed files

PAGE_W, PAGE_H = 595, 842          # A4 in points
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 50, 60, 60
SIZES = {"name": 20.0, "contact": 9.5, "heading": 11.0, "entry": 10.0, "sub": 10.0, "bullet": 10.0, "para": 10.0}
LEAD = 1.45                        # line height as a share of the type size
HEADING_GAP = 8.0                  # extra space above a section heading, in points
BULLET_INDENT = 14.0
HELVETICA_AVERAGE = 0.52           # a rough average glyph width, enough to right-align a date
BULLET = "\u2022"
EN_DASH = "\u2013"


@dataclass(frozen=True)
class Line:
    """One line of a made-up CV: `kind` is name, contact, heading, entry, sub, bullet or para; `right` is
    the text drawn at the right margin (the dates of an entry)."""

    kind: str
    text: str
    right: str = ""


def _cv(*rows: tuple) -> list[Line]:
    return [Line(*r) for r in rows]


HUMANITIES = _cv(
    ("name", "Marnie Quillfeather"),
    ("contact", "Harrowgate Vale | marnie.quillfeather@example.org | 07700 900461"),
    ("heading", "PROFILE"),
    ("para", "History graduate who enjoys turning old records into stories people want to read. Careful with "
             "sources, calm with the public and used to explaining things clearly."),
    ("heading", "EDUCATION"),
    ("entry", "BA (Hons) History and Imaginary Studies, 2:1", "2023 - 2026"),
    ("sub", "University of Nowhere Fen"),
    ("bullet", "Dissertation: Bridges and ballots, parish records in the Fen, 1820 to 1850 (First, 74)"),
    ("bullet", "Led a seminar group of 12 through a term of primary source work"),
    ("heading", "EXPERIENCE"),
    ("entry", "Student Ambassador, University of Nowhere Fen", "Sept 24 - Jun 25"),
    ("bullet", "Guided 40 open-day tours for families and school groups"),
    ("bullet", "Answered questions from visitors about courses and student life"),
    ("entry", "Bookshop Assistant, The Lantern Bookshop, Harrowgate Vale", f"Jun 2022 {EN_DASH} Aug 2023"),
    ("bullet", "Ran the local history table and its monthly reading evening"),
    ("heading", "VOLUNTEERING"),
    ("entry", "Archive Volunteer, Fenland Parish Archive", "2024 - Present"),
    ("bullet", "Catalogued 300 letters from the 1840s into the archive's spreadsheet"),
    ("heading", "SKILLS"),
    ("para", "Research: archival research, oral history, referencing, palaeography, source criticism"),
    ("para", "Digital: Microsoft Excel, Microsoft Word, PowerPoint, online catalogues, social media"),
    ("para", "People: public speaking, customer service, tour guiding, teamwork"),
    ("heading", "INTERESTS"),
    ("para", "Local history walks, choir singing and restoring old maps."),
)

TECHNICAL = _cv(
    ("name", "Tobias Wrenfield"),
    ("contact", "Lowmere | toby.wrenfield@example.org | 07700 900462 | github.com/example-wrenfield"),
    ("heading", "SUMMARY"),
    ("para", "Software Studies graduate who builds small, well-tested tools and likes making data easy to use."),
    ("heading", "EDUCATION"),
    ("entry", "BSc (Hons) Software Studies, First", "Sep 2023 - Jul 2026"),
    ("sub", "Made-up Institute of Technology"),
    ("bullet", "Modules: Algorithms (78), Databases (72), Human Factors (69)"),
    ("heading", "PROJECTS"),
    ("entry", "Tide Tables App", "2025"),
    ("sub", "Python, Flask, SQLite"),
    ("bullet", "Built a web app that shows the week's tides for 14 harbours"),
    ("bullet", "Wrote 120 tests that run in under 2 seconds"),
    ("entry", "Library Seat Finder", "2024"),
    ("sub", "TypeScript, React"),
    ("bullet", "Showed free study seats live to 300 students in a pilot"),
    ("heading", "WORK EXPERIENCE"),
    ("entry", "Junior Developer Intern, Brackwater Analytics, Lowmere", "Jun 2025 - Sep 2025"),
    ("bullet", "Cut a nightly report from 40 minutes to 6 by rewriting its queries"),
    ("bullet", "Reviewed code with two senior developers every week"),
    ("heading", "TECHNICAL SKILLS"),
    ("para", "Languages: Python, TypeScript, SQL, Java"),
    ("para", "Tools: Git, Docker, Linux, PostgreSQL"),
    ("heading", "CERTIFICATIONS"),
    ("para", "Made-up Cloud Practitioner, 2025"),
)

CHANGER = _cv(
    ("name", "Priya Ashdown-Vell"),
    ("contact", "Saltmarsh Cross | priya.ashdown@example.org | 07700 900463"),
    ("heading", "PERSONAL STATEMENT"),
    ("para", "Store manager of nine years moving into project coordination. I plan rotas, budgets and refits, "
             "and I keep people informed when plans change."),
    ("heading", "EMPLOYMENT HISTORY"),
    ("entry", "Store Manager, Corner and Crate Grocers, Saltmarsh Cross", "2019 - Present"),
    ("bullet", "Ran a team of 18 and a weekly budget of 60,000 pounds"),
    ("bullet", "Planned a six-week refit that opened two days early"),
    ("entry", "Assistant Store Manager, Corner and Crate Grocers, Eastwold", "2016 - 2019"),
    ("bullet", "Brought in a stock check that cut waste by a fifth"),
    ("heading", "EDUCATION"),
    ("entry", "Level 3 Diploma in Business Administration, Distinction", "2014 - 2016"),
    ("sub", "Saltmarsh Cross College"),
    ("heading", "CERTIFICATIONS"),
    ("para", "Made-up Project Foundation Certificate, 2025"),
    ("para", "Food Safety Level 2, 2017"),
    ("heading", "SKILLS"),
    ("para", "Planning: rotas, budgets, supplier schedules, risk logs"),
    ("para", "Tools: Microsoft Excel, Microsoft Project, Trello"),
    ("heading", "REFERENCES"),
    ("para", "Available on request."),
)

PEOPLE = {"humanities": HUMANITIES, "technical": TECHNICAL, "changer": CHANGER}
NAMES = {"humanities": "Marnie-Quillfeather-CV", "technical": "Tobias-Wrenfield-CV", "changer": "Priya-Ashdown-Vell-CV"}

# What a reader must find: section kinds in order, and entries per kind.
EXPECTED = {
    "humanities": {"name": "Marnie Quillfeather", "email": "marnie.quillfeather@example.org",
                   "phone": "07700 900461", "city": "Harrowgate Vale",
                   "kinds": ["summary", "education", "experience", "volunteering", "skills", "interests"],
                   "entries": {"education": 1, "experience": 2, "volunteering": 1}, "skills": 14,
                   "unsure_dates": ["Student Ambassador"]},
    "technical": {"name": "Tobias Wrenfield", "email": "toby.wrenfield@example.org", "phone": "07700 900462",
                  "city": "Lowmere",
                  "kinds": ["summary", "education", "projects", "experience", "skills", "certifications"],
                  "entries": {"education": 1, "projects": 2, "experience": 1, "certifications": 1}, "skills": 8,
                  "unsure_dates": []},
    "changer": {"name": "Priya Ashdown-Vell", "email": "priya.ashdown@example.org", "phone": "07700 900463",
                "city": "Saltmarsh Cross",
                "kinds": ["summary", "experience", "education", "certifications", "skills", "additional"],
                "entries": {"experience": 2, "education": 1, "certifications": 2}, "skills": 7,
                "unsure_dates": []},
}


# ------------------------------------------------------------------------------------------------ PDF

def _pdf_string(text: str) -> bytes:
    raw = text.encode("cp1252")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _text_op(x: float, y: float, font: str, size: float, text: str) -> bytes:
    return b"BT /%s %.1f Tf %.2f %.2f Td %s Tj ET\n" % (font.encode(), size, x, y, _pdf_string(text))


def _pages(lines: list[Line]) -> list[bytes]:
    """The content stream of each page."""
    pages, ops = [], []
    y = PAGE_H - MARGIN_TOP
    for line in lines:
        size = SIZES[line.kind]
        step = size * LEAD + (HEADING_GAP if line.kind == "heading" else 0.0)
        if y - step < MARGIN_BOTTOM:
            pages.append(b"".join(ops))
            ops, y = [], PAGE_H - MARGIN_TOP
        y -= step
        bold = line.kind in ("name", "heading", "entry")
        font = "F2" if bold else "F1"
        x = MARGIN_X
        if line.kind == "bullet":
            ops.append(_text_op(x + 2, y, "F1", size, BULLET))
            x += BULLET_INDENT
        ops.append(_text_op(x, y, font, size, line.text))
        if line.right:
            width = len(line.right) * size * HELVETICA_AVERAGE
            ops.append(_text_op(PAGE_W - MARGIN_X - width, y, "F1", size, line.right))
    pages.append(b"".join(ops))
    return pages


def _pdf(objects: list[bytes]) -> bytes:
    """A PDF file from its numbered objects (object n is objects[n - 1])."""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _stream(data: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Length %d %s>>\nstream\n" % (len(data), extra) + data + b"\nendstream"


def pdf_bytes(lines: list[Line], *, pages: int | None = None) -> bytes:
    """A text PDF of `lines`; with `pages`, the first page is repeated until there are that many."""
    streams = _pages(lines)
    if pages is not None:
        streams = (streams * pages)[:pages] if len(streams) < pages else streams[:pages]
    first_page = 5
    kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(len(streams)))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(streams)),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    for i, content in enumerate(streams):
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R "
                       b"/F2 4 0 R >> >> /Contents %d 0 R >>" % (PAGE_W, PAGE_H, first_page + 2 * i + 1))
        objects.append(_stream(content))
    return _pdf(objects)


# ----------------------------------------------------------------------------- a CV printed by a browser

# A fourth made-up person, written as Markdown and printed the way Chrome prints a web page (Producer
# "Skia/PDF"): type set in CSS pixels on a page scaled by 0.75, subset font names, no space characters
# (each word is placed by a TJ offset, so PDFium has to work the spaces out), kerning inside words, list
# markers drawn as small filled squares instead of bullet characters, and long lines wrapped. Blocks are
# (kind, text); `**...**` at the start of a text is a bold lead-in, `[text](url)` a link, `\*` an escape.
BROWSER = (
    ("h1", "Avery Quillon"),
    ("contact", "Lowmere | avery.quillon@example.org | 07700 900464 | "
                "[github.com/example-quillon](https://github.com/example-quillon)"),
    ("h2", "Profile"),
    ("p", "Software Studies graduate who builds careful tools for small teams. I write tested Python services, "
          "keep their releases tidy and explain the trade-offs to the people who use them every day."),
    ("h2", "Education"),
    ("h3", "BSc (Hons) Software Studies, First"),
    ("meta", "Made-up Institute of Technology | Sep 2023 - Jun 2026 | Accredited by an invented society"),
    ("li", "**Dissertation:** reading tide tables from scanned harbour notices with a small vision model, "
           "scored against 400 hand-checked tables from 12 harbours along a made-up coast."),
    ("p", "**Foundation Diploma: Computing**, Lowmere College, 2022 - 2023: Distinction in every unit."),
    ("h2", "Projects"),
    ("h3", "Tide Tables - weekly tides for small harbours"),
    ("meta", "Python, Flask, SQLite | [github.com/example-quillon/tides](https://github.com/example-quillon/tides)"),
    ("li", "Built a web app that shows the week's tides for 14 harbours and warns harbour masters about "
           "storm surges by default."),
    ("li", "**Testing:** wrote 120 tests that run in under 2 seconds on every change, with a nightly run that "
           "checks each harbour's feed against its published API"),
    ("li", "**Speed:** cut a nightly report from 27 minutes to 6 by rewriting its queries with A\\* search and "
           "caching the parsed tables in RAM"),
    ("p", "**Also:** a seat finder for the campus library, used by 300 students in a pilot."),
    ("h3", "Other projects"),
    ("li", "**[Bus Board](https://example.org/bus-board)** (TypeScript): live departures for two bus stops on "
           "a small screen by the door of a made-up student house."),
    ("h2", "Experience"),
    ("h3", "Junior Developer Intern"),
    ("meta", "Brackwater Analytics | Jun 2025 - Sep 2025"),
    ("li", "Wrote the end-to-end tests for a reporting service and reviewed code with two senior developers "
           "every week of the summer placement."),
    ("p", "**Shop Assistant**, Corner and Crate Grocers, Mar - May 2024: served customers and kept the stock "
          "room in order through a busy spring."),
    ("h2", "Skills"),
    ("li", "**Languages:** Python, TypeScript, SQL, Java"),
    ("li", "**Tools:** Git, Docker, Linux, PostgreSQL"),
    ("h2", "Interests"),
    ("p", "Sea kayaking, choir singing and restoring old maps."),
)
BROWSER_NAME = "Avery-Quillon-CV"
BROWSER_EXPECTED = {
    "name": "Avery Quillon", "email": "avery.quillon@example.org", "phone": "07700 900464", "city": "Lowmere",
    "kinds": ["summary", "education", "projects", "experience", "skills", "interests"],
    "headings": ["BSc (Hons) Software Studies", "Foundation Diploma: Computing",
                 "Tide Tables - weekly tides for small harbours", "Other projects", "Junior Developer Intern",
                 "Shop Assistant"],
    "bullets": [1, 0, 3, 1, 1, 0], "texts": [0, 1, 1, 0, 0, 1], "skills": 8,
    "dates": [("Sep 2023", "Jun 2026"), ("2022", "2023"), ("", ""), ("", ""), ("Jun 2025", "Sep 2025"),
              ("Mar", "May 2024")],
}

PX = 0.75                          # Chrome sets type in CSS pixels and scales the page by 0.75 into points
PAGE_PX_W, PAGE_PX_H = PAGE_W / PX, PAGE_H / PX
BROWSER_MARGIN = 56.0              # about 15 mm in pixels, as the real CVs' print style sheet sets
LIST_INDENT = 17.33                # CSS padding-left of 13 pt, in pixels
MARKER = 4.0                       # the side of a list marker square, in pixels
MARKER_GAP = 6.0                   # from the marker's right edge to the item's text, in pixels
LINE_HEIGHT = 1.34                 # CSS line-height of the real CVs
BROWSER_SIZES = {"h1": 26.66, "contact": 11.6, "h2": 14.66, "h3": 13.33, "meta": 12.13, "li": 13.2, "p": 13.2}
BROWSER_ABOVE = {"h1": 0.0, "contact": 2.0, "h2": 14.66, "h3": 8.0, "meta": 0.0, "li": 2.0, "p": 2.67}
LETTER_SPACING = 0.04              # the section titles' CSS letter-spacing, in ems
KERNS = {"T": 70, "W": 50, "Y": 50, "V": 50}   # thousandths of an em a capital tucks the next lower-case letter in
# The standard Helvetica and Helvetica-Bold widths of " " to "~" in thousandths of an em (Adobe's AFM files, as
# PDFium's built-in fonts report them), so lines wrap where a browser with these fonts would wrap them.
HELVETICA_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
    556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667,
    556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556,
    556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722,
    500, 500, 500, 334, 260, 334, 584)
HELVETICA_BOLD_WIDTHS = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
    556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 1072, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722,
    611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556,
    611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778,
    556, 556, 500, 389, 280, 389, 584)
FIRST_CHAR = 32
MARKDOWN_WRAP = 100                # where an editor wraps a long Markdown line, so the reader must join it again


def _plain(text: str) -> str:
    """Markdown inline text as a browser shows it: no bold markers, links as their text, escapes undone."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("\\*", "*")


def _runs(kind: str, text: str) -> list[tuple[str, bool, bool]]:
    """The words of a block: (word, bold, glued), where `glued` means no space before it (the "," after a
    bold lead-in)."""
    whole = kind in ("h1", "h2", "h3")
    m = re.match(r"\*\*(.+?)\*\*(.*)$", text)
    parts = [(m.group(1), True), (m.group(2), False)] if m else [(text, False)]
    out: list[tuple[str, bool, bool]] = []
    for part, bold in parts:
        plain = _plain(part)
        if kind == "h2":
            plain = plain.upper()
        for i, word in enumerate(plain.split(" ")):
            if word:
                out.append((word, bold or whole, i == 0 and bool(out)))
    return out


def _width(word: str, bold: bool, size: float) -> float:
    widths = HELVETICA_BOLD_WIDTHS if bold else HELVETICA_WIDTHS
    return sum(widths[ord(ch) - FIRST_CHAR] for ch in word) * size / 1000


def _tj(word: str) -> bytes:
    """A word as a TJ array body, with capitals kerned into the letter after them."""
    out, piece = [], ""
    for i, ch in enumerate(word):
        piece += ch
        if ch in KERNS and i + 1 < len(word) and word[i + 1].islower():
            out.append(_pdf_string(piece) + b" %d" % KERNS[ch])
            piece = ""
    if piece:
        out.append(_pdf_string(piece))
    return b" ".join(out)


def _browser_pages() -> list[bytes]:
    """Each page's content stream for `BROWSER`, laid out the way the print style sheet lays it out."""
    pages, ops = [], [b"q %.2f 0 0 %.2f 0 0 cm\n" % (PX, PX)]
    top = BROWSER_MARGIN
    for kind, text in BROWSER:
        size = BROWSER_SIZES[kind]
        left = BROWSER_MARGIN + (LIST_INDENT if kind == "li" else 0.0)
        width = PAGE_PX_W - BROWSER_MARGIN - left
        spacing = LETTER_SPACING * size if kind == "h2" else 0.0
        lines: list[list[tuple[str, bool, bool]]] = [[]]
        used = 0.0
        for word, bold, glued in _runs(kind, text):
            w = _width(word, bold, size) + spacing * len(word)
            gap = 0.0 if glued or not lines[-1] else _width(" ", bold, size)
            if lines[-1] and used + gap + w > width and not glued:
                lines.append([])
                used, gap = 0.0, 0.0
            lines[-1].append((word, bold, glued))
            used += gap + w
        top += BROWSER_ABOVE[kind]
        for n, line in enumerate(lines):
            top += size * LINE_HEIGHT
            baseline = PAGE_PX_H - top + size * (LINE_HEIGHT - 1) / 2
            if kind == "li" and n == 0:
                mid = baseline + size * 0.3
                ops.append(b"%.2f %.2f %.2f %.2f re f\n" % (left - MARKER_GAP - MARKER, mid - MARKER / 2,
                                                            MARKER, MARKER))
            x = left
            for i, (word, bold, glued) in enumerate(line):
                if i and not glued:
                    x += _width(" ", bold, size)
                ops.append(b"BT /%s %.2f Tf %.2f Tc %.2f %.2f Td [%s] TJ ET\n" % (
                    b"F2" if bold else b"F1", size, spacing, x, baseline, _tj(word)))
                x += _width(word, bold, size) + spacing * len(word)
    ops.append(b"Q\n")
    pages.append(b"".join(ops))
    return pages


def browser_pdf_bytes() -> bytes:
    """`BROWSER` printed as Chrome prints a page: see the note above `BROWSER`."""
    streams = _browser_pages()
    first_page = 5
    kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(len(streams)))

    def font(name: bytes, widths: tuple[int, ...]) -> bytes:
        listed = b" ".join(b"%d" % w for w in widths)
        return (b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding /FirstChar %d "
                b"/LastChar %d /Widths [%s] >>" % (name, FIRST_CHAR, FIRST_CHAR + len(widths) - 1, listed))

    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(streams)),
               font(b"BAAAAA+Helvetica", HELVETICA_WIDTHS), font(b"AAAAAA+Helvetica-Bold", HELVETICA_BOLD_WIDTHS)]
    for i, content in enumerate(streams):
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R "
                       b"/F2 4 0 R >> >> /Contents %d 0 R >>" % (PAGE_W, PAGE_H, first_page + 2 * i + 1))
        objects.append(_stream(content))
    return _pdf(objects)


def browser_markdown() -> str:
    """`BROWSER` as the Markdown it was printed from, with long paragraphs wrapped as an editor wraps them."""
    out: list[str] = []
    prefix = {"h1": "# ", "h2": "## ", "h3": "### ", "li": "- "}
    for kind, text in BROWSER:
        if kind != "li" or (out and not out[-1].startswith("- ")):
            out.append("")
        body = prefix.get(kind, "") + text
        if kind == "p" and len(body) > MARKDOWN_WRAP:
            cut = body.rindex(" ", 0, MARKDOWN_WRAP)
            body = body[:cut] + "\n" + body[cut + 1:]
        if kind == "li" and len(body) > MARKDOWN_WRAP:
            cut = body.rindex(" ", 0, MARKDOWN_WRAP)
            body = body[:cut] + "\n  " + body[cut + 1:]
        out.append(body)
    return "\n".join(out).lstrip("\n") + "\n"


def scanned_pdf_bytes() -> bytes:
    """A one-page PDF that is only a picture (grey stripes like lines of text), with no text at all."""
    width, height = 60, 80
    rows = []
    for y in range(height):
        ink = 90 if (y % 6) in (2, 3) and 8 < y < height - 8 else 245
        rows.append(bytes([ink]) * width)
    pixels = b"".join(rows)
    image = _stream(pixels, b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
                            b"/BitsPerComponent 8 " % (width, height))
    draw = b"q %d 0 0 %d 0 0 cm /Im1 Do Q\n" % (PAGE_W, PAGE_H)
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                 b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /XObject << /Im1 5 0 R >> >> "
                 b"/Contents 4 0 R >>" % (PAGE_W, PAGE_H),
                 _stream(draw), image])


# ----------------------------------------------------------------------------------------------- Word

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
PKG = "http://schemas.openxmlformats.org/package/2006"
OFFICE = "application/vnd.openxmlformats-officedocument.wordprocessingml"
RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CONTENT_TYPES = (
    f'{XML_HEAD}<Types xmlns="{PKG}/content-types">\n'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    '<Default Extension="xml" ContentType="application/xml"/>\n'
    f'<Override PartName="/word/document.xml" ContentType="{OFFICE}.document.main+xml"/>\n'
    f'<Override PartName="/word/styles.xml" ContentType="{OFFICE}.styles+xml"/>\n'
    f'<Override PartName="/word/numbering.xml" ContentType="{OFFICE}.numbering+xml"/>\n</Types>')
ROOT_RELS = (f'{XML_HEAD}<Relationships xmlns="{PKG}/relationships">\n'
             f'<Relationship Id="rId1" Type="{RELS}/officeDocument" Target="word/document.xml"/>\n</Relationships>')
DOC_RELS = (f'{XML_HEAD}<Relationships xmlns="{PKG}/relationships">\n'
            f'<Relationship Id="rId1" Type="{RELS}/styles" Target="styles.xml"/>\n'
            f'<Relationship Id="rId2" Type="{RELS}/numbering" Target="numbering.xml"/>\n</Relationships>')
STYLES = (f'{XML_HEAD}<w:styles xmlns:w="{W_NS}">\n'
          '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>\n'
          '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>\n'
          '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>\n'
          '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/></w:style>\n'
          '</w:styles>')
NUMBERING = (f'{XML_HEAD}<w:numbering xmlns:w="{W_NS}">\n'
             '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/>'
             '<w:lvlText w:val="&#8226;"/></w:lvl></w:abstractNum>\n'
             '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>\n</w:numbering>')


def _run(text: str, bold: bool = False) -> str:
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(runs: str, style: str = "", bullet: bool = False) -> str:
    props = ""
    if style or bullet:
        inner = f'<w:pStyle w:val="{style}"/>' if style else ""
        if bullet:
            inner += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        props = f"<w:pPr>{inner}</w:pPr>"
    return f"<w:p>{props}{runs}</w:p>"


def _table(rows: list[tuple[str, str]]) -> str:
    cells = "".join(f"<w:tr><w:tc>{_para(_run(a, bold=True))}</w:tc><w:tc>{_para(_run(b))}</w:tc></w:tr>"
                    for a, b in rows)
    return f"<w:tbl>{cells}</w:tbl>"


def _body(lines: list[Line], skills_table: bool) -> str:
    out, i, current = [], 0, ""
    while i < len(lines):
        line = lines[i]
        if line.kind == "heading":
            current = line.text
        if skills_table and current.endswith("SKILLS") and line.kind == "para" and ": " in line.text:
            rows = []
            while i < len(lines) and lines[i].kind == "para" and ": " in lines[i].text:
                rows.append(tuple(lines[i].text.split(": ", 1)))
                i += 1
            out.append(_table(rows))
            continue
        if line.kind == "name":
            out.append(_para(_run(line.text), "Title"))
        elif line.kind == "heading":
            out.append(_para(_run(line.text.title()), "Heading1"))
        elif line.kind == "entry":
            right = '<w:r><w:tab/></w:r>' + _run(line.right) if line.right else ""
            out.append(_para(_run(line.text, bold=True) + right))
        elif line.kind == "bullet":
            out.append(_para(_run(line.text), "ListParagraph", bullet=True))
        else:
            out.append(_para(_run(line.text)))
        i += 1
    return "".join(out)


def docx_bytes(lines: list[Line], *, skills_table: bool = False) -> bytes:
    """A Word file of `lines`, the same on every run."""
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="{W_NS}">'
                f"<w:body>{_body(lines, skills_table)}</w:body></w:document>")
    parts = [("[Content_Types].xml", CONTENT_TYPES), ("_rels/.rels", ROOT_RELS),
             ("word/_rels/document.xml.rels", DOC_RELS), ("word/document.xml", document),
             ("word/styles.xml", STYLES), ("word/numbering.xml", NUMBERING)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = UNIX   # zipfile writes 0 (MS-DOS) on Windows, so the bytes differed there
            z.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def text_of(lines: list[Line], *, bullet: str = BULLET) -> str:
    """The CV as someone would paste it from a word processor, each bullet starting with `bullet`."""
    out = []
    for line in lines:
        if line.kind == "heading":
            out.append("")
        text = f"{bullet} {line.text}" if line.kind == "bullet" else line.text
        out.append(f"{text}    {line.right}" if line.right else text)
    return "\n".join(out) + "\n"


def ascii_text_of(lines: list[Line]) -> str:
    """`text_of` with hyphen bullets and hyphen ranges, as a plain-text editor would keep it."""
    return text_of(lines, bullet="-").replace(EN_DASH, "-")


def files() -> dict[str, bytes]:
    """Every fixture file by name."""
    out: dict[str, bytes] = {}
    for key, lines in PEOPLE.items():
        out[f"{NAMES[key]}.pdf"] = pdf_bytes(lines)
        out[f"{NAMES[key]}.docx"] = docx_bytes(lines, skills_table=key == "technical")
        out[f"{NAMES[key]}.txt"] = ascii_text_of(lines).encode("ascii")
    out["Scanned-CV.pdf"] = scanned_pdf_bytes()
    out[f"{BROWSER_NAME}.pdf"] = browser_pdf_bytes()
    out[f"{BROWSER_NAME}.md"] = browser_markdown().encode("ascii")
    return out


def main(argv: list[str] | None = None) -> int:
    """Writes the fixtures, or with `--check` reports any that differ from what this script makes."""
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--check", action="store_true", help="only say whether the files are up to date")
    opts = args.parse_args(argv)
    made = files()
    if opts.check:
        stale = [n for n, data in made.items() if not (FOLDER / n).is_file() or (FOLDER / n).read_bytes() != data]
        for name in stale:
            print(f"out of date: {name}")
        return 1 if stale else 0
    FOLDER.mkdir(parents=True, exist_ok=True)
    for name, data in made.items():
        (FOLDER / name).write_bytes(data)
        print(f"wrote {FOLDER / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
