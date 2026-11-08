"""Quotes a paragraph may rest on: sentences about the work, the ones with a result or a number first.

`about_work(text)` is False for a line that says nothing of the work: a table row, a bare link, a credit
line, a rating line, a heading, a line under `MIN_WORDS` words with no verb, a command line, a list of
pinned dependencies, statistics about the files, a line about the repository or its documentation rather
than the project, and a fragment that starts in lower case. `sentences(note)` gives the sentences of a
note's body that are about the work, its markup taken out, and `ranked(quotes)` puts those with a result or
a number first, then those that say how, keeping their order otherwise.

Design decisions, each with its reason:

- A card's quotes are what a paragraph about it is drafted from (`profile.writing.quotes`), so a line with
  no sentence in it crowds out a fact: a real card's quotes held "mac-sniffer | Yes | https://...", "Built
  by [@user](...)" and "X - merit 5/5 * Python, Ollama*", while its own repository's 113-test suite and its
  fingerprinting were missing (2026-11-08). These lines are recognised by their shape alone, with no AI.
- A real card's quotes also held a command with its flags and a "# comment", a "Depends on: name>=1.2"
  line, "Languages: Python (16 files)", "The repository contains the source code, tests ...", a sentence
  about the README's own tour, and "tool that goes online, and every ..." cut from the middle of a
  hard-wrapped sentence (2026-11-08). So a note's code blocks are skipped, its hard-wrapped lines are joined
  into the paragraph or list item they belong to before it is split into sentences, a sentence cut off at
  the end of a paragraph or of the part of the note read (`NOTE_CHARS`) is dropped when it has no full stop,
  and a line that starts with a lower-case word (`FRAGMENT`) is the tail of a sentence, not one.
- A result or a number is what makes a paragraph concrete, so a quote holding one is given first, and one
  saying how the work was done (`HOW`) next; a paragraph gets at most `profile.writing.CARD_QUOTES` of them.
- A note's sentences are read from its body only (no front matter), and a paragraph is split at sentence
  ends, so one README paragraph gives several quotes, each a whole sentence.
"""
from __future__ import annotations

import re

MIN_WORDS = 6              # a line shorter than this with no verb is a label, not a sentence about the work
NOTE_CHARS = 20000         # of a note read for sentences: a long README's opening says what the work is
SENTENCE_MAX = 300         # characters of one quote: longer is a paragraph, not one fact
TABLE_ROW = re.compile(r"^\s*\||\|.*\|")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)|<?https?://\S+>?|\bwww\.\S+")
CREDIT = re.compile(r"^\W*(?:(?:built|made|created|maintained|written|designed|developed)\s+(?:with\s+\S+\s+)?by\b|"
                    r"credits?\b|authors?\b|contributors?\b|licen[cs]e[ds]?\b|copyright\b|\(c\)\s)", re.I)
RATING = re.compile(r"\b\d+(?:\.\d+)?\s*/\s*(?:5|10)\b|\b(?:rating|rated|stars?)\b|[\u2605\u2606]", re.I)
HEADING = re.compile(r"^\s*(?:#{1,6}\s|[=-]{3,}\s*$)|:\s*$")
# A command line: a prompt, a command word (and a subcommand) followed by a flag, a long flag anywhere, or a
# shell comment after a space ("tool run --all # save it").
COMMAND = re.compile(r"^\W*\$\s|^[a-z][\w.-]*(?:\s+[a-z][\w.-]*)?\s+--?[a-z]|(?:^|\s)--[a-z][\w-]*|\s#\s")
# A dependency pinned to a version as a package list writes it ("name>=1.2"), or a line naming the list.
PINNED = re.compile(r"\b[A-Za-z][\w.-]*(?:>=|<=|==|~=|!=)\d|^\W*(?:depends on|dependencies|requirements|requires)\s*:",
                    re.I)
# Statistics about the files rather than the work: "Languages: Python (16 files)", "Stars: 4".
STATS = re.compile(r"^\W*(?:languages?|file types?|files|lines(?: of code)?|commits|stars|forks|size|topics|"
                   r"last (?:commit|updated?))\s*:|\(\d[\d,]*\s+(?:files?|lines?|commits?)\)", re.I)
