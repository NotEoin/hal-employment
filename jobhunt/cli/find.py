"""Commands for finding and tracking jobs: discover, rescore, add, screen, list, show, set, note, stats, queue, probe.

The bodies moved from `jobhunt/__main__.py`; the parser, its arguments and help stay there,
because the voice bridge and the /jobs command parse what these commands print.

`discover` searches every switched-on source of the sources registry and prints exactly what it
printed before the registry. Sites skipped on purpose (no key, read with the email) are not counted and
print nothing; Where Hal looks says what they miss. Each source's run, its health and any error are stored,
so the scheduled search tells the page what happened without anyone reading its output.
"""
import re
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import db
from ..core import clock, settings
from ..find import detect, rules
from ..find import goal as goal_mod
from ..find import sources_registry as registry
from ..notify import notify
from ..score import prefilter
from ..sources import academic, aggregators, ats, startups

# The registry's own table of built-in fetchers (the same dict object), named as before for older callers.
STANDALONE = registry.builtin_fetchers()


# Sources whose listings lack the advert text, fetched per job after the cheap checks.
DETAIL = {**ats.DETAIL, "yc": startups.yc_detail, "jobsacuk": academic.detail, "reed": aggregators.reed_detail,
          "adzuna": aggregators.adzuna_detail}
FULL_LISTINGS = ("ats_board", "company_watch")   # these list every open job, so a missing one has gone


def _fetch(source, goal):
    try:
        return source, registry.fetch(source, goal), None
    except registry.Skipped as s:
        return source, None, str(s)


def cmd_discover(args):
    """Handles `jobhunt discover`: searches the enabled sources for new jobs."""
    con = db.connect()
    goal = goal_mod.current()
    sources = registry.enabled(con)
    _resolve_watched(con, sources)
    started = clock.iso()
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(_fetch, src, goal): src for src in sources}
        for f in as_completed(futs):
            src = futs[f]
            try:
                _src, fetched, skipped = f.result()
            except Exception as e:  # noqa: BLE001 - one failing source must not stop the run
                words = f"{type(e).__name__} {e}"
                errors.append(f"{src['name']}: {words}")
                registry.record(con, src, started, error=words)
                registry.note_error(src, words, con)
                continue
            if skipped is not None:
                registry.record(con, src, started, skipped=skipped)
                continue
            results.append((src, fetched[0]))

    new_jobs, total = [], 0
    for src, jobs in results:
        kept = 0
        for j in jobs:
            total += 1
            j.setdefault("board_size", None)
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
                con.execute("UPDATE jobs SET source_id = ? WHERE id = ?", (src["id"], j["id"]))
                if j.get("closes_at"):
                    con.execute("UPDATE jobs SET closes_at = ? WHERE id = ?", (j["closes_at"], j["id"]))
                s = rules.score_and_save(con, j)
                if s > 0:
                    new_jobs.append((s, j))
                    kept += 1
        if src["kind"] in FULL_LISTINGS:
            db.mark_gone(con, jobs[0]["source"] if jobs else src["config"].get("ats"), src["name"],
                         {j["id"] for j in jobs})
        elif src.get("builtin") == "ktp":   # KTP is listed in full too; HN threads are monthly
            db.mark_gone(con, "ktp", "KTP", {j["id"] for j in jobs})
        registry.record(con, src, started, seen=len(jobs), new=kept, kept=kept)
    con.commit()

    new_jobs.sort(key=lambda x: -x[0])
    print(f"{total} postings from {len(results)} sources; {len(new_jobs)} new relevant.")
    for s, j in new_jobs[:25]:
        print(f"  {s:5.0f}  {j['company'][:22]:22}  {j['title'][:60]}")
    if errors:
        print(f"\n{len(errors)} errors:", *errors[:10], sep="\n  ")

    strong = [(s, j) for s, j in new_jobs if s >= settings.get("notify.alert_score")]
    if strong and not args.quiet:
        top = strong[0][1]
        notify(f"{len(strong)} strong new job match{'es' * (len(strong) > 1)}",
               "\n".join(f"{s:.0f} | {j['company']} | {j['title']}" for s, j in strong[:5]),
               url=top["url"])


def _resolve_watched(con, sources):
    """Looks up a few watched companies' boards on every application system before the search."""
    import json
    from datetime import timedelta

    due = []
    cutoff = clock.iso(clock.now_utc() - timedelta(days=registry.REPROBE_DAYS))
    for src in sources:
        cfg = src["config"]
        if src["kind"] == "company_watch" and not cfg.get("slug") and (cfg.get("probed") or "") < cutoff:
            due.append(src)
    due = due[:registry.PROBES_PER_SEARCH]
    if not due:
        return
    found = detect.probe([s["name"] for s in due], in_scope=prefilter)
    for src in due:
        hit = found.get(src["name"])
        cfg = {**src["config"], "probed": clock.iso()}
        if hit is not None:
            cfg.update({"ats": hit.ats, "slug": hit.slug})
            src["area"] = f"{registry.ATS_NAMES.get(hit.ats, hit.ats)} careers page"
        src["config"] = cfg
        con.execute("UPDATE sources SET config = ?, area = ? WHERE id = ?", (json.dumps(cfg), src["area"], src["id"]))
    con.commit()


