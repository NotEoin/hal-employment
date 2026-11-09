"""The sorter's content checks: a word in a file's name is not enough to call it a transcript, a contract or
coursework.

On a real 10,000-file dump (2026-11-09) the name or a stray phrase decided too much: "smart contract.md" was
proposed as a contract, "UMLActivityAnswer.docx" (which mentions a module, credits and marks) as a
transcript, "Lecture 9 Project Planning" as coursework, and Obsidian "Topics/<Concept>.md" notes as
coursework. `sections.sorter.place_kind` asks these checks before it trusts such a guess.

Design decisions, each with its reason:

- A transcript shows marks (`transcript_content`): a row with a module code that ends in a mark, a degree
  classification, a student number, or a certificate's own words ("this is to certify", "awarded"). A
  question worth "10 marks" ends in words, not a mark.
- A contract names the work's terms (`contract_content`): two of its parties (employer and employee), its
  pay (a salary or rate) and its hours.
- A file named as a lecture, a week, a seminar or a workshop is what the person studied
  (`lecture_named`), unless it shows a submission (`sort_rules.SUBMITTED`: a student number, "submitted
  by", a word count, a declaration).
- Coursework is handed in (`submitted_work`): a sign of a submission in its folders or name (an assignment
  number, "coursework", "assignment", "report", "essay", "portfolio", "dissertation", "thesis",
  "submission"), and more words than a stub (`WORK_WORDS_MIN`). Anything less in a course folder is
  lecture material, never coursework: when in doubt, studied, not done.
- Notes in a folder of topics or study notes (`study_note`) are what the person studied, in a course folder
  or not.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from jobhunt.ingest import rank
from jobhunt.sections import sort_rules

# A transcript's row: a module code, then a mark at the end of the line (with a % or a grade letter, and a
# table's closing bar).
MODULE_ROW = re.compile(r"\b[A-Z]{2,5}\s?\d{3,5}[A-Z]?\b.*\b\d{1,3}\s*%?\s*(?:[A-F][+-]?)?\s*\|?\s*$", re.M)
CLASSIFICATION = re.compile(r"first[- ]class|upper second|lower second|\b2:[12]\b|third[- ]class|"
                            r"(?:with|awarded|achieved) (?:a )?(?:distinction|merit)|degree classification", re.I)
STUDENT_NUMBER = re.compile(r"student (?:number|no\.?|id)\s*:?\s*[A-Za-z]*\d", re.I)
PARTIES = re.compile(r"\bemployer\b.*\bemployee\b|\bemployee\b.*\bemployer\b", re.I | re.S)
PAY = re.compile(r"\bsalary\b|\bper annum\b|\bper hour\b|\bhourly rate\b|\bwages?\b|\bpay(?:ment)? of\b", re.I)
HOURS = re.compile(r"hours of work|working hours|\d+(?:\.\d+)? hours (?:per|a|each) week|full[- ]time|part[- ]time",
                   re.I)
CONTRACT_SIGNS_MIN = 2         # parties, pay and hours: any two make a contract of work
LECTURE_NAME = re.compile(r"^(?:lecture|lec|week|wk|seminar|workshop)s?\b|^(?:week|wk)[ _-]?\d", re.I)
SUBMISSION_SIGN = re.compile(r"(?<![a-z])(?:assignment|coursework|cw[ _-]?\d|reports?|essays?|portfolio|"
                             r"dissertation|thesis|submission|submitted)(?![a-z])", re.I)
WORK_WORDS_MIN = 150           # past a title and a stub: a piece handed in has paragraphs
STUDY_FOLDER = re.compile(r"(?<![a-z])(?:topics?|concepts?|notes|study|revision|flashcards?)(?![a-z])", re.I)


def transcript_content(text: str) -> bool:
    """True when `text` shows a transcript's or a degree certificate's content (see the module notes)."""
    return any(p.search(text) for p in (MODULE_ROW, CLASSIFICATION, STUDENT_NUMBER, rank.PEEK_CERTIFICATE))


def contract_content(text: str) -> bool:
    """True when `text` names a contract of work's terms (see the module notes)."""
    return sum(bool(p.search(text)) for p in (PARTIES, PAY, HOURS)) >= CONTRACT_SIGNS_MIN


def lecture_named(stem: str, text: str) -> bool:
    """True when a file named `stem` is named as course material and its text shows no submission."""
    return LECTURE_NAME.search(stem.strip()) is not None and not sort_rules.SUBMITTED.search(text)


def submitted_work(rel: str, text: str) -> bool:
    """True when a document at `rel` shows it was handed in: a sign in its folders or name, and more than a
    stub."""
    path = PurePosixPath(rel)
    named = any(SUBMISSION_SIGN.search(part) for part in (*path.parent.parts[1:], path.stem))
    return named and len(text.split()) >= WORK_WORDS_MIN


def study_note(rel: str) -> bool:
    """True when a document at `rel` sits in a folder of topics or study notes."""
    return any(STUDY_FOLDER.search(part) for part in PurePosixPath(rel).parent.parts[1:][-2:])
