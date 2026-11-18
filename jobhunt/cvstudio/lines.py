"""The `Line` every CV reader produces, and the two helpers each reader uses to make one.

`parse.py` reads a file into `Line`s and then into sections; `pdflines.py`, `mdread.py` and the Word and
pasted-text readers in `parse.py` each make the lines. `Line` lives here so the readers need not import
`parse.py` (which imports them), and so no import cycle forms. Moved out of `parse.py` with no change.

A line also keeps what the file showed inside it as marks (`marked`, see `cvstudio/inline.py`): bold and
italic words and links. A style the whole line shares is structure, not a mark (a bold entry heading, an
italic dates line), so `marked_text` leaves it out.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from jobhunt.cvstudio import inline

BULLET_GLYPHS = ("\u2022", "\u25cf", "\u25aa", "\u25e6", "\u2023", "\u2043", "\u00b7", "\uf0b7", "\u27a2", "\u25ba")


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
    marked: str = ""        # the text with its bold, italic and links as marks; "" when it has none


def marked_text(runs: list[inline.Run], text: str) -> str:
    """`text` with the marks of `runs` (read from the file, the same words), leaving out a style the whole
    line shares; "" when nothing is left to mark or the runs do not hold exactly `text`."""
    if "".join(r.text for r in runs) != text:
        return ""
    shown = [r for r in runs if r.text.strip()]
    bold = bool(shown) and all(r.bold for r in shown)
    italic = bool(shown) and all(r.italic for r in shown)
    kept = [inline.Run(r.text, r.bold and not bold, r.italic and not italic, inline.safe_link(r.link)) for r in runs]
    if not any(r.bold or r.italic or r.link for r in kept):
        return ""
    out = inline.to_text(kept)
    return out if inline.has_marks(out) else ""


def slice_runs(runs: list[inline.Run], start: int, end: int) -> list[inline.Run]:
    """The runs that cover characters `start` to `end` of their joined text, cut to fit."""
    out, at = [], 0
    for r in runs:
        lo, hi = max(start, at), min(end, at + len(r.text))
        if lo < hi:
            out.append(inline.Run(r.text[lo - at:hi - at], r.bold, r.italic, r.link))
        at += len(r.text)
    return out


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
