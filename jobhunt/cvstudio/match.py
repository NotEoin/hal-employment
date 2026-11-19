"""Measures the layout of a person's own CV PDF, for "Match my file's look": the measures of a personal look.

`measure` reads the PDF's characters (`pdfread.chars`: place, printed size, bold) and what is drawn around them
(`pdfread.drawing`: page size, font names, colours, thin rules, pictures) and works out each measure of
`measures.Measures` by a rule of its own, written next to it below. A measure the file does not show keeps
its default (the Classic look's value), and every number is brought into its range before it is checked, so
no file can make a look Hal cannot draw.

- The page is read as one column. A file with text side by side (two columns, a side bar) or with pictures
  is approximated, and `Found.notes` says so in plain words rather than pretend it matched.
- The rows are this module's own grouping of characters by baseline (as `pdflines`, without joining wrapped
  rows), because the measures need each row's place and its gap to the next.
- The parts of a page are found in order: the name (the first row, larger than the body text), the contact
  line (the first row after it that has an address, a phone number or a separator), the section headings
  (rows with a rule just under them, else short rows in capitals or larger bold type), list items (a marker
  shape or a bullet character before them), and entries (a row that starts in bold: wholly bold is a
  stacked heading, bold then plain is a one-line entry).
- Text comes back only as numbers and choices: nothing read from the file is kept in the measures.
"""
from __future__ import annotations

import itertools
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from jobhunt.cvstudio import measures, pdfread

PT_PER_MM = 72 / 25.4
MIN_PT = 1.0                    # PDFium gives its own line breaks a size of 1 pt; real text is larger
CONTACT_ROWS = 3                # the contact line is one of the first three rows after the name
ONE_ITEM = 2.4                  # a step this many type sizes or less between two list items: one row each
ROW_TOLERANCE = 0.45            # share of the type size two baselines may differ by on one row (as pdflines)
COLUMN_GAP = 2.0                # a gap wider than this many type sizes parts a row (dates at the right, columns)
SIZE_STEP = 0.1                 # sizes are compared to a tenth of a point
BIGGER = 1.08                   # a heading is at least this much larger than the body text to count by size
NAME_BIGGER = 1.3               # the name is at least this much larger than the body text
SHORT_HEADING = 40              # a section heading is a few words: at most this many characters
RULE_BELOW = 1.0                # a heading's rule lies within this many heading sizes below its baseline
HALF_PAGE = 0.5                 # a heading's rule spans at least half the text's width
CENTRED = 0.05                  # a row whose middle is this share of the text's width from the middle is centred
INDENTED = 0.1                  # ... and it starts this share of the width in from the left margin
WRAP_STEP = 1.6                 # a row this many type sizes or less below another of the same type continues it
ALIGN_SLACK = 0.2               # two rows start in the same place when this close, in type sizes (as pdflines)
MARKER_REACH = 2.5              # a list marker sits within this many type sizes left of its text (as pdflines)
BULLETS = "\u2022\u25aa\u25cf\u2023\u2043\u25e6-*\u2013"
DATE_HINT = re.compile(r"\d{4}|present|now|current", re.I)
CONTACT_HINT = re.compile(r"@|\+?\d[\d ()-]{7,}|https?://|www\.|linkedin|github", re.I)
SEPARATOR_MARKS = {"bar": "|", "dot": "\u00b7", "bullet": "\u2022"}
COMMA_PARTS = 3                 # a contact line of three or more parts parted only by commas uses commas
SIDE_BY_SIDE = 0.3              # this share of rows with long text on both sides of a gap is two columns
SIDE_TEXT = 12                  # ... long meaning this many characters or more on each side
INK_TO_LINE = 0.1               # a line's box starts about a tenth of its type size above its letters' tops
SPACING_FLOOR = 0.01            # letter spacing under a hundredth of an em is measuring noise, not a style
SERIF_WORDS = ("serif", "times", "tinos", "georgia", "garamond", "cambria", "caladea", "baskerville", "palatino",
               "minion", "charter", "merriweather", "lora", "roman", "bookman", "century", "didot", "bodoni")
SANS_WORDS = ("sans", "arial", "arimo", "helvetica", "calibri", "carlito", "verdana", "segoe", "roboto", "lato",
              "inter", "tahoma", "trebuchet", "futura", "gill", "avenir", "frutiger", "ubuntu", "cantarell")
PAGE_SIZES = {"a4": (595.28, 841.89), "letter": (612.0, 792.0)}
LINK_BELOW = 0.35               # an underline lies within this many type sizes below a link's baseline

