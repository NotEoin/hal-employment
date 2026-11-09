"""Sort a big folder for me: where each file or project folder of a dump would go in Your record, as proposals.

The person gives a folder on this computer, a zip or the drop folder; `propose` walks it with the bulk read's
discovery (`ingest.discover`, `group`, `versions`, `privacy`) and returns one `Proposal` for each file or
project folder: a section and kind of document (`sections.registry`), the card it is for where the section
needs one (an education period, a job), and a showcase guess for projects. Nothing here reads a fact: a
proposal becomes a source only when the person accepts it (`sections.sorts`), and then its section's reader
reads it as if they had added it there by hand (sections plan, 2026-11-09).

Design decisions, each with its reason:

- Cheap signals first: the file's name and its folders' names (`ingest.rank`), a code project's git folder
  or manifest, a folder with a README, a course folder of notes and slides (`group.courses`), and the start
  of a document's text through the wrong-place hints (`wrong_place.looks_like`). The AI is asked only about
  a document the rules cannot place, and only to pick one of the sections' kinds of document (`CHOICES`,
  prompt `sort_pick`); it never reads a fact, and "none" leaves the file out.
- Nothing in a library or build folder is proposed, and a project inside one is part of the project around it
  (`sort_rules`): a real dump's "_build/libs" folders gave about 20 wrong project rows (2026-11-09).
- Folders of notes on one subject, a practice site's exercises and a download are one row each, and a notes
  app's own folders are never projects (`sort_units`): one folder of notes gave 20 project rows (2026-11-09).
  Nor is a course's materials folder or a notes vault (`sort_folders`); their files go one by one.
- A word in a name is not enough (`sort_kinds`): a transcript shows marks, a contract its terms, coursework
  a sign of a submission and more than a stub; a file named as a lecture or week, or a note in a topics
  folder, is what the person studied. When in doubt a course folder's file is lecture material.
- Someone else's work is left out: a published paper, or a poster or report named after another person; and
  what a course gave out (a materials folder, a brief, a handbook) is lecture material at most (`sort_rules`).
- A file Hal cannot place is "Left out" with the reason in plain words, never guessed in: a picture with no
  words, a recording, code outside a project, a file that looks private (`privacy.reason`, checked before the
  AI sees a word), one only online, or one the AI did not recognise either.
- Copies are grouped and only the newest is proposed: the same bytes anywhere in the folder, a document saved
  again beside itself ("Essay final (2).docx", by `saved_as`), and copies of one project anywhere in the
  folder, with or without git history (`sort_copies`). A file already in the record is left out, naming
  where it is.
- A course folder (a folder of notes and slides, or one named like a module, a year or a course) makes its
  notes and slides lecture material and its named work coursework. Its card is the education card whose
  institution, subject or qualification its folders name, else the one whose dates hold the file's date,
  else the only one; with none the proposal waits for the person to choose (`needs_card`).
- A code project or README folder in a course, year or module folder (`_course_folder`,
  `sort_courses.course_of`) is coursework for that course, held as its files: a real dump's ten
  "practical-1" to "practical-10" folders and "assignment-3" repositories were proposed as projects
  (2026-11-09). The review shows a module's code folders as one row (`sort_review`), and the person can
  star coursework on its course to make it a degree project.
- A code project on this computer is proposed as a folder (`repo`), so its history stays readable; one from a
  zip is proposed as its files (`upload`). A project is guessed a showcase piece only when it is the
  person's own (in no course or year folder), holds their own commits (`repo.share`), and its README has
  `SHOWCASE_README_WORDS` or more, as a README that long says what the project is and how to use it; at most
  `STARS_MAX` a sort (`cap_stars`, newest first). Ten first-year practicals were pre-starred when a long
  README was enough (2026-11-09); the person stars the rest.
- The AI is asked about at most `AI_ASKS_MAX` files a sort, so a dump of ten thousand unnamed files costs a
  bounded number of calls; the rest are left out saying so.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from jobhunt.ingest import convert, discover, group, privacy, rank, versions
from jobhunt.profile import merge
from jobhunt.sections import (
    registry,
    sort_copies,
    sort_courses,
    sort_folders,
    sort_kinds,
    sort_rules,
    sort_units,
    store,
    wrong_place,
)
from jobhunt.sections import repo as repo_reader

log = logging.getLogger(__name__)

PURPOSE = "sort_pick"
TIMEOUT_S = 60                 # one short choice; a model that takes longer is busy, and the file is left out
AI_ASKS_MAX = 200              # files a sort asks the AI about (see the module notes)
PROMPT_CHARS = 1500            # the start of a document says what it is
SHOWCASE_README_WORDS = 150    # a README past a title and a line (see the module notes)
STARS_MAX = 8                  # showcase guesses a sort; a CV tells a handful of projects, not dozens
NONE = "none"
COURSE_CODE_WORDS = "Code you wrote for a course: coursework. Star it on its course to make it a degree project."
PROPOSED, LEFT_OUT, COPY = "proposed", "left_out", "copy"
# The choices the AI may pick from, each "section/kind of document" with its words.
CHOICES = {
    "cv/cv": "a CV",
    "letter/cover_letter": "a cover letter",
    "letter/statement": "a personal statement",
    "coursework/essay": "an essay the person wrote for a course",
    "coursework/report": "a report, lab report or other work the person did for a course",
    "coursework/dissertation": "a dissertation or thesis",
    "lecture/notes": "notes or handouts from a course",
    "lecture/slides": "lecture slides",
    "official/transcript": "a transcript of marks",
    "official/certificate": "a degree certificate",
    "document/certificate": "a certificate or award",
    "document/reference": "a reference letter",
    "document/other": "another paper about the person, such as a check or a licence",
    "project/upload": "something the person made outside a course: writing, a design, research",
}
# Folders whose names make a course: a module, a year or term, a course or degree, or a module code.
COURSE_NAME = re.compile(r"(?<![a-z])(?:modules?|courses?|year[ _-]?\d|semester|term|lectures?|seminars?|"
                         r"tutorials?|coursework|assignments?|units?[ _-]?\d|degree|bsc|ba|msc|ma|hnd|"
                         r"foundation|access)(?![a-z])|^[a-z]{2,5}[ _-]?\d{3,5}\b", re.I)
WORK_FOLDER = re.compile(r"(?<![a-z])(?:coursework|assignments?|submissions?|essays?|reports?|dissertation|"
                         r"thesis)(?![a-z])", re.I)
TAUGHT_FOLDER = re.compile(r"(?<![a-z])(?:lectures?|slides|handouts?|readings?|seminars?|tutorials?)(?![a-z])",
                           re.I)
WORK_KINDS = ("report", "writing", "project")
STUDIED_KINDS = ("notes", "slides", *WORK_KINDS)     # what a course folder's file is when not handed in
DISSERTATION_NAME = re.compile(r"(?<![a-z])(?:dissertation|thesis)(?![a-z])", re.I)
ESSAY_NAME = re.compile(r"(?<![a-z])essays?(?![a-z])", re.I)
STATEMENT_NAME = re.compile(r"(?<![a-z])(?:personal|supporting)[ _-]?statement|statement[ _-]?of[ _-]?purpose", re.I)
CONTRACT_NAME = re.compile(r"(?<![a-z])(?:contract|offer[ _-]?letter)(?![a-z])", re.I)
DEGREE_NAME = re.compile(r"(?<![a-z])(?:degree|diploma|graduat\w*|bsc|ba|msc|ma|hnd)(?![a-z])", re.I)
WORD = re.compile(r"[a-z0-9]+")
# Marks people add to a document saved again, at the end of its name; unlike a code project's copies a bare number
# is no mark, as "week 1 notes" and "week 2 notes" are two documents.
SAVED_AGAIN = re.compile(r"(?:[\s_.-]+|^)(?:\(\d+\)|copy|final|v\d+|version[\s_-]?\d*|old|backup|bak|new|latest|draft|"
                         r"revised|edited)$", re.I)
# Words any path or card may hold, which say nothing about which course or job it is.
COMMON_WORDS = {"the", "of", "and", "in", "for", "at", "a", "an", "to", "university", "college", "school",
                "institute", "year", "course", "courses", "module", "modules", "notes", "lectures", "lecture",
                "coursework", "my", "documents", "files", "work", "old", "new", "ltd", "limited", "plc", "uk"}
WORD_MIN = 3                   # "of" and "bs" say nothing; "art" and "law" do

# Reasons a file is left out, in plain words.
LEFT_OUT_WORDS = {
    "not_downloaded": "Only online: this computer has an empty copy. Download it first.",
    "skipped": "Empty, or a program.",
    "picture": "A picture with no words. Add it to a project by hand if it is your work.",
    "media": "A recording. Add it to a project by hand if it is your work.",
    "code": "Code outside a project folder.",
    "email": "An email. Hal does not sort emails.",
    "other": "A kind of file Hal does not read.",
    "no_text": "Hal found no words it could read in it.",
    "unplaced": "Hal could not tell where it goes.",
    "ai_cap": "Hal could not tell where it goes, and did not ask the AI about this many files.",
    "jd": "A job description for a job not on your record yet. Add it from the job's card.",
}


@dataclass
class Proposal:
    """Where one file or project folder of the folder sorted would go, or why it is left out."""

    path: Path
    rel: str                       # inside the folder given, starting with its name
    held: str                      # how it would be added: upload, files or folder (`registry.HELD`)
    size: int = 0
    sha256: str = ""
    mtime: float = 0.0
    section: str = ""
    doc_kind: str = ""
    target_id: int | None = None
    star: bool = False
    by: str = "rules"              # rules | ai
    why: str = ""
    state: str = PROPOSED          # proposed | left_out | copy
    copy_of: str = ""              # the rel of the copy proposed instead
    files: list[Path] = field(default_factory=list)   # a project folder's files
    note_rel: str | None = None    # the older Hal's note a move proposal came from (`sections.move`)

    def place(self, section: str, kind: str, why: str, by: str = "rules") -> Proposal:
        """Proposes this for `section` as a `kind`, saying why; returns itself."""
        self.section, self.doc_kind, self.why, self.by, self.state = section, kind, why, by, PROPOSED
        return self

    def leave_out(self, why: str) -> Proposal:
        """Leaves this out with the reason in plain words; returns itself."""
        self.section, self.doc_kind, self.why, self.state = "", "", why, LEFT_OUT
        return self


@dataclass
class Sorted:
    """What a sort proposes, and how many things the walk left out on purpose (junk folders, programs)."""

    proposals: list[Proposal] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


# Cards ----------------------------------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if len(w) >= WORD_MIN and w not in COMMON_WORDS}


def _cards(con: sqlite3.Connection, kind: str) -> list[tuple[int, dict]]:
    return [(r["id"], json.loads(r["fields"])) for r in con.execute(
        "SELECT id, fields FROM profile_items WHERE kind = ? AND state != 'hidden' AND merged_into IS NULL "
        "ORDER BY id", (kind,))]


def _month(mtime: float) -> int:
    """A file's time as a count of months, on `merge.month_index`'s scale."""
    when = datetime.fromtimestamp(mtime, UTC)
    return when.year * 12 + when.month - 1


def match_card(con: sqlite3.Connection, kind: str, rel: str, mtime: float = 0.0, text: str = "") -> int | None:
    """The card of `kind` (education or job) that a file at `rel` is for, or None when it is not plain which.

    An education card is named by its institution, subject or qualification in the file's folders; else it is
    the one whose dates hold the file's date; else the only one. A job is named by its employer in the
    folders or the start of the text.
    """
    cards = _cards(con, kind)
    if not cards:
        return None
    where = _words(" ".join(PurePosixPath(rel).parent.parts))
    named = ("institution", "subject", "qualification") if kind == "education" else ("employer",)
    scored = []
    for card_id, fields in cards:
        mine = _words(" ".join(str(fields.get(k) or "") for k in named))
        score = len(mine & where)
        if kind == "job" and not score and fields.get("employer") and text:
            score = int(merge.text_key(fields["employer"]) in merge.text_key(text[:rank.PEEK_CHARS]))
        scored.append((score, card_id, fields))
    best = max(s for s, _, _ in scored)
    top = [(c, f) for s, c, f in scored if s == best]
    if best and len(top) == 1:
        return top[0][0]
    if kind == "job":
        return None
    if mtime:
        month = _month(mtime)
        held = [c for c, f in top if (span := merge.span(f)) and span[0] <= month <= span[1]]
        if len(held) == 1:
            return held[0]
    return cards[0][0] if len(cards) == 1 else None


# Placing one document -------------------------------------------------------------------------------------

def _course_folder(rel: str, courses: set[str]) -> bool:
    parents = PurePosixPath(rel).parents
    return any(str(p) in courses for p in parents) or any(
        COURSE_NAME.search(part) for part in PurePosixPath(rel).parent.parts[1:])


def _nearest(rel: str, pattern: re.Pattern) -> bool:
    """True when the file's own folder, or the one above it, has a name `pattern` finds."""
    parts = PurePosixPath(rel).parent.parts[1:]
    return any(pattern.search(p) for p in parts[-2:])


