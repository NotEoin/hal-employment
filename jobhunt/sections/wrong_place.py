"""Wrong-place hints: a cheap check, with no AI, that a source looks like it belongs in another section.

"This looks like a CV. Move it to CVs?" is shown to the person beside the source; Hal never moves anything
on its own, because the section the person chose is what says what a file is (sections plan, 2026-11-09).
The checks are the bulk read's own first-glance rules (`ingest.rank`): the file's name, then the start of its
text. Only three kinds are hinted, the ones a wrong section changes most: a CV, a transcript and a letter.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt.ingest import rank

CV_RULE, TRANSCRIPT_RULE, LETTER_RULE = rank.RULES[0], rank.RULES[1], rank.RULES[2]
# Each hint: the section it suggests, and the sections where such a file is already in its place (a reference
# starts "Dear ..." like a letter; a transcript is one of the person's documents too).
HINTS = (("cv", ("cv",)), ("official", ("official", "document")), ("letter", ("letter", "job_doc", "document")))


def _looks_like(name: str, text: str) -> list[str]:
    """The sections a file with this name and text looks like it belongs in, most telling first."""
    stem = Path(name).stem
    head = text[:rank.PEEK_CHARS]
    found = []
    cv_sections = len(rank.PEEK_CV_SECTIONS.findall(text[:rank.CV_PEEK_CHARS]))
    if rank.PEEK_CV.search(head) or (CV_RULE.words.search(stem) and cv_sections >= rank.CV_SECTIONS_MIN):
        found.append("cv")
    if rank.PEEK_TRANSCRIPT.search(head) or TRANSCRIPT_RULE.words.search(stem):
        found.append("official")
    if rank.PEEK_LETTER.search(head) or LETTER_RULE.words.search(stem):
        found.append("letter")
    return found


def suggest(section: str, name: str, text: str) -> str:
    """The section a source named `name` with this text seems to belong in instead of `section`, or ""."""
    looks = _looks_like(name, text)
    for hint, at_home in HINTS:
        if hint in looks:
            return "" if section in at_home else hint
    return ""
