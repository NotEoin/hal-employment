"""Grades a CV the way the people and software that read it do.

Three kinds of check. Parsing checks read the rendered PDF back as an
applicant tracking system would and test that the text, contact details,
headings and dates survive. Writing checks look at the words: length, how
much is measured, bullets against walls of prose, cliches, first person,
British spelling. Judgement checks ask the AI for what rules cannot see,
against a rubric. Given a job, the CV is also matched against its advert.

Every check returns a score from 0 to 1, what it found, and what to change;
groups are weighted into a mark out of 100. The PDF is read with pypdfium2
(`cvstudio/pdfread.py`), so no check needs poppler. Spelling uses the system's
British English dictionary (enchant) when there is one, else the bundled
pyspellchecker, which accepts a British or American form of a word, so every
install gets a spelling check. Only the profile links the person gave must be
written out, and projects are optional, so a CV outside software is not marked
down for lacking them.
"""
import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path

from . import ai, record
from .core import paths
from .cvstudio import pdfread

log = logging.getLogger(__name__)
SPELL_TIMEOUT_S = 30

# Section names parsers know; a heading outside these may not be recognised as a section at all.
STANDARD_HEADINGS = {"education", "experience", "work experience", "employment", "projects", "skills",
                     "technical skills", "summary", "profile", "additional", "achievements", "awards",
                     "leadership", "positions of responsibility", "interests", "publications", "volunteering",
                     "certifications", "research and projects", "research", "languages", "skills and interests",
                     "employment history", "work history", "education and qualifications"}
CLICHES = re.compile(r"\b(passionate|results[- ]driven|team player|hard[- ]working|go[- ]getter|self[- ]starter|"
                     r"motivated|dynamic|detail[- ]oriented|think outside the box|synergy|best of breed|innovative|"
                     r"excellent communication skills|proven track record|fast learner|leverag\w*|spearhead\w*|"
                     r"cutting[- ]edge|seamless\w*|guru|ninja|rockstar)\b", re.I)
DUTIES = re.compile(r"^(responsible for|duties (included|include)|tasks?:|in charge of)", re.I)
WEAK_VERBS = re.compile(r"^(helped|assisted|worked on|worked with|participated|was involved|involved in|"
                        r"exposed to|gained experience|utili[sz]ed|tasked with|contributed to)\b", re.I)
# The first word of a strong line is a verb in the past tense: built, designed, wrote, ran.
IRREGULAR = {"built", "wrote", "led", "ran", "made", "won", "taught", "drew", "set", "cut", "found", "brought",
             "held", "kept", "sold", "spent", "put", "read", "chose", "gave", "grew", "rewrote", "rebuilt", "shipped",
             "split", "sped", "fed", "met", "took", "began", "became", "overran", "undertook", "oversaw"}
AMERICAN = re.compile(r"\b(optimiz\w*|analyz\w*|organiz\w*|prioritiz\w*|visualiz\w*|customiz\w*|summariz\w*|"
                      r"minimiz\w*|maximiz\w*|recogniz\w*|color\w*|behavior\w*|modeled|modeling|centers?|centered|"
                      r"defense|catalog)\b", re.I)
NUMBER = re.compile(r"\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(\+44\s?7\d{3}|\(?07\d{3}\)?)\s?\d{3}\s?\d{3}")
RANGE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{4}\s*[-–]\s*"
                   r"((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{4}|Present)\b|"
                   r"\b(19|20)\d{2}\s*[-–]\s*((19|20)\d{2}|Present)\b", re.I)
BAD_DATE = re.compile(r"\b(summer|winter|spring|autumn|michaelmas|lent|easter) (term )?\d{4}\b|'\d{2}\b|\b\d{1,2}/\d{4}\b", re.I)
PRIVATE = re.compile(r"\b(date of birth|d\.o\.b|dob|born on|age:|marital status|married|religion|"
                     r"national insurance|ni number|passport number|driving licence number)\b", re.I)
CLASSIFICATION = re.compile(r"first[- ]class|\bfirst\b|2:1|upper second|2:2|lower second|third[- ]class|distinction|merit", re.I)
MODULE_CODE = re.compile(r"\b[A-Z]{3,4}\d{3,4}\b")
ODD_CHAR = re.compile("[ﬀ-ﬆ-�]|\\(cid:\\d+\\)")


def fingerprint(cv: dict) -> str:
    """Tells one version of a CV from another, so an old review is known to be old."""
    return hashlib.sha1(json.dumps(cv, sort_keys=True).encode()).hexdigest()[:12]


def pdf_text(pdf: Path, layout: bool = False) -> str:
    """The PDF's text as a parser reads it; "" when it cannot be read."""
    return pdfread.text(pdf, layout=layout)


def pdf_pages(pdf: Path) -> int:
    """How many pages the PDF has; 0 when it cannot be read."""
    return pdfread.pages(pdf)