def _work_kind(stem: str, suffix: str) -> str:
    if DISSERTATION_NAME.search(stem):
        return "dissertation"
    if ESSAY_NAME.search(stem):
        return "essay"
    if suffix in rank.SLIDE_SUFFIXES:
        return "slides"
    return "report"


def place_kind(p: Proposal, rule: rank.Rule, text: str, in_course: bool, *, vault: bool = False) -> bool:
    """Places a document of `rule`'s kind by its folders and the start of its text, a note in a notes vault
    (`vault`) as what the person studied when nothing else places it; False when the rules cannot. Also how
    a move places an older note by the kind an older Hal gave it (`sections.move`)."""
    stem, suffix = PurePosixPath(p.rel).stem, PurePosixPath(p.rel).suffix.lower()
    looks = wrong_place.looks_like(PurePosixPath(p.rel).name, text)
    kind = rule.doc_type
    if kind not in rank.STRONG and looks:
        kind = {"cv": "cv", "official": "transcript", "letter": "letter"}[looks[0]]
    if kind == "transcript" and not sort_kinds.transcript_content(text):
        kind = rule.doc_type if rule.doc_type != "transcript" else "notes"
    if kind == "job_description" and CONTRACT_NAME.search(stem) and not sort_kinds.contract_content(text):
        kind = "notes"
    taught = "slides" if suffix in rank.SLIDE_SUFFIXES else "notes"
    if kind == "cv":
        p.place("cv", "cv", "Named or set out like a CV.")
    elif kind == "letter":
        p.place("letter", "statement" if STATEMENT_NAME.search(stem) else "cover_letter",
                "A letter or statement you wrote; Hal uses it only to learn how you write.")
    elif kind == "transcript":
        p.place("official", "transcript", "A transcript: it sets your modules and marks.")
    elif kind == "certificate":
        degree = in_course or DEGREE_NAME.search(stem)
        p.place("official" if degree else "document", "certificate",
                "A certificate of your course." if degree else "A certificate or award.")
    elif kind == "reference":
        p.place("document", "reference", "A reference about you.")
    elif kind == "job_description":
        if CONTRACT_NAME.search(stem):
            p.place("document", "contract", "A contract.")
        else:
            p.leave_out(LEFT_OUT_WORDS["jd"])
    elif material := sort_rules.course_material(p.rel, in_course):
        p.place("lecture", taught, material)
    elif sort_kinds.lecture_named(stem, text):
        p.place("lecture", taught, "Named as a lecture, seminar or week's material: what you studied.")
    elif kind in STUDIED_KINDS and sort_kinds.study_note(p.rel):
        p.place("lecture", taught, "Your notes on a topic: what you studied.")
    elif in_course and (kind in WORK_KINDS or _nearest(p.rel, WORK_FOLDER)) \
            and not (kind in ("notes", "slides") and _nearest(p.rel, TAUGHT_FOLDER)) \
            and sort_kinds.submitted_work(p.rel, text):
        p.place("coursework", _work_kind(stem, suffix) if kind != "notes" or rank.work_named(stem) else "notes",
                "Your own work in a course folder.")
    elif in_course and (kind in STUDIED_KINDS or _nearest(p.rel, WORK_FOLDER)):
        p.place("lecture", taught, "Notes or slides in a course folder: what you studied.")
    elif vault and kind in ("notes", "slides"):
        p.place("lecture", taught, "A note in your notes app: what you studied.")
    elif kind == "project":
        p.place("project", "upload", "Named as a project or portfolio piece.")
    else:
        return False
    return True


