"""The Documents reader: transcripts, certificates and references, and the text of anything else.

The person chooses what each document is when they add it (Hal suggests a kind, `wrong_place.suggest_kind`),
and may say which card it is about: an education period, a job or a certificate. What a document gives:

- a transcript: the course's qualification, subject, institution, grade and its modules with their marks, at
  the transcript's rank (`merge.RANKS`, the highest), on the education card it names, else on the card of the
  same institution (`merge.same_item`), else a new one;
- a certificate: a certificate card (name, issuer, date, grade) at the certificate's rank;
- a reference: the job card it is about, with the referee's words as the quote, marked as another person's
  (`roles.OTHERS`), never as the person's own claim; with no job card named it gives no facts;
- anything else (a check, a report, a letter of thanks): its text, a title and a date, and no facts.

Design decisions, each with its reason:

- Marks, grades and dates are copied from the document by rules, never by the AI: they can be read exactly,
  and a mark the AI made up would rank above every CV (sections plan, 2026-11-09).
- A transcript's module is the row as written ("HIS1010 Medieval Europe"), so the profile tells modules apart
  by code (`merge.module_code`) and a CV's "Medieval Europe" is the same module.
- The card a document names fills what the document leaves out of its fields (the institution, the employer),
  so the fact lands on that card; a value the document gives itself is kept, as it is the better source.
- Private documents (medical, bank or ID papers) are kept out before any of this by the pipeline's privacy
  check (`ingest.privacy`).
"""
from __future__ import annotations

import re

from jobhunt.cvstudio import parse as cv_parse
from jobhunt.ingest import roles
from jobhunt.sections import readers

LABEL = r"^\W*(?:{words})\s*[:\-]\s*(?P<value>.+?)\s*$"
QUALIFICATION = re.compile(LABEL.format(words=r"programme|program|course|degree|qualification|award|"
                                              r"programme of study|course of study"), re.I | re.M)
GRADE = re.compile(LABEL.format(words=r"(?:final |degree |overall |award )?(?:classification|grade|result)"),
                   re.I | re.M)
INSTITUTION = re.compile(LABEL.format(words=r"institution|university|college|awarding body|school"), re.I | re.M)
STUDY = re.compile(LABEL.format(words=r"period of study|dates? of study|years? of study|study period"),
                   re.I | re.M)
AWARDED = re.compile(LABEL.format(words=r"date of award|award date|date awarded|conferred"), re.I | re.M)
ORG = (r"university|college|institute|society|academy|school|trust|council|board|association|foundation|ltd|"
       r"limited")
ORG_WORDS = re.compile(rf"\b(?:{ORG})\b", re.I)
# "Issuer: ...", or "... by the Made-up Records Society" up to the organisation's own word.
ISSUER = re.compile(r"^\W*(?:issuer|issued by|awarding body|provider)\s*[:\-]\s*(?P<labelled>.+?)\s*$|"
                    rf"\bby\s+(?:the\s+)?(?P<by>[^,.\n]*?\b(?:{ORG})\b)", re.I | re.M)
ORG_LINE_WORDS = 10        # a line naming an organisation is short; a longer one is a sentence
# A module's mark as transcripts write it: a number out of 100 (with a decimal or a % sign), or a letter or
# word grade.
MARK = re.compile(r"^(?:\d{1,3}(?:\.\d+)?%?|A\*|[A-F][+-]?|Pass|Merit|Distinction|Fail|Credit)$", re.I)
MARK_HEADER = re.compile(r"\b(?:mark|grade|result|score|%)", re.I)
MODULE_HEADER = re.compile(r"\b(?:module|title|name|unit|course)\b", re.I)
CODE_HEADER = re.compile(r"\bcode\b", re.I)
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
PLAIN_ROW = re.compile(r"^\W*(?P<name>.*?[A-Za-z].*?)\s+(?P<mark>\d{1,3}(?:\.\d+)?%?)\s*$")
SENTENCE = re.compile(r"(?<=[.!?])\s+")
GREETING = re.compile(r"^\s*(?:dear\b|to whom it may concern|re:|subject:)", re.I)
QUOTE_WORDS = 40           # a referee's quote is a sentence or two
EDUCATION_KEYS = ("qualification", "subject", "institution")
JOB_KEYS = ("title", "employer")


def _labelled(pattern: re.Pattern, text: str) -> tuple[str, str]:
    """The value of the first line `pattern` matches and the line itself; ("", "") for none."""
    m = pattern.search(text)
    if not m:
        return "", ""
    value = next((g for g in m.groups() if g), "")
    return value.strip(" *_"), m.group(0).strip()


def _org_line(text: str) -> str:
    """The first short line that names an organisation (a university, a society), as written."""
    for line in text.splitlines():
        plain = line.strip("#*-|_ \t")
        if plain and len(plain.split()) <= ORG_LINE_WORDS and ORG_WORDS.search(plain) and ":" not in plain:
            return plain
    return ""


def _cells(line: str) -> list[str]:
    return [c.strip(" *_") for c in line.strip().strip("|").split("|")]


def _table_modules(text: str) -> list[dict]:
    """Modules from Markdown tables: the mark column the header names, else each row's last mark."""
    out, header = [], None
    for line in text.splitlines():
        if "|" not in line:
            header = None
            continue
        if TABLE_RULE.match(line):
            continue
        cells = _cells(line)
        if header is None and not any(MARK.match(c) for c in cells[1:]):
            header = cells
            continue
        marks = [i for i, c in enumerate(cells) if MARK.match(c)]
        if not marks:
            continue
        at = next((i for i, h in enumerate(header or []) if MARK_HEADER.search(h) and i < len(cells)), marks[-1])
        named = [i for i, h in enumerate(header or []) if (MODULE_HEADER.search(h) or CODE_HEADER.search(h))
                 and i < len(cells) and i != at] or [i for i in range(len(cells)) if i not in marks]
        name = " ".join(cells[i] for i in named if cells[i])
        if name and MARK.match(cells[at]):
            out.append({"name": name, "mark": cells[at]})
    return out


