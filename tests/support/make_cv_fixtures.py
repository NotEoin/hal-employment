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

`EXPECTED` lists what a reader must find in each CV; `tests/test_cv_parse.py` checks against it.
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

FOLDER = Path(__file__).resolve().parents[1] / "fixtures" / "cv"
ZIP_DATE = (2026, 1, 1, 0, 0, 0)   # any fixed date keeps the Word files reproducible

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