def _ask_ai(p: Proposal, text: str) -> bool:
    """Asks the AI to pick one of `CHOICES` for a document the rules cannot place; False on "none" or no AI."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    listed = "\n".join(f"- {k}: {words}" for k, words in CHOICES.items())
    folders = "/".join(PurePosixPath(p.rel).parent.parts)
    user = (f"Choices:\n{listed}\n\nFile name: {PurePosixPath(p.rel).name}\nFolders: {folders}\n\n"
            f"Start of its text:\n{text[:PROMPT_CHARS]}")
    try:
        reply = ai.chat_json(prompts.text(PURPOSE), user, role="checker", timeout=TIMEOUT_S,
                             purpose=PURPOSE, priority=2)
    except ai.LLMUnavailable as e:
        log.info("sorted %s without the AI: %s", p.rel, e)
        return False
    choice = str(reply.get("choice") or "").strip().lower()
    if choice not in CHOICES:
        return False
    section, kind = choice.split("/")
    p.place(section, kind, f"The AI says it is {CHOICES[choice]}.", by="ai")
    return True


# The walk -------------------------------------------------------------------------------------------------

def _unit(con: sqlite3.Connection, path: Path, rel: str, files: list[Path], *, local: bool, repo: bool,
          who: repo_reader.Person) -> Proposal:
    """A code project or a project folder, as one proposal for Projects, guessed a showcase piece as the module
    notes say; in a course, year or module folder, coursework for that course."""
    stamp = versions.stamp(path)
    if _course_folder(rel, set()) or sort_courses.course_of(rel) is not None:
        p = Proposal(path, rel, registry.FILES, size=sum(f.stat().st_size for f in files if f.exists()),
                     mtime=stamp.commit or stamp.newest_file, files=files)
        p.place("coursework", "other", COURSE_CODE_WORDS)
        fill_card(con, p)
        return p
    readme = next((f for f in files if f.parent == path and discover.has_readme([f.name])), None)
    try:
        readme_words = len(readme.read_text(encoding="utf-8", errors="replace").split()) if readme else 0
    except OSError:
        readme_words = 0
    held, kind = (registry.FOLDER, "repo") if local and repo else (registry.FILES, "upload")
    p = Proposal(path, rel, held, size=sum(f.stat().st_size for f in files if f.exists()),
                 mtime=stamp.commit or stamp.newest_file, files=files)
    p.place("project", kind, "A code project." if repo else "A project folder with a README.")
    p.star = (readme_words >= SHOWCASE_README_WORDS and bool(who)
              and repo_reader.share(repo_reader.git_history(path), who)["mine"] > 0)
    return p


def cap_stars(proposals: list[Proposal]) -> None:
    """Keeps at most `STARS_MAX` showcase guesses, the newest projects'."""
    starred = sorted((p for p in proposals if p.star and p.state == PROPOSED), key=lambda p: -p.mtime)
    for p in starred[STARS_MAX:]:
        p.star = False


