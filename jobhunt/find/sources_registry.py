"""The sources registry: every place Hal searches is a row of `sources`, run through its kind.

Built-in sites come from `find/seed/sources.toml`; the person's company boards, careers pages and feeds are
rows they added (`added_by = you`) or that the import made from the old `boards.toml` and `companies.txt`.
`sync()` keeps the built-ins in step with the seed on every search and every listing, so an update that
adds a site needs no separate step. A built-in follows the preset's default (on for a humanities graduate,
off for a programmer, and so on) until the person switches it themselves; from then on their choice stays.

Fetching is separate from storing: `fetch()` touches only the network, so the search can run many sources
in threads, and `record()` writes one `source_runs` row and the source's health afterwards. A source that
needs a key Hal does not have is skipped with plain words saying what is missed, never an error, so
someone without job-board keys still gets every source that needs none.

`BUILTIN_FETCHERS` is the same dict object as `cli.find.STANDALONE`, so code and tests that replace a
built-in fetcher there change what the registry runs.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import sqlite3
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobhunt.core import clock

log = logging.getLogger(__name__)

SEED = Path(__file__).resolve().parent / "seed" / "sources.toml"
MAX_QUERIES = 12            # at most 12 queries per source per search
PROBES_PER_SEARCH = 10      # watched companies looked up on every application system in one search
REPROBE_DAYS = 7            # a company not found on any system is looked for again after a week
IMPORTED_FLAG = "find.boards_imported"
NO_ROLE = ""                # phrases read without a role when the goal names none

# Query phrases by kind of role; "{role}" is each of the goal's roles.
PHRASES: dict[str, tuple[str, ...]] = {
    "graduate_job": ("graduate {role}", "junior {role}", "entry level {role}"),
    "graduate_scheme": ("graduate scheme {role}", "graduate programme {role}"),
    "internship": ("{role} internship", "{role} intern", "summer internship {role}", "graduate internship"),
    "placement": ("{role} placement year", "industrial placement {role}", "year in industry {role}"),
    "insight": ("spring week", "insight programme {role}", "insight week"),
    "apprenticeship": ("{role} apprenticeship", "degree apprenticeship {role}"),
    "experienced": ("{role}",),
    "part_time": ("{role} part time",),
    "fixed_term": ("{role} fixed term",),
}
# The kinds whose searches are only for students and people starting out; internship sources follow these.
EARLY_KINDS = frozenset({"internship", "placement", "insight", "apprenticeship"})

LABELS = {"ats_board": "Company", "aggregator": "Job search", "board": "Job board", "careers_page": "Company",
          "company_watch": "Company", "rss": "Company", "email_alerts": "Email", "ai_page": "Company"}
ATS_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "lever_eu": "Lever", "ashby": "Ashby",
             "workable": "Workable", "smartrecruiters": "SmartRecruiters", "recruitee": "Recruitee",
             "personio": "Personio", "workday": "Workday", "teamtailor": "Teamtailor", "bamboohr": "BambooHR",
             "pinpoint": "Pinpoint"}

Fetched = tuple[list[dict], int | None]


class Skipped(Exception):
    """A source Hal leaves out of this search on purpose; str() says why in plain words."""


@dataclass(frozen=True)
class SourceKind:
    """One kind of source: its label on Where Hal looks and how its jobs are fetched."""

    kind: str
    label: str
    fetch: Callable[[dict, Any], Fetched]
    needs_secret: str | None = None


# ---------------------------------------------------------------------------------------------- fetchers


def _builtin_fetchers() -> dict[str, Callable]:
    from jobhunt.sources import academic, aggregators, feeds, hn, ktp, startups

    return {"hn": hn.fetch, "ktp": ktp.fetch, "yc": startups.yc, "80k": startups.eighty_thousand_hours,
            "workable-search": startups.workable_search, "jobsacuk": academic.fetch,
            "adzuna": aggregators.adzuna, "reed": aggregators.reed, "guardian": feeds.guardian}


# Filled at import, so a test or caller that replaces entries always replaces the real ones.
BUILTIN_FETCHERS: dict[str, Callable] = _builtin_fetchers()


def builtin_fetchers() -> dict[str, Callable]:
    """The built-in fetchers by seed `fetch` id (one shared, replaceable table)."""
    return BUILTIN_FETCHERS


def _call(fn: Callable, source: dict, goal: Any) -> Fetched:
    """Calls a fetcher with the source and goal when it takes them; older fetchers take nothing."""
    try:
        takes = len(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        takes = 2
    found = fn(source, goal) if takes >= 2 else fn()
    if isinstance(found, tuple):
        return list(found[0]), found[1]
    return list(found), None


def _builtin(source: dict, goal: Any) -> Fetched:
    fetch_id = source["config"].get("fetch") or source.get("builtin") or source["id"]
    fn = builtin_fetchers().get(fetch_id)
    if fn is None:
        raise Skipped("This version of Hal cannot search this site.")
    jobs, size = _call(fn, source, goal)
    for j in jobs:
        j.setdefault("board_size", size)
    return jobs, size


def _ats(source: dict, goal: Any) -> Fetched:
    from jobhunt.sources import ats

    cfg = source["config"]
    fetcher = ats.FETCHERS.get(cfg.get("ats", ""))
    if fetcher is None:
        raise Skipped(f"Hal cannot read {ATS_NAMES.get(cfg.get('ats'), 'this application system')} "
                      f"boards by itself yet.")
    jobs, size = fetcher(cfg["slug"], source["name"])
    for j in jobs:
        j["board_size"] = size
    return jobs, size


def _company(source: dict, goal: Any) -> Fetched:
    cfg = source["config"]
    if cfg.get("ats") and cfg.get("slug"):
        return _ats(source, goal)
    raise Skipped("Hal has not found this company's job board yet.")


def _feed(source: dict, goal: Any) -> Fetched:
    from jobhunt.sources import feeds

    return feeds.read_feed(source), None


def _page(source: dict, goal: Any) -> Fetched:
    from jobhunt.sources import feeds

    return feeds.read_careers_page(source), None


def _alerts(source: dict, goal: Any) -> Fetched:
    raise Skipped("Read when Hal checks your email.")


def _ai_page(source: dict, goal: Any) -> Fetched:
    raise Skipped("Hal cannot read this page by itself yet. Add one advert instead.")


KINDS: dict[str, SourceKind] = {k.kind: k for k in (
    SourceKind("ats_board", LABELS["ats_board"], _ats),
    SourceKind("aggregator", LABELS["aggregator"], _builtin),
    SourceKind("board", LABELS["board"], _builtin),
    SourceKind("careers_page", LABELS["careers_page"], _page),
    SourceKind("company_watch", LABELS["company_watch"], _company),
    SourceKind("rss", LABELS["rss"], _feed),
    SourceKind("email_alerts", LABELS["email_alerts"], _alerts, needs_secret="email.app_password"),
    SourceKind("ai_page", LABELS["ai_page"], _ai_page),
)}


# ---------------------------------------------------------------------------------------------- the seed


def seed() -> list[dict]:
    """The built-in sources of `find/seed/sources.toml`, in their order."""
    return tomllib.loads(SEED.read_text(encoding="utf-8")).get("source", [])


def seed_by_id() -> dict[str, dict]:
    """The built-in sources by id."""
    return {s["id"]: s for s in seed()}


def on_by_default(entry: dict, preset: str) -> bool:
    """True when a built-in starts switched on for people with this scoring preset."""
    on_for = entry.get("on_for", "all")
    return on_for == "all" or preset in on_for


def _preset() -> str:
    from jobhunt.core import settings

    return settings.get("scoring.preset") or "technical_grad"


def _row(r: sqlite3.Row | dict) -> dict:
    out = dict(r)
    try:
        out["config"] = json.loads(out.get("config") or "{}")
    except ValueError:
        out["config"] = {}
    return out


def sync(con: sqlite3.Connection) -> None:
    """Adds missing built-ins, refreshes their words, applies the preset's on/off where the person has not
    chosen, and imports the old board list once."""
    preset = _preset()
    now = clock.iso()
    for entry in seed():
        on = int(on_by_default(entry, preset))
        cfg = {**entry.get("config", {}), "fetch": entry.get("fetch")} if entry.get("fetch") else \
            dict(entry.get("config", {}))
        row = con.execute("SELECT config FROM sources WHERE id = ?", (entry["id"],)).fetchone()
        if row is None:
            con.execute("""INSERT INTO sources (id, kind, name, area, config, enabled, builtin, needs_secret,
                                                added_by, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'builtin', ?)""",
                        (entry["id"], entry["kind"], entry["name"], entry.get("area"), json.dumps(cfg), on,
                         entry["id"], entry.get("needs_secret"), now))
            continue
        old = _row({"config": row["config"]})["config"]
        merged = {**old, **cfg}
        con.execute("UPDATE sources SET kind = ?, name = ?, area = ?, config = ?, needs_secret = ? WHERE id = ?",
                    (entry["kind"], entry["name"], entry.get("area"), json.dumps(merged),
                     entry.get("needs_secret"), entry["id"]))
        if not old.get("chosen"):
            con.execute("UPDATE sources SET enabled = ? WHERE id = ?", (on, entry["id"]))
    if con.execute("SELECT 1 FROM meta WHERE k = ?", (IMPORTED_FLAG,)).fetchone() is None:
        import_boards(con)
    con.commit()


# ---------------------------------------------------------------------------------------------- the old lists


def ats_id(ats: str, slug: str) -> str:
    """The id of a company board source."""
    return f"ats:{ats}:{slug.lower()}"


def page_id(kind: str, url: str) -> str:
    """The id of a careers page or feed source: stable for one address."""
    prefix = {"rss": "rss", "careers_page": "page", "ai_page": "page"}[kind]
    return f"{prefix}:" + hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:12]


def add_board(con: sqlite3.Connection, name: str, ats: str, slug: str, *, added_by: str) -> str:
    """Adds a company board (or keeps the one already there) and returns its id."""
    sid = ats_id(ats, slug)
    con.execute("""INSERT OR IGNORE INTO sources (id, kind, name, area, config, enabled, added_by, created)
                   VALUES (?, 'ats_board', ?, ?, ?, 1, ?, ?)""",
                (sid, name, f"{ATS_NAMES.get(ats, ats)} careers page", json.dumps({"ats": ats, "slug": slug}),
                 added_by, clock.iso()))
    return sid


def _companies_file() -> Path | None:
    from jobhunt import config
    from jobhunt.core import paths

    imported = paths.data_dir() / "import" / "companies.txt"
    if imported.exists():
        return imported
    beside = Path(config.boards_path()).with_name("companies.txt")
    return beside if beside.exists() else None


def import_boards(con: sqlite3.Connection) -> dict:
    """Turns the old `boards.toml` into company boards and `companies.txt` into watched companies, once.

    Names already on a board are not watched twice. The flag in `meta` stops a board the person removed from
    coming back at the next search; the caller commits.
    """
    from jobhunt import config
    from jobhunt.find.goal import company_key

    boards = 0
    for b in config.boards():
        if b.get("ats") and b.get("slug") and b.get("name"):
            before = con.total_changes
            add_board(con, b["name"], b["ats"], b["slug"], added_by="import")
            boards += con.total_changes > before
    watched = 0
    path = _companies_file()
    if path is not None:
        on_boards = {company_key(r["name"]) for r in con.execute("SELECT name FROM sources WHERE kind = 'ats_board'")}
        for line in path.read_text(encoding="utf-8").splitlines():
            name = line.split("#")[0].strip().split("=")[0].strip()
            if not name or company_key(name) in on_boards:
                continue
            sid = "company:" + company_key(name)
            cur = con.execute("""INSERT OR IGNORE INTO sources (id, kind, name, area, config, enabled, added_by,
                                 created) VALUES (?, 'company_watch', ?, ?, '{}', 1, 'import', ?)""",
                              (sid, name, "A company you watch", clock.iso()))
            watched += cur.rowcount
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (IMPORTED_FLAG, clock.iso()))
    return {"boards": boards, "watched": watched}


# ---------------------------------------------------------------------------------------------- queries


def queries(source: dict, goal: Any, *, extra: tuple[str, ...] = ()) -> list[str]:
    """The search phrases for one source under the goal, at most `MAX_QUERIES`.

    A source's own `queries` in its config win (the owner's imported searches). Otherwise each chosen kind
    adds its phrases for each of the goal's roles, taken in turn so every kind is asked within the limit.
    """
    own = list(source.get("config", {}).get("queries") or [])
    if own:
        return own[:MAX_QUERIES]
    roles = list(goal.roles) or [NO_ROLE]
    chosen = [k for k in PHRASES if k in goal.kinds] or ["graduate_job"]
    out: list[str] = []
    depth = max(len(PHRASES[k]) for k in chosen)
    for level in range(depth):
        for role in roles:
            for kind in chosen:
                phrases = PHRASES[kind]
                if level < len(phrases):
                    out.append(" ".join(phrases[level].format(role=role).split()))
    seen: dict[str, str] = {}
    for q in out:
        if q and q.lower() not in seen:
            seen[q.lower()] = q
    fixed = [e for e in dict.fromkeys(extra) if e.lower() not in seen]
    return [*list(seen.values())[:MAX_QUERIES - len(fixed)], *fixed][:MAX_QUERIES]


def early_terms(goal: Any, terms: dict[str, str]) -> tuple[str, ...]:
    """The fixed internship terms of a site (`{kind: term}`) for the kinds the person chose only."""
    return tuple(term for kind, term in terms.items() if kind in goal.kinds)


# ---------------------------------------------------------------------------------------------- running


def missing_key(source: dict) -> str | None:
    """Plain words for a source left out because Hal has no key for it, else None."""
    name = source.get("needs_secret")
    if not name:
        return None
    from jobhunt.core.settings import secrets

    try:
        if secrets.status(name) == "saved":
            return None
    except Exception as e:  # a broken store reads as "no key"; the reason is logged
        log.warning("could not read the %s key: %s", name, e)
    if source["kind"] == "email_alerts":
        return "Set up email, and Hal reads the job alerts in your inbox."
    return f"{source['name']} has no key, so Hal skips it."


def enabled(con: sqlite3.Connection) -> list[dict]:
    """The sources switched on, in a steady order: job searches and boards first, then companies."""
    sync(con)
    order = "CASE kind WHEN 'aggregator' THEN 0 WHEN 'board' THEN 1 ELSE 2 END, created, id"
    return [_row(r) for r in con.execute(f"SELECT * FROM sources WHERE enabled = 1 ORDER BY {order}")]


def fetch(source: dict, goal: Any) -> Fetched:
    """The source's jobs in upsert shape and its board size; Skipped when it is left out on purpose."""
    if (words := missing_key(source)) is not None:
        raise Skipped(words)
    kind = KINDS.get(source["kind"])
    if kind is None:
        raise Skipped("This version of Hal does not know this kind of site.")
    jobs, size = kind.fetch(source, goal)
    for j in jobs:
        j["source_id"] = source["id"]
    return jobs, size