def body_size(pdf: Path) -> float | None:
    """The type size most of the text is set in, from the size of each character drawn."""
    return pdfread.body_size(pdf)


def last_page_fill(pdf: Path) -> float:
    """How far down the last page the text reaches, from 0 to 1."""
    return pdfread.last_page_fill(pdf)


def lines_of(text: str) -> list[str]:
    """An entry's lines: its bullets when written as "- " lines, else its sentences."""
    text = re.sub(r"\*\*", "", text or "")
    rows = [r.strip() for r in text.splitlines() if r.strip()]
    if rows and all(r.startswith("- ") for r in rows):
        return [r[2:].strip() for r in rows]
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", " ".join(rows)) if len(x.strip()) > 3]


def is_bulleted(text: str) -> bool:
    rows = [r.strip() for r in (text or "").splitlines() if r.strip()]
    return bool(rows) and all(r.startswith("- ") for r in rows)


def entries(cv: dict) -> list[tuple[str, str]]:
    """The CV's entries that describe something done: (where, text)."""
    out = [(f"Education: {e['qualification']}", e.get("detail", "")) for e in cv.get("education", [])[:1]]
    out += [(f"Project: {p['name']}", p.get("body", "")) for p in cv.get("projects", [])]
    out += [(f"Experience: {x['role']}, {x['org']}", x.get("body", "")) for x in cv.get("experience", [])]
    return [(w, t) for w, t in out if t]


def _verb_first(text: str) -> bool:
    first = re.sub(r"^(co|re)-", "", (text.split() or [""])[0].lower())
    first = re.sub(r"[^a-z]", "", first)
    return (first.endswith("ed") or first in IRREGULAR) and not WEAK_VERBS.search(text) and not DUTIES.search(text)


def strong_start(line: str) -> bool:
    if _verb_first(line):
        return True
    # A line led by a label ("Dissertation, Title: tested whether...") is judged by the verb after it.
    led = re.match(r"^[^:]{3,100}:\s+(?=[a-z])", line)
    return bool(led) and _verb_first(line[led.end():])


def check(name: str, score: float, found: str, fix: str = "", weight: float = 1.0) -> dict:
    return {"name": name, "score": round(max(0.0, min(1.0, score)), 2), "found": found,
            "fix": fix if score < 0.999 else "", "weight": weight}


# Words of the trade a general dictionary lacks; the applicant's own go in data/cv-words.txt.
TECH_WORDS = {"backend", "backends", "frontend", "frontends", "config", "configs", "renderer", "renderers", "sandboxed",
              "sandboxing", "reimplementation", "reimplemented", "archiver", "repo", "repos", "runtime", "runtimes",
              "toolchain", "codebase", "async", "middleware", "dataset", "datasets", "hashset", "subprocess",
              "multithreading", "multithreaded", "localhost", "webhook", "webhooks", "plugin", "plugins", "workflow",
              "workflows", "refactored", "refactoring", "linter", "boolean", "enum", "enums", "namespace", "serialised",
              "deserialised", "tokeniser", "tokenised", "embeddings", "chunking", "reranking", "pretrained", "finetuned",
              "hyperparameters", "overfitting", "upsampling", "inpainting", "segmentation", "interpretability",
              "pathologies", "pathology", "backbones", "cybersecurity", "pentesting", "fuzzing", "geolocation",
              "microservices", "microservice", "dockerised", "containerised", "frontmost", "hotkey", "hotkeys",
              "screenshot", "screenshots", "uptime", "latencies", "pipelined", "versioned", "timestamped", "dedupe",
              "deduplicated", "deduplication", "autonomously", "autopilot", "rangefinders", "rangefinder", "flashcard",
              "flashcards", "cashing", "fellwalking", "skateboarding"}


def _words() -> Path:
    # The old layout kept the list as data/cv-words.txt; a moved data folder keeps it as data/cv/words.txt.
    return paths.data_dir() / "cv-words.txt" if paths.is_legacy_layout() else paths.cv_dir() / "words.txt"


# WORDS, the words the applicant accepted, is resolved when used.
_LIVE = {"WORDS": _words}


def __getattr__(name):  # old path constants, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    raise AttributeError(name)


def _live(name: str):
    """An old path constant: the value a test assigned to the module, else the live path."""
    return globals()[name] if name in globals() else _LIVE[name]()


def own_words() -> set[str]:
    words = _live("WORDS")
    return {w.strip().lower() for w in words.read_text().split()} if words.exists() else set()


def misspelt(text: str, known: set[str]) -> list[str] | None:
    """Lower-case words a British English dictionary does not know, or None when no dictionary is installed.

    Capitalised words are left out: names of people, places and tools are not in the dictionary,
    while most typos are in ordinary words."""
    known = known | TECH_WORDS | own_words()
    words = sorted({w for w in re.findall(r"(?<![\w./@-])[a-z][a-z']+(?![\w./@-])", text) if w not in known})
    try:
        out = subprocess.run(["enchant-2", "-d", "en_GB", "-l"], input="\n".join(words), capture_output=True,
                             text=True, timeout=SPELL_TIMEOUT_S)
        if out.returncode == 0:
            return sorted(set(out.stdout.split()))
    except (OSError, subprocess.TimeoutExpired):
        log.info("no system dictionary; using the bundled one")
    return _bundled_check(words)


