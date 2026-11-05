"""The person's edits of Hal's drafts: kept as pairs, and the plain rules they show offered as suggestions.

When the person changes a letter, a form answer, a message or a CV line that Hal wrote, the save path calls
`record` (through `from_documents`, `from_fields` or `from_cv`) with Hal's text and theirs. The pairs live in
`writing_edits` (migration 0021). `learned(con)` reads them with no AI and finds two kinds of rule:

- a word the person took out of `DROP_MIN` or more drafts, and never wrote themselves, becomes "Never use
  <word>?", which adds it to `cvdocs.never_use`;
- letters the person cut, `LENGTH_MIN` times or more, become "Write letters of about N words?", which sets
  `cvdocs.letter_words` to the nearest length it offers.

Each is offered through `learn.items.offer` (the pattern `writing_edits` in `learn/patterns.py`), so nothing
changes until the person says yes, and Undo puts it back. The finals are also the voice exemplars of
`writing.voice`.

Design decisions, each with its reason:

- Repeated edits of one piece keep Hal's first draft and the person's latest text, so a pair always shows
  what Hal wrote against what the person chose, however many saves it took.
- A private question's answer is never kept: the same `autofill.private_question` test the AI guard uses
  is applied before a pair is written.
- Recording never changes what the save path saves; it runs after the save and a failure to keep a pair
  is logged, never raised, because the person's edit matters more than the lesson in it.
"""
from __future__ import annotations

import difflib
import logging
import re
import sqlite3
import statistics
from collections import Counter

from jobhunt.core import clock, events

log = logging.getLogger(__name__)

KINDS = ("letter", "answer", "message", "cv_line")
KIND_WORDS = {"letter": "Letter", "answer": "Answer", "message": "Message", "cv_line": "CV line"}
EVENT = "writing"            # published after a pair is kept; learning's listener looks again
DROP_MIN = 3                 # drafts a word must be taken out of before Hal offers to never use it
WORD_MIN = 4                 # shorter words are grammar, not style
SPAN_MAX = 2                 # words in one cut for it to count as taking a word out, not a clause
LENGTH_MIN = 2               # letters cut before Hal offers a shorter length
TRIM_SHARE = 0.9             # a letter counts as cut when the final keeps less than this share of its words
LETTER_MIN_WORDS = 100       # a shorter letter draft is a fragment, and its length says nothing
RECENT = 200                 # pairs read for the rules, newest first
ANSWER_MIN_CHARS = 80        # shorter form answers are facts (a date, a yes), not writing (autofill.WRITTEN)
EVIDENCE_SHOWN = 4           # evidence lines on a suggestion
QUOTE_CHARS = 60             # characters of context quoted around a change
SOURCE = "writing_edits"     # the pattern's name in learn/patterns.py


def _clean(text) -> str:
    return " ".join(str(text or "").split()) if isinstance(text, str) else ""


def record(con: sqlite3.Connection, kind: str, ref: str, draft, final, *, job_id: str | None = None) -> bool:
    """Keeps one draft and final pair; False when nothing changed. Commits."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
    draft, final = _clean(draft), _clean(final)
    if not draft or not final or draft == final:
        return False
    now = clock.iso()
    row = con.execute("SELECT id FROM writing_edits WHERE kind = ? AND ref = ? AND final = ? "
                      "ORDER BY updated DESC LIMIT 1", (kind, ref, draft)).fetchone()
    if row is not None:
        con.execute("UPDATE writing_edits SET final = ?, updated = ? WHERE id = ?", (final, now, row["id"]))
    else:
        con.execute("INSERT INTO writing_edits (kind, ref, job_id, draft, final, created, updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (kind, ref, job_id, draft, final, now, now))
    con.commit()
    events.publish(EVENT, what="edit", kind=kind)
    return True


def _private(question: str) -> bool:
    from jobhunt import autofill

    return autofill.private_question({"label": question}) is not None


def from_documents(con: sqlite3.Connection, job, before: dict, after: dict) -> int:
    """Keeps a pair for each of the letter, the message and the answers the person changed; returns how many."""
    job_id = str(job["id"])
    kept = 0
    try:
        for kind in ("letter", "message"):
            kept += record(con, kind, job_id, before.get(kind), after.get(kind), job_id=job_id)
        was = {a.get("question", ""): a.get("answer", "") for a in before.get("answers") or [] if isinstance(a, dict)}
        for a in after.get("answers") or []:
            question = a.get("question", "") if isinstance(a, dict) else ""
            if question in was and not _private(question):
                kept += record(con, "answer", f"{job_id}:{question}", was[question], a.get("answer"), job_id=job_id)
    except sqlite3.Error:
        log.exception("could not keep the edits of %s's documents", job_id)
    return kept


def from_fields(con: sqlite3.Connection, fields: list[dict], job=None) -> int:
    """Keeps a pair for each written form answer the person changed from Hal's (`hal_value`); returns how many."""
    job_id = str(job["id"]) if job is not None else None
    kept = 0
    try:
        for f in fields:
            hal, mine = f.get("hal_value"), f.get("value")
            if f.get("source") != "you" or f.get("kind") not in ("text", "textarea") or not isinstance(mine, str):
                continue
            if not isinstance(hal, str) or len(hal) < ANSWER_MIN_CHARS or _private(f.get("label") or ""):
                continue
            kept += record(con, "answer", f.get("label") or "", hal, mine, job_id=job_id)
    except sqlite3.Error:
        log.exception("could not keep the edits of a form's answers")
    return kept


