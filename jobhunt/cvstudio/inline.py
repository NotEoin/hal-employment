"""Bold, italic and links inside a CV line: the one place that reads and writes their marks.

A line keeps its formatting in its own text as Markdown marks, `**bold**`, `*italic*` and
`[words](https://...)`, so no column or migration is needed and a CV exported as `cv.json` keeps them.
Everything else works from what this module gives:

- `runs(text)` splits a line into `Run`s (text, bold, italic, link) for the renderers, and `html(text)`
  writes them as escaped HTML for the preview and the printed PDF;
- `plain(text)` gives the words alone, for every reader that treats a line as words (checks, scores, the
  AI's prompts, the CV choice, spelling, length counts);
- `to_text(runs)` writes runs back as marks that read back the same, and `keep_marks(old, new)` carries a
  line's marks onto new words, keeping only those whose words are still there.

Design decisions, each with its reason:
- A pair of marks counts only when it hugs words: the opening mark has the start of the line, a space or
  punctuation before it and no space after it, and the closing mark the other way round. A star touching a
  letter or digit on its outer side is never a mark, so text written before formatting existed reads as it
  did: "C*", "A*A*A", "D*D*D*", "5* rating", "2*3*4" and a lone "*Subject to approval" stay as typed. This
  is stricter than CommonMark, which lets a star mark inside a word ("un*frigging*believable").
- Underscores never mark anything in a stored line, so "snake_case" and "__init__" stay as typed. The
  Markdown reader turns a file's `_italic_` and `__bold__` into stars when it reads the file.
- A pair never spans a line break: text with several lines is read one line at a time.
- Only http, https and mailto links count. Any other `[words](address)` stays text, so a page never gets a
  `javascript:` link from a CV.
- Pairs nest but never cross: a closing run pairs with the nearest opening run of the same count (or of
  three stars, bold and italic together), and opening runs left inside it stay text, so of two crossing
  pairs the one opened later shows its stars ("**a *b** c*" is bold "a *b" then " c*").
- `\\*`, `\\[` and `\\]` write a star or a bracket that is never a mark. `to_text` adds them only when the
  text would otherwise read back differently, so a hand-typed "C*" is never rewritten as "C\\*".
- A style change inside a word ("Py**thon**") cannot be written under these rules; `to_text` drops that
  formatting rather than store a pair that would read back as stars.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field, replace

STAR = "*"
ESCAPABLE = "*[]"
MAX_STARS = 3            # bold and italic together; a longer run ("*****" as a rating) is always text
SCHEMES = ("http://", "https://", "mailto:")
MARK_CHARS = frozenset("*[\\")
# a control character in an address made the Word file's XML invalid, so Word refused it (review, 2026-11-18)
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Run:
    """A stretch of a line in one style: its words, bold, italic, and the address it links to ("" for none)."""

    text: str
    bold: bool = False
    italic: bool = False
    link: str = ""


@dataclass
class _Tok:
    """A piece of a line while it is read: text, a run of stars that may be a mark, or a whole link."""

    kind: str                       # "text", "stars" or "link"
    text: str = ""                  # the words, or the stars not yet used as marks
    opens: bool = False
    closes: bool = False
    inner: list[Run] = field(default_factory=list)
    url: str = ""
    bold: bool = False
    italic: bool = False


def safe_link(url: str) -> str:
    """The address when it is an http, https or mailto link with no spaces or control characters, else ""."""
    if CONTROL.search(url or ""):
        return ""
    url = (url or "").strip()
    if not url or any(ch.isspace() for ch in url) or not url.lower().startswith(SCHEMES):
        return ""
    return url


def _closing(s: str, i: int, open_ch: str, close_ch: str) -> int:
    """The index of the bracket that closes the one at `i`, counting nested pairs and skipping escapes; -1."""
    depth = 0
    k = i
    while k < len(s):
        ch = s[k]
        if ch == "\\" and k + 1 < len(s) and s[k + 1] in ESCAPABLE:
            k += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return -1


def _link_at(s: str, i: int) -> tuple[str, str, int] | None:
    """(the link's words as written, its address, the index after it) for a link starting at `i`, or None."""
    end = _closing(s, i, "[", "]")
    if end < 0 or end + 1 >= len(s) or s[end + 1] != "(":
        return None
    stop = _closing(s, end + 1, "(", ")")
    if stop < 0:
        return None
    words, url = s[i + 1:end], safe_link(s[end + 2:stop])
    if not url or not words.strip() or url != s[end + 2:stop]:
        return None
    return words, url, stop + 1


def _tokens(s: str, links: bool) -> list[_Tok]:
    toks: list[_Tok] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            toks.append(_Tok("text", "".join(buf)))
            buf.clear()

    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n and s[i + 1] in ESCAPABLE:
            buf.append(s[i + 1])
            i += 2
            continue
        if ch == STAR:
            j = i
            while j < n and s[j] == STAR:
                j += 1
            before = s[i - 1] if i else ""
            after = s[j] if j < n else ""
            opens = closes = False
            if j - i <= MAX_STARS:
                opens = bool(after) and not after.isspace() and not before.isalnum()
                closes = bool(before) and not before.isspace() and not after.isalnum()
            if opens or closes:
                flush()
                toks.append(_Tok("stars", s[i:j], opens, closes))
            else:
                buf.append(s[i:j])
            i = j
            continue
        if ch == "[" and links:
            found = _link_at(s, i)
            if found is not None:
                flush()
                words, url, i = found
                toks.append(_Tok("link", inner=_line_runs(words, links=False), url=url))
                continue
        buf.append(ch)
        i += 1
    flush()
    return toks


def _fits(opener: str, closer: str) -> bool:
    """True when an opening and a closing star run can pair: the same count, or either one three stars."""
    return len(opener) == len(closer) or MAX_STARS in (len(opener), len(closer))


def _pair_up(toks: list[_Tok]) -> None:
    """Matches closing star runs with the nearest opening ones that fit and marks what lies between as bold or
    italic. Opening runs left between a pair stay text, so of two crossing pairs ("**a *b** c*") the one
    opened later is shown as stars."""
    for j, closer in enumerate(toks):
        if closer.kind != "stars" or not closer.closes:
            continue
        while closer.text:
            k = next((k for k in range(j - 1, -1, -1)
                      if toks[k].kind == "stars" and toks[k].opens and toks[k].text
                      and _fits(toks[k].text, closer.text)), None)
            if k is None:
                break
            opener = toks[k]
            use = 2 if len(closer.text) >= 2 and len(opener.text) >= 2 else 1
            for t in toks[k + 1:j]:
                if use == 2:
                    t.bold = True
                else:
                    t.italic = True
                if t.kind == "stars":
                    t.opens = t.closes = False
            opener.text = opener.text[:-use]
            closer.text = closer.text[use:]


def _merge(rs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in rs:
        if not r.text:
            continue
        if out and (out[-1].bold, out[-1].italic, out[-1].link) == (r.bold, r.italic, r.link):
            out[-1] = replace(out[-1], text=out[-1].text + r.text)
        else:
            out.append(r)
    return out


def _line_runs(s: str, *, links: bool = True) -> list[Run]:
    toks = _tokens(s, links)
    _pair_up(toks)
    out: list[Run] = []
    for t in toks:
        if t.kind == "link":
            out += [Run(r.text, r.bold or t.bold, r.italic or t.italic, t.url) for r in t.inner]
        else:
            out.append(Run(t.text, t.bold, t.italic))
    return _merge(out)


def runs(text: str) -> list[Run]:
    """The line as runs of one style each, in order; a line break is a plain run of its own."""
    out: list[Run] = []
    for n, line in enumerate((text or "").split("\n")):
        if n:
            out.append(Run("\n"))
        out += _line_runs(line) if MARK_CHARS & set(line) else ([Run(line)] if line else [])
    return _merge(out) if len(out) > 1 else out


def plain(text: str) -> str:
    """The line's words without any marks: a link gives its words, never its address."""
    if not text or not MARK_CHARS & set(text):
        return text or ""
    return "".join(r.text for r in runs(text))


def has_marks(text: str) -> bool:
    """True when the line holds bold, italic or a link."""
    return any(r.bold or r.italic or r.link for r in runs(text))


# ------------------------------------------------------------------------------------------- writing marks

def _tidy(rs: list[Run]) -> list[Run]:
    """Runs merged, with the spaces at either end of a bold, italic or linked stretch moved outside it, as
    marks need; spaces inside a stretch stay in it. A link's brackets end a bold or italic stretch too."""
    chars = [[ch, r.bold, r.italic, r.link] for r in rs for ch in r.text]
    for slot, off in ((3, ""), (1, False), (2, False)):
        k = 0
        while k < len(chars):
            if chars[k][slot] == off:
                k += 1
                continue
            end = k
            while end < len(chars) and (chars[end][slot], chars[end][3]) == (chars[k][slot], chars[k][3]):
                end += 1
            for ends in (range(k, end), range(end - 1, k - 1, -1)):
                for m in ends:
                    if not chars[m][0].isspace():
                        break
                    chars[m][slot] = off
            k = end
    return _merge([Run(ch, b, i, link) for ch, b, i, link in chars])


def _escaped(text: str, escape: bool) -> str:
    return "".join("\\" + ch if ch in ESCAPABLE else ch for ch in text) if escape else text


def _write_styled(rs: list[Run], escape: bool) -> str:
    """Runs that share one link as text with bold and italic marks, closing and reopening as styles change."""
    marks = {"b": "**", "i": "*"}
    out: list[str] = []
    stack: list[str] = []
    for r in [*rs, Run("")]:
        want = {x for x, on in (("b", r.bold), ("i", r.italic)) if on}
        keep = next((n for n, x in enumerate(stack) if x not in want), len(stack))
        while len(stack) > keep:
            out.append(marks[stack.pop()])
        for x in ("b", "i"):
            if x in want and x not in stack:
                stack.append(x)
                out.append(marks[x])
        out.append(_escaped(r.text, escape))
    return "".join(out)


def _write(rs: list[Run], escape: bool) -> str:
    out: list[str] = []
    i = 0
    while i < len(rs):
        j = i
        while j < len(rs) and rs[j].link == rs[i].link:
            j += 1
        words = _write_styled(rs[i:j], escape)
        out.append(f"[{words}]({rs[i].link})" if rs[i].link else words)
        i = j
    return "".join(out)


def to_text(rs: list[Run]) -> str:
    """Runs written as a line with marks that `runs` reads back the same; formatting that cannot be written
    that way (a style change inside a word) is dropped, never stored as stray stars."""
    want = _tidy(rs)
    tries = (want, want, _tidy([Run(r.text, link=r.link) for r in want]), _tidy([Run(r.text) for r in want]))
    for n, attempt in enumerate(tries):
        out = _write(attempt, escape=n > 0)
        if _tidy(runs(out)) == attempt:
            return out
    return _escaped("".join(r.text for r in want), True)


def keep_marks(old: str, new: str) -> str:
    """`new` (plain words) with each bold, italic or link of `old` whose words are still there unchanged."""
    if not has_marks(old) or not new:
        return to_text([Run(new)]) if new else ""
    bold = [False] * len(new)
    italic = [False] * len(new)
    link = [""] * len(new)
    used: set[int] = set()
    for r in _tidy(runs(old)):
        if not (r.bold or r.italic or r.link):
            continue
        at = _find_words(new, r.text, used)
        if at < 0:
            continue
        used.add(at)
        for k in range(at, at + len(r.text)):
            bold[k] = bold[k] or r.bold
            italic[k] = italic[k] or r.italic
            link[k] = link[k] or r.link
    return to_text(_merge([Run(ch, bold[k], italic[k], link[k]) for k, ch in enumerate(new)]))


def _find_words(text: str, words: str, used: set[int]) -> int:
    """Where `words` stand in `text` as whole words, at a place not used yet; -1 when they are not there."""
    start = 0
    while True:
        at = text.find(words, start)
        if at < 0:
            return -1
        before = text[at - 1] if at else ""
        after = text[at + len(words)] if at + len(words) < len(text) else ""
        if at not in used and not before.isalnum() and not after.isalnum():
            return at
        start = at + 1


# ------------------------------------------------------------------------------------------------- HTML

def html(text: str) -> str:
    """The line as HTML: its words escaped, `<strong>`, `<em>` and `<a href>` for its marks."""
    out = []
    for r in runs(text):
        part = _html.escape(r.text, quote=True)
        if r.italic:
            part = f"<em>{part}</em>"
        if r.bold:
            part = f"<strong>{part}</strong>"
        if r.link:
            part = (f'<a href="{_html.escape(r.link, quote=True)}" target="_blank" '
                    f'rel="noopener noreferrer">{part}</a>')
        out.append(part)
    return "".join(out)
