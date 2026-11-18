"""The editable application documents for one job: CV, cover letter, message, answers.

Drafts come from the first draft, `pack.md`, in the application's folder. Edits are stored in the
applications table; the CV and cover letter PDFs are made again from the edited text, and approving them
writes the whole folder at once (`docs.folders.publish`), so the folder always matches what was approved.

A job drafted before application folders existed still has its `pack.md` in the old shared folder; it is
read from there only when that file names this job, because one old folder could belong to two jobs.

The CV follows My CV's choices: while it is set to send the person's original file, that file goes out byte
for byte under the CV's file name with its own extension, and while "Adjust your CV for each job" is No the
main CV goes as it is. Both were shown on My CV but not followed here before 2026-11-12.

The CV of an application Hal writes now is kept with its change list (`cvstudio/tailor.py`) and printed from
there; an application written before keeps the cv.json path (`cv.tailor`, `undone_changes`) until it is sent,
decided by whether `job_cvs` holds a row for it.

The record revision a job's documents were written from (`written_from`, set by `pack.build` through
`mark_written_from`) is a `meta` row per job, `WRITTEN_FROM_PREFIX` and the job's id: one indexed read, and no
new column while the migration numbers of this release are taken by other work. A job written before has
none, so it is never called out of date.
"""
import contextvars
import json
import re
from contextlib import contextmanager
from pathlib import Path

from jobhunt import ai, db, letter, pack
from jobhunt.ai import gpu
from jobhunt.core import naming, settings
from jobhunt.cvstudio import cv, tailor
from jobhunt.docs import folders

WRITTEN_FROM_PREFIX = "docs_written_from:"
SECTION = re.compile(r"^## (.+?)\n(.*?)(?=^## |\Z)", re.S | re.M)
# Where `render` writes while an approval builds the new folder. A context variable rather than an argument,
# so `render(job, docs)` keeps the two-argument form the legacy tests replace it with.
_OUT: contextvars.ContextVar[Path | None] = contextvars.ContextVar("documents_out", default=None)


@contextmanager
def _connection(con):
    if con is not None:
        yield con
        return
    own = db.connect()
    try:
        yield own
        own.commit()
    finally:
        own.close()


def pack_dir(job, con=None, *, create: bool = True) -> Path | None:
    """The job's application folder; with `create` (the default) the application gets its number first.

    Without `create`, a job that has no application yet answers None. The folder itself is made by whoever
    writes into it.
    """
    with _connection(con) as c:
        return folders.folder_for(c, job, create=create)


def _pack_md(job, con=None) -> Path | None:
    folder = pack_dir(job, con, create=False)
    if folder is not None and (folder / folders.PACK_MD).exists():
        return folder / folders.PACK_MD
    legacy = folders.legacy_folder(job)
    return legacy / folders.PACK_MD if legacy is not None else None


def read_pack(job) -> dict:
    """Returns the pack's sections, keyed by heading, or {} if no pack was drafted."""
    md = _pack_md(job)
    if md is None:
        return {}
    return {m.group(1).strip(): m.group(2).strip() for m in SECTION.finditer(md.read_text())}


def _bullets(text: str) -> list[str]:
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]


def _unwrap(text: str) -> str:
    """Joins hard-wrapped lines back into paragraphs."""
    return "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip())


def summary(job) -> dict:
    """Returns the read-only parts of a pack for display: flags, role, company and fit."""
    s = read_pack(job)
    fit = []
    for line in s.get("Fit", "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Requirement", "---"):
            fit.append({"requirement": cells[0], "evidence": cells[1], "strength": cells[2]})
    gaps = re.search(r"Gaps to address or accept: (.*)", s.get("Fit", ""))
    return {"flags": _bullets(s.get("Check before sending", "")),
            "role": s.get("Role", ""),
            "company": re.sub(r"\n- Source: .*", "", s.get("Company", "")).strip(),
            "sources": re.findall(r"- Source: (\S+)", s.get("Company", "")),
            "company_check": _bullets(s.get("Company check", "")),
            "fit": fit,
            "gaps": gaps.group(1) if gaps else "",
            "has_pack": bool(s)}