TWO_COLUMNS = ("Your file has text side by side, such as two columns. Hal's copy uses one column, with your "
               "file's type, sizes and colours.")
PICTURES = "Your file has pictures or icons. Hal's copy leaves them out."
ONE_PAGE_BOTTOM = "Your file is one page, so Hal used its top margin for the bottom margin too."
UNKNOWN_FONT = "Hal could not tell your file's font, so it used a plain sans-serif font."


class CannotMatch(ValueError):
    """A file Hal cannot measure, with the reason in plain words."""


@dataclass
class Row:
    """One printed row: its characters, in order, with its place and type."""

    page: int
    y: float
    chars: list[pdfread.Char]
    size: float = 0.0
    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    text: str = ""
    parts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.chars.sort(key=lambda c: c.x)
        sizes = Counter(round(c.points, 1) for c in self.chars)
        self.size = sizes.most_common(1)[0][0]
        self.left = min(c.left for c in self.chars)
        self.right = max(c.right for c in self.chars)
        self.top = max(c.top for c in self.chars)
        self.bottom = min(c.bottom for c in self.chars)
        text, parts = "", [""]
        for a, b in zip([None, *self.chars], self.chars, strict=False):
            if a is not None and b.left - a.right > COLUMN_GAP * self.size:
                parts.append("")
                text += "\t"
            elif a is not None and b.left - a.right > pdfread.WORD_GAP * self.size:
                parts[-1] += " "
                text += " "
            parts[-1] += b.char
            text += b.char
        self.text, self.parts = text, [p.strip() for p in parts]

    @property
    def bold(self) -> bool:
        """Every letter is bold."""
        return all(c.bold for c in self.chars if c.char.isalnum())

    def _first_part(self) -> list[pdfread.Char]:
        """The letters before the first wide gap (dates at the right are a part of their own)."""
        out = []
        for a, b in zip([None, *self.chars], self.chars, strict=False):
            if a is not None and b.left - a.right > COLUMN_GAP * self.size:
                break
            if b.char.isalnum():
                out.append(b)
        return out

    @property
    def heading(self) -> bool:
        """Its first part is wholly bold: a stacked entry's heading, its dates (if any) at the right."""
        letters = self._first_part()
        return len(letters) >= 2 and all(c.bold for c in letters)

    @property
    def lead(self) -> bool:
        """Its first part starts with two or more bold letters and goes on in plain type: a one-line entry."""
        letters = self._first_part()
        first = next((i for i, c in enumerate(letters) if not c.bold), len(letters))
        return first >= 2 and first < len(letters)

    @property
    def caps(self) -> bool:
        """Every letter is a capital, and there are a few."""
        letters = [c.char for c in self.chars if c.char.isalpha()]
        return len(letters) >= 3 and all(ch.isupper() for ch in letters)


@dataclass
class Found:
    """What `measure` found: the measures, plain sentences on what it approximated, and the file's pages."""

    measures: measures.Measures
    notes: list[str]
    pages: int


def _rows(chars: list[pdfread.Char]) -> list[Row]:
    """The characters grouped into rows by baseline, top to bottom, page by page."""
    groups: list[list[pdfread.Char]] = []
    for c in sorted(chars, key=lambda c: (c.page, -c.y, c.x)):
        if groups and groups[-1][0].page == c.page and abs(groups[-1][0].y - c.y) <= ROW_TOLERANCE * c.points:
            groups[-1].append(c)
        else:
            groups.append([c])
    return [Row(g[0].page, g[0].y, g) for g in groups]


def _mm(points: float) -> float:
    return round(points / PT_PER_MM, 1)


def _most(values, default=None):
    """The value that comes up most often, or `default` when there are none."""
    counted = Counter(values)
    return counted.most_common(1)[0][0] if counted else default


def _family(names: list[pdfread.Ink]) -> str | None:
    """"serif" or "sans" for the fonts most of these characters are drawn in; None when no name or flag says."""
    votes = Counter()
    for ink in names:
        name = ink.font.lower().replace("-", " ")
        if "sans" in name or any(w in name for w in SANS_WORDS):
            votes["sans"] += 1
        elif any(w in name for w in SERIF_WORDS) or ink.serif:
            votes["serif"] += 1
    return votes.most_common(1)[0][0] if votes else None


def _inks(row: Row, drawing: pdfread.Drawing) -> list[pdfread.Ink]:
    return [ink for c in row.chars if (ink := drawing.inks.get((c.page, c.index)))]


