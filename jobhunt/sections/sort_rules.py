"""The sorter's rules for what is not the person's own work: libraries and build output, published papers,
other people's posters and reports, and what a course gave out.

`sections.sorter` proposes a section for each file and project folder of a dump. A sort of a real 10,000-file
graduate dump proposed 6,931 rows, many of them wrong (2026-11-09), so these rules keep out what a person
should never have to untick. When in doubt the sorter leaves a file out with a reason, or groups it, rather
than proposing it.

Design decisions, each with its reason:

- Nothing inside a library or build folder (`VENDORED`) is proposed, at any depth: a project's
  "_build/libs/<library>" folder was proposed about 20 times as the person's projects. The walk
  (`ingest.discover`) still enters "build" and the like outside a repository, as the bulk read may hold the
  person's own files there; the sorter, which only proposes, leaves them out.
- "lib" and "libs" (`PACKAGE_HOMES`) hold the person's code as often as a third-party package, so only a
  project found inside one (a folder with its own README or manifest) is taken for a package.
- A project's own folder is the top of a project: a project found inside a library or build folder is never
  one, so the project around it is proposed whole.
- A published paper is reading, never the person's work (`published_paper`): its first page has an abstract
  or an arXiv or DOI line, beside a venue, "et al." citations or a references heading. Only the first page,
  before any references heading, counts for the header signs, as an essay's bibliography holds DOIs and
  journal names too; a document that names the person or shows a submission (a declaration, a student
  number) is never a paper.
- A reading-list folder ("papers", "readings", "references", "literature", `READING_FOLDER`) is a strong
  sign on its own: a PDF in one is a paper, and so is any other document there with a paper's sign or a
  title for a name (`paper_title`: a long title-case phrase with no student number or module code). Real
  papers kept in "Dissertation/papers" were proposed as coursework, as their first pages gave too few
  signs and the "Dissertation" folder made them handed-in work (2026-11-09). A file named as the person's
  notes on a reading (`OWN_ON_READING`) is never one, and the sorter asks only for files not named as a
  CV, a reference or another strong kind.
- A poster or report named after someone else is theirs (`someone_elses`): a classmate's
  "CSC3095_<Name>_Project_Poster.pdf" was proposed as the person's coursework. Hal has no list of names, so
  a run of capitalised words counts as a name only when it is not a usual title word (`TITLE_WORDS`) and
  either follows a module code, as submitted files are named, or is a line of its own (an author line) at
  the start of the text. It is never the person's own name (`roles.identity`), and with no name in the
  settings nothing is taken for someone else's.
- What a course gave out is lecture material at most (`course_material`): a file in a downloaded materials
  folder ("Linked materials", "Resources", "Readings", "Handouts"), or a course document by its name (a
  project list, a handbook, guidance, a brief or specification, a marking scheme). A brief says what to
  write, not what the person wrote.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from jobhunt.ingest import roles

# Folders of other people's code or of generated output: dependency folders, virtual environments, build and
# compiler output, and caches (compared lower case).
VENDORED = {"_build", "build", "dist", "vendor", "vendors", "third_party", "third-party", "node_modules",
            "bower_components", "site-packages", ".venv", "venv", "target", "out", "bin", "obj", "pods",
            ".gradle", "__pycache__"}
PACKAGE_HOMES = {"lib", "libs"}
VENDORED_SKIP = "file in a library or build folder"


def _parts(path: Path, base: Path) -> tuple[str, ...]:
    try:
        return tuple(p.lower() for p in path.relative_to(base).parts)
    except ValueError:
        return ()


def in_vendored(path: Path, base: Path) -> bool:
    """True when the file at `path` sits in a library or build folder below `base`."""
    return any(p in VENDORED for p in _parts(path.parent, base))


def vendored_project(folder: Path, base: Path) -> bool:
    """True when a project found at `folder` is a library or build output: it sits in a library or build
    folder, or it is a package inside a "lib" or "libs" folder."""
    parts = _parts(folder, base)
    return any(p in VENDORED for p in parts) or any(p in PACKAGE_HOMES for p in parts[:-1])


# Published papers --------------------------------------------------------------------------------------------

PAPER_HEAD_CHARS = 3000        # a paper's first page: title, authors, venue, abstract
ABSTRACT = re.compile(r"^\W*abstract\b", re.I | re.M)
ARXIV_OR_DOI = re.compile(r"\barxiv\s*:?\s*\d{4}\.\d{4,5}|\b(?:doi\s*:?\s*|doi\.org/)10\.\d{4,9}/\S+", re.I)
VENUE = re.compile(r"proceedings of|conference on|journal of|transactions on|workshop on|\bvol\.\s*\d|"
                   r"\bvolume\s+\d|\bissn\b|\bpreprint\b|published (?:online|by)|accepted for publication|"
                   r"\(c\)\s*\d{4}|copyright\s+\d{4}", re.I)
ET_AL = re.compile(r"\bet al\b", re.I)
ET_AL_MIN = 2                  # one "et al." is a citation in anyone's essay; papers cite many
REFERENCES = re.compile(r"^\W*(?:references|bibliography|works cited)\W*$", re.I | re.M)
PAPER_SIGNS_MIN = 3            # an abstract or an identifier, and two more of the paper's parts
# Signs that a document was handed in by the person, which a published paper never carries.
SUBMITTED = re.compile(r"submitted (?:by|for|to|in partial)|in partial fulfil+ment|student (?:number|no\b|id)|"
                       r"candidate number|word count|declaration|i (?:hereby )?declare|supervis(?:or|ed by)", re.I)
PAPER_WORDS = "A published paper. Hal keeps your own work, not your reading."
# Folders people keep their reading in; "references" only in the plural, as "Reference letters" are about them.
READING_FOLDER = re.compile(r"(?<![a-z])(?:papers|readings?|reading[ _-]?lists?|references|literature|"
                            r"bibliography)(?![a-z])", re.I)
READING_NAME_WORDS = 3         # "Dissertation papers", "Week 3 readings"; a longer name is about something else
READING_DEPTH = 2              # the file's own folder or the one above it ("papers/2021/x.pdf")
TITLE_WORDS_MIN = 5            # "Learning Transferable Visual Models From ..." against "Essay final"
TITLE_CAPS_SHARE = 0.8         # a title capitalises its main words; a sentence-case name of notes does not
# Words a title leaves in lower case.
TITLE_SMALL = {"a", "an", "the", "of", "for", "from", "and", "or", "in", "on", "to", "with", "via", "by", "at",
               "as", "vs", "into", "over", "under", "without", "is", "are"}
STUDENT_ID = re.compile(r"\d{6,}")
# A file's own words that make it the person's notes or work on a reading, never the reading itself.
OWN_ON_READING = re.compile(r"(?<![a-z])(?:notes?|summary|summaries|answers?|response|my|essay|report|draft|"
                            r"annotat\w*|critique)(?![a-z])", re.I)


def own_words(con) -> list[str]:
    """The words that name the person (`roles.identity`): their name's parts, other names, emails and handles
    from the settings and their CVs; [] when the settings give no name."""
    from jobhunt.core import settings

    name = str(settings.get("you.name") or "")
    if not name.strip():
        return []
    return roles.identity(name, str(settings.get("you.other_names") or ""), con, roles.own_links())


def reading_folder(rel: str) -> bool:
    """True when a file at `rel` sits in a folder of reading (`READING_FOLDER`), its own or the one above."""
    parts = PurePosixPath(rel).parent.parts[1:][-READING_DEPTH:]
    return any(READING_FOLDER.search(part) and len(SPLIT.split(part.strip())) <= READING_NAME_WORDS
               for part in parts)


def paper_title(stem: str) -> bool:
    """True when a file's name `stem` is a published work's title: `TITLE_WORDS_MIN` or more words, the main
    ones capitalised, with no student number, module code or word of the person's own notes."""
    words = [w for w in re.split(r"[\s_]+", stem.strip(" ._-")) if w.strip(".")]
    if len(words) < TITLE_WORDS_MIN or STUDENT_ID.search(stem) or OWN_ON_READING.search(stem.replace("_", " ")) \
            or any(MODULE_CODE.match(w) for w in words):
        return False
    main = [w for w in words if w.lower() not in TITLE_SMALL]
    capitalised = [w for w in main if w[0].isupper() or w[0].isdigit()]
    return bool(main) and len(capitalised) >= TITLE_CAPS_SHARE * len(main)


