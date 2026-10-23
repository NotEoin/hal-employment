"""A fixed reference scanner for the looks gate: reads a printed CV back as an applicant tracking system does.

These are the parsing checks of the CV scorer Hal retired (`jobhunt/cvscore.py` at 6ef987b), copied here
unchanged so the goldens in tests/goldens/scanner keep their meaning: a look that prints text a parser can
no longer read moves a score past the gate's tolerance. Every pattern, threshold and weight is that
scorer's; changing one changes the gate, and goes in a commit that records the goldens again.
"""
from __future__ import annotations

import re
from pathlib import Path

from jobhunt.cvstudio import pdfread

# Section names parsers know; a heading outside these may not be recognised as a section at all.
STANDARD_HEADINGS = {"education", "experience", "work experience", "employment", "projects", "skills",
                     "technical skills", "summary", "profile", "additional", "achievements", "awards",
                     "leadership", "positions of responsibility", "interests", "publications", "volunteering",
                     "certifications", "research and projects", "research", "languages", "skills and interests",
                     "employment history", "work history", "education and qualifications"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(\+44\s?7\d{3}|\(?07\d{3}\)?)\s?\d{3}\s?\d{3}")
MONTH = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{4}"
DASH = r"\s*[-\u2013]\s*"
YEAR = r"(19|20)\d{2}"
RANGE = re.compile(rf"\b{MONTH}{DASH}({MONTH}|Present)\b|\b{YEAR}{DASH}({YEAR}|Present)\b", re.I)
SEASON = r"\b(summer|winter|spring|autumn|michaelmas|lent|easter) (term )?\d{4}\b"
BAD_DATE = re.compile(rf"{SEASON}|'\d{{2}}\b|\b\d{{1,2}}/\d{{4}}\b", re.I)
# Ligatures (U+FB00 to U+FB06), private-use glyphs, the replacement character, and glyphs with no text.
ODD_CHAR = re.compile("[\ufb00-\ufb06\ue000-\uf8ff\ufffd]|\\(cid:\\d+\\)")
MIN_WORDS = 30        # fewer words read back means there is no usable text layer
MAX_MB = 2.5          # a larger file is likely an image of a CV
TOP_CHARS = 700       # contact details count as "at the top" within this much of the text


def check(name: str, score: float, found: str, weight: float) -> dict:
    """One check's result, its score clamped to 0 to 1 and rounded as the retired scorer rounded it."""
    return {"name": name, "score": round(max(0.0, min(1.0, score)), 2), "found": found, "weight": weight}


def parsing(cv: dict, pdf: Path) -> list[dict]:
    """The parsing checks of a v1 CV (`model.to_v1`) and the PDF printed from it."""
    raw, laid = pdfread.text(pdf), pdfread.text(pdf, layout=True)
    words = raw.split()
    size_mb = pdf.stat().st_size / 1e6
    out = [check("Text can be read back", 1.0 if len(words) >= MIN_WORDS and size_mb < MAX_MB else 0.0,
                 f"{len(words)} words read back; file {size_mb * 1000:.0f} kB", 1)]
    # Textkernel rejects text whose symbols or word lengths look like garbage.
    symbols = sum(1 for c in raw if not (c.isalnum() or c.isspace() or c in ".,;:-()&/%'+#@|"))
    avg = sum(len(w) for w in words) / max(1, len(words))
    out.append(check("Text reads as words", 1.0 if symbols / max(1, len(raw)) < 0.05 and 4 <= avg <= 20 else 0.3,
                     f"{symbols} symbols in {len(raw)} characters; words average {avg:.1f} letters", 2))
    odd = sorted(set(ODD_CHAR.findall(raw)))
    out.append(check("No ligatures or unmapped glyphs", 1.0 if not odd else 0.0, " ".join(odd[:8]) or "none", 3))
    wide = sum(1 for line in laid.splitlines() if re.search(r"\S\s{6,}\S", line))
    out.append(check("Single-column layout", 1.0 if wide < 6 else max(0.0, 1 - wide / 40),
                     f"{wide} lines with text in separate columns", 4))
    split = len(re.findall(r"^\s*[A-Z][\w &/-]+\n+\s*\d{2,3}\s*$", raw, re.M))
    out.append(check("Tables read back in order", 1.0 if split <= 2 else max(0.0, 1 - split / 10),
                     f"{split} labels parted from their values in plain text", 2))
    top = raw[:TOP_CHARS]
    emails, phones = EMAIL.findall(raw), PHONE.findall(raw)
    # Profile links the person gave must be written out in the text, not only clickable.
    given = [u for k, u in (("github", "github.com"), ("linkedin", "linkedin.com")) if (cv.get("contact") or {}).get(k)]
    visible = [u for u in given if u in raw.lower()]
    contact = (bool(EMAIL.search(top)) + bool(PHONE.search(top)) + (len(emails) == 1) + (len(phones) <= 1)
               + (len(visible) == len(given))) / 5
    out.append(check("Contact details in the body", contact,
                     f"{len(emails)} email, {len(phones)} phone; links written out: {', '.join(visible) or 'none'}", 3))
    heads = [h.strip().lower() for h in re.findall(r"^([A-Z][A-Z &]{3,40})\s*$", raw, re.M)]
    odd_heads = [h for h in heads if h not in STANDARD_HEADINGS]
    out.append(check("Standard section headings", 1.0 - len(odd_heads) / max(1, len(heads)) if heads else 0.0,
                     f"headings: {', '.join(heads) or 'none found'}", 3))
    dated = [x for x in cv.get("experience", []) if RANGE.search(x.get("dates", ""))]
    dated += [e for e in cv.get("education", []) if RANGE.search(e.get("meta", ""))]
    total = len(cv.get("experience", [])) + len(cv.get("education", []))
    bad = BAD_DATE.findall(raw)
    out.append(check("A date range on every entry", (len(dated) / max(1, total)) * (0.5 if bad else 1),
                     f"{len(dated)} of {total} entries dated as 'Mon YYYY - Mon YYYY'", 2))
    return out


def group_score(checks: list[dict]) -> float | None:
    """The checks' scores weighted by their points; None when there are no points."""
    total = sum(c["weight"] for c in checks)
    return sum(c["score"] * c["weight"] for c in checks) / total if total else None
