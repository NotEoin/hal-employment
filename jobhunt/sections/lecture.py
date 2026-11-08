"""The `lecture` reader: slides and notes from a course, read for what the person studied, never what they did.

Lecture material is added on an education card (`sections.registry`, section `lecture`, role taught), so the
card says which course it is from; the person may say which of its modules (`store.MODULE_SECTIONS`). It
gives skill facts only (the section creates nothing else), filed as taught, so each one counts as studied
(`profile.strength`) and never as done.

What the reader keeps:

- the note: the material's words with every email address and phone number taken out
  (`ingest.lectures.scrub`), as a lecturer's email on a slide was filed as the person's own (2026-11-05);
  a converted deck, PDF or Word file is written again as study notes (`ingest.lectures.write`), and notes
  the person typed, or an earlier Hal wrote, stay as they are;
- the topics it teaches, each with its quote (`lecture_topics` prompt), as skills;
- in `details`, Hal's suggested module (`coursework.suggest_module`) and the AI's notes and topics, so a read
  again after the person chooses a module asks the AI nothing new.

Design decisions, each with its reason:

- A large deck is summarised, not read whole: past `DECK_CHARS` its words go to the AI in parts of that size,
  at most `PARTS_MAX` of them, and the study notes of each part make the note. Without the AI the note keeps
  the first `DECK_CHARS` and says the rest was left out. A course's full-year deck ran to hundreds of pages,
  and its every word is not what the person studied.
- A topic whose quote is not in the notes is left out, as with coursework methods.
"""
from __future__ import annotations

import hashlib
import logging

from jobhunt.ingest import lectures
from jobhunt.profile import merge
from jobhunt.sections import coursework, readers

log = logging.getLogger(__name__)

PURPOSE = "lecture_topics"
DECK_CHARS = lectures.PROMPT_CHARS     # one part of a deck the AI reads at once, inside a 64K context
PARTS_MAX = 3                          # parts of a large deck summarised; past this the rest is left out
TIMEOUT_S = lectures.TIMEOUT_S
TOPICS_MAX = 20                        # a lecture's topics; past this a list is every word on its slides
TOPIC_CHARS = 60                       # a topic is a few words; a longer one is a sentence
LEFT_OUT = "\n\n(The rest of this file, {chars:,} characters, was left out: it is long, and Hal summarises it once " \
           "the AI can.)\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def study_notes(source: readers.Source, title: str, words: str) -> tuple[str, bool]:
    """The material as the note keeps it, and whether the AI wrote it: study notes for a converted or large file
    (the last read's when its words are the same), else its words; a large file with no notes keeps its first
    part only."""
    typed = all(lectures.is_typed(f.rel) for f in source.files)
    if typed and len(words) <= DECK_CHARS:
        return words, False
    kept = source.details.get("notes")
    if isinstance(kept, dict) and kept.get("sha") == _sha(words) and kept.get("text"):
        return kept["text"], True
    rel = source.files[0].rel
    parts = [words[i:i + DECK_CHARS] for i in range(0, len(words), DECK_CHARS)][:PARTS_MAX]
    notes = [lectures.write(rel, title, part) for part in parts]
    if all(notes):
        return "\n\n".join(notes), True
    if len(words) <= DECK_CHARS:
        return words, False
    return words[:DECK_CHARS] + LEFT_OUT.format(chars=len(words) - DECK_CHARS), False


def ask(source: readers.Source, title: str, notes: str) -> dict | None:
    """The topics the AI found in the notes, {"sha", "topics": [[name, quote]]}; the last read's when the notes are
    the same; None when the AI cannot answer now."""
    sha = _sha(f"{title}\n{notes}")
    kept = source.details.get("ai")
    if isinstance(kept, dict) and kept.get("sha") == sha:
        return kept
    from jobhunt import ai
    from jobhunt.ai import prompts

    user = f"Course: {(source.target or {}).get('title', '')}\nTitle: {title}\n\nNotes:\n{notes[:DECK_CHARS]}"
    try:
        reply = ai.chat_json(prompts.text(PURPOSE), user, role="checker", timeout=TIMEOUT_S, purpose=PURPOSE,
                             priority=2)
    except ai.LLMUnavailable as e:
        log.info("kept lecture material %s without its topics: %s", source.id, e)
        return None
    whole = " ".join(notes.split()).lower()
    topics, seen = [], set()
    for t in reply.get("topics") or []:
        if not isinstance(t, dict):
            continue
        name, quote = " ".join(str(t.get("name") or "").split()), " ".join(str(t.get("quote") or "").split())
        key = merge.skill_key(name)
        if not name or len(name) > TOPIC_CHARS or not quote or quote.lower() not in whole or key in seen:
            continue
        seen.add(key)
        topics.append([name, quote])
    return {"sha": sha, "topics": topics[:TOPICS_MAX]}


class LectureReader:
    """Reads slides or notes into clean study notes and the topics they teach, as skills studied."""

    name = "lecture"

    def read(self, source: readers.Source) -> readers.Reading:
        """The material's notes, title and date, its topics as skills, and Hal's suggested module in `details`."""
        if not source.files:
            raise ValueError("This source has no files to read.")
        words = lectures.scrub(readers.text_of(source.files))
        name = source.title or (source.files[0].rel if len(source.files) == 1 else source.locator)
        title = readers.title_in(coursework.own_words(words, source.files), name)
        notes, by_ai = study_notes(source, title, words)
        found = ask(source, title, notes)
        facts = []
        for topic, quote in (found or {}).get("topics", []):
            place, line = coursework.locate(quote, notes, source.files)
            facts.append(readers.Fact("skill", {"name": topic}, readers.Quote("name", quote, place, line)))
        choices = [m["name"] for m in ((source.target or {}).get("fields") or {}).get("modules") or []
                   if m.get("name")]
        details = {"module": coursework.suggest_module(name, title, notes, choices),
                   **({"notes": {"sha": _sha(words), "text": notes}} if by_ai else {}),
                   **({"ai": found} if found else {})}
        return readers.Reading(title=title, date=readers.date_in(notes), text=notes, facts=facts, details=details)


readers.register(LectureReader())