def _page(drawing: pdfread.Drawing) -> str:
    """The named page size nearest the first page's."""
    w, h = drawing.sizes[0]
    return min(PAGE_SIZES, key=lambda k: abs(PAGE_SIZES[k][0] - w) + abs(PAGE_SIZES[k][1] - h))


def _has_rule_under(row: Row, rules: list[pdfread.Rule], width: float) -> pdfread.Rule | None:
    for rule in rules:
        if rule.page == row.page and row.y - RULE_BELOW * row.size * 2 <= rule.top <= row.y + 1 and \
                rule.right - rule.left >= HALF_PAGE * width and rule.left <= row.left + row.size:
            return rule
    return None


def _marked(row: Row, shapes: list[pdfread.Shape]) -> bool:
    return any(s.page == row.page and s.right <= row.left and row.left - s.right <= MARKER_REACH * row.size
               and row.y - row.size <= (s.bottom + s.top) / 2 <= row.y + row.size for s in shapes)


def _separator(row: Row) -> str | None:
    """The contact line's separator: a mark between parts, wide gaps, or commas."""
    for name, mark in SEPARATOR_MARKS.items():
        if mark in row.text:
            return name
    if len(row.parts) >= 2:
        return "space"
    if " - " in row.text:
        return "dash"
    if row.text.count(",") + 1 >= COMMA_PARTS:
        return "comma"
    return None


def _spacing(rows: list[Row], drawing: pdfread.Drawing) -> float:
    """Letter spacing in ems: the pen's step from letter to letter less each letter's own advance."""
    steps = []
    for r in rows:
        for a, b in zip(r.chars, r.chars[1:], strict=False):
            advance = drawing.advances.get((a.page, a.index))
            if advance and a.char.isalnum() and b.char.isalnum() and b.left - a.right < COLUMN_GAP * r.size:
                steps.append((b.x - a.x - advance) / r.size)
    if not steps:
        return 0.0
    spacing = statistics.median(steps)
    return round(spacing, 2) if spacing >= SPACING_FLOOR else 0.0


def _pitch(rows: list[Row], size: float) -> float | None:
    """The baseline step between rows of one paragraph in type of `size`: the smallest step between two rows of
    that size starting at the same left edge. Two paragraphs or list items one under the other step further
    (by their margins), so the smallest step is a wrapped line's, which is the line height itself."""
    steps = [round(a.y - b.y, 1) for a, b in itertools.pairwise(rows)
             if a.page == b.page and a.size == b.size == size and abs(a.left - b.left) <= ALIGN_SLACK * size
             and 0 < a.y - b.y <= WRAP_STEP * size]
    return min(steps) if steps else None


