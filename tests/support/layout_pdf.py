"""Made-up CV PDFs with a known layout, drawn in the test itself, for measuring ("Match my file's layout").

`page_pdf` draws rows of text at set places in the base-14 fonts every PDF reader has (Helvetica for sans,
Times for serif, so no font is embedded), with colours, letter spacing (`Tc`), thin rules, square list markers
and link annotations, the way a browser prints a page. `Layout` holds the numbers a test then expects to
measure back. Every name, place and date is invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from support.make_cv_fixtures import _pdf, _pdf_string, _stream

A4 = (595.28, 841.89)
PT_PER_MM = 72 / 25.4
FONTS = {("sans", False): b"Helvetica", ("sans", True): b"Helvetica-Bold",
         ("serif", False): b"Times-Roman", ("serif", True): b"Times-Bold"}
MARKER = 3.0            # a list marker's side in points, as Chrome draws a disc at 9 pt


@dataclass
class Run:
    """Words in one style on a row."""

    text: str
    bold: bool = False
    family: str = "serif"
    colour: str = "#111111"
    spacing: float = 0.0        # letter spacing in points
    link: str = ""


@dataclass
class Row:
    """One row: its baseline from the top of the page, its size, where it starts and its runs; `right` is text
    set at the right margin (dates); `marker` draws a list marker before it; `rule` a rule under it."""

    top: float
    size: float
    runs: list[Run]
    left: float = 0.0
    right: Run | None = None
    marker: bool = False
    rule: str = ""              # the rule's colour, "" for none


@dataclass
class Layout:
    """A made-up page's known numbers, in points unless named otherwise."""

    margin: float = 15 * PT_PER_MM
    body: float = 9.0
    pitch: float = 9.0 * 1.25
    name: float = 18.0
    contact: float = 8.5
    heading: float = 10.5
    spacing_em: float = 0.05
    indent: float = 12.0
    rows: list[Row] = field(default_factory=list)


def _width(text: str, size: float) -> float:
    """A rough width, enough to set dates at the right margin."""
    return len(text) * size * 0.5


def _rgb(colour: str) -> bytes:
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return b"%.3f %.3f %.3f rg " % (r, g, b)


def page_ops(rows: list[Row], margin: float) -> tuple[bytes, list[tuple[float, float, float, float, str]]]:
    """The content stream of one page and its links (box and address)."""
    ops, links = [], []
    height = A4[1]
    for row in rows:
        y = height - row.top
        x = margin + row.left
        if row.marker:
            ops.append(b"0 0 0 rg %.2f %.2f %.2f %.2f re f\n" % (x - 2 * MARKER - 3, y + 1.5, MARKER, MARKER))
        for run in row.runs:
            font = FONTS[(run.family, run.bold)]
            ops.append(b"BT " + _rgb(run.colour) + b"/%s %.2f Tf %.3f Tc %.2f %.2f Td %s Tj ET\n" % (
                font, row.size, run.spacing, x, y, _pdf_string(run.text)))
            width = _width(run.text, row.size) + run.spacing * len(run.text)
            if run.link:
                links.append((x, y - 2, x + width, y + row.size, run.link))
            x += width
        if row.right is not None:
            rx = A4[0] - margin - _width(row.right.text, row.size)
            ops.append(b"BT " + _rgb(row.right.colour) + b"/%s %.2f Tf %.2f %.2f Td %s Tj ET\n" % (
                FONTS[(row.right.family, False)], row.size, rx, y, _pdf_string(row.right.text)))
        if row.rule:
            ops.append(_rgb(row.rule) + b"%.2f %.2f %.2f 0.75 re f\n" % (margin, y - 4, A4[0] - 2 * margin))
    return b"".join(ops), links


