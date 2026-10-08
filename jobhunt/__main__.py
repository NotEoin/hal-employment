"""jobhunt - discover, rank and track job applications.

    python -m jobhunt discover            fetch every source, score, alert on strong new matches
    python -m jobhunt list [-s STATUS] [-m MIN] [-n N] [--new-today]
    python -m jobhunt show ID
    python -m jobhunt set ID STATUS [NOTE]
    python -m jobhunt note ID TEXT
    python -m jobhunt stats
    python -m jobhunt rescore
    python -m jobhunt probe NAME [NAME ...] [--save]   find a company's ATS board
    python -m jobhunt mail [--days N]     sync the job inbox, update statuses, alert on what needs you
    python -m jobhunt inbox [-k KIND] [-n N]   classified recruiting email
    python -m jobhunt digest [--dry-run|--spoken]   email the daily summary, or print it for speech
    python -m jobhunt vault               rewrite the pipeline note in Hal's Employment vault
    python -m jobhunt pack ID|--shortlisted [--no-web]   draft application packs
    python -m jobhunt serve [--port N] [--open]   run the Hal Employment web interface
    python -m jobhunt queue               the jobs waiting in the queue, best first
    python -m jobhunt todo [add TEXT|done ID]   the to-do list

IDs can be given as any unique prefix or substring.
"""
import argparse
import re
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db
from .config import BOARDS_PATH, CONFIG, boards
from .http import NotFound
from .notify import notify
from .score import prefilter, score
from .sources import academic, aggregators, ats, hn, ktp, startups

STANDALONE = {"hn": hn.fetch, "ktp": ktp.fetch, "yc": startups.yc, "80k": startups.eighty_thousand_hours,
              "workable-search": startups.workable_search, "jobsacuk": academic.fetch,
              "adzuna": aggregators.adzuna, "reed": aggregators.reed}
# Sources whose listings lack the advert text, fetched per job after the cheap checks.
DETAIL = {**ats.DETAIL, "yc": startups.yc_detail, "jobsacuk": academic.detail, "reed": aggregators.reed_detail,
          "adzuna": aggregators.adzuna_detail}


def _fetch_board(b):
    jobs, size = ats.FETCHERS[b["ats"]](b["slug"], b["name"])
    for j in jobs:
        j["board_size"] = size
    return b, jobs


def cmd_discover(args):
    con = db.connect()
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(_fetch_board, b): b["name"] for b in boards()}
        futs.update({pool.submit(fn): name for name, fn in STANDALONE.items()})
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:  # noqa: BLE001 - one failing source must not stop the run
                errors.append(f"{futs[f]}: {type(e).__name__} {e}")

    new_jobs, total = [], 0
    for res in results:
        if isinstance(res, tuple):  # ATS boards return (board, jobs)
            b, jobs = res
            source = b["ats"]
        else:
            jobs = res
            source = jobs[0]["source"] if jobs else None
            for j in jobs:
                j.setdefault("board_size", None)
        for j in jobs:
            total += 1
            needs_detail = not j["description"] or j["source"] in ("yc", "reed", "adzuna")
            if needs_detail and j["source"] in DETAIL and prefilter(j):
                known = con.execute("SELECT description FROM jobs WHERE id=?", (j["id"],)).fetchone()
                if known and known["description"]:
                    j["description"] = known["description"]
                else:
                    try:
                        j["description"] = DETAIL[j["source"]](j)
                    except Exception as e:
                        errors.append(f"{j['id']} detail: {e}")
            if db.upsert(con, j):
                s, why = score(j)
                con.execute("UPDATE jobs SET score=?, score_notes=? WHERE id=?", (s, why, j["id"]))
                if s > 0:
                    new_jobs.append((s, j))
        if isinstance(res, tuple):
            db.mark_gone(con, source, b["name"], {j["id"] for j in jobs})
        elif source == "ktp":               # KTP is listed in full too; HN threads are monthly
            db.mark_gone(con, "ktp", "KTP", {j["id"] for j in jobs})
    con.commit()

    new_jobs.sort(key=lambda x: -x[0])
    print(f"{total} postings from {len(results)} sources; {len(new_jobs)} new relevant.")
    for s, j in new_jobs[:25]:
        print(f"  {s:5.0f}  {j['company'][:22]:22}  {j['title'][:60]}")
    if errors:
        print(f"\n{len(errors)} errors:", *errors[:10], sep="\n  ")

    strong = [(s, j) for s, j in new_jobs if s >= CONFIG.get("alert_score", 70)]
    if strong and not args.quiet:
        top = strong[0][1]
        notify(f"{len(strong)} strong new job match{'es' * (len(strong) > 1)}",
               "\n".join(f"{s:.0f} | {j['company']} | {j['title']}" for s, j in strong[:5]),
               url=top["url"])