def published_paper(text: str, own: list[str], *, name: str = "", reading: bool = False,
                    pdf: bool = False) -> bool:
    """True when `text` reads like a published paper's first page, or when a document in a reading folder
    (`reading`) is a PDF (`pdf`), has a title for a name (`name`, the file's stem) or shows a paper's sign
    (see the module notes)."""
    refs = REFERENCES.search(text)
    head = text[:min(PAPER_HEAD_CHARS, refs.start() if refs else len(text))]
    if SUBMITTED.search(head) or (own and roles.names_person(head, own)):
        return False
    first = bool(ABSTRACT.search(head)) or bool(ARXIV_OR_DOI.search(head))
    signs = [ABSTRACT.search(head), ARXIV_OR_DOI.search(head), VENUE.search(head),
             len(ET_AL.findall(text)) >= ET_AL_MIN, refs]
    if reading and not OWN_ON_READING.search(name.replace("_", " ")) and (pdf or paper_title(name) or any(signs)):
        return True
    return first and sum(bool(s) for s in signs) >= PAPER_SIGNS_MIN


# Other people's posters and reports ---------------------------------------------------------------------------

POSTER_OR_REPORT = re.compile(r"^(?:posters?|reports?)$", re.I)
MODULE_CODE = re.compile(r"^[A-Za-z]{2,5}\d{3,5}[A-Za-z]?$")
NAME_WORD = re.compile(r"^[A-Z][a-z]+(?:['-][A-Z]?[a-z]+)?$")
NAME_WORDS_MAX = 3             # a given name, a middle name and a family name
# Capitalised words in the names of course work that are not people's names.
TITLE_WORDS = {
    "project", "projects", "poster", "posters", "report", "reports", "final", "draft", "group", "individual",
    "team", "module", "coursework", "assignment", "presentation", "research", "interim", "progress",
    "technical", "lab", "practical", "the", "of", "and", "for", "on", "in", "a", "an", "my", "our", "to",
    "design", "database", "data", "system", "systems", "software", "web", "network", "security", "analysis",
    "development", "management", "history", "art", "science", "engineering", "computing", "business",
    "study", "case", "literature", "review", "evaluation", "proposal", "introduction", "advanced", "applied",
    "digital", "modern", "early", "social", "public", "policy", "theory", "practice", "media", "creative",
    "writing", "studies", "methods", "dissertation", "thesis", "summary", "abstract", "results", "week",
    "term", "year", "semester", "submission", "version", "copy", "new", "old", "first", "second", "third",
}
SPLIT = re.compile(r"[\s_.\-]+")