def drafts(job) -> dict:
    """Returns the documents as drafted in the pack."""
    s = read_pack(job)
    msg = s.get("Message to the hiring manager", "")
    to = re.match(r"To: (.*)\n", msg)
    answers = re.findall(r"\*\*(.+?)\*\*\n\n(.*?)(?=\n\*\*|\Z)", s.get("Form answers", ""), re.S)
    variant = {}
    md = _pack_md(job)
    if md is not None and (md.parent / "cv.json").exists():
        variant = json.loads((md.parent / "cv.json").read_text())
    return {
        # Packs drafted before long dashes were removed at the source still hold them.
        "letter": ai.undash(s.get("Cover letter", "")),
        "message_to": to.group(1) if to else "",
        "message": ai.undash(msg[to.end():].strip() if to else msg),
        "answers": [{"question": q.strip(), "answer": ai.undash(_unwrap(a))} for q, a in answers],
        "cv_title": variant.get("title", ""),
        "cv_summary": variant.get("summary", ""),
        "approved": False,
    }


def written_from_key(job_id: str) -> str:
    """The `meta` key holding the record revision a job's documents were written from."""
    return f"{WRITTEN_FROM_PREFIX}{job_id}"


def mark_written_from(con, job_id: str, revision: int) -> None:
    """Notes that the job's documents were written from record revision `revision`; the caller commits."""
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (written_from_key(job_id), str(int(revision))))


def written_from(con, job_id: str) -> int | None:
    """The record revision the job's documents were written from, or None for documents written before Hal kept
    it. Below `record.revision()` means the record changed since: the documents are out of date."""
    row = con.execute("SELECT v FROM meta WHERE k = ?", (written_from_key(job_id),)).fetchone()
    return int(row[0]) if row else None


def load(con, job) -> dict:
    """Returns the current documents: the stored edits, else the pack's drafts."""
    row = con.execute("SELECT docs FROM applications WHERE job_id = ?", (job["id"],)).fetchone()
    if row and row["docs"]:
        return json.loads(row["docs"])
    return drafts(job)


def save(con, job, docs: dict) -> None:
    """Stores edited documents and makes the PDFs again; approved documents rewrite the whole folder."""
    con.execute("""INSERT INTO applications (job_id, stage, docs, updated) VALUES (?, 'documents', ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET docs = excluded.docs, updated = excluded.updated""",
                (job["id"], json.dumps(docs), db.now()))
    folders.number_for(con, job["id"])
    con.commit()
    if docs.get("approved"):
        folders.publish(con, job, docs, render=lambda out: _render_into(job, docs, out),
                        cv_version_id=tailor.version_of(job["id"], con))
        con.commit()
    else:
        pack_dir(job, con)
        con.commit()
        render(job, docs)


def original_file(job=None) -> str | None:
    """The kept file id of the person's own CV while that CV is set to send it unchanged, else None: the CV
    `job`'s documents are built from (`choose.base_doc`), else the lead CV."""
    from jobhunt.cvstudio import choose, store

    d = store.doc(doc_id=choose.base_doc(job["id"]) if job is not None else None)
    return d["baseline_file_id"] if d["send"] == "original" and d["baseline_file_id"] else None


def cv_file_name(job=None) -> str:
    """The CV's file name employers see: the template's, with the original file's extension while it is sent."""
    name = naming.file_name("cv")
    fid = original_file(job)
    if fid is None:
        return name
    from jobhunt.cvstudio import store

    ext = Path(store.file_row(fid)["name"]).suffix.lower()
    return Path(name).stem + ext if ext else name


def files(job, con=None) -> dict:
    """Returns the paths of the documents to upload, where they exist."""
    with _connection(con) as c:
        d = pack_dir(job, c, create=False)
    if d is None:
        return {}
    return {k: str(p) for k, p in (("cv", d / cv_file_name(job)),
                                    ("letter", d / naming.file_name("letter"))) if p.exists()}


