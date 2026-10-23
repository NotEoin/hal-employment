"""Work on one job that the person starts from the Jobs pages: check it again, check its requirements, add one
advert, and "This does not matter to me".

Each slow piece is a persisted action, so it shows in the status line with its steps and can be stopped.

Requirements change a job's score on its own page and card, never the stored `jobs.score`: the points are
worked out when the job is read (`requirement_points`), so a re-check of every job keeps them, the command
line keeps today's numbers, and "This does not matter to me" changes only that one job.

Adding one advert reads JSON-LD `JobPosting` first, then the page's title and text; reading a page with the
AI is not built yet, so a page without either is refused with plain words.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
from urllib.parse import urlparse

from jobhunt.core import clock, events, runner
from jobhunt.find import rules

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 20        # one advert page; a slower site is reported, not waited on
ADVERT_CHARS = 20000        # enough for any advert; the rest of a page is menus and footers
# Points a requirement costs when Hal checked it; a desirable one costs half (rounded down).
REQUIREMENT_POINTS = {"miss": -4, "part": -2}
STATES = ("met", "part", "miss", "off")
JSON_LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def requirement_points(state: str, essential: int | None) -> int:
    """What one checked requirement adds to its job's score (0 or less)."""
    points = REQUIREMENT_POINTS.get(state, 0)
    return points if essential != 0 else int(points / 2)


def requirements(con: sqlite3.Connection, job_id: str) -> list[dict]:
    """The job's checked requirements in order, each with the points it costs."""
    rows = con.execute("SELECT * FROM job_requirements WHERE job_id = ? ORDER BY n", (job_id,)).fetchall()
    return [{"n": r["n"], "s": r["state"], "text": r["text"], "quote": r["quote"] or "", "mine": r["mine"] or "",
             "tip": r["tip"] or "", "essential": r["essential"],
             "points": requirement_points(r["state"], r["essential"])} for r in rows]


def adjusted(score: float | None, reqs: list[dict]) -> int:
    """A shown job's score with its requirement points, kept between 1 and 100; a hidden job stays 0."""
    if not score:
        return 0
    low, high = rules.SCORE_RANGE
    return max(low, min(high, round(score) + sum(r["points"] for r in reqs)))


