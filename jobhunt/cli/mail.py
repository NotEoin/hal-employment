"""Commands for the job inbox and what it asks of the applicant: mail, digest, inbox, todo.

The bodies moved unchanged from `jobhunt/__main__.py`; the parser, its arguments and help stay there,
because the voice bridge and the /jobs command parse what these commands print.
"""
from .. import db
from ..notify import notify


def cmd_mail(args):
    from .. import gmail
    con = db.connect()
    attention = gmail.sync(con, args.days)
    from .. import tasks
    tasks.sync(con)
    rows = con.execute("SELECT kind, COUNT(*) n FROM emails GROUP BY kind ORDER BY n DESC").fetchall()
    print("emails:", ", ".join(f"{r['kind']} {r['n']}" for r in rows))
    from .. import vault
    vault.write(con)
    for a in attention:
        print(f"  NEEDS YOU  {a['kind']:10} {a['company'] or '?'} | {a['subject']}")
        if not args.quiet:
            notify(f"{a['kind'].title()}: {a['company'] or a['sender']}", a["subject"], urgent=a["kind"] != "human")


def cmd_digest(args):
    from .. import digest
    con = db.connect()
    print(digest.spoken(con) if args.spoken else digest.send(con, args.dry_run))


def cmd_todo(args):
    con = db.connect()
    if args.op == "add":
        db.add_todo(con, "task", " ".join(args.rest))
        con.commit()
    elif args.op == "done":
        con.execute("UPDATE todos SET done = ? WHERE id = ?", (db.now(), int(args.rest[0])))
        con.commit()
    rows = con.execute("""SELECT t.*, j.company FROM todos t LEFT JOIN jobs j ON j.id = t.job_id
                          WHERE t.done IS NULL ORDER BY t.priority, t.due IS NULL, t.due, t.created""").fetchall()
    for r in rows:
        flag = "!" if r["priority"] <= 1 else " "
        print(f"{flag} {r['id']:4}  {r['title']}" + (f"  (due {r['due']})" if r["due"] else ""))
    if not rows:
        print("Nothing to do.")


def cmd_inbox(args):
    con = db.connect()
    where = "kind = ?" if args.kind else "kind NOT IN ('alert', 'admin', 'other')"
    rows = con.execute(f"SELECT * FROM emails WHERE {where} ORDER BY date DESC LIMIT ?",
                       ((args.kind, args.n) if args.kind else (args.n,))).fetchall()
    for r in rows:
        print(f"{r['date'][:10]}  {r['kind']:10} {(r['company'] or '?')[:22]:22} {(r['role'] or '')[:34]:34} {r['subject'][:60]}")
        if r["job_id"]:
            print(f"            -> {r['job_id']}")