# British endings and their American forms, so a dictionary that knows only one form accepts both.
_UK_US = [(r"ise$", "ize"), (r"ised$", "ized"), (r"ising$", "izing"), (r"isation$", "ization"), (r"our$", "or"),
          (r"ours$", "ors"), (r"tre$", "ter"), (r"tres$", "ters"), (r"lled$", "led"), (r"lling$", "ling"),
          (r"ogue$", "og"), (r"ence$", "ense"), (r"yse$", "yze"), (r"ysed$", "yzed"), (r"ysing$", "yzing"),
          (r"mme$", "m"), (r"mmes$", "ms"), (r"ae", "e"), (r"oe", "e")]


def _bundled_check(words: list[str]) -> list[str] | None:
    """Words pyspellchecker does not know in either their British or American form; None without it."""
    try:
        from spellchecker import SpellChecker
    except ImportError:
        return None
    spell = SpellChecker(language="en")
    out = []
    for w in spell.unknown(words):
        variants = {re.sub(uk, us, w) for uk, us in _UK_US if re.search(uk, w)}
        if not variants or spell.unknown(variants) == variants:
            out.append(w)
    return sorted(out)


# A. Parsing -------------------------------------------------------------------------

def parsing(cv: dict, pdf: Path) -> list[dict]:
    raw, laid = pdf_text(pdf), pdf_text(pdf, layout=True)
    words = raw.split()
    size_mb = pdf.stat().st_size / 1e6
    # Whether there is a text layer at all; how long the CV is belongs to the length checks.
    out = [check("Text can be read back", 1.0 if len(words) >= 30 and size_mb < 2.5 else 0.0,
                 f"{len(words)} words read back; file {size_mb * 1000:.0f} kB",
                 "Render the CV from text, under 2.5 MB; an image of a CV cannot be read at all.", 1)]
    # Textkernel rejects text whose symbols or word lengths look like garbage.
    symbols = sum(1 for c in raw if not (c.isalnum() or c.isspace() or c in ".,;:-()&/%'+#@|"))
    avg = sum(len(w) for w in words) / max(1, len(words))
    out.append(check("Text reads as words", 1.0 if symbols / max(1, len(raw)) < 0.05 and 4 <= avg <= 20 else 0.3,
                     f"{symbols} symbols in {len(raw)} characters; words average {avg:.1f} letters",
                     "Some text extracts as symbols; avoid icon fonts and decorative characters.", 2))
    odd = sorted(set(ODD_CHAR.findall(raw)))
    out.append(check("No ligatures or unmapped glyphs", 1.0 if not odd else 0.0, "none" if not odd else " ".join(odd[:8]),
                     "Some letters extract as ligatures or private glyphs (fi, fl, icons); use a font with a ToUnicode map.", 3))
    wide = sum(1 for line in laid.splitlines() if re.search(r"\S\s{6,}\S", line))
    out.append(check("Single-column layout", 1.0 if wide < 6 else max(0.0, 1 - wide / 40),
                     f"{wide} lines with text in separate columns",
                     "Keep to one column; side-by-side columns are read across, mixing their text.", 4))
    split = len(re.findall(r"^\s*[A-Z][\w &/-]+\n+\s*\d{2,3}\s*$", raw, re.M))
    out.append(check("Tables read back in order", 1.0 if split <= 2 else max(0.0, 1 - split / 10),
                     f"{split} labels parted from their values in plain text",
                     "Lay out marks and similar tables as lines of text, 'Module 80', so each value stays with its label.", 2))
    top = raw[:700]
    emails, phones = EMAIL.findall(raw), PHONE.findall(raw)
    # Profile links the person gave must be written out in the text, not only clickable.
    given = [u for k, u in (("github", "github.com"), ("linkedin", "linkedin.com")) if (cv.get("contact") or {}).get(k)]
    visible = [u for u in given if u in raw.lower()]
    contact = (bool(EMAIL.search(top)) + bool(PHONE.search(top)) + (len(emails) == 1) + (len(phones) <= 1)
               + (len(visible) == len(given))) / 5
    out.append(check("Contact details in the body", contact,
                     f"{len(emails)} email, {len(phones)} phone at the top; links written out: {', '.join(visible) or 'none'}",
                     "One email and one phone as plain text at the top, with any profile links written out, "
                     "not only linked.", 3))
    heads = [h.strip().lower() for h in re.findall(r"^([A-Z][A-Z &]{3,40})\s*$", raw, re.M)]
    odd_heads = [h for h in heads if h not in STANDARD_HEADINGS]
    out.append(check("Standard section headings", 1.0 - len(odd_heads) / max(1, len(heads)) if heads else 0.0,
                     f"headings: {', '.join(heads) or 'none found'}" + (f"; unusual: {', '.join(odd_heads)}" if odd_heads else ""),
                     "Use the headings parsers expect, each on its own line: Education, Experience, Projects, Skills.", 3))
    dated = [x for x in cv.get("experience", []) if RANGE.search(x.get("dates", ""))]
    dated += [e for e in cv.get("education", []) if RANGE.search(e.get("meta", ""))]
    total = len(cv.get("experience", [])) + len(cv.get("education", []))
    bad = BAD_DATE.findall(raw)
    out.append(check("A date range on every entry", (len(dated) / max(1, total)) * (0.5 if bad else 1),
                     f"{len(dated)} of {total} entries dated as 'Mon YYYY - Mon YYYY'" + (", with terms or short years" if bad else ""),
                     "Give each job and qualification a range like 'Sep 2023 - Jun 2026'; parsers date your skills from it.", 2))
    return out