def set_requirement_off(con: sqlite3.Connection, job_id: str, n: int, off: bool) -> dict:
    """Marks one requirement as not mattering (or mattering again); returns its state and the job's score."""
    row = con.execute("SELECT state, mine, quote FROM job_requirements WHERE job_id = ? AND n = ?",
                      (job_id, n)).fetchone()
    if row is None:
        raise LookupError("Hal has not checked that requirement.")
    if off:
        state = "off"
    else:
        # the state before "off" is not stored; what the person has decides it again
        state = "met" if row["state"] == "met" else "miss" if not row["mine"] else "part"
    con.execute("UPDATE job_requirements SET state = ?, updated = ? WHERE job_id = ? AND n = ?",
                (state, clock.iso(), job_id, n))
    con.commit()
    score = con.execute("SELECT score FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    events.publish("job", id=job_id)
    return {"state": state, "score": adjusted(score, requirements(con, job_id))}


def _job(con: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("That job is not in Hal's list.")
    return row


def _first_job(world) -> str:
    return world.con.execute("SELECT id FROM jobs WHERE status = 'new' ORDER BY id LIMIT 1").fetchone()["id"]


def _check_example(world) -> dict:
    return {"job_id": _first_job(world), "open_check": False}


@runner.action("find.check_job", area="find", label="Checking {title} at {company} again",
               why="You asked Hal to check this job again", priority=0, example=_check_example)
def check_job(ctx, job_id: str, open_check: bool = True) -> dict:
    """Checks the advert is still open, then scores the job again under the live goal."""
    from jobhunt.find import closed_check

    job = dict(_job(ctx.con, job_id))
    state = job.get("open_state")
    if open_check:
        ctx.step("Opening the advert to see that it is still open")
        state = closed_check.check(job)
        ctx.con.execute("UPDATE jobs SET open_state = ?, open_checked_at = ? WHERE id = ?",
                        (state, clock.iso(), job_id))
        job["open_state"] = state
    ctx.checkpoint()
    ctx.step("Scoring it against your goal")
    score = rules.score_and_save(ctx.con, job)
    ctx.con.commit()
    ctx.changed(f"{job['title']} at {job['company']} now scores {score}"
                + (". The advert has closed." if state == "closed" else "."))
    events.publish("job", id=job_id)
    return {"score": score, "open_state": state}


def _requirements_example(world) -> dict:
    return {"job_id": _first_job(world)}


@runner.action("find.requirements", area="find", label="Checking what {title} asks for",
               why="You opened this job", priority=0, example=_requirements_example)
def check_requirements(ctx, job_id: str) -> dict:
    """Lists the advert's requirements and what the person has for each."""
    from jobhunt import cv, cvscore

    job = _job(ctx.con, job_id)
    ctx.step("Reading the advert and your CV")
    found, why_not = cvscore.requirements(job, cv.facts_text())
    ctx.checkpoint()
    if not found:
        ctx.step(f"Hal could not check the requirements now: {why_not}")
        return {"n": 0, "why": why_not}
    ctx.step(f"Comparing {len(found)} requirements with your CV and your record", total=2)
    now = clock.iso()
    ctx.con.execute("DELETE FROM job_requirements WHERE job_id = ? AND state != 'off'", (job_id,))
    kept_off = {r[0] for r in ctx.con.execute("SELECT text FROM job_requirements WHERE job_id = ?", (job_id,))}
    ctx.con.execute("DELETE FROM job_requirements WHERE job_id = ?", (job_id,))
    for n, r in enumerate(found, 1):
        state = "off" if r["need"] in kept_off else {"direct": "met", "adjacent": "part"}.get(r["match"], "miss")
        where = {"cv": "Your CV", "record": "Your record"}.get(r["where"], "")
        mine = f"{where}: {r['evidence']}" if r["evidence"] else ""
        tip = ("" if state == "met" else "The advert lists this as desirable, not essential." if r["kind"] == "nice"
               else "Add an example to your CV, or tell Hal this does not matter.")
        ctx.con.execute("""INSERT INTO job_requirements (job_id, n, text, essential, state, quote, mine, tip, updated)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (job_id, n, r["need"], 0 if r["kind"] == "nice" else 1, state, r["advert"], mine, tip, now))
    ctx.con.commit()
    met = sum(1 for r in found if r["match"] == "direct")
    ctx.changed(f"{met} of {len(found)} requirements met")
    events.publish("job", id=job_id)
    return {"n": len(found), "met": met}


def _job_posting(page: str) -> dict | None:
    for block in JSON_LD.findall(page):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("JobPosting", ["JobPosting"]):
                return item
    return None


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAGS.sub(" ", fragment or ""))).strip()


def read_advert(url: str, page: str) -> dict:
    """A job in the shape `db.upsert` takes, read from an advert page; ValueError when it holds no advert."""
    host = urlparse(url).hostname or "the site"
    posting = _job_posting(page)
    if posting:
        org = posting.get("hiringOrganization") or {}
        place = posting.get("jobLocation") or {}
        place = place[0] if isinstance(place, list) and place else place
        address = place.get("address", {}) if isinstance(place, dict) else {}
        location = address.get("addressLocality", "") if isinstance(address, dict) else ""
        salary = posting.get("baseSalary") or {}
        value = salary.get("value", {}) if isinstance(salary, dict) else {}
        pay = ""
        if isinstance(value, dict) and (value.get("minValue") or value.get("value")):
            pay = f"GBP {value.get('minValue') or value.get('value')}"
            if value.get("maxValue"):
                pay += f" - {value['maxValue']}"
            pay += f" per {str(value.get('unitText') or 'year').lower()}"
        return {"title": _text(posting.get("title", "")), "company": _text(org.get("name", "") if isinstance(org, dict)
                                                                            else str(org)) or host,
                "location": location, "description": _text(posting.get("description", ""))[:ADVERT_CHARS],
                "salary": pay or None, "posted_at": posting.get("datePosted"),
                "closes_at": (posting.get("validThrough") or "")[:10] or None}
    title = TITLE.search(page)
    body = _text(page)
    if not title or len(body) < 200:
        raise ValueError("Hal could not find an advert on that page. Paste the address of the advert itself.")
    return {"title": _text(title.group(1)).split(" | ")[0].split(" - ")[0], "company": host, "location": "",
            "description": body[:ADVERT_CHARS], "salary": None, "posted_at": None, "closes_at": None}


def advert_id(url: str) -> str:
    """The id an added advert gets: stable for one address, so pasting it twice adds it once."""
    return "advert:" + hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:12]


def _fetch(url: str) -> str:
    from jobhunt import http

    return http.get(url, timeout=FETCH_TIMEOUT_S, retries=0)


def _add_example(world) -> dict:
    row = world.con.execute("SELECT url FROM jobs WHERE url IS NOT NULL ORDER BY id LIMIT 1").fetchone()
    return {"url": row["url"]}


@runner.action("find.add_advert", area="find", label="Reading the advert you pasted",
               why="You pasted an advert address", priority=0, example=_add_example)
def add_advert(ctx, url: str) -> dict:
    """Reads one advert, scores it and adds it to Matches; an address Hal already has is not read again."""
    from jobhunt import db

    url = (url or "").strip()
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Paste the advert's address, starting with https://")
    known = ctx.con.execute("SELECT id, title FROM jobs WHERE url = ? OR id = ?", (url, advert_id(url))).fetchone()
    if known:
        ctx.step(f"Hal already has this job: {known['title']}")
        return {"job_id": known["id"], "added": False}
    ctx.step("Reading the advert", total=2)
    job = read_advert(url, _fetch(url))
    ctx.checkpoint()
    job.update({"id": advert_id(url), "source": "advert", "url": url, "board_size": None})
    db.upsert(ctx.con, job)
    if job.get("closes_at"):
        ctx.con.execute("UPDATE jobs SET closes_at = ? WHERE id = ?", (job["closes_at"], job["id"]))
    ctx.step(f"Scoring {job['title']} against your goal", total=2)
    score = rules.score_and_save(ctx.con, dict(_job(ctx.con, job["id"])))
    ctx.con.commit()
    ctx.changed(f"Added {job['title']} at {job['company']} (scores {score})")
    events.publish("job", id=job["id"])
    return {"job_id": job["id"], "added": True, "score": score}
