"""Y Combinator's London job board and the 80,000 Hours job board.

YC listings name a hiring manager, which makes direct contact possible. 80,000
Hours lists AI safety, policy and other high-impact roles; its public search
key is the one its own site uses in the browser.
"""
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..http import get
from .ats import strip_html

BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}
YC_PAGES = ["https://www.ycombinator.com/jobs/location/london",
            "https://www.ycombinator.com/jobs/location/remote"]
ALGOLIA = "https://W6KM1UDIB3-dsn.algolia.net/1/indexes/jobs_prod/query"
ALGOLIA_HEADERS = {"X-Algolia-Application-Id": "W6KM1UDIB3",
                   "X-Algolia-API-Key": "d1d7f2c8696e7b36837d5ed337c4a319"}


def _page_props(url: str) -> dict:
    page = get(url, headers=BROWSER, timeout=30)
    match = re.search(r'data-page="([^"]+)"', page)
    return json.loads(html.unescape(match.group(1)))["props"] if match else {}


def yc():
    jobs = {}
    for url in YC_PAGES:
        for j in _page_props(url).get("jobPostings", []):
            manager = j.get("hiringManager") or {}
            if isinstance(manager, dict):
                manager = ", ".join(filter(None, [manager.get("name"), manager.get("title")]))
            desc = "\n".join(filter(None, [
                j.get("companyOneLiner"), f"Experience: {j.get('minExperience')}",
                f"Skills: {', '.join(j.get('skills') or [])}",
                f"Hiring manager: {manager}" if manager else "",
                f"YC batch: {j.get('companyBatchName')}"]))
            jobs[j["id"]] = {
                "id": f"yc:{j['id']}", "source": "yc", "company": j.get("companyName") or "YC startup",
                "title": j["title"], "location": j.get("location") or "",
                "url": "https://www.ycombinator.com" + j["url"], "description": desc,
                "salary": j.get("salaryRange") or None, "posted_at": j.get("createdAt"), "board_size": None,
            }
    return list(jobs.values())


def yc_detail(job: dict) -> str:
    """Returns the full advert text from a YC job page, kept after the summary."""
    props = _page_props(job["url"])
    posting = props.get("jobPosting") or props.get("job") or {}
    body = strip_html(posting.get("description") or "")
    return f"{job['description']}\n\n{body}".strip()


def eighty_thousand_hours():
    params = ("query=&hitsPerPage=1000&facetFilters="
              + urllib.request.quote(json.dumps([["tags_location_80k:London, UK", "tags_location_80k:UK"]])))
    req = urllib.request.Request(ALGOLIA, data=json.dumps({"params": params}).encode(),
                                 headers={**ALGOLIA_HEADERS, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        hits = json.loads(r.read())["hits"]
    jobs = []
    for h in hits:
        posted = h.get("posted_at")
        desc = "\n".join(filter(None, [
            strip_html(h.get("description_short")), strip_html(h.get("description")),
            "Experience: " + ", ".join(h.get("tags_exp_required") or []),
            "Area: " + ", ".join(h.get("tags_area") or []),
            strip_html(h.get("company_description"))]))
        jobs.append({
            "id": f"80k:{h['objectID']}", "source": "80k", "company": h.get("company_name") or "",
            "title": h.get("title") or "", "location": " / ".join(h.get("card_locations") or []),
            "url": h.get("url_external") or "https://jobs.80000hours.org/", "description": desc,
            "salary": h.get("salary") or None, "board_size": None,
            "posted_at": datetime.fromtimestamp(int(posted), timezone.utc).isoformat() if posted else None,
        })
    return jobs


WORKABLE_SEARCH = "https://jobs.workable.com/api/v1/jobs"
WORKABLE_QUERIES = ["software engineer", "software developer", "graduate developer", "junior developer",
                    "machine learning", "AI engineer", "data engineer", "python developer",
                    "backend engineer", "security engineer", "research engineer"]
WORKABLE_PLACES = ["London, UK", "City, UK", "Town, UK"]


def workable_search(max_pages: int = 3):
    """Searches every Workable employer's adverts, which skews towards small companies."""
    jobs = {}
    for query in WORKABLE_QUERIES:
        for place in WORKABLE_PLACES:
            token = None
            for _ in range(max_pages):
                params = {"query": query, "location": place, **({"pageToken": token} if token else {})}
                data = json.loads(get(WORKABLE_SEARCH + "?" + urllib.parse.urlencode(params),
                                      headers=BROWSER, timeout=30))
                for j in data.get("jobs", []):
                    company = (j.get("company") or {}).get("title") or ""
                    desc = "\n\n".join(strip_html(j.get(k)) for k in
                                       ("description", "requirementsSection", "benefitsSection"))
                    jobs[j["id"]] = {
                        "id": f"workable-search:{j['id']}", "source": "workable-search", "company": company,
                        "title": j.get("title") or "", "location": " / ".join(j.get("locations") or []),
                        "url": j.get("url"), "description": desc, "salary": None,
                        "posted_at": j.get("created"), "board_size": None,
                    }
                token = data.get("nextPageToken")
                if not token:
                    break
    return list(jobs.values())
