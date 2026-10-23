"""Actions for Where Hal looks: look at a pasted address or a company, save what was found, and the
one-time import of the old board lists (data task `find.import_boards`).

Looking happens in the background (`find.detect_site`, priority 0, because the person waits on it). What it
found is kept in this process for `HELD_S` under a random id, with the jobs read from the site, so pressing
Save stores exactly what the person saw without reading the site a second time. After a restart the id is
gone and the dialog asks the person to look again.
"""

from __future__ import annotations

import json
import secrets as token_source
import threading
import time
from urllib.parse import urlparse

from jobhunt.core import activity, clock, events, runner
from jobhunt.core.db.tasks import data_task
from jobhunt.find import detect, rules
from jobhunt.find import sources_registry as registry

HELD_S = 30 * 60            # how long a look stays ready to save
SOURCES_MIGRATION = 12      # the migration that made the sources table
PAGE_TIMEOUT = 20           # seconds to read a pasted page
SUB_CHARS = 60

_held: dict[str, tuple[float, detect.Detected]] = {}
_lock = threading.Lock()


def hold(found: detect.Detected) -> str:
    """Keeps a look for saving and returns its id."""
    now = time.monotonic()
    with _lock:
        for key in [k for k, (at, _) in _held.items() if now - at > HELD_S]:
            del _held[key]
        key = token_source.token_hex(8)
        _held[key] = (now, found)
    return key


def held(detected_id: str) -> detect.Detected:
    """A look kept by `hold`; RuntimeError (409) when it has run out or Hal restarted since."""
    with _lock:
        item = _held.get(detected_id or "")
    if item is None or time.monotonic() - item[0] > HELD_S:
        raise RuntimeError("Hal no longer has that look at the site. Press Look again.")
    return item[1]


def _page(url: str) -> str:
    from jobhunt import http

    return http.get(url, timeout=PAGE_TIMEOUT, retries=0)


def _read_board(ats: str, slug: str, name: str) -> tuple[list[dict], int]:
    from jobhunt.sources import ats as ats_mod

    return ats_mod.FETCHERS[ats](slug, name)


def _read_feed(url: str, name: str) -> list[dict]:
    from jobhunt.sources import feeds

    return feeds.read_feed_url(url, name)


def _scored(found: detect.Detected) -> list[dict]:
    """The jobs on a site, scored against the goal, best first, for the dialog's checkboxes."""
    from jobhunt.core import settings

    lowest = int(settings.get("scoring.min_show") or 0)
    out = []
    for j in found.jobs:
        s = rules.score(j)
        where = ", ".join(x for x in (j.get("location"), j.get("salary")) if x)
        out.append({"title": j["title"], "sub": where[:SUB_CHARS], "score": s.score, "url": j["url"],
                    "hidden": s.hidden.label if s.hidden else None, "keep": s.hidden is None and s.score >= lowest})
    return sorted(out, key=lambda x: -x["score"])


def _result(found: detect.Detected) -> dict:
    return {"detected_id": hold(found), "kind": found.kind, "ats": found.ats, "name": found.name,
            "can_watch": found.can_watch, "words": found.words, "jobs": _scored(found)}


def _look_at_company(ctx, company: str) -> dict:
    from jobhunt.score import prefilter

    ctx.step(f"Looking for {company}'s job board on every application system Hal knows")
    best = detect.probe([company], in_scope=prefilter).get(company.split("=")[0])
    if best is None:
        found = detect.Detected("company_watch", company, can_watch=False,
                                words=f"Hal could not find a job board for {company}. Paste the address of its "
                                      "careers page instead.")
        return {**_result(found), "ask_for_page": True}
    label = registry.ATS_NAMES.get(best.ats, best.ats)
    found = detect.Detected("ats_board", company, ats=best.ats, slug=best.slug, jobs=list(best.jobs),
                            words=f"{company} uses {label}. Hal can check it at every search. It has "
                                  f"{best.size} jobs now.")
    return _result(found)


def _example(world) -> dict:
    # An application system Hal has no reader for: decided from the address alone, so no page is fetched.
    return {"url": "https://made-up-trust.teamtailor.com/jobs"}


@runner.action("find.detect_site", area="find", label="Looking at the site you pasted",
               why="You asked Hal to look at a job site", priority=0, example=_example)
def detect_site(ctx, url: str = "", company: str = "") -> dict:
    """Works out what a pasted address (or a company name) is and lists the jobs on it now."""
    url, company = (url or "").strip(), (company or "").strip()
    if company and not url.startswith(("http://", "https://")):
        return _look_at_company(ctx, company or url)
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Paste the site's address, starting with https://")
    ctx.step("Checking which application system the address belongs to", total=2)
    page = None
    if detect.ats_from_url(url) is None:
        ctx.step("Reading the page", total=2)
        page = _page(url)
        ctx.checkpoint()
    found = detect.detect(url, page, fetch_ats=_read_board, read_feed=_read_feed)
    ctx.step(found.words)
    return _result(found)


def save(con, detected_id: str, keep_urls: list[str], *, session_id: str | None = None) -> dict:
    """Adds a looked-at site to Where Hal looks and keeps the jobs the person ticked."""
    found = held(detected_id)
    if not found.can_watch:
        raise RuntimeError(found.words or "Hal cannot check this site by itself.")
    if found.kind == "ats_board":
        sid = registry.ats_id(found.ats, found.slug)
        area = f"{registry.ATS_NAMES.get(found.ats, found.ats)} careers page, added {clock.local():%d %b}"
    else:
        sid = registry.page_id(found.kind, found.url)
        area = f"Careers page, added {clock.local():%d %b}"
    if con.execute("SELECT 1 FROM sources WHERE id = ?", (sid,)).fetchone():
        con.execute("UPDATE sources SET enabled = 1 WHERE id = ?", (sid,))
    else:
        con.execute("""INSERT INTO sources (id, kind, name, area, config, enabled, added_by, created)
                       VALUES (?, ?, ?, ?, ?, 1, 'you', ?)""",
                    (sid, found.kind, found.name, area, json.dumps(found.config()), clock.iso()))
    wanted = set(keep_urls or [])
    jobs = [{**j, "source_id": sid} for j in found.jobs if j["url"] in wanted]
    _seen, _kept, _shown = registry.keep(con, jobs)
    activity.record("find", "find.source_added", "You added a job site",
                    f"You added {found.name} in Where Hal looks", changed=f"Hal now checks {found.name} at every "
                    f"search, and kept {len(jobs)} of its jobs", undo={"type": "source_added", "id": sid},
                    session_id=session_id, con=con)
    con.commit()
    for j in jobs:
        events.publish("job", id=j["id"])
    return {"id": sid, "added": len(jobs)}


@data_task("find.import_boards", after=SOURCES_MIGRATION, title="Moved your job sites into Where Hal looks")
def import_boards(ctx) -> None:
    """Puts the built-in sites in, and the old board list and watched companies, once."""
    registry.sync(ctx.con)
    row = ctx.con.execute("SELECT count(*) FROM sources WHERE added_by = 'import'").fetchone()
    ctx.step(f"Where Hal looks has {row[0]} company boards and companies from your old lists")
