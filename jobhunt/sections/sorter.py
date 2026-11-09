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
- A file Hal cannot place is "Left out" with the reason in plain words, never guessed in: a picture with no
  words, a recording, code outside a project, a file that looks private (`privacy.reason`, checked before the
  AI sees a word), one only online, or one the AI did not recognise either.
- Copies are grouped and only the newest is proposed: the same bytes anywhere in the folder, a document saved
  again beside itself ("Essay final (2).docx", by `saved_as`), and copies of one code project
  (`versions.project_key`, confirmed by shared files as the bulk read does). A file already in the record is
  left out, naming where it is.
- A course folder (a folder of notes and slides, or one named like a module, a year or a course) makes its
  notes and slides lecture material and its named work coursework. Its card is the education card whose
  institution, subject or qualification its folders name, else the one whose dates hold the file's date,
  else the only one; with none the proposal waits for the person to choose (`needs_card`).
- A code project on this computer is proposed as a folder (`repo`), so its history stays readable; one from a
  zip is proposed as its files (`upload`). A project with a README of `SHOWCASE_README_CHARS` or more is
  guessed a showcase piece, as a README that long says what the project is and how to use it.
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
from jobhunt.sections import registry, store, wrong_place

log = logging.getLogger(__name__)

PURPOSE = "sort_pick"
TIMEOUT_S = 60                 # one short choice; a model that takes longer is busy, and the file is left out
AI_ASKS_MAX = 200              # files a sort asks the AI about (see the module notes)
PROMPT_CHARS = 1500            # the start of a document says what it is
SHOWCASE_README_CHARS = 600    # a README past a title and a line (see the module notes)
NONE = "none"
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


def place_kind(p: Proposal, rule: rank.Rule, text: str, in_course: bool) -> bool:
    """Places a document of `rule`'s kind by its folders and the start of its text; False when the rules cannot.
    Also how a move places an older note by the kind an older Hal gave it (`sections.move`)."""
    stem, suffix = PurePosixPath(p.rel).stem, PurePosixPath(p.rel).suffix.lower()
    looks = wrong_place.looks_like(PurePosixPath(p.rel).name, text)
    kind = rule.doc_type
    if kind not in rank.STRONG and looks:
        kind = {"cv": "cv", "official": "transcript", "letter": "letter"}[looks[0]]
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
    elif in_course and (kind in ("report", "writing", "project") or _nearest(p.rel, WORK_FOLDER)) \
            and not (kind in ("notes", "slides") and _nearest(p.rel, TAUGHT_FOLDER)):
        p.place("coursework", _work_kind(stem, suffix) if kind != "notes" else "notes",
                "Your own work in a course folder.")
    elif in_course and kind in ("notes", "slides"):
        p.place("lecture", "slides" if suffix in rank.SLIDE_SUFFIXES else "notes",
                "Notes or slides in a course folder: what you studied.")
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

def _unit(path: Path, rel: str, files: list[Path], *, local: bool, repo: bool) -> Proposal:
    """A code project or a project folder, as one proposal for Projects."""
    stamp = versions.stamp(path)
    readme = next((f for f in files if f.parent == path and discover.has_readme([f.name])), None)
    try:
        readme_chars = len(readme.read_text(encoding="utf-8", errors="replace")) if readme else 0
    except OSError:
        readme_chars = 0
    held, kind = (registry.FOLDER, "repo") if local and repo else (registry.FILES, "upload")
    p = Proposal(path, rel, held, size=sum(f.stat().st_size for f in files if f.exists()),
                 mtime=stamp.commit or stamp.newest_file, files=files)
    p.place("project", kind, "A code project." if repo else "A project folder with a README.")
    p.star = readme_chars >= SHOWCASE_README_CHARS
    return p


def _repo_copies(units: list[Proposal]) -> None:
    """Marks every copy of a code project but the newest (see the module notes)."""
    groups: list[list[Proposal]] = []
    for u in units:
        key, names = versions.project_key(u.path), {f.relative_to(u.path).as_posix() for f in u.files}
        for g in groups:
            first = g[0]
            same_key = versions.project_key(first.path) == key
            if (same_key and key.startswith("remote:")) or (same_key and versions.shared(
                    names, {f.relative_to(first.path).as_posix() for f in first.files})):
                g.append(u)
                break
        else:
            groups.append([u])
    for g in groups:
        newest = max(g, key=lambda u: u.mtime)
        for u in g:
            if u is not newest:
                u.state, u.copy_of, u.why = COPY, newest.rel, f"An older copy of {newest.rel}."


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
    walk = discover.walk(Path(root), prefix)
    out = Sorted(skipped=dict(walk.left_out))
    repos = group.outermost(walk.repos)
    folders = group.projects(walk.readmes, walk.repos)
    units: list[Proposal] = []
    unit_files: dict[Path, list[Path]] = {u: [] for u in (*repos, *folders)}
    loose: list[discover.Found] = []
    for f in walk.files:
        holder = group.repo_of(f.path, repos) or group.project_of(f.path, folders)
        if holder is not None:
            unit_files[holder].append(f.path)
        elif str(f.path) not in known:
            loose.append(f)
    base = Path(root)
    for folder, files in sorted(unit_files.items()):
        if str(folder) in known or not files:
            continue
        rel = (PurePosixPath(prefix if prefix is not None else base.name) / folder.relative_to(base).as_posix()
               ).as_posix()
        units.append(_unit(folder, rel, files, local=local, repo=folder in repos))
    _repo_copies(units)
    courses = {str(PurePosixPath(members[0][1]).parent)
               for members in group.courses([(f.path, f.rel) for f in loose]).values()}
    docs, asks = [], 0
    for n, f in enumerate(loose):
        if progress:
            progress(n, len(loose))
        discover.fill(f)
        p = Proposal(f.path, f.rel, registry.UPLOAD, size=f.size, sha256=f.sha256, mtime=f.mtime)
        docs.append(p)
        if f.state in ("not_downloaded", "skipped"):
            p.leave_out(LEFT_OUT_WORDS[f.state])
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
        if not place_kind(p, rule, text, _course_folder(f.rel, courses)):
            if asks >= AI_ASKS_MAX:
                p.leave_out(LEFT_OUT_WORDS["ai_cap"])
                continue
            asks += 1
            if not _ask_ai(p, text):
                p.leave_out(LEFT_OUT_WORDS["unplaced"])
                continue
        fill_card(con, p, text)
    mark_copies(con, docs)
    out.proposals = units + docs
    return out


def mark_copies(con: sqlite3.Connection, docs: list[Proposal]) -> None:
    """Marks all but the newest copy of each document, and keeps one dissertation a course."""
    _doc_copies(docs)
    _one_dissertation(con, docs)