def _cv_texts(doc) -> dict[str, str]:
    """Each line of a CV the person may reword, by a target that survives other edits."""
    out = {}
    for s in doc.sections:
        if s.text.strip():
            out[f"s:{s.id}/text"] = s.text
        for e in s.entries:
            for b in e.bullets:
                out[f"s:{s.id}/e:{e.id}/b:{b.id}"] = b.text
    return out


def from_cv(con: sqlite3.Connection, before, after) -> int:
    """Keeps a pair for each CV line or profile the person reworded between two versions; returns how many."""
    was, now = _cv_texts(before), _cv_texts(after)
    kept = 0
    try:
        for target, text in now.items():
            if target in was:
                kept += record(con, "cv_line", target, was[target], text)
    except sqlite3.Error:
        log.exception("could not keep the edits of the CV")
    return kept


# ----------------------------------------------------------------------------------------- what they show

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'-]*", text.lower())


def changes(draft: str, final: str) -> tuple[Counter, Counter, list[tuple[str, str]]]:
    """(words taken out, words put in, one-for-one swaps) between a draft and its final, word by word.

    Only small edits count as taking a word out: a cut of more than `SPAN_MAX` words is a cut of content
    (a clause, a sentence), which says nothing about the words in it.
    """
    d, f = _tokens(draft), _tokens(final)
    dropped, added, swaps = Counter(), Counter(), []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, d, f, autojunk=False).get_opcodes():
        if tag in ("delete", "replace") and i2 - i1 <= SPAN_MAX:
            dropped.update(d[i1:i2])
        if tag in ("insert", "replace"):
            added.update(f[j1:j2])
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1:
            swaps.append((d[i1], f[j1]))
    return dropped, added, swaps


def pairs(con: sqlite3.Connection, kind: str | None = None, limit: int = RECENT) -> list[sqlite3.Row]:
    """The kept pairs, newest first, of one kind or all."""
    if kind is None:
        return con.execute("SELECT * FROM writing_edits ORDER BY updated DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return con.execute("SELECT * FROM writing_edits WHERE kind = ? ORDER BY updated DESC, id DESC LIMIT ?",
                       (kind, limit)).fetchall()


def _quote(text: str, word: str) -> str:
    m = re.search(rf"\b{re.escape(word)}\b", text, re.I)
    if not m:
        return ""
    start = max(0, m.start() - QUOTE_CHARS // 2)
    return ("..." if start else "") + text[start:m.end() + QUOTE_CHARS // 2].strip() + "..."


def dropped_words(rows: list) -> dict[str, list]:
    """Each word the person took out of drafts and never wrote in a final, with the pairs it left."""
    from jobhunt import record as record_mod

    written = {w for r in rows for w in _tokens(r["final"])}
    out: dict[str, list] = {}
    for r in rows:
        dropped, _added, _swaps = changes(r["draft"], r["final"])
        for word in dropped:
            if len(word) >= WORD_MIN and word not in record_mod.STOP and word not in written:
                out.setdefault(word, []).append(r)
    return out


def _never_use(word: str, found: list) -> dict:
    swaps = {new for r in found for old, new in changes(r["draft"], r["final"])[2] if old == word}
    instead = f' and wrote "{sorted(swaps)[0]}" instead' if len(swaps) == 1 else ""
    evidence = [{"label": f'{KIND_WORDS[r["kind"]]}: "{_quote(r["draft"], word)}"', **(
        {"job_id": r["job_id"]} if r["job_id"] else {})} for r in found[:EVIDENCE_SHOWN]]
    return {"fingerprint": f"writing:never_use:{word}", "area": "writing",
            "title": f'Never use "{word}"?',
            "why": f'You took "{word}" out of {len(found)} of Hal\'s drafts{instead}.',
            "effect": f'Hal will leave "{word}" out of your letters, answers and CV suggestions, and list it under '
                      "Check before sending if it appears.",
            "evidence": evidence, "source": SOURCE,
            "change": {"setting": "cvdocs.never_use", "add": word, "learned": f'Never use "{word}"'}}


def _letter_length(con: sqlite3.Connection) -> list[dict]:
    from jobhunt.core import settings

    cut = [r for r in pairs(con, "letter") if len(r["draft"].split()) >= LETTER_MIN_WORDS
           and len(r["final"].split()) < TRIM_SHARE * len(r["draft"].split())]
    if len(cut) < LENGTH_MIN:
        return []
    now = int(settings.get("cvdocs.letter_words") or 0)
    lengths = [len(r["final"].split()) for r in cut]
    median = round(statistics.median(lengths))
    offered = [value for value, _ in settings.setting("cvdocs.letter_words").choices]
    target = min(offered, key=lambda v: (abs(v - median), v))
    if target >= now:
        return []
    return [{"fingerprint": f"writing:letter_words:{now}:{target}", "area": "writing",
             "title": f"Write letters of about {target} words?",
             "why": f"You cut {len(cut)} of Hal's letters to about {median} words.",
             "effect": f"Hal will write letters of about {target} words instead of {now}.",
             "evidence": [{"label": f"Letter cut from {len(r['draft'].split())} to {len(r['final'].split())} words",
                           **({"job_id": r["job_id"]} if r["job_id"] else {})} for r in cut[:EVIDENCE_SHOWN]],
             "source": SOURCE,
             "change": {"setting": "cvdocs.letter_words", "value": target, "learned": f"Letters of about {target} "
                                                                                       "words"}}]


def learned(con: sqlite3.Connection) -> list[dict]:
    """The suggestions the person's edits make: words to never use, and a shorter letter length."""
    from jobhunt.core import settings

    have = {str(p).lower() for p in settings.get("cvdocs.never_use") or []}
    out = [_never_use(word, found) for word, found in sorted(dropped_words(pairs(con)).items())
           if len(found) >= DROP_MIN and word not in have]
    return out + _letter_length(con)
