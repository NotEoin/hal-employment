"""The person's voice, read from their own writing with no AI: a voice card and a few exemplars.

`samples(con)` gathers what the person wrote themselves: their CVs, letters, statements and essays in the
record (by the document type the ingest wrote in a note's frontmatter, else by the file's name), the answers
they rewrote (`answers2` rows from them that are never reused on forms) and the finals of their edits of
Hal's drafts (`writing_edits`). `card(samples)` measures them: sentence length, contractions, British or
American spelling, favourite words and openings, and the words they take out of Hal's drafts.
`exemplars(kind, samples)` picks two or three short passages closest in kind to the document being written.

Design decisions, each with its reason:

- Numbers, not impressions: a small model follows "sentences average 14 words; no contractions" more
  reliably than it infers a style from examples, so the card states measured values.
- Exemplars are style only, cut to `EXEMPLAR_WORDS` and `BUDGET_CHARS` in all, so they never crowd out the
  facts, and the prompt says never to copy their facts.
- Nothing private is read: record files in private folders are skipped (`record.files` marks them), and an
  answer to a question `autofill.private_question` marks is never a sample.
- The record is read as it is: a document Hal has not converted yet is skipped rather than converted here,
  so building a prompt never writes into the person's folder. It works with or without the bulk ingest.
- Words from the checker's lists that the person uses in their own writing are returned by `own_words`, so
  the checker never flags a person for their own word.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PROSE = ("letter", "answer", "writing")      # the kinds whose sentences say how the person writes prose
# A record file's kind from its name or path; the first match wins.
NAME_KINDS = (("letter", re.compile(r"cover[ _-]?letter|\bletter\b|supporting[ _-](statement|information)|"
                                    r"personal[ _-]statement|\bstatement\b", re.I)),
              ("cv", re.compile(r"\bcv\b|resume|curriculum[ _-]vitae", re.I)),
              ("writing", re.compile(r"essay|dissertation|thesis|\bblog\b|article|\breport\b|coursework", re.I)))
# The ingest's `doc_type` values (Sources notes' frontmatter) for the same kinds.
DOC_TYPES = {"cv": "cv", "resume": "cv", "cover_letter": "letter", "letter": "letter",
             "personal_statement": "letter", "statement": "letter", "supporting_statement": "letter",
             "essay": "writing", "dissertation": "writing", "thesis": "writing", "report": "writing",
             "coursework": "writing", "blog": "writing", "article": "writing", "writing": "writing"}
TEXT_SUFFIXES = (".md", ".txt")
MAX_FILES = 12               # record files read per card; the newest names first would need a stat each
FILE_CHARS = 12000           # characters read from each file: enough for a letter or a few pages of essay
CARD_MIN_WORDS = 120         # fewer words of prose than this say nothing reliable about sentence length
LONG_SENTENCE = 25           # words; the share of sentences longer than this is on the card
CONTRACTIONS_PER_100 = 0.5   # contractions per 100 words of prose before the card says "uses contractions"
FAVOURITE_MIN = 2            # samples a word must appear in to be a favourite
FAVOURITES_SHOWN = 6
OPENERS_SHOWN = 3
EXEMPLARS_MAX = 3            # two or three exemplars, never more
EXEMPLAR_WORDS = 80          # each exemplar is cut to about this many words
BUDGET_CHARS = 1500          # all the exemplars together
CONTRACTION = re.compile(r"\b\w+'(s|re|ve|ll|d|m|t)\b|\b(can't|won't|don't|isn't|aren't|didn't|doesn't)\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
FRONT_TYPE = re.compile(r"^doc_type:\s*\"?([A-Za-z_ -]+)\"?\s*$", re.M)


@dataclass(frozen=True)
class Sample:
    """One piece of the person's own writing: its kind (letter, cv, answer, writing), text and where from."""

    kind: str
    text: str
    source: str


def _kind_of(rel: str, text: str) -> str | None:
    front = FRONT_TYPE.search(text[:600]) if text.startswith("---") else None
    if front:
        return DOC_TYPES.get(front.group(1).strip().lower().replace(" ", "_").replace("-", "_"))
    return next((kind for kind, rx in NAME_KINDS if rx.search(rel)), None)