# A line about the repository or its documentation rather than what the project does: "The repository
# contains ...", "The terminal UI tour covers ...", "See the docs for ...". The verb after the noun keeps
# "The scanner reads document files" a sentence about the work.
META = re.compile(r"^\W*(?:the|this|our)\s+(?:[\w-]+\s+){0,2}?(?:repo(?:sitory)?|readme|codebase|docs?|"
                  r"documentation|document|guide|tour|manual|wiki|tutorial|walkthrough|folder|directory)\s+"
                  r"(?:also\s+)?(?:contains?|covers?|explains?|describes?|lists?|shows?|has|holds?|includes?|is|"
                  r"are|provides?|gives?|walks?|documents?|goes)\b|"
                  r"\b(?:see|read|refer to|check out)\s+(?:the\s+)?(?:[\w-]+\s+){0,2}?(?:docs?|documentation|readme|"
                  r"guide|wiki|tour|manual)\b", re.I)
# A sentence's tail: it starts with a word all in lower case ("tool that goes online, ..."); a name with a
# capital or a digit inside ("iOS", "p5.js") is no tail.
FRAGMENT = re.compile(r"^[^\w]*[a-z][a-z'-]*\b(?![\d.]\w)")
SENTENCE_STOP = re.compile(r"[.!?][\"')\]]*$")
# A verb in a short line: a word ending as one usually does, or a common verb (crude, and enough for a label).
VERB = re.compile(r"\b(?:\w{3,}(?:ed|s|ing)|is|are|was|were|has|have|had|can|runs?|uses?|lets?|makes?|"
                  r"builds?|reads?|writes?|logs?|finds?|keeps?|shows?|gives?|takes?|holds?|sends?)\b", re.I)
RESULT = re.compile(r"\d|\b(?:reduc|improv|increas|cut|sav|fast|speed|pass|won|win|award|user|test|accura|"
                    r"cover|scor|rank|grew|grow|halv|doubl)\w*", re.I)
HOW = re.compile(r"\b(?:using|uses?|used|with|via|through|by)\b", re.I)
MARKUP = re.compile(r"[*`]+|^\s*(?:[>#]+|[-+]|\d+[.)])\s+")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
FENCE = re.compile(r"^\s*(?:```|~~~)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def plain(text: str) -> str:
    """A line without its markup: links as their words, no emphasis, bullets, headings' hashes or extra spaces."""
    out = LINK.sub(lambda m: m.group(1) or "", text or "")
    return " ".join(MARKUP.sub("", out).split())


def about_work(text: str) -> bool:
    """True when a line is a sentence about the work, not a table row, link, credit, rating, heading, label,
    command, dependency list, file statistics, line about the repository or its docs, or a sentence's tail."""
    raw = (text or "").strip()
    if not raw or TABLE_ROW.search(raw) or CREDIT.match(raw) or RATING.search(raw) or HEADING.search(raw):
        return False
    words = plain(raw).split()
    said = " ".join(words)
    if COMMAND.search(raw) or PINNED.search(said) or STATS.search(said) or META.search(said) \
            or FRAGMENT.match(said):
        return False
    return len(words) >= MIN_WORDS or (len(words) > 1 and VERB.search(said) is not None)


def ranked(quotes: list[str]) -> list[str]:
    """The quotes with a result or a number first, then those saying how, each group in its own order."""
    return sorted(quotes, key=lambda q: (RESULT.search(q) is None, HOW.search(q) is None))


def _blocks(body: str) -> list[tuple[str, bool]]:
    """The paragraphs and list items of a note's body, each hard-wrapped line joined to the one before, with
    whether its end was cut off; code blocks, headings and table rows are left out."""
    text = (body or "")[:NOTE_CHARS]
    cut = len(body or "") > NOTE_CHARS
    blocks: list[list[str]] = []
    joining, in_code = False, False
    for line in text.splitlines():
        if FENCE.match(line):
            in_code, joining = not in_code, False
            continue
        if in_code or not line.strip() or HEADING.search(line) or TABLE_ROW.search(line):
            joining = False
            continue
        if joining and not LIST_ITEM.match(line):
            blocks[-1].append(line.strip())
        else:
            blocks.append([line.strip()])
        joining = True
    return [(" ".join(b), cut and i == len(blocks) - 1) for i, b in enumerate(blocks)]


def sentences(body: str) -> list[str]:
    """The sentences of a note's body that are about the work (`about_work`), markup taken out, in order."""
    out = []
    for block, cut in _blocks(body):
        if CREDIT.match(block):
            continue
        pieces = SENTENCE_END.split(plain(block))
        for i, said in enumerate(pieces):
            tail = i == len(pieces) - 1 and (cut or len(pieces) > 1)
            if tail and not SENTENCE_STOP.search(said):
                continue                   # cut off: the rest of the sentence was never read
            if about_work(said) and MIN_WORDS <= len(said.split()) and len(said) <= SENTENCE_MAX:
                out.append(said)
    return list(dict.fromkeys(out))
