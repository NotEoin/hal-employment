"""The action kinds of notes about job sites: turning a report into a note (`sites.propose_note`) and the
data task `sites.import_state`, which moves what the old screens knew about sites into the `sites` table.

The import only reads: `signed-in.json` stays where it is, because the engines still read it until their
sign-in check moves onto `sites.account`, and the "too much effort" events stay in `events`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from jobhunt.core import clock, paths, runner
from jobhunt.core.db import tasks
from jobhunt.sites import notes as N
from jobhunt.sites import report

log = logging.getLogger(__name__)

IMPORT_TASK = "sites.import_state"
IMPORT_AFTER = 14     # migration 0014 made the sites tables
SIGNED_IN_FILE = "signed-in.json"


def _report_example(world: Any) -> dict:
    """A made-up report about one of the made-up world's jobs, for gate G9."""
    con = world.con
    job = con.execute("SELECT id, url FROM jobs ORDER BY id LIMIT 1").fetchone()
    cur = con.execute("INSERT INTO site_reports (at, job_id, site, what, words, state) VALUES (?, ?, ?, ?, ?, 'new')",
                      (clock.iso(), job["id"], N.site_key(job["url"] or "https://example.org/"),
                       "Something else", "The page asked me to pick a region first."))
    con.commit()
    return {"report_id": cur.lastrowid, "words": "The page asked me to pick a region first."}


@runner.action("sites.propose_note", area="learning", label="Turning what you told Hal into a note",
               why="You told Hal: {words}", priority=0, example=_report_example)
def propose_note(ctx, report_id: int, words: str = "") -> dict:
    """Drafts a note from a report for the person to check, or saves their words as a note; `words` fill
    the Activity why only, the report row holds them."""
    return report.propose_note(ctx, int(report_id))


def import_state(con) -> dict:
    """Marks the sites the old screens were signed in to, per employer, and those found too much effort."""
    counts = {"signed_in": 0, "effort": 0}
    path = paths.data_dir() / SIGNED_IN_FILE
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError) as e:
        log.warning("could not read %s: %s", path, e)
        old = []
    now = clock.iso()
    keys: set[str] = set()
    for site in [str(s) for s in old if s]:
        keys.add(site)
        # an old key named the whole system (myworkdayjobs.com); each employer's own page there was signed in
        for r in con.execute("SELECT url FROM jobs WHERE url LIKE ?", (f"%{site}%",)):
            if N.registrable(r["url"]) == site:
                keys.add(N.site_key(r["url"]))
    for key in sorted(keys):
        url = "https://" + key
        con.execute("INSERT INTO sites (key, kind, ats, account, first_seen, last_seen) "
                    "VALUES (?, ?, ?, 'signed_in', ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET account = 'signed_in'",
                    (key, "tenant" if N.site_key(url) != N.registrable(url) else "site", N.ats_of(url), now, now))
        counts["signed_in"] += 1
    for r in con.execute("SELECT detail, count(*) n FROM events WHERE kind = 'effort' AND detail != '' "
                         "GROUP BY detail"):
        url = "https://" + r["detail"]
        con.execute("INSERT INTO sites (key, kind, ats, effort, first_seen, last_seen) VALUES (?, 'site', ?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET effort = excluded.effort",
                    (r["detail"], N.ats_of(url), r["n"], now, now))
        counts["effort"] += 1
    con.commit()
    return counts


@tasks.data_task(IMPORT_TASK, after=IMPORT_AFTER, title="Moved what Hal knew about job sites into its site notes")
def run_import(ctx) -> None:
    """The data task body: imports, then says how many sites it marked."""
    counts = import_state(ctx.con)
    ctx.step(f"Marked {counts['signed_in']} sites you were signed in to and {counts['effort']} you found too much "
             f"effort")