def tailored(job, docs: dict) -> dict:
    """The CV Hal made for this job: its kept CV (`tailor.v1`) when it has one; for an application written
    before that, the base CV reordered for it (`cv.tailor`) with any title or summary given for it, or the
    base CV as it is while adjusting for each job is No or the original file is sent."""
    if tailor.kept(job["id"]):
        return tailor.v1(job["id"])
    if settings.get("cvdocs.tailor") == "no" or original_file(job) is not None:
        return cv.load()
    variant = cv.tailor(pack._job_text(job))[0]
    for key in ("title", "summary"):    # a CV may have neither
        given = docs.get(f"cv_{key}") or variant.get(key)
        if given is not None:
            variant[key] = given
    return variant


def proposed_cv(job, docs: dict) -> dict:
    """The CV as made for this application, before any of Hal's changes the person undid."""
    return docs.get("cv") or tailored(job, docs)


def current_cv(job, docs: dict) -> dict:
    """The CV this application sends: its kept CV, which holds its own Undo (`tailor.document`); for an older
    application, the applicant's own edit of it, else Hal's tailoring of the base CV, with the changes the
    person undid put back as the base CV has them."""
    if tailor.kept(job["id"]):
        return tailor.v1(job["id"])
    made = proposed_cv(job, docs)
    undone = docs.get("undone_changes")
    return cv.revert(cv.load(), made, undone) if undone else made


def render(job, docs: dict, out: Path | None = None) -> dict:
    """Renders the CV and cover letter PDFs from the documents into `out`; returns their paths.

    `out` defaults to the application's folder. A kept CV (`tailor.kept`) is printed from the version it was
    made on with its changes. For an older application, a CV the applicant has not edited for this job is
    made again from the base CV, so a change to the base reaches it while it waits to be sent. The CV
    takes the file name from the `cvdocs.file_name` setting, whatever name the PDF maker gave it.
    """
    d = out or _OUT.get() or pack_dir(job)
    d.mkdir(parents=True, exist_ok=True)
    made = {}
    sent = current_cv(job, docs)
    want = d / cv_file_name(job)
    fid = original_file(job)
    if fid is not None:
        from jobhunt.cvstudio import store

        want.write_bytes(store.file_bytes(fid))
        made["cv"] = str(want)
    else:
        pdf = cv.render(sent, d)
        if pdf and Path(pdf).exists():
            if Path(pdf) != want:
                Path(pdf).replace(want)
            made["cv"] = str(want)
    body = _ascii(docs.get("letter", ""))
    if body.strip():
        target = d / naming.file_name("letter")
        letter.render(body, target, [f"{job['company']} recruitment team"], f"Application: {_ascii(job['title'])}",
                      cv_sent=sent)   # the letter's header repeats the CV sent with it
        made["letter"] = str(target)
    return made


def _render_into(job, docs: dict, out: Path) -> dict:
    token = _OUT.set(out)
    try:
        return render(job, docs) or {}
    finally:
        _OUT.reset(token)


def _ascii(text: str) -> str:
    """Replaces the typographic characters the PDF fonts cannot draw."""
    table = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
             "\u2014": ", ", "\u2026": "...", "\u00a0": " ", "\u00a3": "GBP "}
    return "".join(table.get(ch, ch) for ch in text).encode("ascii", "ignore").decode()


def draft_letter(con, job) -> str:
    """Writes a cover letter for a job that has none, when a form asks for one, and renders its PDF.

    It takes the pack's letter path (`pack.write_letter`: advert, record, fit map, checks), so the letter
    is checked like any other; what the checks found is kept with it as `letter_notes` and `letter_flags`.
    """
    if gpu.busy():
        return ""
    from jobhunt.cvstudio import choose

    try:
        with cv.based_on(choose.base_doc(job["id"], con)):
            written = pack.write_letter(con, job)
    except ai.LLMUnavailable:
        return ""
    docs = {**load(con, job), "letter": written["text"], "letter_notes": written["notes"],
            "letter_flags": written["flags"]}
    save(con, job, docs)
    return written["text"]
