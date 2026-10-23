"""The words test: words on screen use the app's plain vocabulary, never code words.

It reads every string literal and template text in `jobhunt/web/static/app/**/*.js` (not the vendored
`lib/`), keeps the ones that read as words for people, and looks for banned words. Exempt are:

- the literal right after a `/* details */` comment, and everything inside it (Advanced and Details panels);
- addresses such as `opera://extensions`;
- in the browser helper's setup step, words in double quotes (the browser's own button names);
- in "Use Hal from another computer", the terminal words that page is allowed.

Every file must pass (the test is strict).
"""
from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "jobhunt" / "web" / "static" / "app"
DETAILS = "/* details */"
HELPER_STEP = "setup/steps/helper.js"
REMOTE_PAGE = "settings/remote.js"

# (pattern, what to say instead), from the vocabulary table. Matched case-insensitively on words for people.
BANNED = [
    (r"\bpacks?\b", "documents for this job"),
    (r"\bbaseline\b", "your main CV"),
    (r"\brecipes?\b|\bCV styles?\b", "CV type"),
    (r"\blayouts?\b|\btemplates?\b", "look"),
    (r"\btailor(ing|ed)?\b", "adjust for each job"),
    (r"\bre-?scor(e|ed|ing)\b", "re-check jobs"),
    (r"\bgoal brief\b|\bpreferences\b", "your goal"),
    (r"\bsite memory\b|\brules?\b", "notes about job sites, note"),
    (r"\bATS\b|\bplatforms?\b", "application system"),
    (r"\bSTAR\b", "your examples"),
    (r"\bhealth check\b|\bdoctor\b", "Is Hal working?"),
    (r"\bscreening stages?\b", "how Hal picks jobs"),
    (r"\bvaults?\b", "your record"),
    (r"\bmodels?\b|\bGPUs?\b|\bOllama\b|\bLLMs?\b", "AI, graphics card, \"on this computer\""),
    (r"\bextensions?\b", "browser helper"),
    (r"\binbox\b", "Messages"),
    (r"\bstudents?-only\b|\beligib(le|ility)\b", "\"For current students only\""),
    (r"\bupdate (token|key)\b|\bPAT\b|\breleases?\b", "\"GitHub sign-in\", \"Update Hal\""),
    (r"\brepo(sitory|sitories)?\b|\bclone\b|\bgit pull\b|\bcommits?\b", "\"Hal's files\", \"update\""),
    (r"\bSSH\b|\btunnel\b|\bport forwarding\b", "only in \"Use Hal from another computer\""),
    (r"\bsessions?\b|\bcookies?\b|\btokens?\b", "\"Open Hal from its shortcut\""),
    (r"\bmigrat(e|ed|es|ing|ion|ions)\b", "\"Hal is updating its records\""),
    (r"\b\d+\s*%\s+of the\b[^.]*\blimit\b", "counts Hal can know"),
]
# Bare code words for kinds of role are banned; these full phrases are what the app says instead. The words
# are matched in lower case only, so an employer named "Civic Insight" is not flagged.
KIND_PHRASES = re.compile(r"paid internships?|placement years?|insight weeks?( and spring weeks)?|"
                          r"apprenticeships?|graduate schemes?", re.I)
KIND_INSTEAD = "\"Paid internships\", \"Placement years\" or \"Insight weeks and spring weeks\""
KIND_WORDS = re.compile(r"\b(internships?|placements?|insights?)\b")
SUBMIT = re.compile(r"\bsubmit", re.I)
SUBMIT_OK = re.compile(r"\bnever\b|stops before|you press|press(es)? submit .*yourself|yourself|\bnot\b|n't\b", re.I)
ADDRESS = re.compile(r"\b[a-z][a-z0-9+.-]*://\S*", re.I)
QUOTED = re.compile(r"\"[^\"]*\"|&quot;.*?&quot;")
TAG = re.compile(r"<[^<>]*>")
LABEL_ATTRS = re.compile(r"\b(?:aria-label|title|placeholder|alt)=\"([^\"$]*)\"")
EXPR = "\0"   # stands for a ${...} hole in a template while it is scanned
SPLIT = "\1"  # where a tag was: text on either side is read separately
HOLE = "_"     # a hole inside a sentence reads as a placeholder word


@dataclass
class Text:
    """One piece of text found in a file."""

    file: str
    line: int
    text: str
    details: bool


def _skip_regex(src: str, i: int) -> int:
    """Index just past a regex literal starting at src[i] == '/'."""
    j, in_class = i + 1, False
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < len(src) and src[j].isalpha():
                j += 1
            return j
        elif c == "\n":
            return j
        j += 1
    return j