# B. Structure and length -------------------------------------------------------------

def structure(cv: dict, pdf: Path) -> list[dict]:
    pages, raw = pdf_pages(pdf), pdf_text(pdf).lower()
    fill = last_page_fill(pdf)
    if pages == 1:
        length = 1.0
    elif pages == 2:
        length = 0.85 if fill >= 0.6 else 0.6
    else:
        length = 0.0
    out = [check("Length", length, f"{pages} page{'s' if pages != 1 else ''}, last page {round(100 * min(1, fill))}% full",
                 "One full page is the norm for graduate tech roles; two only if both are full of relevant evidence.", 4)]
    order = [s for s in ("education", "experience", "projects", "skills") if s in raw]
    pos = {s: raw.find(s) for s in order}
    early = "education" in pos and pos["education"] < len(raw) * 0.35
    out.append(check("Education first, then the strongest evidence", 1.0 if early else 0.5,
                     "education " + ("opens" if early else "is not in the first third of") + " the CV",
                     "Put education straight after your contact details while you are a recent graduate.", 3))
    parts = entries(cv)
    bulleted = [w for w, t in parts if is_bulleted(t)]
    out.append(check("Bullets, not paragraphs", len(bulleted) / max(1, len(parts)),
                     f"{len(bulleted)} of {len(parts)} entries are bullets",
                     "Write each entry as 2-4 bullets, one result each; recruiters skim, and paragraphs hide the results.", 2))
    every = [line for _, t in parts for line in lines_of(t)]
    good = [line for line in every if 8 <= len(line.split()) <= 35]
    out.append(check("Bullet length", len(good) / max(1, len(every)),
                     f"{len(good)} of {len(every)} lines between 8 and 35 words",
                     "Keep each bullet to one or two printed lines.", 2))
    size = body_size(pdf)
    out.append(check("Readable type", 1.0 if size and size >= 10 else 0.6 if size and size >= 9 else 0.3,
                     f"body text {size or '?'}pt", "Set body text at 10-12pt.", 1))
    return out


# C. Content ------------------------------------------------------------------------

def content(cv: dict) -> list[dict]:
    parts = entries(cv)
    every = [line for _, t in parts for line in lines_of(t)]
    strong = [line for line in every if strong_start(line)]
    weak = [line for line in every if not strong_start(line)]
    out = [check("Lines open with a past-tense verb", len(strong) / max(1, len(every)) / 0.9,
                 f"{len(strong)} of {len(every)} lines" + (f"; e.g. {weak[0][:60]!r}" if weak else ""),
                 "Open each line with what you did: Built, Designed, Cut, Wrote; not 'Responsible for' or 'Helped'.", 4)]
    measured = [line for line in every if NUMBER.search(line)]
    share = len(measured) / max(1, len(every))
    out.append(check("Measured results", 1.0 if share >= 0.4 else share / 0.4,
                     f"{len(measured)} of {len(every)} lines carry a number ({round(100 * share)}%)",
                     "Say how much, how fast or how many where you truly know it: tests, users, accuracy, speed.", 5))
    modules = [r[0] for e in cv.get("education", []) for g in e.get("modules") or [] for r in g["rows"]]
    text = _canon(" ".join([t for _, t in parts] + [f"{p.get('name', '')} {p.get('stack', '')}" for p in cv.get("projects", [])]
                           + [cv.get("moreProjects", "")] + [e.get("detail", "") for e in cv.get("education", [])]
                           + modules))
    listed = [i for g in cv.get("skills", []) for i in g[1]]
    stems = {w[:5] for w in re.findall(r"[a-z]{4,}", text)}
    # Backed when any name the skill goes by appears, or every long word of it does, in any order and form.
    backed = [i for i in listed if any(_has(w, text) for w in _skill_words(i))
              or (re.findall(r"[a-z]{4,}", i.lower())
                  and all(w[:5] in stems for w in re.findall(r"[a-z]{4,}", i.lower())))]
    out.append(check("Skills backed up by the entries", len(backed) / max(1, len(listed)) / 0.7,
                     f"{len(backed)} of {len(listed)} listed skills appear in an entry"
                     + (f"; not shown in use: {', '.join(i for i in listed if i not in backed)[:160]}" if len(backed) < len(listed) else ""),
                     "Show each listed skill in use in a project or job, or take it off the list.", 4))
    projects = cv.get("projects", [])
    proven = [p for p in projects if NUMBER.search(p.get("body", ""))]
    # Projects are optional; when listed, each should show what came of it.
    out.append(check("Projects with evidence", 1.0 if not projects else len(proven) / len(projects),
                     f"{len(projects)} projects, {len(proven)} with a measured result",
                     "Give each project a result: what it produced, for whom, how many or how well it was "
                     "received.", 3))
    team = [line for line in every if re.search(r"\bteam\b|\bgroup\b", line, re.I)]
    owned = [line for line in team if NUMBER.search(line) and strong_start(line)]
    out.append(check("Your part in team work", 1.0 if not team else len(owned) / len(team),
                     f"{len(team)} lines mention a team; {len(owned)} give its size and your part",
                     "In team work, give the team's size and what you yourself built or decided.", 2))
    return out


