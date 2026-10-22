"""Draws CVs and letters as PDFs by hand, against the base-14 fonts every reader has built in.

Hal's own fallback PDF writer, so no other repository is needed to make a CV. It keeps the
guarantees of the builder it was adapted from: no dependencies, nothing embedded, text in WinAnsi (ASCII plus
the Latin-1 letters, so accented names survive) and real spaces between words, so applicant tracking systems
extract the text cleanly. The name, email and phone come from the `you.*` settings, read when drawn. Font
metrics come from the URW Nimbus Sans files when installed (Linux), else from the Helvetica width table
below, which is identical; on Windows the table is always used.

Helvetica widths: Adobe Core14 AFM files, Copyright (c) 1985, 1987, 1989, 1990, 1997 Adobe Systems
Incorporated. All rights reserved. Adobe permits the AFM files to be used, copied and distributed for any
purpose and without charge, with or without modification, provided the copyright notices are retained.
Only the width numbers are copied here.

Callers set `OUT` to the file to write before calling `write_pdf`.
"""
import re
import zlib
from pathlib import Path

OUT = Path("CV.pdf")
IDENTITY = {"NAME": "you.name", "EMAIL": "you.email", "PHONE": "you.phone"}


def __getattr__(name: str) -> str:
    """`NAME`, `EMAIL` and `PHONE`, read from the settings when used, so a changed setting needs no restart."""
    if name in IDENTITY:
        from .core import settings

        return str(settings.get(IDENTITY[name]) or "")
    raise AttributeError(name)


def _me(name: str) -> str:
    """An identity value: one a caller or test assigned to the module, else the setting."""
    return globals()[name] if name in globals() else __getattr__(name)


# ------------------------------------------------------------ page setup
PW, PH = 595.276, 841.890          # A4
ML, MR = 52.0, 52.0
MT, MB = 44.0, 38.0
CW = PW - ML - MR                  # content width
COL_GAP = 26.0
COL_W = (CW - COL_GAP) / 2

# ----------------------------------------------------------- typography
F_REG, F_BOLD, F_ITAL = "F1", "F2", "F3"

NAME_SIZE = 20.0
CONTACT_SIZE = 8.6
SECTION_SIZE = 8.4
HEAD_SIZE = 10.2
META_SIZE = 8.6
BODY_SIZE = 9.2
BODY_LEAD = 11.6
MODULE_SIZE = 8.8
MODULE_ROW = 15.4      # one module: name, then its bar, then the gap
MODULE_INDENT = 14.0   # the transcript hangs under its own degree, not loose

INK = 0.10          # headings
BODY_INK = 0.22     # running text
META_INK = 0.42     # dates, stacks, the contact block
RULE = 0.78         # hairlines
BAR = 0.30          # the filled part of a module bar

# ------------------------------------------------------------- metrics
AFM_DIR = Path("/usr/share/fonts/type1/urw-base35")
AFM_FILES = {
    F_REG: "NimbusSans-Regular.afm",
    F_BOLD: "NimbusSans-Bold.afm",
    F_ITAL: "NimbusSans-Italic.afm",
}

# ASCII 32-126 by PostScript glyph name, so a width lookup is exact rather
# than dependent on which encoding vector the AFM happens to use.
GLYPHS = (
    "space exclam quotedbl numbersign dollar percent ampersand quotesingle "
    "parenleft parenright asterisk plus comma hyphen period slash "
    "zero one two three four five six seven eight nine "
    "colon semicolon less equal greater question at "
    "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z "
    "bracketleft backslash bracketright asciicircum underscore grave "
    "a b c d e f g h i j k l m n o p q r s t u v w x y z "
    "braceleft bar braceright asciitilde"
).split()
assert len(GLYPHS) == 95

# Helvetica widths for ASCII 32-126, per Adobe's AFM. The fallback when the
# URW metrics are not installed; identical numbers, just frozen here.
FALLBACK = {
    F_REG: [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
            556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
            1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
            667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
            333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
            556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584],
    F_BOLD: [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
             556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
             975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
             667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
             333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
             611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584],
}
FALLBACK[F_ITAL] = FALLBACK[F_REG]