def _group_unit(con: sqlite3.Connection, path: Path, rel: str, files: list[Path], kind: str, *, local: bool,
                repo: bool) -> Proposal:
    """A folder that is one proposal (`sort_units.groups`): notes on one subject, practice exercises, or a
    download left out."""
    stamp = versions.stamp(path)
    p = Proposal(path, rel, registry.FILES, size=sum(f.stat().st_size for f in files if f.exists()),
                 mtime=stamp.commit or stamp.newest_file, files=files)
    why = sort_units.WORDS[kind]
    if kind == sort_units.DOWNLOAD:
        return p.leave_out(why)
    if kind == sort_units.PRACTICE:
        p.held = registry.FOLDER if local and repo else registry.FILES
        return p.place("project", "repo" if p.held == registry.FOLDER else "upload", why)
    if _course_folder(rel, set()):
        p.place("coursework", "notes", why)
        fill_card(con, p)
        return p
    return p.place("project", "upload", why)


def saved_as(stem: str) -> str:
    """A document's name without the marks of a save again: "Essay final (2)" is "essay"."""
    name = stem.strip().lower()
    while (cut := SAVED_AGAIN.sub("", name).strip()) != name and cut:
        name = cut
    return name


def _doc_copies(docs: list[Proposal]) -> None:
    """Marks every copy of a document but the newest: the same bytes anywhere, or the same name saved again in
    one folder."""
    by_key: dict[tuple, list[Proposal]] = {}
    for p in docs:
        if p.state != PROPOSED:
            continue
        rel = PurePosixPath(p.rel)
        by_key.setdefault(("sha", p.sha256), []).append(p)
        by_key.setdefault(("name", str(rel.parent), saved_as(rel.stem), rel.suffix.lower(), p.section),
                          []).append(p)
    for copies in by_key.values():
        live = [p for p in copies if p.state == PROPOSED]
        if len(live) < 2:
            continue
        newest = max(live, key=lambda p: (p.mtime, p.rel))
        for p in live:
            if p is not newest:
                p.state, p.copy_of, p.why = COPY, newest.rel, f"A copy of {newest.rel}."


