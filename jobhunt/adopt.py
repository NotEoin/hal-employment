"""Finds the job for an application the applicant opened in a tab of their own.

The tab is matched to a known job by its address, then by the employer and
title it names; failing both, a job is made from the page itself, so any
application form on the web can be filled the same way as one Hal found.
"""
import hashlib
import re
from urllib.parse import parse_qsl, urlparse

from . import db, gpu, llm, score
from .ai import prompts
from .browser import application_url

# Applicant tracking systems that name the employer in the address: (host pattern, where the name is).
ATS = [(re.compile(r"(^|\.)(job-boards|boards)\.(eu\.)?greenhouse\.io$"), "path"),
       (re.compile(r"(^|\.)jobs\.(eu\.)?lever\.co$"), "path"), (re.compile(r"(^|\.)jobs\.ashbyhq\.com$"), "path"),
       (re.compile(r"(^|\.)apply\.workable\.com$"), "path"), (re.compile(r"(^|\.)jobs\.smartrecruiters\.com$"), "path"),
       (re.compile(r"\.wd\d+\.myworkdayjobs\.com$"), "host"), (re.compile(r"\.recruitee\.com$"), "host"),
       (re.compile(r"\.jobs\.personio\.(de|com)$"), "host"), (re.compile(r"\.teamtailor\.com$"), "host"),
       (re.compile(r"\.bamboohr\.com$"), "host"), (re.compile(r"\.pinpointhq\.com$"), "host")]
GENERIC_HOST = {"www", "careers", "career", "jobs", "job", "apply", "join", "work", "boards", "recruiting", "hire"}
# Query parameters that pick out one job on a site that keeps every job at one address.
JOB_PARAM = re.compile(r"id$|^jid$|job|^token$|^for$|vacanc|posting|requisition", re.I)
OPEN = ("new", "shortlisted", "ready", "applying")

def address(url: str) -> str:
    """Reduces a page address to what identifies the job: host, path and any job-naming parameters."""
    u = urlparse(url or "")
    host = u.netloc.lower().removeprefix("www.")
    keep = sorted((k, v) for k, v in parse_qsl(u.query) if JOB_PARAM.search(k))
    return f"{host}{u.path.rstrip('/')}" + ("?" + "&".join(f"{k}={v}" for k, v in keep) if keep else "")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", (text or "").lower()) if len(w) > 2}


def match(con, page: dict):
    """Returns the known job a tab shows, or None."""
    here = address(page["url"])
    rows = con.execute("SELECT * FROM jobs WHERE status NOT IN ('skipped', 'closed') ORDER BY last_seen DESC").fetchall()
    for r in rows:
        for url in {r["url"], application_url(r)}:
            known = address(url)
            # The form is often a step under the advert: .../jobs/123 then .../jobs/123/apply.
            if known and (here == known or here.startswith(known + "/")):
                return r
    named = re.sub(r"[^a-z0-9]", "", f"{page.get('site', '')} {urlparse(page['url']).netloc} "
                                     f"{page.get('title', '')} {page.get('text', '')[:3000]}".lower())
    heading = _words(f"{page.get('title', '')} {page.get('heading', '')}")
    best, overlap = None, 0.0
    for r in rows:
        key = score.company_key(r["company"])
        wanted = _words(r["title"])
        if r["status"] not in OPEN or len(key) < 3 or key not in named or not wanted:
            continue
        share = len(wanted & heading) / len(wanted)
        if share >= 2 / 3 and share > overlap:
            best, overlap = r, share
    return best


def _from_address(url: str) -> str:
    """The employer as an applicant tracking system's address names it, or the site's own name."""
    u = urlparse(url)
    host = u.netloc.lower().removeprefix("www.")
    for pattern, where in ATS:
        if pattern.search(host):
            slug = host.split(".")[0] if where == "host" else (u.path.strip("/").split("/") or [""])[0]
            if slug and slug != "embed":
                return re.sub(r"[-_]+", " ", slug).title()
    labels = [x for x in host.split(".")[:-1] if x not in GENERIC_HOST and len(x) > 2]
    return labels[-1].title() if labels else host


def _guess(page: dict) -> tuple[str, str]:
    """Names the employer and the job from the page alone, for when the model cannot run."""
    company = (page.get("site") or "").strip() or _from_address(page["url"])
    heading = (page.get("heading") or "").strip()
    title = heading if 3 <= len(heading) <= 120 else re.split(r"\s+[-|–—]\s+|\s+at\s+", page.get("title") or "")[0]
    return company, title.strip() or "Application"


def describe(page: dict) -> tuple[str, str]:
    """Returns (company, title) for a page that matches no known job."""
    company, title = _guess(page)
    if gpu.busy():
        return company, title
    try:
        reply = llm.chat_json(prompts.text("adopt_name"),
                              f"Address: {page['url']}\nPage title: {page.get('title', '')}\n"
                              f"Heading: {page.get('heading', '')}\nSite name: {page.get('site', '')}\n\n"
                              f"{page.get('text', '')[:2000]}", role="main", timeout=60)
    except llm.LLMUnavailable:
        return company, title
    named = str(reply.get("company") or "").strip()
    called = str(reply.get("title") or "").strip()
    return (named if 1 < len(named) <= 80 else company), (called if 2 < len(called) <= 120 else title)


def adopt(con, page: dict) -> tuple[dict, bool]:
    """Returns the job a tab shows, making it when no known job matches, and whether it was made."""
    found = match(con, page)
    if found is not None:
        return found, False
    company, title = describe(page)
    job_id = "manual:" + hashlib.sha1(address(page["url"]).encode()).hexdigest()[:12]
    text = page.get("text") or ""
    db.upsert(con, {"id": job_id, "source": "manual", "company": company, "title": title, "location": "",
                    "url": page["url"], "description": text[:20000], "salary": "", "posted_at": None,
                    "board_size": None})
    db.set_status(con, job_id, "applying", "opened by you in your own browser")
    con.commit()
    return con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(), True