def load_widths():
    """{font: [width per ASCII 32-126]} in 1/1000 em."""
    widths = {}
    for font, filename in AFM_FILES.items():
        path = AFM_DIR / filename
        if not path.exists():
            widths[font] = list(FALLBACK[font])
            continue
        by_name = {}
        for line in path.read_text(errors="replace").splitlines():
            if not line.startswith("C "):
                continue
            wx = name = None
            for field in line.split(";"):
                field = field.strip()
                if field.startswith("WX "):
                    wx = float(field[3:])
                elif field.startswith("N "):
                    name = field[2:].strip()
            if name is not None and wx is not None:
                by_name[name] = wx
        widths[font] = [by_name.get(g, FALLBACK[font][i]) for i, g in enumerate(GLYPHS)]
    return widths


WIDTHS = load_widths()


def _base(ch):
    """An accented Latin-1 letter's unaccented form, whose width it shares closely enough to set text."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", ch)[:1]
    return plain if " " <= plain <= "~" else "o"


def measure(text, font, size, tracking=0.0):
    table = WIDTHS[font]
    total = 0.0
    for ch in text:
        i = ord(ch) - 32
        if i >= 95:
            i = ord(_base(ch)) - 32
        total += table[i] if 0 <= i < 95 else table[0]
    return total * size / 1000.0 + tracking * len(text)


# ---------------------------------------------------------- inline runs
BOLD_SPLIT = re.compile(r"(\*\*.+?\*\*)", re.S)


def runs_of(text):
    """'plain **bold** plain' -> [(font, 'plain '), (F_BOLD, 'bold'), ...]"""
    out = []
    for part in BOLD_SPLIT.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((F_BOLD, part[2:-2]))
        else:
            out.append((F_REG, part))
    return out


def wrap(text, width, size):
    """Greedy wrap over bold-aware runs. Returns lines of [(font, word)]."""
    words = []
    for font, chunk in runs_of(text):
        for word in chunk.split(" "):
            if word == "":
                continue
            words.append((font, word))
    lines, line, used = [], [], 0.0
    for font, word in words:
        w = measure(word, font, size)
        if w > width:
            # one token wider than the whole column: break it, rather than
            # let it run off the edge of the page
            if line:
                lines.append(line)
                line, used = [], 0.0
            piece = ""
            for ch in word:
                if piece and measure(piece + ch, font, size) > width:
                    lines.append([(font, piece)])
                    piece = ch
                else:
                    piece += ch
            if piece:
                line, used = [(font, piece)], measure(piece, font, size)
            continue
        space = measure(" ", font, size) if line else 0.0
        if line and used + space + w > width:
            lines.append(line)
            line, used = [(font, word)], w
        else:
            line.append((font, word))
            used += space + w
    if line:
        lines.append(line)
    return lines


def esc(text):
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def text_op(s, x, y, font, size, ink=BODY_INK, tracking=0.0):
    """One positioned show-text operator.

    Tc belongs to the text state, which BT/ET does not reset - so it is
    written on every run, or tracking from one heading silently spaces out
    the rest of the document."""
    return (
        f"BT {ink:.3f} g {tracking} Tc /{font} {size} Tf "
        f"1 0 0 1 {x:.2f} {y:.2f} Tm ({esc(s)}) Tj ET"
    )


# ------------------------------------------------------------- document
class Doc:
    def __init__(self):
        self.pages = []
        self.ops = []
        self.links = []          # (page index, x0, y0, x1, y1, url)
        self.y = 0.0
        self._new_page()

    def _new_page(self):
        if self.ops:
            self.pages.append(self.ops)
        self.ops = []
        self.y = PH - MT

    def need(self, height):
        """Break the page if `height` will not fit below the cursor."""
        if self.y - height < MB:
            self._new_page()
            return True
        return False

    def finish(self):
        self.pages.append(self.ops)
        return self.pages

    # -- primitives ------------------------------------------------------
    def text(self, s, x, y, font, size, ink=BODY_INK, tracking=0.0):
        if not s:
            return
        self.ops.append(text_op(s, x, y, font, size, ink, tracking))

    def rule(self, x, y, width, ink=RULE, thickness=0.5):
        self.ops.append(
            f"{ink:.3f} G {thickness} w {x:.2f} {y:.2f} m {x + width:.2f} {y:.2f} l S"
        )

    def line_runs(self, line, x, y, size, pad=False):
        """Draw one wrapped line as contiguous same-font runs, joined with
        real space characters.

        Drawing it word by word - one text object each, advancing by the
        width of a space without ever emitting one - looks identical on the
        page, because the text matrix puts every word in the right place.
        But a reader that simply concatenates the (...) Tj literals in
        stream order, which is what a plain-text CV parser does, then gets
        `Upper-secondHistorygraduate...` and fails silently. Helvetica
        and Helvetica-Bold share a space width, so the geometry is unchanged.
        """
        cursor, i = x, 0
        while i < len(line):
            font = line[i][0]
            j = i
            words = []
            while j < len(line) and line[j][0] == font:
                words.append(line[j][1])
                j += 1
            run = " ".join(words)
            if i:
                run = " " + run      # the gap to the previous run
            if pad and j == len(line):
                run += " "           # the gap to the line below
            self.text(run, cursor, y, font, size)
            cursor += measure(run, font, size)
            i = j

    # -- blocks ----------------------------------------------------------
    def paragraph(self, text, x=ML, width=CW, size=BODY_SIZE, lead=BODY_LEAD, gap=0.0):
        lines = wrap(text, width, size)
        self.need(len(lines) * lead)
        for line in lines:
            self.y -= size
            self.line_runs(line, x, self.y, size, pad=True)
            self.y -= lead - size
        self.y -= gap

    def height_of(self, text, width, size=BODY_SIZE, lead=BODY_LEAD):
        return len(wrap(text, width, size)) * lead

    def section(self, title):
        # reserving only the heading lets one strand at the foot of a page
        # with its first entry overleaf. Derived, not hand-counted, so it
        # cannot go stale the next time the typography is tightened.
        self.need(26 + HEAD_SIZE + 3 + META_SIZE + 4 + 1 + BODY_LEAD)
        self.y -= 11
        self.text(title.upper() + " ", ML, self.y, F_BOLD, SECTION_SIZE, INK, tracking=1.5)
        self.y -= 5
        self.rule(ML, self.y, CW, ink=0.55, thickness=0.7)
        self.y -= 9

    def heading(self, text, size=HEAD_SIZE):
        self.y -= size
        self.text(text + " ", ML, self.y, F_BOLD, size, INK)
        self.y -= 3

    def meta(self, text):
        self.y -= META_SIZE
        self.text(text + " ", ML, self.y, F_REG, META_SIZE, META_INK)
        self.y -= 4

    def link_line(self, segments, x, size):
        """A run of text where some segments are clickable. Rectangles are
        recorded against the page being built and become /Link annotations."""
        self.y -= size
        cursor = x
        for n, (text, url) in enumerate(segments):
            # pad the last segment so it cannot merge into the line below;
            # the rect and the advance stay measured on the real text
            drawn = text + " " if n == len(segments) - 1 else text
            self.text(drawn, cursor, self.y, F_REG, size, META_INK)
            width = measure(text, F_REG, size)
            if url:
                self.links.append(
                    (len(self.pages), cursor, self.y - 2.0,
                     cursor + width, self.y + size * 0.78, url)
                )
            cursor += width

    def entry(self, title, meta, body, size=HEAD_SIZE, grade=None):
        """A titled entry that never splits: no heading stranded at a page
        foot with its prose on the next page. `grade` is appended to the
        heading rather than set apart, so it extracts as one line for an ATS."""
        head = f"{title}, {grade}" if grade else title
        self.need(size + 3 + META_SIZE + 4 + 1 + self.height_of(body, CW))
        self.heading(head, size=size)
        self.meta(meta)
        self.y -= 1
        self.paragraph(body)


def check_ascii(node, path: str = "cv.json") -> None:
    """Refuses text the fonts cannot draw: anything outside printable ASCII and the Latin-1 letters."""
    if isinstance(node, str):
        for i, ch in enumerate(node):
            if not (32 <= ord(ch) <= 126 or 160 <= ord(ch) <= 255):
                raise ValueError(
                    f"non-ASCII character {ch!r} (U+{ord(ch):04X}) at {path}, offset {i}:\n"
                    f"  {node[max(0, i - 40):i + 40]}"
                )
    elif isinstance(node, list):
        for i, item in enumerate(node):
            check_ascii(item, f"{path}[{i}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("_"):
                continue
            check_ascii(value, f"{path}.{key}")


# ------------------------------------------------------------ pdf output
def add_footers(pages):
    """Name and page number in the bottom margin, so a page that gets
    separated from the rest still identifies itself. Page 1 carries the name
    at full size already, so it gets the number alone. Drawn after layout,
    inside the margin, so this costs no content height."""
    total = len(pages)
    for i, ops in enumerate(pages):
        if i:
            ops.append(text_op(f"{_me('NAME')} - CV ", ML, 22.0, F_REG, 7.6, 0.55))
        label = f"Page {i + 1} of {total}"
        ops.append(
            text_op(label, PW - MR - measure(label, F_REG, 7.6), 22.0,
                    F_REG, 7.6, 0.55)
        )


def write_pdf(pages, links=(), title="CV"):
    """Object ids are allocated in sequence rather than hardcoded, so adding
    a font or an annotation cannot silently collide with a page."""
    objects = {}
    counter = [0]

    def alloc():
        counter[0] += 1
        return counter[0]

    catalog_id, pages_id, res_id = alloc(), alloc(), alloc()
    font_ids = {name: alloc() for name in (F_REG, F_BOLD, F_ITAL)}
    base = {F_REG: "Helvetica", F_BOLD: "Helvetica-Bold", F_ITAL: "Helvetica-Oblique"}

    page_ids, content_ids = [], []
    for _ in pages:
        page_ids.append(alloc())
        content_ids.append(alloc())

    annots_by_page = {}
    for page_i, x0, y0, x1, y1, url in links:
        aid = alloc()
        objects[aid] = (
            f"<< /Type /Annot /Subtype /Link "
            f"/Rect [{x0:.2f} {y0:.2f} {x1:.2f} {y1:.2f}] /Border [0 0 0] "
            f"/A << /S /URI /URI ({esc(url)}) >> >>"
        ).encode()
        annots_by_page.setdefault(page_i, []).append(aid)

    objects[catalog_id] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id] = (
        f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>"
    ).encode()
    objects[res_id] = (
        "<< /Font << "
        + " ".join(f"/{name} {oid} 0 R" for name, oid in font_ids.items())
        + " >> >>"
    ).encode()
    for name, oid in font_ids.items():
        objects[oid] = (
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{base[name]} "
            f"/Encoding /WinAnsiEncoding >>"
        ).encode()

    for i, ops in enumerate(pages):
        # WinAnsiEncoding draws Latin-1's accented letters at the same codes, so Latin-1 bytes are exact.
        stream = zlib.compress("\n".join(ops).encode("latin-1", errors="replace"))
        annots = annots_by_page.get(i)
        annot_ref = (
            " /Annots [" + " ".join(f"{a} 0 R" for a in annots) + "]" if annots else ""
        )
        objects[page_ids[i]] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /Resources {res_id} 0 R "
            f"/MediaBox [0 0 {PW:.3f} {PH:.3f}] "
            f"/Contents {content_ids[i]} 0 R{annot_ref} >>"
        ).encode()
        objects[content_ids[i]] = (
            f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )

    info_id = alloc()
    objects[info_id] = (
        f"<< /Title ({esc(_plain(_me('NAME')))} - {esc(title)}) /Author ({esc(_plain(_me('NAME')))}) "
        f"/Subject (Curriculum vitae) /Creator (Hal Employment) >>"
    ).encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for oid in sorted(objects):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode() + objects[oid] + b"\nendobj\n"

    xref_at = len(out)
    count = max(objects) + 1
    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for oid in range(1, count):
        out += f"{offsets[oid]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {count} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()

    OUT.write_bytes(bytes(out))




def _plain(text):
    """Text for the PDF's information block, which is ASCII here."""
    return "".join(c if " " <= c <= "~" else _base(c) for c in text)


def contact_line(contact):
    """The contact line's segments: email, phone, then whichever profile links the applicant gave."""
    email, phone = _me("EMAIL"), _me("PHONE")
    parts = [(email, f"mailto:{email}")] if email else []
    if phone:
        parts.append((phone, None))
    for key in ("linkedin", "site", "github"):
        value = (contact or {}).get(key)
        if value:
            parts.append((value, value if value.startswith("http") else f"https://{value}"))
    out = []
    for i, part in enumerate(parts):
        if i:
            out.append((" | ", None))
        out.append(part)
    return out