def _body(text: str) -> str:
    """A note's text without its frontmatter and Hal's own conversion line."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4:] if end != -1 else text
    return re.sub(r"^Text of \[\[.*?\]\], read by Hal Employment\.\s*$", "", text, flags=re.M).strip()


def _readable(path: Path) -> Path | None:
    """The text of a record file: itself when it is text, else the note Hal wrote beside it, else None."""
    if path.suffix.lower() in TEXT_SUFFIXES:
        return path
    note = path.with_name(f"{path.stem} ({path.suffix[1:].upper()}).md")
    return note if note.is_file() else None


def record_samples() -> list[Sample]:
    """The person's CVs, letters, statements and essays in the record, outside private folders."""
    from jobhunt import record

    try:
        found = record.files()
    except OSError as e:
        log.info("the record could not be read for the voice card: %s", e)
        return []
    out = []
    for f in found:
        if f["private"] or len(out) >= MAX_FILES:
            continue
        try:
            path = _readable(record.file_path(f["path"]))
            text = path.read_text(encoding="utf-8", errors="replace")[:FILE_CHARS] if path else ""
        except (LookupError, OSError):
            continue
        kind = _kind_of(f["path"], text)
        body = _body(text)
        if kind and body:
            out.append(Sample(kind, body, f["path"]))
    return out


def answer_samples(con) -> list[Sample]:
    """The answers the person rewrote themselves, never one to a private question."""
    from jobhunt import autofill, db

    rows = con.execute("SELECT label, question, answer FROM answers2 WHERE scope = 'never' "
                       "AND source IN ('you', 'learned') ORDER BY updated DESC").fetchall()
    out = []
    for row in rows:
        if autofill.private_question({"label": row["label"]}) or autofill.private_question({"label": row["question"]}):
            continue
        value = db.json_value(row["answer"])
        if isinstance(value, str) and value.strip():
            out.append(Sample("answer", " ".join(value.split()), f"answer: {row['label']}"))
    return out


def edit_samples(con) -> list[Sample]:
    """The person's final text of each edit of Hal's drafts, newest first."""
    from jobhunt.writing import edits

    kinds = {"letter": "letter", "answer": "answer", "message": "letter", "cv_line": "cv"}
    try:
        rows = edits.pairs(con)
    except sqlite3.OperationalError as e:   # a database from before migration 0021
        log.info("no edits to read for the voice card: %s", e)
        return []
    return [Sample(kinds[r["kind"]], r["final"], f"your edit of a {r['kind'].replace('_', ' ')}") for r in rows]


def samples(con) -> list[Sample]:
    """Everything the person wrote themselves that Hal may read: edits first, then answers, then the record."""
    return [*edit_samples(con), *answer_samples(con), *record_samples()]


# ---------------------------------------------------------------------------------------------- the card

