"""Which file to read first, and what kind of document it probably is, from its path and its first words.

Rich personal documents come first so the person's profile fills early in a long ingest: a CV gives jobs,
education and skills in one go, while a lecture note gives a few tags. The order is `RULES`, top first; a
file's rank is the first rule whose words appear in its path or name, else its kind by suffix. For plain
text files the first `PEEK_CHARS` characters are read too, so "notes.txt" holding a CV still ranks as one.

The words are field-neutral on purpose: a humanities graduate's dissertation, placement report and
museum volunteering reference rank as a software graduate's do.

Real folders are messy (checked on a 37,000-file dump, 2026-11-05), so a word alone is not enough:
- Only document files (`DOC_SUFFIXES`) are rich; code, data and logs go by their suffix whatever their name
  says ("resume_checkpoint.json" is no CV).
- Folder names count only near the file: a strong kind (`STRONG`, such as a CV or a transcript) from the
  `STRONG_DEPTH` nearest folders, any other kind from the nearest one, so every file under "Personal
  projects" is not a project document.
- A bare "transcript" is usually a transcription; an academic transcript is named as one (transcript of
  study, a student number), or is a PDF or Word file, or says so in its first words.
- Project boilerplate (`NOT_DOCUMENTS`: LICENSE, CHANGELOG and the like) is never a certificate or a report.
- A text file named as a CV must read like one (`PEEK_CV_SECTIONS`), so a "resume" command file is not; one
  named as a certificate must say what it certifies (`PEEK_CERTIFICATE`), so a study note on TLS
  certificates is not, and one named as a reference must read like a referee's letter (`PEEK_REFERENCE`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PEEK_CHARS = 2000
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".rst", ".org"}
SLIDE_SUFFIXES = {".pptx", ".ppt", ".key", ".odp"}
EMAIL_SUFFIXES = {".eml", ".msg", ".mbox"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif", ".heic", ".bmp"}
MEDIA_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".mp4", ".mov", ".avi", ".mkv", ".webm"}
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".rb",
                 ".php", ".swift", ".kt", ".scala", ".r", ".m", ".sql", ".sh", ".ipynb", ".html", ".css", ".lua"}
BINARY_DOCS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".pages", ".epub"}
DOC_SUFFIXES = BINARY_DOCS | TEXT_SUFFIXES | {".tex"}
STRONG = {"cv", "transcript", "letter", "certificate", "reference", "job_description"}
STRONG_DEPTH = 2          # a CV folder's files are CVs; its grandparent's name is still about them
WEAK_DEPTH = 1            # "Reports/x.pdf" is a report, "Projects/hambot/notes/x.md" is not a project


@dataclass(frozen=True)
class Rule:
    """One kind of document: its rank, doc_type, vault category and the words that name it."""

    rank: int
    doc_type: str
    category: str
    words: re.Pattern
    rich: bool = True        # read by the model in full; light documents get tags from their text alone


def _w(pattern: str) -> re.Pattern:
    """Words matched at word edges, where "_", "-" and "." count as edges too, as in file names."""
    return re.compile(rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])", re.I)


RULES = (
    Rule(0, "cv", "CVs and letters", _w(r"cv|c\.v|resume|r\u00e9sum\u00e9|curriculum[ _-]?vitae")),
    Rule(1, "transcript", "Education", _w(r"transcript[ _-]?of[ _-]?(?:study|records?)|academic[ _-]?transcript|"
                                          r"transcript[ _-]?\d+|higher[ _-]?education[ _-]?achievement[ _-]?report|"
                                          r"academic[ _-]?record|degree[ _-]?certificate|"
                                          r"diploma|statement[ _-]?of[ _-]?results|exam[ _-]?results")),
    Rule(2, "letter", "CVs and letters", _w(r"cover[ _-]?letter|covering[ _-]?letter|personal[ _-]?statement|"
                                            r"supporting[ _-]?statement|statement[ _-]?of[ _-]?purpose|"
                                            r"motivation[ _-]?letter|application[ _-]?letter")),
    Rule(3, "certificate", "Certificates", _w(r"certificates?|certifications?|certified|awards?|prizes?|"
                                              r"badges?|(?:personal|driving|practising|professional)[ _-]?licen[cs]e|"
                                              r"accreditation|completion")),
    Rule(4, "reference", "CVs and letters", _w(r"references?|referees?|testimonials?|recommendations?")),
    Rule(5, "job_description", "Work", _w(r"job[ _-]?description|jd|person[ _-]?specification|contract|"
                                          r"offer[ _-]?letter|employment|appraisal|timesheet|placement")),
    Rule(6, "report", "Coursework", _w(r"dissertation|thesis|report|coursework|assignment|assessment|"
                                       r"project[ _-]?report|lab[ _-]?report|proposal|literature[ _-]?review")),
    Rule(7, "project", "Projects", _w(r"portfolio|projects?|case[ _-]?study|showcase")),
    Rule(8, "writing", "Writing", _w(r"blog|essays?|articles?|poems?|stor(?:y|ies)|reviews?|columns?|"
                                     r"newsletter|zine|writing")),
    Rule(9, "notes", "Study notes", _w(r"notes?|lectures?|lecs?|seminars?|tutorials?|revision|week[ _-]?\d+|"
                                       r"wk[ _-]?\d+|chapter|reading[ _-]?list|handout"), rich=False),
    Rule(10, "slides", "Slides", _w(r"slides?|presentation|deck"), rich=False),
)
EMAIL = Rule(11, "email", "Email", _w(r"email|mail"), rich=False)
ARTWORK = Rule(12, "artwork", "Creative work", _w(r"art|artwork|drawing|painting|illustration|poster|design|"
                                                   r"sketch|photography|portfolio"), rich=False)
IMAGE = Rule(13, "image", "Media", _w(r"image"), rich=False)
MEDIA = Rule(14, "media", "Media", _w(r"media"), rich=False)
CODE = Rule(15, "code", "Code", _w(r"code"), rich=False)
OTHER = Rule(16, "other", "Other", _w(r"other"), rich=False)
REPO = Rule(7, "repository", "Code", _w(r"repository"))
COURSE = Rule(9, "course", "Study notes", _w(r"course"), rich=False)
# CV first-page words, for a plain text file whose name says nothing.
PEEK_CV = re.compile(r"curriculum vitae|\b(work|employment) (history|experience)\b.*\beducation\b", re.I | re.S)
PEEK_LETTER = re.compile(r"^\s*dear\b", re.I | re.M)
PEEK_TRANSCRIPT = re.compile(r"transcript of (?:study|records?)|academic transcript|"
                             r"\bmodule\b.*\bcredits?\b.*\bmarks?\b", re.I | re.S)
PEEK_CV_SECTIONS = re.compile(r"^\W*(?:profile|personal profile|summary|experience|work experience|employment|"
                              r"work history|education|qualifications|skills|key skills|references)\W*$", re.I | re.M)
CV_SECTIONS_MIN = 2       # a CV has headed sections; a note that says "resume" once does not
TRANSCRIPT_WORD = _w(r"transcripts?")
ACADEMIC_NAME = re.compile(r"\d|year|stage|semester|term|university|college|school|academic|official|degree", re.I)
PEEK_CERTIFICATE = re.compile(r"this is to certify|has (?:successfully )?completed|is awarded|awarded to|"
                              r"certificate of (?:completion|achievement|attendance|participation)", re.I)
WEAK_RICH = {"report", "project", "writing"}
PEEK_REFERENCE = re.compile(r"to whom it may concern|i have known|pleased to (?:recommend|support)|"
                            r"reference for|in my capacity as|without hesitation|i (?:would )?recommend", re.I)
NOT_DOCUMENTS = re.compile(r"(?:licen[cs]e|copying|notice|third[ _-]?party[ _-]?notices|changelog|history|"
                           r"code[ _-]?of[ _-]?conduct|contributing|security|authors|maintainers)", re.I)


def _peek(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            return f.read(PEEK_CHARS)
    except OSError:
        return ""


def by_suffix(suffix: str) -> Rule:
    """The rule for a file whose name says nothing, by its kind."""
    suffix = suffix.lower()
    if suffix in SLIDE_SUFFIXES:
        return RULES[10]
    if suffix in EMAIL_SUFFIXES:
        return EMAIL
    if suffix in IMAGE_SUFFIXES:
        return IMAGE
    if suffix in MEDIA_SUFFIXES:
        return MEDIA
    if suffix in CODE_SUFFIXES:
        return CODE
    if suffix in TEXT_SUFFIXES:
        return RULES[9]
    return OTHER


def _named(name: str, folders: tuple[str, ...], binary: bool) -> Rule | None:
    """The first rule the file's name, then its nearest folders, name; strong kinds reach further up.

    A weaker rich kind (report, project, writing) comes from a folder only for a PDF or Word file, and never
    beats a name that says lecture, week or slides: a course folder called "Major Project" holds notes.
    """
    for depth, text in enumerate((name, *reversed(folders))):
        reach = {r.doc_type: STRONG_DEPTH if r.doc_type in STRONG else (WEAK_DEPTH if binary else 0) if r.rich
                 else len(folders) for r in RULES}
        hit = next((r for r in RULES if r.words.search(text) and depth <= reach[r.doc_type]), None)
        if hit is not None and hit.doc_type in WEAK_RICH and depth == 0:
            light = next((r for r in RULES if not r.rich and r.words.search(name)), None)
            return light or hit
        if hit:
            return hit
    return None


def rule_for(rel: str, path: Path | None = None) -> Rule:
    """The kind of document at `rel` (its path in the folder given); `path` lets a text file's start count.

    The file's own name is tried before its folders, so "Lecture notes/CV.pdf" is a CV and
    "CV/notes.txt" is notes only when the start of its text says nothing else.
    """
    p = Path(rel)
    suffix = p.suffix.lower()
    name = p.stem
    if suffix in IMAGE_SUFFIXES:
        hit = next((r for r in (RULES[3],) if r.words.search(name)), None)
        return hit or (ARTWORK if ARTWORK.words.search(rel) else IMAGE)
    if suffix in SLIDE_SUFFIXES:
        return RULES[10]        # a deck is never read in full by the model, whatever its name says
    if suffix not in DOC_SUFFIXES and suffix not in EMAIL_SUFFIXES:
        return by_suffix(suffix)
    if NOT_DOCUMENTS.fullmatch(name):
        return OTHER
    hit = _named(name, p.parent.parts, suffix in BINARY_DOCS)
    if hit is None and suffix in BINARY_DOCS and TRANSCRIPT_WORD.search(name) \
            and (ACADEMIC_NAME.search(name) or TRANSCRIPT_WORD.fullmatch(name.strip())):
        hit = RULES[1]
    start = _peek(path) if path is not None and suffix in TEXT_SUFFIXES else None
    if hit is RULES[0] and start is not None and len(PEEK_CV_SECTIONS.findall(start)) < CV_SECTIONS_MIN:
        hit = None
    if hit is RULES[3] and suffix in TEXT_SUFFIXES and not PEEK_CERTIFICATE.search(f"{name}\n{start or ''}"):
        hit = None
    if hit is RULES[4] and suffix in TEXT_SUFFIXES and not (start and PEEK_REFERENCE.search(start)):
        hit = None      # "12. Reference Types" was a programming lecture, not a referee's letter
    if start is not None and (hit is None or not hit.rich):
        if PEEK_CV.search(start):
            return RULES[0]
        if PEEK_TRANSCRIPT.search(start):
            return RULES[1]
        if PEEK_LETTER.search(start):
            return RULES[2]
    if hit is not None and suffix in EMAIL_SUFFIXES and hit.rank > EMAIL.rank:
        return EMAIL
    return hit or by_suffix(suffix)