def _skill_words(item: str) -> list[str]:
    """The words a listed skill goes by in running text: "Flask" for "Flask", "rag" for "Retrieval-augmented generation (RAG)"."""
    inner = re.findall(r"\(([^)]+)\)", item)
    names = [*re.split(r"/", re.sub(r"\s*\(.*?\)", "", item)), *(re.split(r",\s*", inner[0]) if inner else [])]
    return [w for n in names for w in [_canon(n).strip()] if w]


# D. Language ---------------------------------------------------------------------------

def language(cv: dict) -> list[dict]:
    parts = entries(cv)
    every = [line for _, t in parts for line in lines_of(t)]
    text = " ".join([cv.get("summary", "")] + [t for _, t in parts] + [cv.get("moreProjects", "")]
                    + [a[1] for a in cv.get("additional", [])])
    text = re.sub(r"\*\*", "", text)
    known = {w.lower() for g in cv.get("skills", []) for i in g[1] for w in re.findall(r"[A-Za-z]+", i)}
    wrong = misspelt(text, known)
    out = [check("Spelling", 0.5 if wrong is None else 1.0 if not wrong else 0.6 if len(wrong) == 1 else 0.0,
                 "no British English dictionary installed" if wrong is None else
                 (f"words the dictionary does not know: {', '.join(wrong)}" if wrong else "no unknown words"),
                 "Correct any that are typos (two errors measurably cut a CV's interview chances); "
                 "mark the rest as fine so they are not flagged again.", 5)]
    out[0]["words"] = wrong or []
    us = sorted({m.group(0).lower() for m in AMERICAN.finditer(text)})
    out.append(check("Consistent British spelling", 1.0 if not us else 0.5, ", ".join(us) or "consistent",
                     "Use British spellings for UK employers: optimise, analyse, behaviour, modelling.", 2))
    cl = sorted({m.group(0).lower() for m in CLICHES.finditer(text)})
    out.append(check("No buzzwords", 1.0 if not cl else max(0.0, 1 - len(cl) / 3), ", ".join(cl) or "none",
                     "Replace adjectives about yourself with the evidence behind them.", 3))
    codes = sorted(set(MODULE_CODE.findall(text)))
    out.append(check("No unexplained codes", 1.0 if not codes else 0.4, ", ".join(codes) or "none",
                     "Name modules and internal projects in words; a recruiter cannot decode COMP3001.", 2))
    first = [line for line in every if re.search(r"\b(I|my|we|our)\b", line)]
    ends = {line.rstrip()[-1:] == "." for line in every}
    out.append(check("Consistent voice and punctuation", (1.0 if not first else 0.6) * (1.0 if len(ends) <= 1 else 0.8),
                     f"{len(first)} lines in the first person; end punctuation {'consistent' if len(ends) <= 1 else 'mixed'}",
                     "Drop I, my and we; end every bullet the same way.", 3))
    return out


# E. UK graduate ------------------------------------------------------------------------