def cmd_mail(args):
    from . import gmail
    con = db.connect()
    attention = gmail.sync(con, args.days)
    from . import tasks
    tasks.sync(con)
    rows = con.execute("SELECT kind, COUNT(*) n FROM emails GROUP BY kind ORDER BY n DESC").fetchall()
    print("emails:", ", ".join(f"{r['kind']} {r['n']}" for r in rows))
    from . import vault
    vault.write(con)
    for a in attention:
        print(f"  NEEDS YOU  {a['kind']:10} {a['company'] or '?'} | {a['subject']}")
        if not args.quiet:
            notify(f"{a['kind'].title()}: {a['company'] or a['sender']}", a["subject"], urgent=a["kind"] != "human")


def cmd_digest(args):
    from . import digest
    con = db.connect()
    print(digest.spoken(con) if args.spoken else digest.send(con, args.dry_run))


def cmd_pack(args):
    from . import cv, llm, pack
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


def cmd_add(args):
    from . import research
    con = db.connect()
    text = research.page_text(args.url, limit=20000)
    job = {"id": f"manual:{re.sub(r'[^a-z0-9]+', '-', (args.company + '-' + args.title).lower()).strip('-')}",
           "source": "manual", "company": args.company, "title": args.title, "location": args.location,
           "url": args.url, "description": text, "salary": args.salary, "posted_at": None, "board_size": None}
    db.upsert(con, job)
    con.execute("UPDATE jobs SET score = NULL, score_notes = 'added by hand' WHERE id = ?", (job["id"],))
    db.set_status(con, job["id"], "shortlisted", "added by hand")
    con.commit()
    print(job["id"])


def cmd_screen(args):
    from . import screen
    con = db.connect()
    for job_id, ok, reason in screen.screen(con, args.min, args.n):
        print(f"{'PASS' if ok else 'fail'}  {job_id[:60]:60}  {reason}")


def cmd_letter(args):
    import re as _re
    from . import letter, pack
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
    from . import pack
    con = db.connect()
    rows = con.execute("SELECT * FROM jobs WHERE status = 'ready'").fetchall() if args.ready else [_find(con, args.id)]
    for r in rows:
        pack_md = pack.PACKS / pack.slug(f"{r['company']}-{r['title']}") / "pack.md"
        if pack_md.exists():
            pack.add_company_check(pack_md, r["company"])
            print(f"checked {r['company']}")


def cmd_serve(args):
    from .web import server
    server.serve(args.port, args.open)


def cmd_queue(args):
    from . import replenish
    con = db.connect()
    rows = con.execute("""SELECT * FROM jobs WHERE status = 'ready' AND active = 1
                          ORDER BY score DESC, first_seen DESC LIMIT ?""", (replenish.TARGET,)).fetchall()
    for r in rows:
        print(f"{(r['score'] or 0):4.0f}  {r['company'][:22]:22}  {r['title'][:50]:50}  {r['id']}")
    print(f"({len(rows)} of {replenish.TARGET}; open http://127.0.0.1:{CONFIG.get('web_port', 8737)}/ to apply)")


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


def cmd_vault(args):
    from . import vault
    print(vault.write(db.connect()) or f"vault not found: {vault.VAULT}")


def cmd_inbox(args):
    con = db.connect()
    where = "kind = ?" if args.kind else "kind NOT IN ('alert', 'admin', 'other')"
    rows = con.execute(f"SELECT * FROM emails WHERE {where} ORDER BY date DESC LIMIT ?",
                       ((args.kind, args.n) if args.kind else (args.n,))).fetchall()
    for r in rows:
        print(f"{r['date'][:10]}  {r['kind']:10} {(r['company'] or '?')[:22]:22} {(r['role'] or '')[:34]:34} {r['subject'][:60]}")
        if r["job_id"]:
            print(f"            -> {r['job_id']}")


def cmd_rescore(args):
    con = db.connect()
    # Applications found in email have no advert to score.
    rows = con.execute("SELECT * FROM jobs WHERE source != 'email'").fetchall()
    for r in rows:
        s, why = score(dict(r))
        con.execute("UPDATE jobs SET score=?, score_notes=? WHERE id=?", (s, why, r["id"]))
    con.commit()
    print(f"rescored {len(rows)}")