def _plain_modules(text: str) -> list[dict]:
    """Modules from lines such as "HIS1010 Medieval Europe 66": a module code, a name and a mark at the end."""
    from jobhunt.profile import merge

    out = []
    for line in text.splitlines():
        if "|" in line:
            continue
        m = PLAIN_ROW.match(line.strip())
        if m and merge.module_code(m.group("name")):
            out.append({"name": m.group("name").strip(" *_-"), "mark": m.group("mark")})
    return out


def modules_in(text: str) -> list[dict]:
    """A transcript's modules with their marks, as written: [{name, mark}]."""
    return _table_modules(text) or _plain_modules(text)


def _filled(fields: dict, target: dict | None, kind: str, keys: tuple[str, ...]) -> dict:
    """`fields` with the named card's values for `keys` it leaves out, when that card is of `kind`."""
    if not target or target.get("kind") != kind:
        return fields
    have = target.get("fields") or {}
    return {**{k: have[k] for k in keys if have.get(k) and not fields.get(k)}, **fields}


def _place(source: readers.Source) -> str:
    return source.files[0].rel if len(source.files) == 1 else ""


def _line_of(text: str, words: str) -> int | None:
    for n, line in enumerate(text.splitlines(), 1):
        if words and words in line:
            return n
    return None


def _quote(text: str, field: str, words: str, place: str) -> readers.Quote:
    return readers.Quote(field, words, place, _line_of(text, words))


def transcript_facts(text: str, source: readers.Source) -> list[readers.Fact]:
    """The education fact of a transcript: qualification, subject, institution, grade, dates and modules."""
    qualification, q_line = _labelled(QUALIFICATION, text)
    grade, g_line = _labelled(GRADE, text)
    institution, i_line = _labelled(INSTITUTION, text)
    institution = institution or _org_line(text)
    period, _ = _labelled(STUDY, text)
    awarded, _ = _labelled(AWARDED, text)
    split = cv_parse.DEGREE_SPLIT.match(qualification)
    fields = {"qualification": split.group("degree") if split else qualification,
              "subject": split.group("subject") if split else "", "institution": institution, "grade": grade,
              "modules": modules_in(text)}
    if period:
        fields["period"] = period
    elif awarded:
        fields["end"] = awarded
    fields = _filled({k: v for k, v in fields.items() if v}, source.target, "education", EDUCATION_KEYS)
    if not any(fields.get(k) for k in EDUCATION_KEYS):
        return []
    quoted = (("grade", g_line), ("qualification", q_line), ("institution", i_line or institution))
    field, words = next(((f, w) for f, w in quoted if w), ("modules", ""))
    return [readers.Fact("education", fields, _quote(text, field, words, _place(source)))]


def certificate_facts(text: str, title: str, date: str, source: readers.Source) -> list[readers.Fact]:
    """The certificate fact: its name (the title), issuer, date and grade."""
    issuer, _ = _labelled(ISSUER, text)
    issuer = issuer or _org_line(text)
    grade, _ = _labelled(GRADE, text)
    fields = {k: v for k, v in {"name": title, "issuer": issuer, "date": date, "grade": grade}.items() if v}
    if not fields.get("name"):
        return []
    return [readers.Fact("certificate", fields, _quote(text, "name", title, _place(source)))]


def _sentences(text: str) -> list[str]:
    """The body's sentences, without headings, greetings or sign-off lines of a few words."""
    body = " ".join(line.strip() for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#") and not GREETING.match(line)
                    and len(line.split()) > 3)
    return [s.strip() for s in SENTENCE.split(body) if s.strip()]


def referee_quote(text: str, target: dict | None) -> str:
    """The referee's sentence about the person's work: the first naming the job or employer, else the first;
    as written, at most `QUOTE_WORDS` words."""
    sentences = _sentences(text)
    have = (target or {}).get("fields") or {}
    names = [str(have[k]).lower() for k in JOB_KEYS if have.get(k)]
    chosen = next((s for s in sentences if any(n in s.lower() for n in names)), sentences[0] if sentences else "")
    return " ".join(chosen.split()[:QUOTE_WORDS])


def reference_facts(text: str, source: readers.Source) -> list[readers.Fact]:
    """The job fact of a reference about the job card it names, quoting the referee as another person's words;
    none when it names no job card."""
    if not source.target or source.target.get("kind") != "job":
        return []
    fields = _filled({}, source.target, "job", JOB_KEYS)
    words = referee_quote(text, source.target)
    if not fields or not words:
        return []
    return [readers.Fact("job", fields, _quote(text, "title", words, _place(source)), role=roles.OTHERS)]


class DocumentsReader:
    """Reads a document of the Documents section by the kind the person chose for it."""

    name = "document"

    def read(self, source: readers.Source) -> readers.Reading:
        """The text, title and date of any document, and the facts of a transcript, certificate or reference."""
        reading = readers.DocumentReader().read(source)
        if source.doc_kind == "transcript":
            reading.facts = transcript_facts(reading.text, source)
        elif source.doc_kind == "certificate":
            reading.facts = certificate_facts(reading.text, reading.title, reading.date, source)
        elif source.doc_kind == "reference":
            reading.facts = reference_facts(reading.text, source)
        return reading


readers.register(DocumentsReader())
