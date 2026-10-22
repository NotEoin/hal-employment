"""Commands for application documents: pack, letter, check, contacts.

The bodies moved unchanged from `jobhunt/__main__.py`; the parser, its arguments and help stay there,
because the voice bridge and the /jobs command parse what these commands print.
"""
import sys

from .. import db
from .find import _find


def cmd_pack(args):
    from .. import cv, llm, pack
    if args.cv_only:
        con = db.connect()
        for r in con.execute("SELECT * FROM jobs WHERE status = 'ready'"):
            out_dir = pack.PACKS / pack.slug(f"{r['company']}-{r['title']}")
            if out_dir.exists():
                variant, _ = cv.tailor(f"{r['title']}\n{r['description'] or ''}")
                cv.render(variant, out_dir)
                print(f"  {out_dir.name}")
        return
    if not llm.available():
        sys.exit("Ollama is not reachable; packs are drafted by the local models.")
    con = db.connect()
    if args.shortlisted:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM jobs WHERE status = 'shortlisted' AND active = 1 ORDER BY score DESC")]
    else:
        ids = [_find(con, args.id)["id"]]
    for job_id in ids:
        print(f"{job_id}: drafting...", flush=True)
        print(f"  {pack.build(con, job_id, use_web=not args.no_web)}")


def cmd_letter(args):
    import re as _re

    from .. import letter, pack
    con = db.connect()
    r = _find(con, args.id)
    out_dir = pack.PACKS / pack.slug(f"{r['company']}-{r['title']}")
    section = _re.search(r"## Cover letter\n(.*?)(?=\n## )", (out_dir / "pack.md").read_text(), _re.S)
    if not section:
        sys.exit("no cover letter in this pack")
    recipient = args.to.split("|") if args.to else [f"{r['company']} recruitment team"]
    subject = args.subject or f"Application: {r['title']}"
    print(letter.render(section.group(1), out_dir / "Firstname-Lastname-Cover-Letter.pdf", recipient, subject))


def cmd_check(args):
    from .. import pack
    con = db.connect()
    rows = con.execute("SELECT * FROM jobs WHERE status = 'ready'").fetchall() if args.ready else [_find(con, args.id)]
    for r in rows:
        pack_md = pack.PACKS / pack.slug(f"{r['company']}-{r['title']}") / "pack.md"
        if pack_md.exists():
            pack.add_company_check(pack_md, r["company"])
            print(f"checked {r['company']}")


def cmd_contacts(args):
    from .. import contacts
    con = db.connect()
    found = contacts.find(con, _find(con, args.id))
    for p in found["people"]:
        print(f"{p['name']}  |  {p['role']}  |  {p['source']}{'' if p['sure'] else '  (check)'}")
        print(f"    {p.get('url') or p.get('email') or ''}  {p['why']}")
    print(f"({len(found['people'])} suggested by {found['by']}{'; ' + found['note'] if found['note'] else ''})")
