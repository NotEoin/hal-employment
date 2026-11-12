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
"""
from __future__ import annotations

import ctypes
import itertools
import logging
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


@dataclass(frozen=True)
class Char:
    """One character as drawn: page (1-based), its box in points from the bottom left, its pen position on
    the baseline (`x`, `y`) and its type size."""

    char: str
    page: int
    left: float
    bottom: float
    right: float
    top: float
    size: float
    x: float = 0.0
    y: float = 0.0


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


def _page_chars(page: pdfium.PdfPage, number: int) -> list[Char]:
    textpage = page.get_textpage()
    try:
        out = []
        for i in range(textpage.count_chars()):
            code = pdfium_c.FPDFText_GetUnicode(textpage.raw, i)
            if code in (0, 0xFFFE, 0xFFFF):
                continue
            left, bottom, right, top = textpage.get_charbox(i)
            x, y = ctypes.c_double(), ctypes.c_double()
            pdfium_c.FPDFText_GetCharOrigin(textpage.raw, i, ctypes.byref(x), ctypes.byref(y))
            out.append(Char(chr(code), number, left, bottom, right, top,
                            float(pdfium_c.FPDFText_GetFontSize(textpage.raw, i)), x.value, y.value))
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
