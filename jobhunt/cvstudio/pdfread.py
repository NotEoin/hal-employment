"""Reads PDFs with pypdfium2: their text, pages, type size, how full the last page is, and page pictures.

Poppler (`pdftotext`, `pdfinfo`, `pdftoppm`) was a separate program to install, and on
Windows a 43 MB bundle under the GPL. pypdfium2 ships as a wheel, so the CV checks, the page pictures and
the record's scans work wherever Hal installs. Poppler stays a developer reference only
(`scripts/dev/compare_pdf_readers.py`).

- `text(pdf, layout=True)` lays each line out by the characters' positions, the way `pdftotext -layout`
  does, so the checks that look for side-by-side columns see the same gaps.
- PDFium is not safe to call from two threads at once (pypdfium2's own advice), so every call holds `_LOCK`.
- A file PDFium cannot open reads as empty (no text, 0 pages), as poppler's failure did, so a broken PDF is
  marked down instead of stopping the checks.
- `page_png` encodes the picture with a few lines of `zlib`, so no imaging library is needed.
- Each character says whether its font is bold or italic, from the font's name first (a browser's subset fonts
  are named like `AAAAAA+Arimo-Bold` and report no useful weight: Chrome's Arimo-Bold reads as weight 380,
  checked 2026-11-18), then its flags and weight. A name that says the face is regular (`-Regular`, `Book`,
  `Roman`, `Light`) is not bold whatever its weight: PDFium reported weight 764 for Hal's own Source Serif 4
  Regular as Chrome prints it, which read a whole page as bold (checked 2026-11-19). A slanted character
  counts as italic too: a browser slants the regular face when a page asks for italic and has no italic font,
  which Hal's own CV fonts do not have (Chrome's slant is a quarter of the height, checked 2026-11-18). Its
  `index` is its place in PDFium's text order, which includes the spaces PDFium works out from the gaps, so a
  reader can ask whether a space lies between two characters instead of guessing from their gap.
"""
from __future__ import annotations

import ctypes
import itertools
import logging
import math
import statistics
import struct
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

log = logging.getLogger(__name__)
_LOCK = threading.Lock()

FOOTER_ZONE = 40.0      # points above the page's foot where Hal's footer sits; not body text
FILL_MARGINS = 80.0     # top and bottom margins together, taken off the page height for "how full"
LINE_TOLERANCE = 0.45   # share of the type size two baselines may differ by and still be one line
SPACE_GAP = 0.6         # a gap up to this share of the type size is an ordinary space between words
WORD_GAP = 0.25         # a gap wider than this share of the type size parts two words, as pdftotext treats it
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FONT_NAME_BYTES = 256   # longer than any PostScript font name (the PDF spec caps names at 127 bytes)
BOLD_WEIGHT = 600       # CSS "semibold" and heavier count as bold
FLAG_ITALIC = 1 << 6    # the font descriptor's Italic flag (PDF 32000-1:2008, table 123, bit 7)
FLAG_FORCE_BOLD = 1 << 18   # the font descriptor's ForceBold flag (same table, bit 19)
# PDFium reports a hyphen drawn at the end of a line as character 2, guessing it may be a soft hyphen; Chrome
# draws the real hyphen of "end-to-end" there when it wraps after it (checked 2026-11-18)
PDFIUM_LINE_END_HYPHEN = 2
MARKER_MIN = 1.0        # points: smaller shapes are hairlines and specks, not list markers
MARKER_MAX = 8.0        # points: a marker is under a type size across; a bigger shape is an icon or a box
MARKER_RATIO = 2.0      # a marker is about as tall as it is wide; a rule under a heading is not
MARKER_DEPTH = 5        # how deep into nested form objects to look for markers
BOLD_NAMES = ("bold", "black", "heavy", "demi", "semibold")
REGULAR_NAMES = ("regular", "book", "roman", "light", "thin", "normal")
ITALIC_NAMES = ("italic", "oblique")
SLANT_MIN = 0.1         # a shear this share of the height or more is a slanted (italic) letter; upright is 0


