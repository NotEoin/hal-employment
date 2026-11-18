"""Reads a PDF's characters into the CV's lines: rows grouped, list items found, wrapped rows joined.

Two modules read PDFs on purpose. `pdfread.py` is the low level: it talks to PDFium and returns characters
(position, printed size, bold, italic, text order), page widths and list marker shapes, and is also used for
page pictures and the record's scans. This module holds only the CV's own judgement over those characters:
which characters share a row, which rows are one wrapped line, which lines are list items. Keeping the
judgement here means `pdfread.py` stays free of CV rules and this one stays free of PDFium calls.

- A PDF printed by a browser marks little: list markers are shapes, not text, and long lines wrap with no
  sign. `pdf_lines` therefore finds bold from font names, list items from marker shapes or indents, and
  joins a row to the one above when its first word would not have fitted there (see the constants below).
- The thresholds are shares of the type size, so they hold at any scale; each says where its value comes from.
- Each row keeps every character's bold, italic and link (`styles`, one per character of its text, a space
  taking what both its neighbours share), so a line keeps bold and italic words and links inside it as marks
  (`Line.marked`). A character links to an address when its middle lies inside a link annotation of its
  page (`pdfread.links`), as a browser prints `<a href>`; only http, https and mailto addresses are kept.

Moved out of `parse.py` with no change.
"""
from __future__ import annotations

import bisect
import itertools
from collections import Counter
from dataclasses import dataclass

from jobhunt.cvstudio import inline, pdfread
from jobhunt.cvstudio.lines import Line, _bulleted, _hash, marked_text

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

PLAIN = (False, False, "")      # a character's (bold, italic, link) when it is none of them
Style = tuple[bool, bool, str]


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
    styles: tuple[Style, ...] = ()     # (bold, italic, link) of each character of `text`


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


def _link_of(c: pdfread.Char, links: list[pdfread.Link]) -> str:
    """The web or email address of the link annotation over the character's middle, or ""."""
    x, y = (c.left + c.right) / 2, (c.bottom + c.top) / 2
    return next((inline.safe_link(k.url) for k in links
                 if k.page == c.page and k.left <= x <= k.right and k.bottom <= y <= k.top), "")


def _shared(a: Style, b: Style) -> Style:
    """The style a space between two characters takes: what both of them have."""
    return (a[0] and b[0], a[1] and b[1], a[2] if a[2] == b[2] else "")


def _row(row: list[pdfread.Char], spaced, links: list[pdfread.Link] = ()) -> _Row | None:
    """A row of characters as text, with its geometry and how much of it is bold."""
    glyphs = sorted((c for c in row if not c.char.isspace()), key=lambda c: c.x)
    if not glyphs:
        return None
    text, lead, tab, starts, styles = "", None, False, [], []
    own = [(c.bold, c.italic, _link_of(c, links) if links else "") for c in glyphs]
    for i, c in enumerate(glyphs):
        if i:
            prev = glyphs[i - 1]
            gap = _shared(own[i - 1], own[i])
            if c.left - prev.right > COLUMN_GAP * max(c.points, 1.0):
                text, tab = text + "\t", True
                styles.append(gap)
            elif spaced(prev, c):
                text += " "
                styles.append(gap)
        if lead is None and not c.bold and i:
            lead = text
        starts.append(len(text))
        text += c.char
        styles.append(own[i])
    body, bullet = _bulleted(text)
    off = text.find(body)
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
                all(c.italic for c in rest), bullet, tab, tuple(styles[off:off + len(body)]))


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
    last, first = a.styles[-1] if a.styles else PLAIN, b.styles[0] if b.styles else PLAIN
    joint = (_shared(last, first),) if glue else ()
    return _Row(a.text + glue + b.text, a.page, b.y, a.size, a.left, b.right, a.first_word, a.bold and b.bold,
                a.text + glue + b.text if a.bold and b.bold else lead, a.italic and b.italic, a.bullet, False,
                a.styles + joint + b.styles)


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
    links = pdfread.links(data)
    rows = [r for r in (_row(g, spaced, links) for g in grouped) if r is not None and r.text]
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
                 left=r.left, lead=r.lead, italic=r.italic, marked=_marked(r)) for n, r in enumerate(joined)]


def _marked(r: _Row) -> str:
    """The row's text with its bold and italic words and its links as marks; "" when it has none."""
    if len(r.styles) != len(r.text) or "\t" in r.text:
        return ""
    return marked_text([inline.Run(ch, *style) for ch, style in zip(r.text, r.styles, strict=True)], r.text)
