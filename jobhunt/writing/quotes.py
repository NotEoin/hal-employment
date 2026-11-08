"""Quotes a paragraph may rest on: sentences about the work, the ones with a result or a number first.

`about_work(text)` is False for a line that says nothing of the work: a table row, a bare link, a credit
line, a rating line, a heading, or a line under `MIN_WORDS` words with no verb. `sentences(note)` gives the
sentences of a note's body that are about the work, its markup taken out, and `ranked(quotes)` puts those
with a result or a number first, keeping their order otherwise.

Design decisions, each with its reason:

- A card's quotes are what a paragraph about it is drafted from (`profile.writing.quotes`), so a line with
  no sentence in it crowds out a fact: a real card's quotes held "mac-sniffer | Yes | https://...", "Built
  by [@user](...)" and "X - merit 5/5 * Python, Ollama*", while its own repository's 113-test suite and its
  fingerprinting were missing (2026-11-08). These lines are recognised by their shape alone, with no AI.
- A result or a number is what makes a paragraph concrete, so a quote holding one is given first; a
  paragraph gets at most `profile.writing.CARD_QUOTES` of them.
- A note's sentences are read from its body only (no front matter), and a line is split at sentence ends,
  so one README paragraph gives several quotes, each a whole sentence.
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
# A verb in a short line: a word ending as one usually does, or a common verb (crude, and enough for a label).
VERB = re.compile(r"\b(?:\w{3,}(?:ed|s|ing)|is|are|was|were|has|have|had|can|runs?|uses?|lets?|makes?|"
                  r"builds?|reads?|writes?|logs?|finds?|keeps?|shows?|gives?|takes?|holds?|sends?)\b", re.I)
RESULT = re.compile(r"\d|\b(?:reduc|improv|increas|cut|sav|fast|speed|pass|won|win|award|user|test|accura|"
                    r"cover|scor|rank|grew|grow|halv|doubl)\w*", re.I)
MARKUP = re.compile(r"[*`]+|^\s*(?:[>#]+|[-+]|\d+[.)])\s+")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def plain(text: str) -> str:
    """A line without its markup: links as their words, no emphasis, bullets, headings' hashes or extra spaces."""
    out = LINK.sub(lambda m: m.group(1) or "", text or "")
    return " ".join(MARKUP.sub("", out).split())


def about_work(text: str) -> bool:
    """True when a line is a sentence about the work, not a table row, link, credit, rating, heading or label."""
    raw = (text or "").strip()
    if not raw or TABLE_ROW.search(raw) or CREDIT.match(raw) or RATING.search(raw) or HEADING.search(raw):
        return False
    words = plain(raw).split()
    return len(words) >= MIN_WORDS or (len(words) > 1 and VERB.search(" ".join(words)) is not None)


def ranked(quotes: list[str]) -> list[str]:
    """The quotes with a result or a number first, each group in its own order."""
    return sorted(quotes, key=lambda q: RESULT.search(q) is None)


def sentences(body: str) -> list[str]:
    """The sentences of a note's body that are about the work (`about_work`), markup taken out, in order."""
    out = []
    for line in (body or "")[:NOTE_CHARS].splitlines():
        if not about_work(line):
            continue
        for said in SENTENCE_END.split(plain(line)):
            if MIN_WORDS <= len(said.split()) and len(said) <= SENTENCE_MAX:
                out.append(said)
    return list(dict.fromkeys(out))
