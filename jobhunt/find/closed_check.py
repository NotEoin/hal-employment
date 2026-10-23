"""Is the advert still open? The cheap check before a job first appears in Matches.

One fetch per job: a 404 or 410, the closed wording Hal's form filler already knows, or
Greenhouse's `error=true` redirect means closed. At most `PER_SEARCH` checks a search, `AT_ONCE` at a time,
so a search never hammers an employer's site. Saved jobs are re-checked daily by the scheduler. The fetch
is passed in, so tests never touch the network.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from jobhunt.core import clock

log = logging.getLogger(__name__)

PER_SEARCH = 20
AT_ONCE = 4
FETCH_TIMEOUT = 15          # seconds per advert page
HEAD_CHARS = 6000           # closed wording sits near the top of the page
GONE = (404, 410)
CLOSED = re.compile(r"(no longer|not) (available|accepting applications|open|exists?|active|live)|"
                    r"not available anymore|position (has been )?(filled|closed)|vacancy (has )?closed|"
                    r"applications? (are |is )?(now )?closed|this job (has )?expired|job not found", re.I)

Fetch = Callable[[str, float], tuple[int, str, str]]   # (url, timeout) -> (status, final url, page text)


def verdict(status: int, final_url: str, text: str) -> str:
    """"closed", "open" or "unknown" for one fetched advert page."""
    if status in GONE or ("greenhouse.io" in final_url and "error=true" in final_url):
        return "closed"
    if status >= 400:
        return "unknown"
    return "closed" if CLOSED.search((text or "")[:HEAD_CHARS]) else "open"


def _default_fetch(url: str, timeout: float) -> tuple[int, str, str]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Hal Employment)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""


def check(job: dict, fetch: Fetch | None = None) -> str:
    """The open state of one job's advert; "unknown" when the page could not be read."""
    if not job.get("url"):
        return "unknown"
    try:
        return verdict(*(fetch or _default_fetch)(job["url"], FETCH_TIMEOUT))
    except Exception as e:
        log.info("open check failed for %s: %s", job.get("id"), e)
        return "unknown"


def check_new(con, job_ids: list[str], fetch: Fetch | None = None) -> dict[str, str]:
    """Checks up to `PER_SEARCH` new jobs, `AT_ONCE` at a time; stores and returns each state."""
    ids = job_ids[:PER_SEARCH]
    rows = {r["id"]: dict(r) for r in con.execute(
        f"SELECT id, url FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    with ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
        states = dict(zip(rows, pool.map(lambda j: check(j, fetch), rows.values()), strict=False))
    now = clock.iso()
    for job_id, state in states.items():
        con.execute("UPDATE jobs SET open_state = ?, open_checked_at = ? WHERE id = ?", (state, now, job_id))
        if state == "closed":
            con.execute("UPDATE jobs SET score = 0, hidden_rule = 'closed', score_notes = 'advert closed' WHERE id = ?",
                        (job_id,))
            con.execute("""INSERT OR REPLACE INTO job_reasons (job_id, rule, stage, points, hide, label, note)
                           VALUES (?, 'closed', 'closed', 0, 1, ?, 'advert closed')""",
                        (job_id, "The advert had closed before Hal could show it"))
    con.commit()
    return states