def uk(cv: dict, pdf: Path) -> list[dict]:
    raw = pdf_text(pdf)
    private = sorted({m.group(0).lower() for m in PRIVATE.finditer(raw)})
    out = [check("No personal details employers must not ask for", 1.0 if not private else 0.0,
                 ", ".join(private) or "none", "Remove date of birth, age, marital status and similar.", 2)]
    degree = (cv.get("education") or [{}])[0]
    line = f"{degree.get('qualification', '')} {degree.get('grade', '')} {degree.get('meta', '')}"
    parts = [bool(re.search(r"\b(BSc|BEng|MEng|MSc|BA|MA|MComp|MSci)\b", line)), bool(CLASSIFICATION.search(line)),
             bool(re.search(r"universit|college|institute", line, re.I)), bool(RANGE.search(line)),
             not re.search(r"predicted|expected", line, re.I)]
    out.append(check("Degree line complete", sum(parts) / 5, line.strip()[:120],
                     "Give the degree, classification, university and dates on the degree's own lines.", 3))
    rows = [r for g in degree.get("modules") or [] for r in g["rows"]]
    floor = degree.get("pdfModulesMin")
    shown = [r for r in rows if floor is None or r[1] >= floor]
    low = [r for r in shown if r[1] < 70]
    out.append(check("A few strong modules", 1.0 if 0 < len(shown) <= 6 and not low else 0.6 if shown else 0.8,
                     f"{len(shown)} modules shown" + (f", {len(low)} under 70" if low else ""),
                     "Show five or six of your best, role-relevant modules, 70 and over.", 1))
    school = [e for e in cv.get("education", [])[1:] if len(e.get("detail", "")) > 260]
    out.append(check("School results kept short", 1.0 if not school else 0.5,
                     f"{len(school)} earlier qualifications run long", "Summarise school results in a line or two.", 1))
    rtw = re.search(r"right to work|citizen|no (visa )?sponsorship|settled status", raw, re.I)
    out.append(check("Right to work stated", 1.0 if rtw else 0.5, "stated" if rtw else "not stated",
                     "One line saying you need no visa sponsorship answers a screening question early.", 1))
    refs = re.search(r"references (are )?available on request|referees?:", raw, re.I)
    out.append(check("No references line", 0.0 if refs else 1.0, "present" if refs else "none",
                     "Drop 'References available on request'; it is assumed.", 0.5))
    summary = cv.get("summary", "")
    words = len(summary.split())
    bland = CLICHES.search(summary)
    out.append(check("Profile earns its place", 1.0 if words == 0 or (words <= 50 and not bland) else 0.6 if words <= 70 else 0.3,
                     f"{words} words" + (", with buzzwords" if bland else ""),
                     "Keep a profile to two lines of facts (role sought, degree, strongest evidence), or leave it out.", 1))
    return out


# Judgement -------------------------------------------------------------------------

AI_TIMEOUT_S = 600
AI_CTX = 16384     # a two-page CV, an advert and the record's passages fit in this many tokens


def _ask(prompt_id: str, user: str) -> dict:
    """One JSON request with prompt `prompt_id`; raises `ai.LLMUnavailable` when the AI cannot answer now."""
    from .ai import prompts

    return ai.chat_json(prompts.text(prompt_id), user, role="checker", timeout=AI_TIMEOUT_S, ctx=AI_CTX,
                        purpose=prompt_id)


def judgement(cv_text: str, persona: str = "") -> tuple[list[dict], str]:
    """The AI's grade for each criterion of the recruiter's rubric, or why there is none.

    `persona` names the readers the CV type is for ("a recruiter for {fits}"")."""
    reason = ai.ready(purpose="cv_judge")
    if reason:
        return [], reason
    try:
        reply = _ask("cv_judge", f"Read it as {persona}.\n\n{cv_text}" if persona else cv_text)
    except ai.LLMUnavailable as e:
        return [], str(e)
    out = []
    for g in reply.get("grades", []):
        try:
            grade = max(1, min(5, int(g.get("grade"))))
        except (TypeError, ValueError):
            continue
        out.append(check(str(g.get("criterion") or "?"), (grade - 1) / 4, f"{grade}/5: {g.get('why') or ''}",
                         str(g.get("fix") or ""), 1.5))
    return out, ""


# Totals ------------------------------------------------------------------------------
# The mark comes from the rule-based checks alone: a model grading the same CV twice can
# differ by fifteen points, so its review is shown beside the mark, not folded into it.

GROUPS = [("Parsing", parsing, True), ("Structure and length", structure, True), ("Content", content, False),
          ("Language", language, False), ("UK graduate", uk, True)]
# A fault that stops a CV being read, or that employers must not see, caps the whole mark.
CAPS = {"Text can be read back": 10, "No ligatures or unmapped glyphs": 50, "Single-column layout": 55,
        "No personal details employers must not ask for": 40}


def _group_score(checks: list[dict]) -> float | None:
    total = sum(c["weight"] for c in checks)
    return sum(c["score"] * c["weight"] for c in checks) / total if total else None


def grade(mark: float) -> str:
    return "A" if mark >= 85 else "B" if mark >= 72 else "C" if mark >= 60 else "D" if mark >= 45 else "E"


