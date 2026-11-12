"""The sections of Your record: what each one takes, whose its files are, and what their facts may become.

The person puts each file, link or folder in a section, and the section says what it is: its kind of
document, its role (whose it is), the card it belongs to, and how much its facts are trusted. Nothing
guesses those later: a bulk read that guessed each file's kind and owner crossed facts between people and
filed lecture examples as the person's own work (sections plan, 2026-11-09).

Design decisions, each with its reason:

- A section's `role` is one of `own` (the person's own work), `taught` (course material they were given),
  `official` (papers about them: a transcript, a certificate, a reference) or `others`. Evidence keeps the
  ingest's roles (`ingest.roles`), where official papers count as the person's own, as the profile and the
  writer already treat a transcript (`evidence_role`).
- `authority` is the rank of a section's facts per field, on the scale of `profile.merge.RANKS` (a higher one
  wins; `*` is every other field). Today a card folds its sources by each piece of evidence's document type
  (`DocKind.doc_type`); the per-field ranks are declared here so a later phase can fold by them without
  changing any section.
- A section names a reader per document kind (`readers`, with `reader` for the rest); `sections.readers` holds
  them, and a reader not written yet raises `readers.NotReadYet`, naming the phase that brings it.
- A section's sources may also belong to a card of another kind (`targets`), never required: a transcript
  under Documents may name the education card it is for, a reference the job.
- `folder` is where the section's notes go inside `Sources/` of the record; `{target}` is the title of the card
  a source belongs to, so the record's folders mirror the sections (`Sources/Education/<BSc ...>/Coursework`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from jobhunt.ingest import roles
from jobhunt.profile import merge

OWN, TAUGHT, OFFICIAL, OTHERS = "own", "taught", "official", "others"
SECTION_ROLES = (OWN, TAUGHT, OFFICIAL, OTHERS)
# Whose a section's files are, as a piece of evidence says it (`ingest.roles`): papers about the person are
# theirs, as the profile treats a transcript.
EVIDENCE_ROLE = {OWN: roles.OWN, TAUGHT: roles.TAUGHT, OFFICIAL: roles.OWN, OTHERS: roles.OTHERS}
ALL_FIELDS = "*"
# How a source is held: files the person uploaded (one, or several as a folder or zip), a web link, an
# account on a site, a folder on the computer Hal runs on, or a project typed in by hand.
UPLOAD, FILES, LINK, ACCOUNT, FOLDER, MANUAL = "upload", "files", "link", "account", "folder", "manual"
HELD = (UPLOAD, FILES, LINK, ACCOUNT, FOLDER, MANUAL)
NOTES_RANK = merge.RANKS["notes"]
LETTER_RANK = merge.RANKS["letter"]
CV_RANK = merge.RANKS["cv"]
CERTIFICATE_RANK = merge.RANKS["certificate"]
TRANSCRIPT_RANK = merge.RANKS["transcript"]
VOICE_ONLY = 0             # cover letters are samples of the person's writing voice, never facts


@dataclass(frozen=True)
class DocKind:
    """One kind of document within a section: its id, the page's words, and the document type its evidence
    carries (which sets its rank in `merge.RANKS` and what the record's search makes of its note)."""

    id: str
    label: str
    doc_type: str
    held: str = UPLOAD


DOC_KINDS: dict[str, DocKind] = {k.id: k for k in (
    DocKind("cv", "CV", "cv"),
    DocKind("cover_letter", "Cover letter", "cover_letter"),
    DocKind("statement", "Personal statement", "personal_statement"),
    DocKind("essay", "Essay", "essay"),
    DocKind("report", "Report or lab report", "report"),
    DocKind("dissertation", "Dissertation", "dissertation"),
    DocKind("slides", "Slides", "slides"),
    DocKind("notes", "Notes", "notes"),
    DocKind("transcript", "Transcript", "transcript"),
    DocKind("certificate", "Certificate or award", "certificate"),
    DocKind("reference", "Reference", "reference"),
    DocKind("contract", "Contract", "contract"),
    DocKind("job_description", "Job description", "job_description"),
    DocKind("repo", "Code project", "repository", FOLDER),
    DocKind("github_account", "GitHub account", "repository", ACCOUNT),
    DocKind("repo_url", "Link to a code project", "repository", LINK),
    DocKind("local_folder", "Folder on this computer", "project", FOLDER),
    DocKind("upload", "Uploaded folder or files", "project", FILES),
    DocKind("portfolio_site", "Portfolio site", "project", LINK),
    DocKind("web_page", "Web page or online document", "writing", LINK),
    DocKind("manual", "Added by hand", "by_you", MANUAL),
    DocKind("other", "Something else", "other"),
)}


@dataclass(frozen=True)
class Section:
    """One section of Your record: what it takes and what its facts may become."""

    id: str
    label: str
    folder: str                          # inside the record's Sources/; `{target}` is the target card's title
    doc_kinds: tuple[str, ...]           # the first is the default
    role: str                            # one of SECTION_ROLES
    creates: tuple[str, ...]             # profile kinds its facts may create or fill (`profile.store.KINDS`)
    authority: dict[str, int]            # rank per field, `ALL_FIELDS` for the rest
    reader: str                          # the reader of its sources (`readers.READERS`)
    readers: dict[str, str] = field(default_factory=dict)    # a reader per document kind, where it differs
    target: str = ""                     # the profile kind of the card a source belongs to, "" for none
    target_required: bool = False
    targets: tuple[str, ...] = ()        # other kinds of card a source may belong to, never required
    hint: str = ""                       # one plain line for the page

    def rank(self, field_name: str) -> int:
        """How much this section's value of `field_name` is trusted (a higher rank wins)."""
        return self.authority.get(field_name, self.authority.get(ALL_FIELDS, 0))

    def accepts(self, doc_kind: str) -> bool:
        """True when `doc_kind` is one of this section's kinds of document."""
        return doc_kind in self.doc_kinds

    def belongs_to(self, kind: str) -> bool:
        """True when a source of this section may belong to a card of profile kind `kind`."""
        return bool(kind) and (kind == self.target or kind in self.targets)

    def reader_of(self, doc_kind: str) -> str:
        """The name of the reader for a source of `doc_kind` in this section."""
        return self.readers.get(doc_kind, self.reader)

    @property
    def evidence_role(self) -> str:
        """The role the evidence of this section's facts carries (`ingest.roles`)."""
        return EVIDENCE_ROLE[self.role]


EVERY_FACT = ("job", "education", "skill", "project", "achievement", "certificate", "basic", "link")
PROJECT_READERS = {"repo": "repo", "local_folder": "local_folder", "repo_url": "repo_url",
                   "github_account": "github_account", "upload": "project_files", "portfolio_site": "portfolio",
                   "web_page": "page", "manual": "manual"}

SECTIONS: dict[str, Section] = {s.id: s for s in (
    Section("cv", "CVs", "CVs", ("cv",), OWN, EVERY_FACT, {ALL_FIELDS: CV_RANK}, "cv",
            hint="Every CV you have. Hal uses the newest as your main one."),
    Section("letter", "Cover letters and personal statements", "Letters", ("cover_letter", "statement"), OWN, (),
            {ALL_FIELDS: VOICE_ONLY}, "letter",
            hint="Hal reads these only to learn how you write, never as facts about you."),
    Section("job_doc", "Documents for a job", "Work/{target}", ("reference", "contract", "job_description", "other"),
            OFFICIAL, ("job", "skill", "achievement"), {ALL_FIELDS: LETTER_RANK}, "job_doc", target="job",
            target_required=True, hint="A reference, a contract or the job description."),
    Section("coursework", "Coursework", "Education/{target}/Coursework",
            ("essay", "report", "dissertation", "slides", "notes", "other"), OWN, ("skill", "project"),
            {ALL_FIELDS: NOTES_RANK}, "coursework", target="education", target_required=True,
            hint="Essays, reports, lab reports, portfolios and your dissertation."),
    Section("lecture", "Lecture material", "Education/{target}/Lecture material", ("slides", "notes", "other"),
            TAUGHT, ("skill",), {ALL_FIELDS: NOTES_RANK}, "lecture", target="education", target_required=True,
            hint="Slides and notes from your course. Hal counts these as what you studied."),
    Section("official", "Official papers", "Education/Official papers", ("transcript", "certificate"), OFFICIAL,
            ("education", "certificate", "achievement"),
            {"modules": TRANSCRIPT_RANK, "grade": TRANSCRIPT_RANK, ALL_FIELDS: CERTIFICATE_RANK}, "official",
            target="education", hint="Your transcript and certificates. These set your marks and grade."),
    Section("project", "Projects", "Projects/{target}", tuple(PROJECT_READERS), OWN, ("project", "skill"),
            {ALL_FIELDS: CERTIFICATE_RANK}, "manual", readers=PROJECT_READERS, target="project",
            hint="Anything you made: code, designs, research, writing, events, campaigns."),
    Section("document", "Documents", "Documents",
            ("other", "certificate", "transcript", "reference", "contract", "report"), OFFICIAL,
            ("certificate", "achievement", "education", "job"), {ALL_FIELDS: LETTER_RANK}, "document",
            targets=("education", "job", "certificate"),
            hint="Certificates, awards, references, checks and anything else that helps."),
)}


def get(section_id: str) -> Section:
    """The section named `section_id`; LookupError naming the sections for any other."""
    try:
        return SECTIONS[section_id]
    except KeyError:
        raise LookupError(f"{section_id!r} is not a section of your record; use one of "
                          f"{', '.join(SECTIONS)}.") from None


def doc_kind(section: Section, kind: str | None) -> DocKind:
    """The kind of document `kind` in `section`, its default when `kind` is empty; ValueError for one the section
    does not take."""
    chosen = kind or section.doc_kinds[0]
    if not section.accepts(chosen):
        words = ", ".join(DOC_KINDS[k].label.lower() for k in section.doc_kinds)
        raise ValueError(f"{section.label} takes {words}.")
    return DOC_KINDS[chosen]


def _check() -> None:
    """Fails loudly when a section names a role, kind of document or profile kind that does not exist."""
    from jobhunt.profile import store

    for s in SECTIONS.values():
        if s.role not in SECTION_ROLES:
            raise ValueError(f"section {s.id}: role {s.role!r} is not one of {SECTION_ROLES}")
        for k in s.doc_kinds:
            if k not in DOC_KINDS:
                raise ValueError(f"section {s.id}: no kind of document {k!r}")
        for k in s.creates:
            if k not in store.KINDS:
                raise ValueError(f"section {s.id}: no profile kind {k!r}")
        for k in (s.target, *s.targets):
            if k and k not in store.KINDS:
                raise ValueError(f"section {s.id}: no profile kind {k!r} to belong to")


_check()