def _find(con, ref):
    rows = con.execute("SELECT * FROM jobs WHERE id = ? OR id LIKE ?", (ref, f"%{ref}%")).fetchall()
    if len(rows) != 1:
        sys.exit(f"{len(rows)} jobs match {ref!r}" + "".join(f"\n  {r['id']}" for r in rows[:10]))
    return rows[0]


def cmd_list(args):
    con = db.connect()
    # Applications found via email have no score, so only filter on score
    # when asked to, or when browsing unreviewed postings.
    where, params = ["1"], []
    if args.min is not None or args.status in ("new", "shortlisted", "ready"):
        where.append("COALESCE(score, 0) >= ?")
        params.append(args.min if args.min is not None else 1)
    if args.status != "all":
        where.append("status = ?")
        params.append(args.status)
    if args.status in ("new", "shortlisted", "ready"):
        where.append("active = 1")
    internships = "COALESCE(score_notes, '') LIKE '%graduate internship%'"
    where.append(internships if args.internships else f"NOT {internships}")
    if args.new_today:
        where.append("first_seen >= datetime('now', '-1 day')")
    rows = con.execute(
        f"SELECT * FROM jobs WHERE {' AND '.join(where)} ORDER BY score DESC, first_seen DESC LIMIT ?",
        (*params, args.n)).fetchall()
    for r in rows:
        print(f"{(r['score'] or 0):4.0f}  {r['status']:11} {r['company'][:20]:20}  {r['title'][:55]:55}  {(r['location'] or '')[:28]}")
        print(f"      {r['id']}  | {r['score_notes']}")
    print(f"({len(rows)} shown)")


def cmd_show(args):
    con = db.connect()
    r = _find(con, args.id)
    print(f"{r['title']}\n{r['company']} | {r['location']} | {r['salary'] or 'salary n/a'}")
    print(f"{r['url']}\nstatus {r['status']} | score {r['score']:.0f} ({r['score_notes']})")
    print(f"posted {r['posted_at']} | first seen {r['first_seen']} | {'open' if r['active'] else 'CLOSED'}\n")
    print(textwrap.shorten(r["description"] or "", 3000, placeholder=" ...") if not args.full else r["description"])
    events = con.execute("SELECT * FROM events WHERE job_id=? ORDER BY at", (r["id"],)).fetchall()
    if events:
        print("\nHistory:")
        for e in events:
            print(f"  {e['at'][:16]}  {e['kind']:7} {e['detail']}")


def cmd_set(args):
    con = db.connect()
    r = _find(con, args.id)
    db.set_status(con, r["id"], args.status, " ".join(args.note))
    con.commit()
    print(f"{r['company']} | {r['title']} -> {args.status}")


def cmd_note(args):
    con = db.connect()
    r = _find(con, args.id)
    db.log(con, r["id"], "note", " ".join(args.text))
    con.commit()


def cmd_stats(args):
    con = db.connect()
    for r in con.execute("SELECT status, COUNT(*) n FROM jobs WHERE status != 'new' OR score > 0 GROUP BY status ORDER BY n DESC"):
        print(f"{r['status']:12} {r['n']}")
    for r in con.execute("SELECT source, COUNT(*) n, COALESCE(SUM(score>0), 0) rel FROM jobs WHERE active=1 GROUP BY source"):
        print(f"  {r['source']:16} {r['n']:5} open, {r['rel']} relevant")


def _slugs(name):
    """'Name=slug' pins the slug; otherwise try the joined and hyphenated forms.
    Never the first word alone: 'Bloom & Wild' must not match some 'bloom'."""
    if "=" in name:
        return [name.split("=", 1)[1]]
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    return list(dict.fromkeys([base.replace(" ", ""), base.replace(" ", "-")]))


def _names(raw):
    out = []
    for n in raw:
        if n.startswith("@"):
            out += [l.split("#")[0].strip() for l in open(n[1:]) if l.split("#")[0].strip()]
        else:
            out.append(n)
    return out


