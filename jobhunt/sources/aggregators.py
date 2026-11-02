"""Adzuna and Reed: UK job aggregators with free developer APIs.

Each needs a free key, saved as the secrets `sites.adzuna_key` and `sites.reed_key`, and returns nothing
without one, so discovery runs the same with or without them (the sources registry says what is missed).
Searches are the goal's phrases for the kinds of role chosen in the goal's places, as far as the
goal's travel distance; both return snippets, so full adverts are fetched for jobs that pass the first checks.
"""
import base64
import json
import re
import urllib.parse

from ..http import get
from . import places
from .ats import strip_html

SECRETS = {"adzuna": "sites.adzuna_key", "reed": "sites.reed_key"}
# Used only when a caller passes no source and goal (older callers and tests); searches ask the goal.
QUERIES = ["graduate"]
KM_PER_MILE = 1.609
DEFAULT_MILES = 10          # the old fixed radius, for a call with no goal
DEFAULT_AGE_DAYS = 21


def _key(name: str) -> str | None:
    from ..core.settings import secrets

    return (secrets.get(SECRETS[name]) or "").strip() or None


def _plan(source: dict | None, goal) -> tuple[list[str], int, int]:
    """(queries, travel miles, oldest advert in days) for one search."""
    if source is None or goal is None:
        return list(QUERIES), DEFAULT_MILES, DEFAULT_AGE_DAYS
    from ..core import settings
    from ..find import sources_registry

    return (sources_registry.queries(source, goal), goal.max_distance_miles or DEFAULT_MILES,
            int(settings.get("sites.max_age_days") or DEFAULT_AGE_DAYS))


def adzuna(source: dict | None = None, goal=None):
    """Returns jobs from Adzuna; empty without a saved key."""
    creds = _key("adzuna")  # "<app_id>:<app_key>"
    if not creds:
        return []
    app_id, app_key = creds.split(":", 1)
    queries, miles, age = _plan(source, goal)
    jobs = {}
    for query in queries:
        for place in places():
            params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50, "what": query,
                      "where": place, "distance": round(miles * KM_PER_MILE), "max_days_old": age}
            data = json.loads(get("https://api.adzuna.com/v1/api/jobs/gb/search/1?" + urllib.parse.urlencode(params),
                                  timeout=30))
            for j in data.get("results", []):
                low, high = j.get("salary_min"), j.get("salary_max")
                jobs[j["id"]] = {
                    "id": f"adzuna:{j['id']}", "source": "adzuna",
                    "company": (j.get("company") or {}).get("display_name") or "",
                    "title": strip_html(j.get("title")), "location": (j.get("location") or {}).get("display_name") or "",
                    "url": j.get("redirect_url"), "description": strip_html(j.get("description")),
                    "salary": f"GBP {low:,.0f}-{high:,.0f}" if low and high else None,
                    "posted_at": j.get("created"), "board_size": None,
                }
    return list(jobs.values())


def _reed(path: str, key: str):
    auth = base64.b64encode(f"{key}:".encode()).decode()
    return json.loads(get("https://www.reed.co.uk/api/1.0/" + path, headers={"Authorization": f"Basic {auth}"},
                          timeout=30))


def reed(source: dict | None = None, goal=None):
    """Returns jobs from Reed; empty without a saved key."""
    key = _key("reed")
    if not key:
        return []
    queries, miles, _age = _plan(source, goal)
    jobs = {}
    for query in queries:
        for place in places():
            params = {"keywords": query, "locationName": place, "distanceFromLocation": miles, "resultsToTake": 100}
            for j in _reed("search?" + urllib.parse.urlencode(params), key).get("results", []):
                low, high = j.get("minimumSalary"), j.get("maximumSalary")
                jobs[j["jobId"]] = {
                    "id": f"reed:{j['jobId']}", "source": "reed", "company": j.get("employerName") or "",
                    "title": j.get("jobTitle") or "", "location": j.get("locationName") or "",
                    "url": j.get("jobUrl"), "description": strip_html(j.get("jobDescription")),
                    "salary": f"GBP {low:,.0f}-{high:,.0f}" if low and high else None,
                    "posted_at": j.get("date"), "board_size": None,
                }
    return list(jobs.values())


def reed_detail(job: dict) -> str:
    """Returns the full Reed advert; search results carry only a summary."""
    key = _key("reed")
    job_id = job["id"].split(":", 1)[1]
    return strip_html(_reed(f"jobs/{job_id}", key).get("jobDescription")) if key else job["description"]


def adzuna_detail(job: dict) -> str:
    """Returns the full advert from Adzuna's job page; the API returns a snippet."""
    page = get(job["url"], headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 "
                                                  "Firefox/130.0"}, timeout=30)
    text = strip_html(re.sub(r"<(script|style|nav|footer|header|svg|noscript)[^>]*>.*?</\1>", " ", page,
                             flags=re.S | re.I))
    start = text.find(job["title"])
    return text[start if start != -1 else 0:][:12000]