class _Reader:
    """One measuring of one PDF: its rows and drawing, and the measures and notes found so far."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.chars = [c for c in pdfread.chars(data) if not c.char.isspace() and c.points >= MIN_PT]
        self.drawing = pdfread.drawing(data)
        if not self.chars or not self.drawing.sizes:
            raise CannotMatch("Hal found no text in your file to measure. It may be a scan or a picture of a page.")
        self.rows = _rows(self.chars)
        self.pages = len(self.drawing.sizes)
        self.got: dict = {"page": _page(self.drawing)}
        self.notes: list[str] = []
        self.body = _most((round(c.points, 1) for c in self.chars), measures.Measures().size)
        self.got["size"] = self.body
        self.left = min(r.left for r in self.rows)
        self.right = max(r.right for r in self.rows)
        self.width = self.right - self.left
        self.lh = measures.Measures().line_height
        self.pitch: float | None = None

    def colour(self, rows: list[Row]) -> str | None:
        """The colour most of these rows' letters are drawn in."""
        return _most((ink.colour for r in rows for ink in _inks(r, self.drawing)), None)

    def gap(self, upper: Row, lower: Row) -> float:
        """The space between two rows beyond their line boxes, in points (CSS margins between their blocks)."""
        return upper.y - lower.y - self.lh * (upper.size + lower.size) / 2

    def margins(self) -> None:
        """Margins from where the letters and rules reach. The right edge is known only from a rule or from text
        set at the right (dates); without either, ragged lines stop short, so the left margin is taken."""
        page_w, page_h = self.drawing.sizes[0]
        edges = [rule.right for rule in self.drawing.rules if rule.right - rule.left >= HALF_PAGE * self.width]
        edges += [r.right for r in self.rows if len(r.parts) >= 2]
        if edges:
            self.right = max([self.right, *edges])
            self.width = self.right - self.left
        self.got["margin_left"] = _mm(self.left)
        self.got["margin_right"] = _mm(page_w - self.right) if edges else min(_mm(page_w - self.right), _mm(self.left))
        first = self.rows[0]
        self.got["margin_top"] = _mm(page_h - first.top - INK_TO_LINE * first.size)
        # the bottom margin shows only on a full page: the lowest letters of every page but the last
        lower = [r.bottom for r in self.rows if r.page < self.pages]
        if lower:
            self.got["margin_bottom"] = _mm(min(lower))
        else:
            self.got["margin_bottom"] = self.got["margin_top"]
            self.notes.append(ONE_PAGE_BOTTOM)
        side = [r for r in self.rows if len([p for p in r.parts if len(p) >= SIDE_TEXT]) >= 2]
        if len(side) >= SIDE_BY_SIDE * len(self.rows):
            self.notes.append(TWO_COLUMNS)
        if self.drawing.pictures:
            self.notes.append(PICTURES)

    def head(self) -> list[Row]:
        """The name (the first row, when larger than the body text) and the contact line (the first of the next
        few rows with an address, a phone number or a link); the rows they took."""
        first = self.rows[0]
        name = first if first.size >= NAME_BIGGER * self.body else None
        self.name = name
        if name is not None:
            self.got["name_size"], self.got["name_bold"] = name.size, name.bold
            middle = (name.left + name.right) / 2
            centred = abs(middle - (self.left + self.right) / 2) <= CENTRED * self.width \
                and name.left - self.left >= INDENTED * self.width
            self.got["head_align"] = "center" if centred else "left"
        after = self.rows[1:] if name is not None else self.rows
        bound = name.size if name else self.body * NAME_BIGGER
        contact = next((r for r in after[:CONTACT_ROWS] if r.size < bound and CONTACT_HINT.search(r.text)), None)
        if contact is not None:
            self.got["contact_size"] = contact.size
            self.got["contact_first"] = contact is after[0]
            sep = _separator(contact)
            if sep:
                self.got["contact_sep"] = sep
            muted = self.colour([contact])
            if muted:
                self.got["muted_colour"] = muted
        return [r for r in (name, contact) if r is not None]

    def headings(self, rest: list[Row]) -> list[Row]:
        """Section headings: short rows with a rule under them, else short capital rows in bold or larger type,
        else the largest short bold rows."""
        ruled = {id(r): rule for r in rest if len(r.text) <= SHORT_HEADING
                 and (rule := _has_rule_under(r, self.drawing.rules, self.width))}
        heads = [r for r in rest if id(r) in ruled]
        if not heads:
            heads = [r for r in rest if len(r.text) <= SHORT_HEADING and r.caps
                     and (r.bold or r.size >= BIGGER * self.body)]
        if not heads:
            big = [r for r in rest if len(r.text) <= SHORT_HEADING and r.bold and r.size >= BIGGER * self.body]
            top_size = max((r.size for r in big), default=0)
            heads = [r for r in big if r.size == top_size]
        if not heads:
            return []
        got = self.got
        got["heading_size"] = statistics.median(r.size for r in heads)
        got["heading_bold"] = sum(r.bold for r in heads) * 2 >= len(heads)
        got["heading_caps"] = sum(r.caps for r in heads) * 2 >= len(heads)
        got["heading_rule"] = len(ruled) * 2 >= len(heads)
        got["heading_spacing"] = _spacing(heads, self.drawing)
        colour = self.colour(heads)
        if colour:
            got["heading_colour"] = colour
        if ruled:
            got["rule_colour"] = _most(rule.colour for rule in ruled.values())
        index = {id(r): i for i, r in enumerate(self.rows)}
        above, below = [], []
        rule_space = measures.RULE_GAP_MM * PT_PER_MM + measures.RULE_PT
        for r in heads:
            i = index[id(r)]
            if i and self.rows[i - 1].page == r.page:
                above.append(self.gap(self.rows[i - 1], r))
            if i + 1 < len(self.rows) and self.rows[i + 1].page == r.page:
                below.append(self.gap(r, self.rows[i + 1]) - (rule_space if id(r) in ruled else 0.0))
        if above:
            got["heading_above"] = max(_mm(statistics.median(above)), 0.0)
        if below:
            got["heading_below"] = max(_mm(statistics.median(below)), 0.0)
        return heads

    def spacing(self, plain: list[Row]) -> None:
        """Line height: the usual step between the rows of one paragraph of body text."""
        self.pitch = _pitch(self.rows, self.body)
        if self.pitch:
            self.lh = round(self.pitch / self.body, 2)
            self.got["line_height"] = self.lh

    def items(self, rest: list[Row]) -> list[Row]:
        """List items: a marker shape just left of the row, or a bullet character first. Their indent is where
        their text starts; their gap is the step between two one-row items beyond the line pitch (the items'
        margins collapse into one)."""
        shapes = pdfread.markers(self.data)
        items = [r for r in rest if r.text[:1] in BULLETS or _marked(r, shapes)]
        if not items:
            return []

        def text_left(r: Row) -> float:
            return r.chars[1].left if r.text[:1] in BULLETS and len(r.chars) > 1 else r.left

        self.got["bullet_indent"] = max(_mm(statistics.median(text_left(r) - self.left for r in items)), 0.0)
        steps = [a.y - b.y for a, b in itertools.pairwise(items)
                 if a.page == b.page and 0 < a.y - b.y <= ONE_ITEM * a.size]
        if steps and self.pitch:
            self.got["bullet_gap"] = max(_mm(min(steps) - self.pitch), 0.0)
        return items

    def entries(self, rest: list[Row], heads: list[Row]) -> None:
        """Entries: a row whose first part is wholly bold is a stacked heading, one that starts bold and goes on
        plain is a one-line entry; the form more rows take wins. Dates are at the right when an entry row has a
        last part with a year or "present" that ends at the right edge."""
        stacked = [r for r in rest if r.heading]
        lines = [r for r in rest if r.lead]
        if not stacked and not lines:
            return
        got = self.got
        got["entry_form"] = "line" if len(lines) > len(stacked) else "stacked"
        shown = lines if got["entry_form"] == "line" else stacked
        got["entry_size"] = statistics.median(r.size for r in shown)
        got["entry_bold"] = True
        dated_right = [r for r in stacked + lines if len(r.parts) >= 2 and DATE_HINT.search(r.parts[-1])
                       and self.right - r.right <= COLUMN_GAP * r.size]
        got["dates"] = "right" if dated_right else "inline"
        starts = {id(r) for r in stacked + lines}
        head_ids = {id(r) for r in heads}
        gaps = [self.gap(self.rows[i - 1], r) for i, r in enumerate(self.rows)
                if i and id(r) in starts and self.rows[i - 1].page == r.page and id(self.rows[i - 1]) not in head_ids]
        if gaps:
            got["entry_gap"] = max(_mm(statistics.median(gaps)), 0.0)

    def colours_and_fonts(self, plain: list[Row], heads: list[Row]) -> None:
        """Text and link colours, whether links are underlined, and the body's and headings' font families."""
        got = self.got
        text_colour = self.colour(plain)
        if text_colour:
            got["text_colour"] = got["link_colour"] = text_colour
        links = pdfread.links(self.data)
        linked = [c for c in self.chars if any(k.page == c.page and k.left <= (c.left + c.right) / 2 <= k.right
                                               and k.bottom <= (c.bottom + c.top) / 2 <= k.top for k in links)]
        if linked:
            colour = _most(ink.colour for c in linked if (ink := self.drawing.inks.get((c.page, c.index))))
            if colour:
                got["link_colour"] = colour
            got["link_underline"] = any(rule.page == c.page and c.left - 1 <= rule.left <= c.right
                                        and c.y - LINK_BELOW * c.points <= rule.top <= c.y + 1
                                        for c in linked for rule in self.drawing.rules)
        family = _family([ink for r in plain for ink in _inks(r, self.drawing)])
        if family is None:
            self.notes.append(UNKNOWN_FONT)
        got["font"] = family or "sans"
        top = ([self.name] if self.name else []) + heads
        got["heading_font"] = _family([ink for r in top for ink in _inks(r, self.drawing)]) or got["font"]


def measure(data: bytes) -> Found:
    """The measures of a PDF's layout; `CannotMatch` when it has no text to measure."""
    reader = _Reader(data)
    reader.margins()
    taken = {id(r) for r in reader.head()}
    rest = [r for r in reader.rows if id(r) not in taken]
    plain = [r for r in rest if r.size == reader.body and not r.bold] or rest
    reader.spacing(plain)
    heads = reader.headings(rest)
    rest = [r for r in rest if id(r) not in {id(h) for h in heads}]
    items = reader.items(rest)
    reader.entries([r for r in rest if id(r) not in {id(i) for i in items}], heads)
    reader.colours_and_fonts(plain, heads)
    return Found(measures.validate(measures.clamped(reader.got)), reader.notes, reader.pages)