def _name_blocks(words: list[str]) -> list[tuple[int, tuple[str, ...]]]:
    """Each run of capitalised words that could be a person's name, with the index of its first word."""
    out, run, start = [], [], 0
    for i, w in enumerate([*words, ""]):
        if NAME_WORD.match(w) and w.lower() not in TITLE_WORDS:
            if not run:
                start = i
            run.append(w)
            continue
        if 0 < len(run) <= NAME_WORDS_MAX:
            out.append((start, tuple(run)))
        run = []
    return out


def _author_line(name: str, head: str) -> bool:
    folded = " ".join(name.lower().split())
    return any(" ".join(line.lower().split()).removeprefix("by ") == folded for line in head.splitlines())


def someone_elses(rel: str, text: str, own: list[str]) -> str:
    """Why a poster or report at `rel` is someone else's, naming them; "" when it is not plain that it is."""
    stem = PurePosixPath(rel).stem
    words = [w for w in SPLIT.split(stem) if w]
    if not own or not any(POSTER_OR_REPORT.match(w) for w in words) or roles.names_person(stem, own):
        return ""
    head = text[:PAPER_HEAD_CHARS]
    if roles.names_person(head, own):
        return ""
    for start, block in _name_blocks(words):
        name = " ".join(block)
        after_code = start > 0 and MODULE_CODE.match(words[start - 1]) is not None and len(block) > 1
        if after_code or _author_line(name, head):
            return f"Someone else's work: it is named after {name}, not you."
    return ""


# Documents that list projects ---------------------------------------------------------------------------------

LISTING_MIN = 3                # two names could be a project and the one it grew from; three is a list
LISTING_NAME_MIN = 3           # "hal" names a project; "ui" or "x" could be any word
LISTING_WORDS = "Describes several of your projects. Hal reads each project from its own files."
NOT_WORD = re.compile(r"[^a-z0-9]+")


def lists_projects(text: str, names: set[str]) -> bool:
    """True when `text` names `LISTING_MIN` or more of the projects `names` (folder names, compared without
    case or punctuation): a plan or dossier about many projects, not one project's own document. A real
    dump's "PORTFOLIO-PLAN.md" and "PROJECT-DOSSIER.md" were proposed as projects (2026-11-09)."""
    folded = f" {NOT_WORD.sub(' ', text.lower())} "
    keys = {NOT_WORD.sub(" ", n.lower()).strip() for n in names}
    return sum(len(k) >= LISTING_NAME_MIN and f" {k} " in folded for k in keys) >= LISTING_MIN


# What a course gave out ---------------------------------------------------------------------------------------

MATERIALS_FOLDER = re.compile(r"(?<![a-z])(?:materials?|resources?|readings?|handouts?|reading[ _-]?lists?)"
                              r"(?![a-z])", re.I)
COURSE_DOCUMENT = re.compile(r"(?<![a-z])(?:project[ _-]?lists?|potential[ _-]?projects?|proposals|handbooks?|"
                             r"guidance|guidelines|guides?|briefs?|specifications?|specs?|"
                             r"marking[ _-]?schemes?|mark[ _-]?schemes?|rubrics?|syllabus|reading[ _-]?lists?|"
                             r"assessment[ _-]?criteria|module[ _-]?(?:outline|descriptor))(?![a-z])", re.I)
# Names that speak of everyone's work, which in a course folder is the course's document, not the person's.
COURSE_PLURALS = re.compile(r"(?<![a-z])(?:projects|posters|demonstrations|dissertations)(?![a-z])", re.I)


def course_material(rel: str, in_course: bool) -> str:
    """Why a document at `rel` is what a course gave out, at most lecture material; "" when it is not."""
    path = PurePosixPath(rel)
    if any(MATERIALS_FOLDER.search(part) for part in path.parent.parts[1:]):
        return "In a folder of course materials: what the course gave you."
    if COURSE_DOCUMENT.search(path.stem) or (in_course and COURSE_PLURALS.search(path.stem)):
        return "A course document, such as a brief or a project list: what the course set, not your work."
    return ""
