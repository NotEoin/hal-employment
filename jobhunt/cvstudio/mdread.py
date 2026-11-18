"""Reads a Markdown CV into the CV's lines.

Markdown is read line by line with regular expressions, not a Markdown library: a CV uses headings, lists
and bold. `#` is the name, `##` a section, `###` and deeper an entry heading, so the same `Line` styles as a
Word file's come out. Each line's text is its plain words; its bold, italic and links are kept in
`Line.marked` as the marks Hal stores (`cvstudio/inline.py`): `__bold__` and `_italic_` become stars, a link
to anything but a web or email address keeps only its words, and code, pictures and HTML tags keep their
words alone. When Hal's reading of the marks would give other words than the plain reading (Markdown lets a
star mark inside a word; Hal does not), the line keeps no marks rather than different words.
"""
from __future__ import annotations

import re

from jobhunt.cvstudio import inline
from jobhunt.cvstudio.lines import Line, _hash, marked_text

MD_HEADING = re.compile(r"^ {0,3}(?P<marks>#{1,6})\s+(?P<text>.*?)(?:\s+#+)?\s*$")
MD_ITEM = re.compile(r"^(?P<indent>\s*)(?:[-*+]|\d{1,3}[.)])\s+(?P<text>.*)$")
MD_RULE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
MD_SETEXT = re.compile(r"^ {0,3}(?P<marks>=+|-+)\s*$")
MD_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
MD_FENCE = re.compile(r"^ {0,3}(```|~~~)")
MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
MD_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\([^)]*\)")
MD_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)\s]*)(?:\s+\"[^\"]*\")?\)")
MD_AUTOLINK = re.compile(r"<(?P<url>(?:https?://|mailto:)[^>\s]+)>")
MD_TAG = re.compile(r"</?[A-Za-z][^>]*>")
MD_CODE = re.compile(r"`+([^`]*)`+")
MD_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
MD_EMPHASIS = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
MD_LEAD = re.compile(r"^(\*\*|__)(?=\S)(?P<lead>.+?)(?<=\S)\1")
MD_UNDER_STRONG = re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])")
MD_UNDER_EM = re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
ESCAPED = "\ue000"      # a private-use character standing in for each escaped one while markers are stripped
H1, H2 = 1, 2           # "#" is the name and "##" a section; "###" and deeper are entry headings


def _md_plain(text: str) -> str:
    """Markdown inline text as plain words: links as their text, no emphasis markers, escapes undone."""
    kept: list[str] = []

    def hold(m: re.Match) -> str:
        kept.append(m.group(1))
        return ESCAPED
    out = MD_ESCAPE.sub(hold, text)
    out = MD_IMAGE.sub(lambda m: m.group("alt"), out)
    out = MD_LINK.sub(lambda m: m.group("text").strip() or m.group("url"), out)
    out = MD_AUTOLINK.sub(lambda m: m.group("url").removeprefix("mailto:"), out)
    out = MD_TAG.sub(" ", out)
    out = MD_CODE.sub(lambda m: m.group(1), out)
    for _ in range(2):      # bold inside italic and the other way round
        out = MD_STRONG.sub(lambda m: m.group(2), out)
        out = MD_EMPHASIS.sub(lambda m: m.group(1) or m.group(2), out)
    for char in kept:
        out = out.replace(ESCAPED, char, 1)
    return re.sub(r"[ \t]+", " ", out).strip()