def score(cv: dict, pdf: Path) -> dict:
    """Grades a CV from its data and its rendered PDF, by rule; the same CV always gets the same mark.

    Each check carries points; the mark is the share of points earned, so a check
    worth 5 matters five times as much as one worth 1."""
    out, earned, possible, capped = [], 0.0, 0.0, []
    for name, fn, needs_pdf in GROUPS:
        checks = fn(cv, pdf) if needs_pdf else fn(cv)
        got, of = sum(c["score"] * c["weight"] for c in checks), sum(c["weight"] for c in checks)
        out.append({"name": name, "score": round(100 * got / of) if of else None, "checks": checks})
        earned, possible = earned + got, possible + of
        capped += [(c["name"], CAPS[c["name"]]) for c in checks if c["name"] in CAPS and c["score"] < 0.5]
    mark = round(100 * earned / possible) if possible else 0
    if capped:
        mark = min(mark, *(cap for _, cap in capped))
    return {"mark": mark, "grade": grade(mark), "groups": out, "capped": [n for n, _ in capped]}


def review(cv_text: str) -> dict:
    """A recruiter's read of the CV by the local model: a grade and a fix for each criterion."""
    grades, note = judgement(cv_text)
    return {"checks": grades, "note": note,
            "average": round(sum(c["score"] for c in grades) / len(grades) * 4 + 1, 1) if grades else None}


# Matching a job -------------------------------------------------------------------------
# Matching against one advert is reported apart from the CV's own mark: a CV is not worse
# for missing a skill one employer wants. The model finds requirements and evidence; every
# quote it gives is checked against the text, and the arithmetic is done here.

# Names that mean the same thing to a recruiter, so a CV is never told to add what it already has.
ALIASES = {"js": "javascript", "ts": "typescript", "node.js": "node", "nodejs": "node", "c++": "cpp", "c#": "csharp",
           ".net": "dotnet", "golang": "go", "python3": "python", "reactjs": "react", "react.js": "react",
           "next.js": "next", "nextjs": "next", "vue.js": "vue", "postgres": "postgresql", "psql": "postgresql",
           "mongo": "mongodb", "k8s": "kubernetes", "amazon web services": "aws", "gcp": "google cloud",
           "google cloud platform": "google cloud", "microsoft azure": "azure", "restful": "rest", "rest api": "rest",
           "rest apis": "rest", "ci/cd": "ci", "continuous integration": "ci", "github actions": "ci",
           "machine learning": "ml", "artificial intelligence": "ai", "large language models": "llm", "llms": "llm",
           "retrieval-augmented generation": "rag", "scikit-learn": "sklearn", "unit testing": "testing",
           "test-driven development": "tdd", "object-oriented": "oop", "object oriented": "oop",
           "data structures and algorithms": "algorithms", "linux/unix": "linux", "unix": "linux",
           "bash": "shell", "shell scripting": "shell", "mysql/mariadb": "mysql", "sqlite3": "sqlite"}
SKILLS = sorted({*ALIASES.values(), "python", "java", "javascript", "typescript", "go", "rust", "c", "cpp",
                 "csharp", "sql", "lua", "kotlin", "swift", "scala", "ruby", "php", "react", "angular", "flask",
                 "fastapi", "django", "spring", "docker", "kubernetes", "terraform", "aws", "azure", "linux", "git",
                 "graphql", "grpc", "kafka", "redis", "postgresql", "mysql", "sqlite", "mongodb", "pytorch",
                 "tensorflow", "numpy", "pandas", "sklearn", "ml", "ai", "llm", "rag", "nlp", "computer vision",
                 "distributed systems", "microservices", "concurrency", "multithreading", "networking", "security",
                 "cryptography", "testing", "pytest", "tdd", "ci", "agile", "scrum", "oop", "algorithms",
                 "data structures", "api", "rest", "html", "css", "tailwind", "electron", "embedded", "spark",
                 "airflow", "dbt", "snowflake", "excel", "tableau", "power bi", "statistics", "shell", "jira"}
                - {"go", "c"})  # ordinary words too often ("go live", "a, b, c") to count as skills
REQUIREMENTS_HEADING = re.compile(r"requirement|qualification|must[- ]have|about you|you have|you will have|"
                                  r"what we('re| are) looking for|skills|experience", re.I)


def _canon(text: str, aliases: dict[str, str] | None = None) -> str:
    table = ALIASES if aliases is None else aliases
    text = f" {text.lower()} "
    for alias in sorted(table, key=len, reverse=True):
        text = re.sub(rf"(?<![\w+#.]){re.escape(alias)}(?![\w+#])", f" {table[alias]} ", text)
    return text