def page_pdf(pages: list[list[Row]], margin: float) -> bytes:
    """A PDF of one or more pages of rows."""
    fonts = list(FONTS.values())
    first_font, first_page = 3, 3 + len(fonts)
    kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(len(pages)))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages))]
    objects += [b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % f for f in fonts]
    resources = b" ".join(b"/%s %d 0 R" % (f, first_font + i) for i, f in enumerate(fonts))
    annots: list[bytes] = []
    first_annot = first_page + 2 * len(pages)
    for i, rows in enumerate(pages):
        ops, links = page_ops(rows, margin)
        refs = b" ".join(b"%d 0 R" % (first_annot + len(annots) + k) for k in range(len(links)))
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << %s >> >> "
                       b"/Contents %d 0 R%s >>" % (A4[0], A4[1], resources, first_page + 2 * i + 1,
                                                    b" /Annots [%s]" % refs if links else b""))
        objects.append(_stream(ops))
        annots += [b"<< /Type /Annot /Subtype /Link /Rect [%.2f %.2f %.2f %.2f] /Border [0 0 0] "
                   b"/A << /S /URI /URI %s >> >>" % (a, b, c, d, _pdf_string(url)) for a, b, c, d, url in links]
    return _pdf(objects + annots)


def _listed_entry(lay: Layout, top: float, form: str) -> tuple[list[Row], float]:
    """An entry with a list under it: stacked (heading alone, then "place | dates") or as one line."""
    if form == "stacked":
        rows = [Row(top, lay.body, [Run("Made-up Degree in Tide Studies", True)]),
                Row(top + lay.pitch, lay.body, [Run("Made-up University | 2021 - 2024", colour="#333333")])]
        top += 2 * lay.pitch
    else:
        rows = [Row(top, lay.body, [Run("Made-up Degree in Tide Studies", True),
                                    Run(", Made-up University, 2021 - 2024: a made-up course.")])]
        top += lay.pitch
    for words in ("Wrote a made-up dissertation on harbour tides.", "Led a made-up study group of six."):
        rows.append(Row(top, lay.body, [Run(words)], left=lay.indent, marker=True))
        top += lay.pitch + 1.0
    return rows, top + 2.0


def one_line_cv(lay: Layout | None = None, *, stacked: bool = False, rule: str = "#444444",
                listed: str = "") -> tuple[bytes, Layout]:
    """A one-page made-up CV: sans name, contact line and headings in capitals over a rule, serif body text,
    entries written as one line (or stacked, with dates at the right), and a list. With `listed` ("stacked"
    or "line"), each section of entries starts with an entry that has a list under it, written that way."""
    lay = lay or Layout()
    top = lay.margin + lay.name
    rows = [Row(top, lay.name, [Run("Avery Quillon", True, "sans")])]
    top += lay.name * 0.9
    rows.append(Row(top, lay.contact, [Run("avery@example.org | 07700 900464 | ", family="sans", colour="#333333"),
                                       Run("example.org/avery", family="sans", colour="#333333",
                                           link="https://example.org/avery")]))
    sections = [("PROFILE", "para"), ("EXPERIENCE", "entries"), ("PROJECTS", "list"), ("EDUCATION", "entries")]
    for title, kind in sections:
        top += lay.heading * 2.2
        rows.append(Row(top, lay.heading, [Run(title, True, "sans", spacing=lay.spacing_em * lay.heading)],
                        rule=rule))
        top += lay.heading * 1.6
        if kind == "para":
            for words in ("Made-up profile text about a graduate who likes tidy data and plain words, long enough",
                          "to wrap onto a second row of the same paragraph."):
                rows.append(Row(top, lay.body, [Run(words)]))
                top += lay.pitch
        elif kind == "list":
            for words in ("Built a made-up tide table for small harbours.", "Wrote a made-up shelf finder.",
                          "Kept a made-up allotment planner going."):
                rows.append(Row(top, lay.body, [Run(words)], left=lay.indent, marker=True))
                top += lay.pitch + 1.0
        else:
            if listed:
                more, top = _listed_entry(lay, top, listed)
                rows += more
            for heading, place, dates in (("Shop Assistant", "Made-up Books", "2022 - 2023"),
                                          ("Archive Volunteer", "Made-up Archive", "2023 - Present"),
                                          ("Cafe Assistant", "Made-up Cafe", "2021 - 2022")):
                if stacked:
                    rows.append(Row(top, lay.body, [Run(heading, True)], right=Run(dates)))
                    top += lay.pitch
                    rows.append(Row(top, lay.body, [Run(place)]))
                else:
                    rows.append(Row(top, lay.body, [Run(heading, True),
                                                    Run(f", {place}, {dates}: shelved stock and answered questions.")]))
                top += lay.pitch + 2.0
    return page_pdf([rows], lay.margin), lay