def record(con: sqlite3.Connection, source: dict, started: str, *, seen: int = 0, new: int = 0, kept: int = 0,
           error: str | None = None, skipped: str | None = None) -> dict:
    """Writes one `source_runs` row and the source's health; the caller commits."""
    ended = clock.iso()
    ok = None if skipped else int(error is None)
    cur = con.execute("""INSERT INTO source_runs (source_id, started, ended, ok, seen, new, kept, error)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                      (source["id"], started, ended, ok, seen, new, kept, error or skipped))
    if error:
        con.execute("UPDATE sources SET last_run = ?, last_error = ? WHERE id = ?", (ended, error, source["id"]))
    elif skipped is None:
        con.execute("""UPDATE sources SET last_run = ?, last_ok = ?, last_error = NULL, seen = ?, kept = ?
                       WHERE id = ?""", (ended, ended, seen, kept, source["id"]))
    return {"id": cur.lastrowid, "source_id": source["id"], "started": started, "ended": ended, "ok": ok,
            "seen": seen, "new": new, "kept": kept, "error": error or skipped}


def note_error(source: dict, error: str, con: sqlite3.Connection) -> None:
    """An Activity row for a source that failed, so the person sees it without reading the search's output."""
    from jobhunt.core import activity

    activity.record("find", "find.source_error", f"Hal could not search {source['name']}", "A scheduled or "
                    "requested search", detail=error[:500], tone="warn", con=con)


def keep(con: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int, list[tuple[int, dict]]]:
    """Stores fetched jobs and scores the new ones: (seen, kept, [(score, job)] for new jobs worth showing)."""
    from jobhunt import db
    from jobhunt.find import rules

    shown: list[tuple[int, dict]] = []
    for j in jobs:
        j.setdefault("board_size", None)
        if db.upsert(con, j):
            if j.get("source_id"):
                con.execute("UPDATE jobs SET source_id = ? WHERE id = ?", (j["source_id"], j["id"]))
            s = rules.score_and_save(con, j)
            if s > 0:
                shown.append((s, j))
    return len(jobs), len(shown), shown


def run(con: sqlite3.Connection, source: dict, goal: Any) -> dict:
    """Searches one source now and stores what it found; returns its `source_runs` row."""
    started = clock.iso()
    try:
        jobs, _size = fetch(source, goal)
    except Skipped as s:
        row = record(con, source, started, skipped=str(s))
    except Exception as e:  # one failing site is recorded and never stops the others
        words = f"{type(e).__name__} {e}"
        row = record(con, source, started, error=words)
        note_error(source, words, con)
    else:
        seen, kept, _ = keep(con, jobs)
        row = record(con, source, started, seen=seen, new=kept, kept=kept)
    con.commit()
    return row


# ---------------------------------------------------------------------------------------------- the page


def get(con: sqlite3.Connection, source_id: str) -> dict:
    """One source; LookupError when there is none."""
    row = con.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None and source_id in seed_by_id():
        sync(con)
        row = con.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise LookupError("That site is not in Where Hal looks.")
    return _row(row)


def switch(con: sqlite3.Connection, source_id: str, on: bool) -> dict:
    """Switches a source on or off and remembers that the person chose it."""
    src = get(con, source_id)
    cfg = {**src["config"], "chosen": True}
    con.execute("UPDATE sources SET enabled = ?, config = ? WHERE id = ?", (int(on), json.dumps(cfg), source_id))
    con.commit()
    return get(con, source_id)


def configure(con: sqlite3.Connection, source_id: str, config: dict) -> dict:
    """Changes the person-settable parts of a source's config (its own search phrases)."""
    src = get(con, source_id)
    allowed = {k: v for k, v in config.items() if k == "queries"}
    if set(config) - set(allowed):
        raise ValueError("Only a site's search words can be changed here.")
    queries_in = allowed.get("queries", [])
    if not isinstance(queries_in, list) or not all(isinstance(q, str) for q in queries_in):
        raise ValueError("Search words must be a list of phrases.")
    cfg = {**src["config"], "queries": [q.strip() for q in queries_in if q.strip()][:MAX_QUERIES]}
    con.execute("UPDATE sources SET config = ? WHERE id = ?", (json.dumps(cfg), source_id))
    con.commit()
    return get(con, source_id)


def remove(con: sqlite3.Connection, source_id: str) -> None:
    """Removes a site the person or the import added; built-ins can only be switched off."""
    src = get(con, source_id)
    if src["added_by"] == "builtin":
        raise PermissionError("Hal's own sites cannot be removed. Switch it off instead.")
    con.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    con.commit()


def fix_for(source: dict) -> dict | None:
    """The one button that would make a source work: a key, email set-up or a page to check."""
    if source.get("needs_secret") and missing_key(source):
        if source["kind"] == "email_alerts":
            return {"label": "Set up email", "route": "settings-email"}
        entry = seed_by_id().get(source.get("builtin") or "", {})
        return {"label": "Get a free key", "route": "settings-sites", "secret": source["needs_secret"],
                "steps": entry.get("key_steps", []), "url": entry.get("key_url")}
    if source.get("last_error"):
        return {"label": "Search it again", "action": "find.discover"}
    return None


def listing(con: sqlite3.Connection) -> list[dict]:
    """Every source as Where Hal looks shows it."""
    sync(con)
    order = "enabled DESC, CASE kind WHEN 'aggregator' THEN 0 WHEN 'board' THEN 1 WHEN 'email_alerts' THEN 2 " \
            "ELSE 3 END, created, name"
    out = []
    for r in con.execute(f"SELECT * FROM sources ORDER BY {order}"):
        src = _row(r)
        missed = missing_key(src)
        out.append({"id": src["id"], "name": src["name"], "kind": src["kind"], "kind_label": LABELS.get(src["kind"],
                    "Job board"), "area": src["area"] or "", "on": bool(src["enabled"]), "last": src["last_run"],
                    "seen": src["seen"], "kept": src["kept"], "ok": not missed and not src["last_error"],
                    "error": src["last_error"], "missed": missed, "added_by": src["added_by"],
                    "removable": src["added_by"] != "builtin", "fix": fix_for(src)})
    return out


def missed(con: sqlite3.Connection) -> list[str]:
    """What Hal is missing because a key is not saved, one sentence per switched-on site."""
    return [m for src in enabled(con) if src["kind"] != "email_alerts" and (m := missing_key(src))]