def _one_dissertation(con: sqlite3.Connection, docs: list[Proposal]) -> None:
    """Keeps one dissertation a course: the newest; the others, and any for a course that has one, are
    reports (`store.check_dissertation`)."""
    taken = {r[0] for r in con.execute("SELECT target_id FROM record_sources WHERE section = 'coursework' AND "
                                       "doc_kind = 'dissertation' AND target_id IS NOT NULL")}
    for p in sorted(docs, key=lambda p: -p.mtime):
        if p.state != PROPOSED or p.doc_kind != "dissertation" or p.target_id is None:
            continue
        if p.target_id in taken:
            p.doc_kind = "report"
        else:
            taken.add(p.target_id)


def needs_card(section: str, target_id: int | None) -> bool:
    """True when a proposal for `section` cannot be added until the person chooses its card."""
    return section in registry.SECTIONS and registry.get(section).target_required and target_id is None


def fill_card(con: sqlite3.Connection, p: Proposal, text: str = "") -> None:
    """Finds the card a proposal is for, where its section names one (see `match_card`)."""
    if not p.section:
        return
    section = registry.get(p.section)
    kind = section.target or ("job" if p.doc_kind in ("reference", "contract") else "")
    if kind in ("education", "job"):
        p.target_id = match_card(con, kind, p.rel, p.mtime, text)
    if p.section == "document" and p.doc_kind in ("reference", "contract") and p.target_id is not None:
        p.section = "job_doc"          # a reference or contract for a job on the record goes on that job