def _sentences(text: str) -> list[str]:
    lines = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n(?=\s*[-*#] )", text)]
    return [s for p in lines if not p.startswith(("#", "-", "*")) for s in SENTENCE.split(p) if len(s.split()) > 2]


def card(found: list[Sample], dropped: dict | None = None) -> dict:
    """The measured voice of the samples: {} when there is too little prose to say anything.

    `dropped` is `edits.dropped_words` of the person's edits, for the words they take out of drafts.
    """
    from jobhunt import cvscore
    from jobhunt import record as record_mod
    from jobhunt.cvstudio import checks as cv_checks
    from jobhunt.writing import edits

    prose = [s for s in found if s.kind in PROSE]
    sentences = [x for s in prose for x in _sentences(s.text)]
    words = [w for s in prose for w in WORD.findall(s.text)]
    if len(words) < CARD_MIN_WORDS or not sentences:
        return {}
    lengths = [len(x.split()) for x in sentences]
    everything = " ".join(s.text for s in found)
    british = len(cv_checks.BRITISH.findall(everything))
    american = len(cvscore.AMERICAN.findall(everything))
    lower_in = Counter()
    for s in found:
        lower_in.update({w for w in WORD.findall(s.text) if w.islower() and len(w) >= 5})
    favourites = [w for w, n in lower_in.most_common() if n >= FAVOURITE_MIN and w not in record_mod.STOP]
    openers = Counter(x.split()[0].strip("\"'").capitalize() for x in sentences)
    return {
        "sentence_words": round(statistics.median(lengths)),
        "long_share": round(sum(n > LONG_SENTENCE for n in lengths) / len(lengths), 2),
        "contractions": len(CONTRACTION.findall(" ".join(s.text for s in prose))) * 100 / len(words)
        >= CONTRACTIONS_PER_100,
        "spelling": "British" if british > american else "American" if american > british else "",
        "favourites": favourites[:FAVOURITES_SHOWN],
        "openers": [w for w, n in openers.most_common(OPENERS_SHOWN + 1) if n >= 2 and w != "I"][:OPENERS_SHOWN],
        "sentences": len(sentences), "words": len(words),
        "drops": sorted(w for w, rows in (dropped or {}).items() if len(rows) >= edits.DROP_MIN - 1),
    }


def card_lines(c: dict) -> list[str]:
    """The card as plain lines for a prompt."""
    if not c:
        return []
    out = [f"Sentences average about {c['sentence_words']} words; "
           + ("few run past 25." if c["long_share"] < 0.2 else f"{round(c['long_share'] * 100)}% run past 25.")]
    out.append("Uses contractions (I'm, it's)." if c["contractions"] else
               "No contractions: writes I am, it is, do not.")
    if c["spelling"]:
        out.append(f"{c['spelling']} spelling.")
    if c["openers"]:
        out.append("Often starts sentences with: " + ", ".join(f'"{w}"' for w in c["openers"]) + ".")
    if c["favourites"]:
        out.append("Words they often use: " + ", ".join(c["favourites"]) + ".")
    if c["drops"]:
        out.append("Words they take out of drafts, so leave out: " + ", ".join(c["drops"]) + ".")
    return out


# ------------------------------------------------------------------------------------------ the exemplars

def _cut(text: str, words: int = EXEMPLAR_WORDS) -> str:
    parts = text.split()
    return " ".join(parts) if len(parts) <= words else " ".join(parts[:words]) + " ..."


def _overlap(text: str, query: set[str]) -> int:
    return len(query & {w.lower() for w in WORD.findall(text)})


def exemplars(kind: str, found: list[Sample], query: str = "") -> list[str]:
    """Two or three passages of the person's own writing closest in kind to `kind`, cut to the budget."""
    want = {"letter": ("letter", "answer", "writing"), "answer": ("answer", "letter", "writing"),
            "cv": ("cv",)}.get(kind, PROSE)
    words = {w.lower() for w in WORD.findall(query) if len(w) >= 5}
    ranked = sorted((s for s in found if s.kind in want),
                    key=lambda s: (want.index(s.kind), -_overlap(s.text, words)))
    out, used = [], 0
    for s in ranked:
        text = _cut(s.text)
        if len(out) == EXEMPLARS_MAX or used + len(text) > BUDGET_CHARS:
            break
        if text not in out:
            out.append(text)
            used += len(text)
    return out


def own_words(found: list[Sample]) -> list[str]:
    """Words from the checker's lists that the person uses in their own writing: theirs to keep."""
    from jobhunt.writing import check

    text = " ".join(s.text for s in found)
    lists = (check.AI_WORDS, check.CLUSTER_WORDS)
    return sorted({m.group(0).lower() for rx in lists for m in rx.finditer(text)})


def voice_for(con, kind: str = "letter", query: str = "") -> dict:
    """The person's voice for one kind of document: {card, lines, exemplars, own_words}."""
    from jobhunt.writing import edits

    found = samples(con)
    try:
        dropped = edits.dropped_words(edits.pairs(con))
    except sqlite3.OperationalError:   # a database from before migration 0021
        dropped = {}
    c = card(found, dropped)
    return {"card": c, "lines": card_lines(c), "exemplars": exemplars(kind, found, query),
            "own_words": own_words(found)}