@dataclass(frozen=True)
class Char:
    """One character as drawn: page (1-based), its box in points from the bottom left, its pen position on
    the baseline (`x`, `y`), its type size, its font's style and its place in PDFium's text order.

    `size` is the size the PDF sets the font in; `scale` is how much the page's transform shrinks or grows
    it (Chrome sets type in CSS pixels and scales the page by 0.75), so `points` is the size as printed.
    """

    char: str
    page: int
    left: float
    bottom: float
    right: float
    top: float
    size: float
    x: float = 0.0
    y: float = 0.0
    bold: bool = False
    italic: bool = False
    index: int = -1
    scale: float = 1.0

    @property
    def points(self) -> float:
        """The type size as printed on the page, in points."""
        return self.size * self.scale


class Unreadable(Exception):
    """PDFium could not open the file."""


def _open(pdf: Path | bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(pdf if isinstance(pdf, bytes) else str(pdf))
    except (pdfium.PdfiumError, OSError) as e:
        raise Unreadable(f"could not read the PDF: {e}") from e


def pages(pdf: Path | bytes) -> int:
    """How many pages the PDF has; 0 when it cannot be read."""
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return 0
        try:
            return len(doc)
        finally:
            doc.close()


def metadata(pdf: Path | bytes) -> dict[str, str]:
    """The PDF's information dictionary without empty values (`Author`, `Title` ...); {} when it cannot be read."""
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return {}
        try:
            return {str(k): str(v) for k, v in doc.get_metadata_dict(skip_empty=True).items()}
        except pdfium.PdfiumError:
            return {}
        finally:
            doc.close()


def first_pages_text(pdf: Path | bytes, limit: int) -> str:
    """The plain text of the first `limit` pages, without layout; "" when the PDF cannot be read."""
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return ""
        try:
            out = []
            for number in range(min(limit, len(doc))):
                page = doc[number]
                found = page.get_textpage()
                out.append(found.get_text_range())
                found.close()
                page.close()
            return "\n\n".join(out)
        except pdfium.PdfiumError:
            return ""
        finally:
            doc.close()


def _style(textpage: pdfium.PdfTextPage, i: int, styles: dict[tuple[str, int, int], tuple[bool, bool]]
           ) -> tuple[bool, bool]:
    """(bold, italic) for character `i`, from its font's name, flags and weight."""
    name = ctypes.create_string_buffer(FONT_NAME_BYTES)
    flags = ctypes.c_int()
    pdfium_c.FPDFText_GetFontInfo(textpage.raw, i, name, FONT_NAME_BYTES, ctypes.byref(flags))
    weight = pdfium_c.FPDFText_GetFontWeight(textpage.raw, i)
    key = (name.value.decode("latin-1"), flags.value, weight)
    if key not in styles:
        # the part after "+" is the real name; the six letters before it only mark a subset
        base = key[0].rsplit("+", 1)[-1].lower()
        named_regular = any(w in base for w in REGULAR_NAMES)
        bold = any(w in base for w in BOLD_NAMES) or (not named_regular and (
            bool(flags.value & FLAG_FORCE_BOLD) or weight >= BOLD_WEIGHT))
        italic = any(w in base for w in ITALIC_NAMES) or bool(flags.value & FLAG_ITALIC)
        styles[key] = (bold, italic)
    return styles[key]


def _transform(textpage: pdfium.PdfTextPage, i: int) -> tuple[float, bool]:
    """(how much the character's transform scales its type size, whether it slants it); (1, False) when
    PDFium cannot say."""
    m = pdfium_c.FS_MATRIX()
    if not pdfium_c.FPDFText_GetMatrix(textpage.raw, i, ctypes.byref(m)):
        return 1.0, False
    slanted = m.b == 0 and m.d != 0 and abs(m.c / m.d) >= SLANT_MIN
    return math.sqrt(abs(m.a * m.d - m.b * m.c)) or 1.0, slanted


def _page_chars(page: pdfium.PdfPage, number: int) -> list[Char]:
    textpage = page.get_textpage()
    styles: dict[tuple[str, int, int], tuple[bool, bool]] = {}
    try:
        out = []
        for i in range(textpage.count_chars()):
            code = pdfium_c.FPDFText_GetUnicode(textpage.raw, i)
            if code in (0, 0xFFFE, 0xFFFF):
                continue
            if code == PDFIUM_LINE_END_HYPHEN:
                code = ord("-")
            left, bottom, right, top = textpage.get_charbox(i)
            x, y = ctypes.c_double(), ctypes.c_double()
            pdfium_c.FPDFText_GetCharOrigin(textpage.raw, i, ctypes.byref(x), ctypes.byref(y))
            bold, italic = _style(textpage, i, styles)
            scale, slanted = _transform(textpage, i)
            out.append(Char(chr(code), number, left, bottom, right, top,
                            float(pdfium_c.FPDFText_GetFontSize(textpage.raw, i)), x.value, y.value,
                            bold, italic or slanted, i, scale))
        return out
    finally:
        textpage.close()


def chars(pdf: Path | bytes) -> list[Char]:
    """Every character drawn, in reading order, with its box and size; [] when the PDF cannot be read."""
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return []
        try:
            return [c for n in range(len(doc)) for c in _page_chars(doc[n], n + 1)]
        finally:
            doc.close()


def page_widths(pdf: Path | bytes) -> list[float]:
    """Each page's width in points, in order; [] when the PDF cannot be read."""
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return []
        try:
            return [float(doc[n].get_width()) for n in range(len(doc))]
        finally:
            doc.close()


@dataclass(frozen=True)
class Link:
    """A link annotation: the box it covers on its page, in points from the bottom left, and its address."""

    page: int
    left: float
    bottom: float
    right: float
    top: float
    url: str


def _uri(doc: pdfium.PdfDocument, link: pdfium_c.FPDF_LINK) -> str:
    """The address a link's action opens, or "" for a link inside the document."""
    action = pdfium_c.FPDFLink_GetAction(link)
    if not action:
        return ""
    size = pdfium_c.FPDFAction_GetURIPath(doc.raw, action, None, 0)
    if size <= 1:
        return ""
    buf = ctypes.create_string_buffer(size)
    pdfium_c.FPDFAction_GetURIPath(doc.raw, action, buf, size)
    return buf.value.decode("utf-8", "replace")


def links(pdf: Path | bytes) -> list[Link]:
    """Every link annotation that opens an address, with the box it covers; [] when the PDF cannot be read."""
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return []
        try:
            out = []
            for n in range(len(doc)):
                page = doc[n]
                pos, link = ctypes.c_int(0), pdfium_c.FPDF_LINK()
                while pdfium_c.FPDFLink_Enumerate(page.raw, ctypes.byref(pos), ctypes.byref(link)):
                    box = pdfium_c.FS_RECTF()
                    url = _uri(doc, link)
                    if url and pdfium_c.FPDFLink_GetAnnotRect(link, ctypes.byref(box)):
                        out.append(Link(n + 1, min(box.left, box.right), min(box.bottom, box.top),
                                        max(box.left, box.right), max(box.bottom, box.top), url))
            return out
        except pdfium.PdfiumError:
            return []
        finally:
            doc.close()


@dataclass(frozen=True)
class Shape:
    """A small drawn shape, such as a list marker a browser draws instead of a bullet character."""

    page: int
    left: float
    bottom: float
    right: float
    top: float


def markers(pdf: Path | bytes) -> list[Shape]:
    """Every small, roughly square drawn shape (a disc, circle or square list marker); [] when unreadable.

    Chrome draws a list's discs as paths of 3 pt at 9 pt type (checked 2026-11-18), so a shape from
    `MARKER_MIN` to `MARKER_MAX` points across, no more than twice as tall as wide, is taken as a marker.
    """
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return []
        try:
            out = []
            for n in range(len(doc)):
                for obj in doc[n].get_objects(max_depth=MARKER_DEPTH):
                    if obj.type != pdfium_c.FPDF_PAGEOBJ_PATH:
                        continue
                    left, bottom, right, top = obj.get_bounds()
                    w, h = right - left, top - bottom
                    if MARKER_MIN <= min(w, h) and max(w, h) <= MARKER_MAX and max(w, h) <= MARKER_RATIO * min(w, h):
                        out.append(Shape(n + 1, left, bottom, right, top))
            return out
        except pdfium.PdfiumError:
            return []
        finally:
            doc.close()


def _words(row: list[Char]) -> list[list[Char]]:
    """A line's characters split into words at spaces and at gaps wider than a quarter of the type size."""
    words: list[list[Char]] = []
    for c in sorted(row, key=lambda c: c.x):
        if c.char.isspace():
            words.append([])
            continue
        if not words or not words[-1] or c.x - words[-1][-1].right > WORD_GAP * max(c.size, 1.0):
            words.append([])
        words[-1].append(c)
    return [w for w in words if w]


def _layout_lines(found: list[Char]) -> list[str]:
    """A page's characters as lines of text, each word placed in the column its position gives."""
    drawn = [c for c in found if not c.char.isspace()]
    if not drawn:
        return []
    rows: list[list[Char]] = []
    for c in sorted(found, key=lambda c: (-c.y, c.x)):
        if rows and abs(rows[-1][0].y - c.y) <= LINE_TOLERANCE * max(c.size, 1.0):
            rows[-1].append(c)
        else:
            rows.append([c])
    lines = [_words(row) for row in rows]
    steps = [b.x - a.x for line in lines for word in line for a, b in itertools.pairwise(word) if b.x > a.x]
    advance = statistics.median(steps) if steps else 1.0
    origin = min(c.x for c in drawn)
    out = []
    for line in lines:
        text, end = "", None
        for word in line:
            column = round((word[0].x - origin) / advance)
            # A gap no wider than a space is one space, whatever the type size; a wider one keeps its column.
            if end is not None and word[0].x - end <= SPACE_GAP * max(word[0].size, 1.0):
                column = len(text) + 1
            text += " " * max(column - len(text), 1 if text else 0) + "".join(c.char for c in word)
            end = word[-1].right
        out.append(text)
    return out


def text(pdf: Path | bytes, layout: bool = False) -> str:
    """The PDF's text, pages separated by form feeds as pdftotext does; "" when it cannot be read.

    With `layout`, each line keeps its characters at their positions, so columns stay apart.
    """
    with _LOCK:
        try:
            doc = _open(pdf)
        except Unreadable:
            return ""
        try:
            out = []
            for n in range(len(doc)):
                page = doc[n]
                if layout:
                    out.append("\n".join(_layout_lines(_page_chars(page, n + 1))) + "\n")
                    continue
                textpage = page.get_textpage()
                try:
                    out.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n") + "\n")
                finally:
                    textpage.close()
            return "\f".join(out)
        finally:
            doc.close()


def body_size(pdf: Path | bytes) -> float | None:
    """The type size most characters are set in, to a tenth of a point; None when there is no text."""
    counts: dict[float, int] = {}
    for c in chars(pdf):
        if not c.char.isspace() and c.size > 0:
            counts[round(c.size, 1)] = counts.get(round(c.size, 1), 0) + 1
    return max(counts, key=counts.get) if counts else None


def _page_height(pdf: Path | bytes, number: int) -> float:
    with _LOCK:
        doc = _open(pdf)
        try:
            return float(doc[number - 1].get_height())
        finally:
            doc.close()


def last_page_fill(pdf: Path | bytes) -> float:
    """How far down the last page the body text reaches, from 0 to 1; the footer in the margin is left out."""
    total = pages(pdf)
    if not total:
        return 0.0
    height = _page_height(pdf, total)
    from_top = [height - c.bottom for c in chars(pdf) if c.page == total and not c.char.isspace()]
    body = [y for y in from_top if y < height - FOOTER_ZONE]
    return max(body or [0.0]) / (height - FILL_MARGINS)


def _png(width: int, height: int, rgb: bytes) -> bytes:
    """An 8-bit RGB PNG of `rgb` (rows top to bottom, no padding)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def page_png(pdf: Path | bytes, number: int, scale: float = 2.0) -> bytes:
    """Page `number` (1-based) as a PNG at `scale` times 72 dots per inch.

    Raises:
        Unreadable: the PDF cannot be opened.
        IndexError: there is no such page.
    """
    with _LOCK:
        doc = _open(pdf)
        try:
            if not 1 <= number <= len(doc):
                raise IndexError(f"the PDF has {len(doc)} pages, not {number}")
            bitmap = doc[number - 1].render(scale=scale, rev_byteorder=True)
            width, height, stride = bitmap.width, bitmap.height, bitmap.stride
            channels = len(bitmap.mode)          # "RGB" or "RGBA", as rev_byteorder asks
            buffer = bytes(bitmap.buffer)
        finally:
            doc.close()
    rows = []
    for y in range(height):
        row = buffer[y * stride:y * stride + width * channels]
        rows.append(row if channels == 3 else b"".join(row[x:x + 3] for x in range(0, len(row), channels)))
    return _png(width, height, b"".join(rows))