def literals(src: str, file: str = "") -> list[Text]:
    """The string literals and template texts in JavaScript source, with their line and details flag.

    A small lexer: it skips comments and regex literals, and walks into `${...}` inside templates so
    nested templates are found too. Each template contributes its text with every hole replaced by EXPR.
    """
    out: list[Text] = []
    details_next = False

    def line_of(i: int) -> int:
        return src.count("\n", 0, i) + 1

    def scan(i: int, end_brace: bool, details: bool) -> int:
        nonlocal details_next
        depth = 0
        prev = ""
        while i < len(src):
            c = src[i]
            if src.startswith("//", i):
                i = src.find("\n", i)
                i = len(src) if i < 0 else i
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                j = len(src) if j < 0 else j + 2
                if src[i:j].strip() == DETAILS:
                    details_next = True
                i = j
                continue
            if c in "\"'":
                j = i + 1
                while j < len(src) and src[j] != c and src[j] != "\n":
                    j += 2 if src[j] == "\\" else 1
                out.append(Text(file, line_of(i), src[i + 1:j], details or details_next))
                details_next = False
                i, prev = j + 1, "x"
                continue
            if c == "`":
                i = template(i, details or details_next)
                details_next = False
                prev = "x"
                continue
            if c == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"):
                i = _skip_regex(src, i)
                prev = "x"
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if end_brace and depth == 0:
                    return i + 1
                depth -= 1
            if not c.isspace():
                prev = c
            i += 1
        return i

    def template(i: int, details: bool) -> int:
        start_line = line_of(i)
        j, parts = i + 1, []
        while j < len(src) and src[j] != "`":
            if src[j] == "\\":
                parts.append(src[j:j + 2])
                j += 2
            elif src.startswith("${", j):
                parts.append(EXPR)
                j = scan(j + 2, True, details)
            else:
                parts.append(src[j])
                j += 1
        out.append(Text(file, start_line, "".join(parts), details))
        return j + 1

    scan(0, False, False)
    return out


def words_for_people(text: str) -> list[str]:
    """The parts of a literal that a person would read: template text between tags and label attributes."""
    if "<" in text and ">" in text:
        pieces = LABEL_ATTRS.findall(text) + TAG.sub(SPLIT, text).split(SPLIT)
    else:
        pieces = [text]
    pieces = [p.replace(EXPR, HOLE) for p in pieces]
    keep = []
    for p in pieces:
        p = p.strip()
        # one lowercase token, a path, a CSS class list or a key is code, not words
        if not re.search(r"[A-Za-z]", p.replace(HOLE, "")) or p.startswith(("/", "#", ".", "--")):
            continue
        if " " not in p and not re.match(r"[A-Z][a-z]", p):
            continue
        if re.fullmatch(r"[a-z0-9 _-]+", p) and not re.search(r"\b(the|a|an|to|of|and|is|your|you)\b", p):
            continue  # "btn small stop" and the like
        keep.append(p)
    return keep


def problems(text: str, file: str) -> list[str]:
    """The banned words in one piece of words for people, as "word: say instead" lines."""
    t = ADDRESS.sub(" ", text)
    if file == HELPER_STEP:
        t = QUOTED.sub(" ", t)
    found = []
    for pat, instead in BANNED:
        if file == REMOTE_PAGE and "SSH" in pat:
            continue
        m = re.search(pat, t, re.I if not pat.startswith(r"\bATS") else 0)
        if m:
            found.append(f"{m.group(0)!r}: say {instead}")
    m = KIND_WORDS.search(KIND_PHRASES.sub(" ", t))
    if m:
        found.append(f"{m.group(0)!r}: say {KIND_INSTEAD}")
    for sentence in re.split(r"(?<=[.!?])\s+", t):
        if SUBMIT.search(sentence) and re.search(r"\bHal\b", sentence) and not SUBMIT_OK.search(sentence):
            found.append("Hal never submits: say \"Hal stops before Submit\"")
    return found


def scan_app(root: Path = APP) -> list[tuple[str, int, str, str]]:
    """(file, line, text, problem) for every banned word in the app's own JavaScript."""
    hits = []
    for path in sorted(root.rglob("*.js")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("lib/"):
            continue
        for lit in literals(path.read_text(encoding="utf-8"), rel):
            if lit.details:
                continue
            for piece in words_for_people(lit.text):
                hits += [(rel, lit.line, piece, p) for p in problems(piece, rel)]
    return hits


class WordsTest(unittest.TestCase):
    """Words on screen."""

    def test_the_scanner_finds_banned_words_in_text_and_templates(self) -> None:
        src = ('const a = "Your pack is ready";\n'
               "const b = html`<p class=\"sub\">Hal used the ${x} model</p>`;\n"
               "const c = /* details */ html`<p>Model: ${name}</p>`;\n"
               'const d = "Open the inbox";\n'
               "const e = html`<button aria-label=\"Open the vault\">${html`<b>Hal will submit it</b>`}</button>`;\n")
        found = [(lit.line, p) for lit in literals(src, "x.js") if not lit.details
                 for piece in words_for_people(lit.text) for p in problems(piece, "x.js")]
        self.assertEqual([line for line, _ in found], [1, 2, 4, 5, 5])

    def test_the_words_people_are_meant_to_read_pass(self) -> None:
        ok = ["Paid internships", "Hal stops before Submit.", "You press Submit on the employer's site.",
              "Switch on \"Developer mode\" in opera://extensions", "Search or jump", "Help and shortcuts",
              "For your safety, Hal opens from its shortcut.", "Insight weeks and spring weeks"]
        for text in ok:
            with self.subTest(text=text):
                self.assertEqual(problems(text, HELPER_STEP), [])

    def test_code_strings_are_not_read_as_words(self) -> None:
        for code in ("inbox", "btn small stop", "/api/v2/session", "settings-health", "application/json"):
            with self.subTest(code=code):
                self.assertEqual(words_for_people(code), [])

    def test_words_on_screen_use_the_apps_vocabulary(self) -> None:
        hits = scan_app()
        self.assertEqual([f"{r}:{n} {t!r}: {p}" for r, n, t, p in hits], [])


if __name__ == "__main__":
    unittest.main()
