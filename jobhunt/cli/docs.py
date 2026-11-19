"""Commands for application documents: pack, letter, check, contacts, docs-time.

The bodies moved from `jobhunt/__main__.py`; the parser, its arguments and help stay there, because the
voice bridge and the /jobs command parse what these commands print. `docs-time`, added in 2.3.2, registers
itself here (`register`), as the rebuild's other commands do. File names that carry the person's
name come from `core.naming`. Each command reads and writes the job's numbered application folder,
and a pack drafted before those folders existed is still found where it was.
"""
import sys
from contextlib import closing

from jobhunt import db
from jobhunt.cli.find import _find
from jobhunt.core import naming


def _pack_md(job):
    """The job's `pack.md`, in its application folder or, for an older pack, its old folder; None if none."""
    from jobhunt import documents
    from jobhunt.docs import folders

    folder = documents.pack_dir(job, create=False)
    if folder is not None and (folder / folders.PACK_MD).exists():
        return folder / folders.PACK_MD
    legacy = folders.legacy_folder(job)
    return legacy / folders.PACK_MD if legacy is not None and (legacy / folders.PACK_MD).exists() else None


def cmd_pack(args):
    """Handles `jobhunt pack`: drafts the CV and cover letter for a job."""
    from jobhunt import ai, documents, pack
    from jobhunt.cvstudio import cv
    if args.cv_only:
        con = db.connect()
        for r in con.execute("SELECT * FROM jobs WHERE status = 'ready'"):
            found = _pack_md(r)
            if found is not None:
                cv.render(documents.current_cv(r, documents.load(con, r)), found.parent)
                print(f"  {found.parent.name}")
        return
    if not ai.available():
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

    from jobhunt import letter
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
    from jobhunt import pack
    with closing(db.connect()) as con:
        rows = con.execute("SELECT * FROM jobs WHERE status = 'ready'").fetchall() if args.ready else [_find(con, args.id)]
    for r in rows:
        pack_md = _pack_md(r)
        if pack_md is not None:
            pack.add_company_check(pack_md, r["company"], r)
            print(f"checked {r['company']}")


def cmd_contacts(args):
    """Handles `jobhunt contacts`: finds people to contact for a job."""
    from jobhunt.docs import contacts
    con = db.connect()
    found = contacts.find(con, _find(con, args.id))
    for p in found["people"]:
        print(f"{p['name']}  |  {p['role']}  |  {p['source']}{'' if p['sure'] else '  (check)'}")
        print(f"    {p.get('url') or p.get('email') or ''}  {p['why']}")
    print(f"({len(found['people'])} suggested by {found['by']}{'; ' + found['note'] if found['note'] else ''})")


def _figure(value) -> str:
    return "-" if value is None else f"{value:,}"


def cmd_docs_time(args):
    """Handles `jobhunt docs-time`: what the AI did for a job's last document write, by prompt."""
    from jobhunt.ai import usage
    with closing(db.connect()) as con:
        if args.action:
            found = con.execute("SELECT id, state, started, ended FROM actions WHERE id = ?", (args.action,)).fetchone()
            if found is None:
                sys.exit(f"no action {args.action!r}")
        elif not args.id:
            sys.exit("name a job, or an action with --action")
        else:
            job = _find(con, args.id)
            found = usage.last_documents(con, job["id"])
            if found is None:
                sys.exit(f"{job['id']}: Hal has not finished writing documents for this job")
        costs = usage.by_prompt(con, found["id"])
    print(f"action {found['id']} ({found['state']}), {found['started']} to {found['ended']}")
    print(f"{'prompt':<18} {'effort':<7} {'calls':>5} {'seconds':>8} {'read':>8} {'written':>8} {'thinking':>8}")
    for c in costs:
        print(f"{c.purpose:<18} {c.effort or '-':<7} {c.calls:>5} {c.seconds:>8.0f} {_figure(c.tokens_in):>8} "
              f"{_figure(c.tokens_out):>8} {_figure(c.thinking):>8}")
    calls, seconds = sum(c.calls for c in costs), sum(c.seconds for c in costs)
    failed = sum(c.failed for c in costs)
    model = sum(c.model_seconds for c in costs)
    print(f"{calls} calls ({failed} failed), {seconds / 60:.1f} minutes of calls, about {model / 60:.1f} minutes of "
          f"model time at {usage.READ_TOKENS_S} tokens a second read and {usage.WRITE_TOKENS_S} written")


def register(sub) -> None:
    """Adds `docs-time` to the command line's subcommands."""
    s = sub.add_parser("docs-time", help="show the AI calls, tokens and time of a job's last document write")
    s.add_argument("id", nargs="?", help="the job, as `list` shows it")
    s.add_argument("--action", help="an action id instead of a job's last document write")
    s.set_defaults(fn=cmd_docs_time)