def _has(term: str, text: str) -> int:
    return len(re.findall(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", text))


def keywords(advert: str, cv_text: str, terms: list[str] | None = None,
             aliases: dict[str, list[str]] | None = None) -> dict:
    """Which of the advert's skills the CV names, weighted by how much the advert leans on them.

    `terms` and `aliases` come from the CV type's `[keywords]`; without them the built-in list of
    software skills is used."""
    table = None if terms is None else {a.lower(): k.lower() for k, names in (aliases or {}).items() for a in names}
    skills = SKILLS if terms is None else sorted({t.lower() for t in terms} | set(table.values()))
    mine = _canon(cv_text, table)
    # Terms under a requirements heading count for more than those in the company blurb.
    lines, weight_of, boost = advert.splitlines(), {}, 1.0
    for line in lines:
        if len(line) < 60 and REQUIREMENTS_HEADING.search(line):
            boost = 1.5
        for term in skills:
            n = _has(term, _canon(line, table))
            if n:
                weight_of[term] = weight_of.get(term, 0) + n * boost
    terms = sorted(weight_of, key=lambda t: -weight_of[t])[:25]
    have = [t for t in terms if _has(t, mine)]
    thin = [t for t in have if _has(t, mine) == 1]
    total = sum(weight_of[t] for t in terms)
    return {"covered": round(100 * sum(weight_of[t] for t in have) / total) if total else None,
            "have": have, "missing": [t for t in terms if t not in have], "thin": thin, "of": len(terms)}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w%+#.]", " ", text.lower()).split())


def _quoted(quote: str, source: str) -> bool:
    """True when a quote the model gave really is in the source, allowing for spacing and punctuation."""
    q = _norm(quote)
    return len(q) >= 3 and q in _norm(source)


def requirements(job, cv_text: str, facts: str = "") -> tuple[list[dict], str]:
    """The advert's requirements and the CV's evidence for each, in two separate passes."""
    advert = (job["description"] or "")[:12000]
    reason = ai.ready(purpose="cv_needs")
    if reason or not advert.strip():
        return [], reason or "the job has no advert text"
    try:
        needs = _ask("cv_needs", f"Job: {job['title']} at {job['company']}\n<<<ADVERT\n{advert}\nADVERT>>>"
                     ).get("requirements", [])
        needs = [n for n in needs if isinstance(n, dict) and n.get("need") and _quoted(str(n.get("quote", "")), advert)][:12]
        if not needs:
            return [], "the model found no requirements it could quote from the advert"
        listing = "\n".join(f"{i}. {n['need']}" for i, n in enumerate(needs, 1))
        kept = record.context([n["need"] for n in needs])
        found = _ask("cv_evidence", f"Requirements:\n{listing}\n\n<<<CV\n{cv_text}\nCV>>>"
                     f"\n\n<<<FACTS\n{facts}\nFACTS>>>\n\n<<<RECORD\n{kept}\nRECORD>>>").get("evidence", [])
    except ai.LLMUnavailable as e:
        return [], str(e)
    by_id = {}
    for e in found:
        try:
            by_id[int(e.get("id"))] = e
        except (TypeError, ValueError, AttributeError):
            continue
    out = []
    for i, n in enumerate(needs, 1):
        e = by_id.get(i, {})
        quote = str(e.get("quote") or "")
        match = e.get("match") if e.get("match") in ("direct", "adjacent") else "missing"
        # Where the quote really is: the CV (or the letter sent with it), the record only, or nowhere.
        where = "cv" if _quoted(quote, f"{cv_text}\n{facts}") else "record" if kept and _quoted(quote, kept) else ""
        if match != "missing" and not where:
            match, quote = "missing", ""  # evidence the CV does not hold is no evidence
        kind = n.get("kind") if n.get("kind") in ("knockout", "must", "nice") else "must"
        try:
            weight = max(1, min(3, int(n.get("weight") or 2)))
        except (TypeError, ValueError):
            weight = 2
        out.append({"need": str(n["need"]), "kind": kind, "weight": weight, "advert": str(n["quote"]),
                    "match": match, "evidence": quote, "where": where if match != "missing" else ""})
    return out, ""


def match(job, cv_text: str, facts: str = "") -> dict:
    """How well a CV fits one job: requirement coverage with its evidence, and the advert's skills it names."""
    reqs, note = requirements(job, cv_text, facts)
    core = [r for r in reqs if r["kind"] != "nice"]
    total = sum(r["weight"] for r in core)
    # Coverage is what the employer will see: evidence only in the record counts once it is on the CV.
    shown = lambda r: r["match"] != "missing" and r["where"] == "cv"  # noqa: E731 - a one-line test, used twice
    met = sum(r["weight"] * (1.0 if r["match"] == "direct" else 0.5) for r in core if shown(r))
    coverage = round(100 * met / total) if total else None
    knocked = [r for r in reqs if r["kind"] == "knockout" and not shown(r)]
    label = None if coverage is None else (
        "Long shot" if knocked or coverage < 50 else "Strong" if coverage >= 80 else "Reasonable stretch")
    return {"coverage": coverage, "label": label, "requirements": reqs, "knockouts_unmet": [r["need"] for r in knocked],
            "in_record": [r["need"] for r in reqs if r["where"] == "record"],
            "keywords": keywords(job["description"] or "", cv_text), "note": note}