def propose(con: sqlite3.Connection, root: Path, *, local: bool, prefix: str | None = None,
            known: set[str] = frozenset(), progress: Callable[[int, int], None] | None = None) -> Sorted:
    """Every file and project folder under `root` with where it would go, or why it is left out.

    `local` is True for a folder on this computer (its code projects are proposed as folders, keeping their
    history), False for an unpacked zip. `prefix` starts each `rel` (the folder's own name when None). Files
    at paths in `known` (proposed by an earlier sort of the drop folder) are passed over.
    """
    base = Path(root)
    walk = discover.walk(base, prefix)
    out = Sorted(skipped=dict(walk.left_out))
    found_repos = {r for r in walk.repos if not sort_rules.vendored_project(r, base)}
    found_readmes = {r for r in walk.readmes if not sort_rules.vendored_project(r, base)}
    libraries = (walk.repos | walk.readmes) - found_repos - found_readmes
    notes_vaults = sort_folders.vaults(base, [f.path for f in walk.files])
    apps = {f for f in found_repos | found_readmes
            if sort_folders.not_a_unit(f, base, notes_vaults, repo=f in found_repos)}
    repos = group.outermost(found_repos - apps)
    folders = group.projects(found_readmes - apps, found_repos - apps)
    kept = [f.path for f in walk.files if not sort_rules.in_vendored(f.path, base)]
    grouped = {g: kind for g, kind in sort_units.groups(base, kept, repos, folders).items()
               if group.repo_of(g, repos) is None}
    units: list[Proposal] = []
    who = repo_reader.person()
    unit_files: dict[Path, list[Path]] = {u: [] for u in (*grouped, *repos, *folders)
                                          if u in grouped or sort_units.holder(u, grouped) is None}
    loose: list[discover.Found] = []
    for f in walk.files:
        holder = sort_units.holder(f.path, grouped) or group.repo_of(f.path, repos) or \
            group.project_of(f.path, folders)
        if holder is not None:
            unit_files[holder].append(f.path)
        elif sort_rules.in_vendored(f.path, base) or any(lib in f.path.parents for lib in libraries):
            out.skipped[sort_rules.VENDORED_SKIP] = out.skipped.get(sort_rules.VENDORED_SKIP, 0) + 1
        elif str(f.path) not in known:
            loose.append(f)
    for folder, files in sorted(unit_files.items()):
        if str(folder) in known or not files:
            continue
        rel = (PurePosixPath(prefix if prefix is not None else base.name) / folder.relative_to(base).as_posix()
               ).as_posix()
        if folder in grouped:
            units.append(_group_unit(con, folder, rel, files, grouped[folder], local=local, repo=folder in repos))
        else:
            units.append(_unit(con, folder, rel, files, local=local, repo=folder in repos, who=who))
    sort_copies.mark([u for u in units if u.section == "project"], COPY)
    courses = {str(PurePosixPath(members[0][1]).parent)
               for members in group.courses([(f.path, f.rel) for f in loose]).values()}
    docs, asks, own = [], 0, sort_rules.own_words(con)
    for n, f in enumerate(loose):
        if progress:
            progress(n, len(loose))
        discover.fill(f)
        p = Proposal(f.path, f.rel, registry.UPLOAD, size=f.size, sha256=f.sha256, mtime=f.mtime)
        docs.append(p)
        if f.state in ("not_downloaded", "skipped"):
            p.leave_out(LEFT_OUT_WORDS[f.state])
            continue
        if why := sort_units.app_file(f.rel):
            p.leave_out(why)
            continue
        if (same := store.by_hash(con, f.sha256)) is not None:
            p.leave_out(f"Already in your record, under {registry.get(same['section']).label}.")
            continue
        rule = rank.rule_for(f.rel, f.path)
        suffix = f.path.suffix.lower()
        if suffix in rank.IMAGE_SUFFIXES:
            if rule.doc_type == "certificate":
                p.place("document", "certificate", "A picture of a certificate or award.")
            else:
                p.leave_out(LEFT_OUT_WORDS["picture"])
            continue
        if suffix in rank.CODE_SUFFIXES and sort_folders.materials_folder(f.path.parent, base):
            p.place("lecture", "other", "Code in a folder of course materials: what the course gave you.")
            fill_card(con, p)
            continue
        if suffix not in rank.DOC_SUFFIXES and suffix not in rank.SLIDE_SUFFIXES:
            p.leave_out(LEFT_OUT_WORDS[{"media": "media", "code": "code", "email": "email"}.get(rule.doc_type,
                                                                                              "other")])
            continue
        try:
            text = convert.text_of(f.path, rank.RULES[9] if suffix == ".pdf" else rule)
        except Exception as e:  # a converter can fail any way; one broken file is left out, not the sort
            log.info("could not read %s to sort it: %s", f.rel, e)
            text = ""
        if not text.strip():
            p.leave_out(LEFT_OUT_WORDS["no_text"])
            continue
        if why := privacy.held_back({"allowed": False, "rel": f.rel}, text):
            p.leave_out(f"Looks private ({why}), so Hal kept it out.")
            continue
        if sort_rules.published_paper(text, own, name=f.path.stem, pdf=suffix == ".pdf",
                                      reading=rule.doc_type not in rank.STRONG and sort_rules.reading_folder(f.rel)):
            p.leave_out(sort_rules.PAPER_WORDS)
            continue
        if why := sort_rules.someone_elses(f.rel, text, own):
            p.leave_out(why)
            continue
        if not place_kind(p, rule, text, _course_folder(f.rel, courses),
                          vault=sort_folders.in_vault(f.path, notes_vaults)):
            if asks >= AI_ASKS_MAX:
                p.leave_out(LEFT_OUT_WORDS["ai_cap"])
                continue
            asks += 1
            if not _ask_ai(p, text):
                p.leave_out(LEFT_OUT_WORDS["unplaced"])
                continue
        fill_card(con, p, text)
    mark_copies(con, docs)
    cap_stars(units)
    out.proposals = units + docs
    return out


def mark_copies(con: sqlite3.Connection, docs: list[Proposal]) -> None:
    """Marks all but the newest copy of each document, and keeps one dissertation a course."""
    _doc_copies(docs)
    _one_dissertation(con, docs)