def cmd_add(args):
    """Handles `jobhunt add`: adds a job from its page address; a page that cannot be read (no network, a dead
    link) adds the job without its advert, with one line saying so."""
    import urllib.error

    from .. import http, research
    con = db.connect()
    try:
        text = research.page_text(args.url, limit=20000)
    except (urllib.error.URLError, http.NotFound, TimeoutError, OSError) as e:
        print(f"Hal could not read the advert at {args.url} ({getattr(e, 'reason', e)}), so the job is added "
              "without it and its pack has less to go on.")
        text = ""
    job = {"id": f"manual:{re.sub(r'[^a-z0-9]+', '-', (args.company + '-' + args.title).lower()).strip('-')}",
           "source": "manual", "company": args.company, "title": args.title, "location": args.location,
           "url": args.url, "description": text, "salary": args.salary, "posted_at": None, "board_size": None}
    db.upsert(con, job)
    con.execute("UPDATE jobs SET score = NULL, score_notes = 'added by hand' WHERE id = ?", (job["id"],))
    db.set_status(con, job["id"], "shortlisted", "added by hand")
    con.commit()
    print(job["id"])


def cmd_screen(args):
    """Handles `jobhunt screen`: screens jobs above a minimum score."""
    from .. import screen
    con = db.connect()
    for job_id, ok, reason in screen.screen(con, args.min, args.n):
        print(f"{'PASS' if ok else 'fail'}  {job_id[:60]:60}  {reason}")


def cmd_queue(args):
    """Handles `jobhunt queue`: lists the jobs ready to apply to."""
    from .. import replenish
    con = db.connect()
    rows = con.execute("""SELECT * FROM jobs WHERE status = 'ready' AND active = 1
                          ORDER BY score DESC, first_seen DESC LIMIT ?""", (replenish.TARGET,)).fetchall()
    for r in rows:
        print(f"{(r['score'] or 0):4.0f}  {r['company'][:22]:22}  {r['title'][:50]:50}  {r['id']}")
    print(f"({len(rows)} of {replenish.TARGET}; open http://127.0.0.1:{settings.get('advanced.port')}/ to apply)")


def cmd_rescore(args):
    """Handles `jobhunt rescore`: scores every job again."""
    con = db.connect()
    print(f"rescored {rules.rescore_all(con)}")


def _find(con, ref):
    rows = con.execute("SELECT * FROM jobs WHERE id = ? OR id LIKE ?", (ref, f"%{ref}%")).fetchall()
    if len(rows) != 1:
        sys.exit(f"{len(rows)} jobs match {ref!r}" + "".join(f"\n  {r['id']}" for r in rows[:10]))
    return rows[0]


def cmd_list(args):
    """Handles `jobhunt list`: lists jobs by status and score."""
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
    """Handles `jobhunt show`: prints one job."""
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
    """Handles `jobhunt set`: changes a job's status."""
    con = db.connect()
    r = _find(con, args.id)
    db.set_status(con, r["id"], args.status, " ".join(args.note))
    con.commit()
    print(f"{r['company']} | {r['title']} -> {args.status}")


def cmd_note(args):
    """Handles `jobhunt note`: adds a note to a job."""
    con = db.connect()
    r = _find(con, args.id)
    db.log(con, r["id"], "note", " ".join(args.text))
    con.commit()


def cmd_stats(args):
    """Handles `jobhunt stats`: counts jobs by status."""
    con = db.connect()
    for r in con.execute("SELECT status, COUNT(*) n FROM jobs WHERE status != 'new' OR score > 0 GROUP BY status ORDER BY n DESC"):
        print(f"{r['status']:12} {r['n']}")
    for r in con.execute("SELECT source, COUNT(*) n, COALESCE(SUM(score>0), 0) rel FROM jobs WHERE active=1 GROUP BY source"):
        print(f"  {r['source']:16} {r['n']:5} open, {r['rel']} relevant")


def _names(raw):
    out = []
    for n in raw:
        if n.startswith("@"):
            with open(n[1:], encoding="utf-8") as f:
                out += [line.split("#")[0].strip() for line in f if line.split("#")[0].strip()]
        else:
            out.append(n)
    return out


def cmd_probe(args):
    """Handles `jobhunt probe`: looks for employers' job boards not yet in the sources."""
    con = db.connect()
    registry.sync(con)
    known = {(r["config"].get("ats"), r["config"].get("slug")) for r in registry.enabled(con)}
    args.names = _names(args.names)
    best = detect.probe(args.names, in_scope=prefilter)
    for name in (n.split("=")[0] for n in args.names):
        if name in best:
            b = best[name]
            print(f"  {name:28} {b.ats:15} {b.slug:24} {b.size:4} open, {b.in_scope:3} in scope")
        else:
            print(f"  {name:28} -- not found on a supported ATS")
    print(f"{len(best)}/{len(args.names)} found")

    if args.save:
        added = [b for b in best.values() if (b.ats, b.slug) not in known and (b.in_scope > 0 or args.keep_empty)]
        for b in added:
            registry.add_board(con, b.name, b.ats, b.slug, added_by="you")
        con.commit()
        print(f"saved {len(added)} boards to Where Hal looks")
