"""Commands for application documents: pack, letter, check, contacts.

The bodies moved from `jobhunt/__main__.py`; the parser, its arguments and help stay there, because the
voice bridge and the /jobs command parse what these commands print. File names that carry the person's
name come from `core.naming`. Each command reads and writes the job's numbered application folder,
and a pack drafted before those folders existed is still found where it was.
"""
import sys
from contextlib import closing

from .. import db
from ..core import naming
from .find import _find


def _pack_md(job):
    """The job's `pack.md`, in its application folder or, for an older pack, its old folder; None if none."""
    from .. import documents
    from ..docs import folders

    folder = documents.pack_dir(job, create=False)
    if folder is not None and (folder / folders.PACK_MD).exists():
        return folder / folders.PACK_MD
    legacy = folders.legacy_folder(job)
    return legacy / folders.PACK_MD if legacy is not None and (legacy / folders.PACK_MD).exists() else None


def cmd_pack(args):
    """Handles `jobhunt pack`: drafts the CV and cover letter for a job."""
    from .. import ai, cv, llm, pack
    if args.cv_only:
        con = db.connect()
        for r in con.execute("SELECT * FROM jobs WHERE status = 'ready'"):
            found = _pack_md(r)
            if found is not None:
                variant, _ = cv.tailor(f"{r['title']}\n{r['description'] or ''}")
                cv.render(variant, found.parent)
                print(f"  {found.parent.name}")
        return
    if not llm.available():
        why = ai.backend().status().why or "the AI is not ready"
        sys.exit(f"Packs need the AI: {why}")
    con = db.connect()
    if not pack.has_facts(con):
        sys.exit(pack.NOTHING_CHECKED)
    if args.shortlisted:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM jobs WHERE status = 'shortlisted' AND active = 1 ORDER BY score DESC")]
    else:
        ids = [_find(con, args.id)["id"]]
    for job_id in ids:
        print(f"{job_id}: drafting...", flush=True)
        print(f"  {pack.build(con, job_id, use_web=not args.no_web)}")


def cmd_letter(args):
    """Handles `jobhunt letter`: drafts a cover letter."""
    import re as _re

    from .. import letter
    # closed here, not left to the garbage collector: Windows cannot remove an open database (CI, 2026-10-23)
    with closing(db.connect()) as con:
        r = _find(con, args.id)
    found = _pack_md(r)
    section = found and _re.search(r"## Cover letter\n(.*?)(?=\n## )", found.read_text(), _re.S)
    if not section:
        sys.exit("no cover letter in this pack")
    out_dir = found.parent
    recipient = args.to.split("|") if args.to else [f"{r['company']} recruitment team"]
    subject = args.subject or f"Application: {r['title']}"
    # the person's name in the file comes from the `cvdocs.letter_file_name` template, never from code
    print(letter.render(section.group(1), out_dir / naming.file_name('letter'), recipient, subject))


def cmd_check(args):
    """Handles `jobhunt check`: checks the documents of ready jobs."""
    from .. import pack
    with closing(db.connect()) as con:
        rows = con.execute("SELECT * FROM jobs WHERE status = 'ready'").fetchall() if args.ready else [_find(con, args.id)]
    for r in rows:
        pack_md = _pack_md(r)
        if pack_md is not None:
            pack.add_company_check(pack_md, r["company"], r)
            print(f"checked {r['company']}")


def cmd_contacts(args):
    """Handles `jobhunt contacts`: finds people to contact for a job."""
    from .. import contacts
    con = db.connect()
    found = contacts.find(con, _find(con, args.id))
    for p in found["people"]:
        print(f"{p['name']}  |  {p['role']}  |  {p['source']}{'' if p['sure'] else '  (check)'}")
        print(f"    {p.get('url') or p.get('email') or ''}  {p['why']}")
    print(f"({len(found['people'])} suggested by {found['by']}{'; ' + found['note'] if found['note'] else ''})")
