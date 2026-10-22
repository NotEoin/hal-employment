"""Adzuna and Reed: UK job aggregators with free developer APIs.

Each needs a key in ~/.config/jobhunt/ and returns nothing without one, so
discovery runs the same with or without them. Both searches return snippets;
full adverts are fetched for jobs that pass the first checks.
"""
import base64
import json
import re
import urllib.parse
from pathlib import Path

from ..http import get
from . import places
from .ats import strip_html

KEYS = Path.home() / ".config" / "jobhunt"
QUERIES = ["graduate software", "junior developer", "software engineer graduate",
           "machine learning graduate", "junior data engineer", "graduate cyber security"]


def _key(name: str) -> str | None:
    path = KEYS / name
    return path.read_text().strip() if path.exists() else None


def adzuna():
    creds = _key("adzuna")  # "<app_id>:<app_key>"
    if not creds:
        return []
    app_id, app_key = creds.split(":", 1)
    jobs = {}
    for query in QUERIES:
        for place in places():
            params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50, "what": query,
                      "where": place, "distance": 10, "max_days_old": 21}
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


def reed():
    key = _key("reed")
    if not key:
        return []
    jobs = {}
    for query in QUERIES:
        for place in places():
            params = {"keywords": query, "locationName": place, "distanceFromLocation": 10, "resultsToTake": 100}
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
