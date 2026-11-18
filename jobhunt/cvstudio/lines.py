"""The `Line` every CV reader produces, and the two helpers each reader uses to make one.

`parse.py` reads a file into `Line`s and then into sections; `pdflines.py`, `mdread.py` and the Word and
pasted-text readers in `parse.py` each make the lines. `Line` lives here so the readers need not import
`parse.py` (which imports them), and so no import cycle forms. Moved out of `parse.py` with no change.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

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