def _md_marked(raw: str, text: str) -> str:
    """The Markdown inline text with the marks Hal keeps; "" when it has none or they would change `text`."""
    kept: list[str] = []

    def hold(m: re.Match) -> str:
        kept.append(m.group(1))
        return ESCAPED

    def link(m: re.Match) -> str:
        words = m.group("text").strip() or m.group("url")
        url = inline.safe_link(m.group("url"))
        return f"[{words}]({url})" if url else words
    out = MD_ESCAPE.sub(hold, raw)
    out = MD_IMAGE.sub(lambda m: m.group("alt"), out)
    out = MD_LINK.sub(link, out)
    out = MD_AUTOLINK.sub(lambda m: m.group("url").removeprefix("mailto:"), out)
    out = MD_TAG.sub(" ", out)
    out = MD_CODE.sub(lambda m: m.group(1), out)
    for _ in range(2):      # bold inside italic and the other way round
        out = MD_UNDER_STRONG.sub(lambda m: f"**{m.group(1)}**", out)
        out = MD_UNDER_EM.sub(lambda m: f"*{m.group(1)}*", out)
    for char in kept:
        out = out.replace(ESCAPED, "\\" + char if char in inline.ESCAPABLE else char, 1)
    out = re.sub(r"[ \t]+", " ", out).strip()
    return marked_text(inline.runs(out), text)


def _md_line(raw: str, index: int, *, bullet: bool = False, style: str = "") -> Line | None:
    """One Markdown block as a line, with its bold lead-in kept apart, its markers stripped from its text and
    kept in `marked`."""
    text = _md_plain(raw)
    if not text:
        return None
    m = MD_LEAD.match(raw.strip())
    lead = _md_plain(m.group("lead")) if m else ""
    bold = bool(m) and m.end() == len(raw.strip())
    if style in ("title", "entry"):
        bold, lead = True, text
    marked = "" if bold else _md_marked(raw, text)
    if "\t" not in text and raw.lstrip().startswith("|"):
        cells = [_md_plain(c) for c in raw.strip().strip("|").split("|")]
        text, marked = "\t".join(c for c in cells if c), ""
    return Line(text, bold=bold, bullet=bullet, style=style, index=index, hash=_hash(text),
                lead=text if bold else lead, marked=marked)


def md_lines(text: str) -> list[Line]:
    """A Markdown CV as lines: "#" the name, "##" sections, "###" entry headings, list items as bullets.

    Wrapped lines of a paragraph or list item are joined, as Markdown shows them. Emphasis and link markers
    are taken out of the text, keeping a bold lead-in ("**Security:** ...") in `Line.lead` and the marks in
    `Line.marked`.
    """
    out: list[Line] = []
    block: list[str] = []
    start, item, fenced = 0, False, False

    def flush() -> None:
        nonlocal block, item
        if block:
            line = _md_line(" ".join(block), start, bullet=item)
            if line is not None:
                out.append(line)
        block, item = [], False

    rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if rows and rows[0].strip() == "---":      # front matter, as static site generators write it
        end = next((n for n in range(1, len(rows)) if rows[n].strip() in ("---", "...")), None)
        if end is not None:
            rows = [""] * (end + 1) + rows[end + 1:]
    for n, raw in enumerate(rows):
        if MD_FENCE.match(raw):
            flush()
            fenced = not fenced
            continue
        if fenced:
            line = _md_line(raw, n)
            if line is not None:
                out.append(line)
            continue
        if not raw.strip():
            flush()
            continue
        setext = MD_SETEXT.match(raw)
        if setext and block and not item:
            level = H1 if setext.group("marks")[0] == "=" else H2
            heading, block = " ".join(block), []
            line = _md_line(heading, start, style="title" if level == H1 else "heading 2")
            if line is not None:
                out.append(line)
            continue
        if MD_RULE.match(raw) or (MD_TABLE_RULE.match(raw) and "|" in raw):
            flush()
            continue
        heading = MD_HEADING.match(raw)
        if heading:
            flush()
            level = len(heading.group("marks"))
            style = "title" if level == H1 else "heading 2" if level == H2 else "entry"
            line = _md_line(heading.group("text"), n, style=style)
            if line is not None:
                out.append(line)
            continue
        listed = MD_ITEM.match(raw)
        if listed or raw.lstrip().startswith("|"):
            flush()
            block, start, item = [listed.group("text") if listed else raw], n, bool(listed)
            if not listed:
                flush()
            continue
        hard_break = raw.endswith("  ") or raw.endswith("\\")
        if not block:
            start = n
        block.append(raw.strip().removesuffix("\\").lstrip(">").strip())
        if hard_break:
            flush()
    flush()
    return out
