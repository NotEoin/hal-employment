"""Wrong-place hints: a cheap check, with no AI, that a source looks like it belongs in another section.

"This looks like a CV. Move it to CVs?" is shown to the person beside the source; Hal never moves anything
on its own, because the section the person chose is what says what a file is (sections plan, 2026-11-09).
The checks are the bulk read's own first-glance rules (`ingest.rank`): the file's name, then the start of its
text. Only three kinds are hinted, the ones a wrong section changes most: a CV, a transcript and a letter.

Within Documents the person chooses what each file is, and Hal suggests another of the section's kinds when
the file looks like one (`suggest_kind`: a transcript, a certificate or a reference), the same way: shown,
never acted on. It is kept in the source's `suggestion` as `kind:<doc kind>`, apart from a section's id.
"""
from __future__ import annotations

from pathlib import Path

from jobhunt.ingest import rank
from jobhunt.sections import registry

CV_RULE, TRANSCRIPT_RULE, LETTER_RULE = rank.RULES[0], rank.RULES[1], rank.RULES[2]
# Each hint: the section it suggests, and the sections where such a file is already in its place (a reference
# starts "Dear ..." like a letter; a transcript is one of the person's documents too).
HINTS = (("cv", ("cv",)), ("official", ("official", "document")), ("letter", ("letter", "job_doc", "document")))
CERTIFICATE_RULE, REFERENCE_RULE = rank.RULES[3], rank.RULES[4]
KIND_PREFIX = "kind:"
# Each kind of document suggested within a section: the words at the start of its text, then its file's name.
KIND_HINTS = (("transcript", rank.PEEK_TRANSCRIPT, TRANSCRIPT_RULE),
              ("certificate", rank.PEEK_CERTIFICATE, CERTIFICATE_RULE),
              ("reference", rank.PEEK_REFERENCE, REFERENCE_RULE))


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


def suggest_kind(section: str, doc_kind: str, name: str, text: str) -> str:
    """`kind:<doc kind>` when a source of `section` named `name` with this text looks like another of the
    section's kinds of document than `doc_kind` (a transcript, certificate or reference), else ""."""
    kinds = registry.get(section).doc_kinds
    stem, head = Path(name).stem, text[:rank.PEEK_CHARS]
    for kind, peek, rule in KIND_HINTS:
        if kind in kinds and (peek.search(head) or rule.words.search(stem)):
            return "" if kind == doc_kind else KIND_PREFIX + kind
    return ""