def cmd_probe(args):
    known = {(b["ats"], b["slug"]) for b in boards()}
    args.names = _names(args.names)
    found = []

    def attempt(name, kind, slug):
        try:
            jobs, size = ats.FETCHERS[kind](slug, name)
        except NotFound:
            return None
        except Exception:
            return None
        if size == 0:
            return None
        uk = sum(1 for j in jobs if prefilter(j))
        return name, kind, slug, size, uk

    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = [pool.submit(attempt, n, k, s) for n in args.names for s in _slugs(n) for k in ats.FETCHERS]
        for f in as_completed(futs):
            if r := f.result():
                found.append(r)

    best = {}
    for name, kind, slug, size, uk in found:
        name = name.split("=")[0]
        if name not in best or (uk, size) > (best[name][4], best[name][3]):
            best[name] = (name, kind, slug, size, uk)
    for name in (n.split("=")[0] for n in args.names):
        if name in best:
            _, kind, slug, size, uk = best[name]
            print(f"  {name:28} {kind:15} {slug:24} {size:4} open, {uk:3} in scope")
        else:
            print(f"  {name:28} -- not found on a supported ATS")
    print(f"{len(best)}/{len(args.names)} found")

    if args.save:
        added = [b for b in best.values() if (b[1], b[2]) not in known and (b[4] > 0 or args.keep_empty)]
        with BOARDS_PATH.open("a") as f:
            for name, kind, slug, *_ in added:
                f.write(f'\n[[board]]\nname = "{name}"\nats = "{kind}"\nslug = "{slug}"\n')
        print(f"saved {len(added)} boards to {BOARDS_PATH.name}")


def main():
    p = argparse.ArgumentParser(prog="jobhunt", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("discover"); s.add_argument("-q", "--quiet", action="store_true"); s.set_defaults(fn=cmd_discover)
    sub.add_parser("rescore").set_defaults(fn=cmd_rescore)
    s = sub.add_parser("mail"); s.add_argument("--days", type=int); s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(fn=cmd_mail)
    s = sub.add_parser("digest"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--spoken", action="store_true"); s.set_defaults(fn=cmd_digest)
    sub.add_parser("vault").set_defaults(fn=cmd_vault)
    sub.add_parser("queue").set_defaults(fn=cmd_queue)
    s = sub.add_parser("todo"); s.add_argument("op", nargs="?", choices=["add", "done"])
    s.add_argument("rest", nargs="*"); s.set_defaults(fn=cmd_todo)
    s = sub.add_parser("serve", help="run the Hal Employment web interface")
    s.add_argument("--port", type=int); s.add_argument("--open", action="store_true"); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("add", help="track an advert found by hand, skipping the filters")
    s.add_argument("url"); s.add_argument("--company", required=True); s.add_argument("--title", required=True)
    s.add_argument("--location", default=""); s.add_argument("--salary"); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("screen", help="ask the local model which jobs a new graduate could get")
    s.add_argument("-m", "--min", type=float, default=50); s.add_argument("-n", type=int, default=60)
    s.set_defaults(fn=cmd_screen)
    s = sub.add_parser("letter", help="render a pack's cover letter as a PDF in the CV's style")
    s.add_argument("id"); s.add_argument("--to", help="recipient lines separated by |"); s.add_argument("--subject")
    s.set_defaults(fn=cmd_letter)
    s = sub.add_parser("check"); s.add_argument("id", nargs="?"); s.add_argument("--ready", action="store_true")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("pack"); s.add_argument("id", nargs="?"); s.add_argument("--shortlisted", action="store_true")
    s.add_argument("--no-web", action="store_true")
    s.add_argument("--cv-only", action="store_true", help="re-render tailored CVs for drafted packs")
    s.set_defaults(fn=cmd_pack)
    s = sub.add_parser("inbox"); s.add_argument("-k", "--kind"); s.add_argument("-n", type=int, default=60)
    s.set_defaults(fn=cmd_inbox)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)

    s = sub.add_parser("list")
    s.add_argument("-s", "--status", default="new", help=f"one of {', '.join(db.STATUSES)}, or 'all'")
    s.add_argument("-m", "--min", type=float)
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--new-today", action="store_true")
    s.add_argument("--internships", action="store_true", help="list graduate internships instead of jobs")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show"); s.add_argument("id"); s.add_argument("--full", action="store_true"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("set"); s.add_argument("id"); s.add_argument("status", choices=db.STATUSES)
    s.add_argument("note", nargs="*"); s.set_defaults(fn=cmd_set)
    s = sub.add_parser("note"); s.add_argument("id"); s.add_argument("text", nargs="+"); s.set_defaults(fn=cmd_note)

    s = sub.add_parser("probe"); s.add_argument("names", nargs="+")
    s.add_argument("--save", action="store_true"); s.add_argument("--keep-empty", action="store_true")
    s.set_defaults(fn=cmd_probe)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
