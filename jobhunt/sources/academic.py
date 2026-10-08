"""University and research posts from jobs.ac.uk.

Research software engineer, research assistant and KTP posts suit a new
graduate with a research dissertation, and are rarely advertised elsewhere.
The search's location filter is loose, so the scorer does the location work.
"""
import html
import re
import urllib.parse

from ..http import get
from .ats import strip_html

BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}
SEARCHES = ["research software engineer", "software developer", "software engineer",
            "machine learning", "artificial intelligence", "data scientist", "KTP associate",
            "research assistant computer science", "cyber security", "internship"]
PLACES = ["London", "City"]
BASE = "https://www.jobs.ac.uk"


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def fetch():
    jobs = {}
    for keywords in SEARCHES:
        for place in PLACES:
            url = f"{BASE}/search/?" + urllib.parse.urlencode({"keywords": keywords, "location": place})
            page = get(url, headers=BROWSER, timeout=30)
            for block in page.split('class="j-search-result__result')[1:]:
                link = re.search(r'href="(/job/([A-Z0-9]+)/[^"]*)"\s*>\s*(.*?)\s*</a>', block, re.S)
                if not link:
                    continue
                field = lambda name: _text(m.group(1)) if (m := re.search(
                    rf'class="j-search-result__{name}">(.*?)</div>', block, re.S)) else ""
                location = re.search(r"Location:\s*(.*?)\s*</div>", block, re.S)
                salary = re.search(r"Salary:\s*</strong>(.*?)</div>", block, re.S)
                jobs[link.group(2)] = {
                    "id": f"jobsacuk:{link.group(2)}", "source": "jobsacuk",
                    "company": field("employer"), "title": _text(link.group(3)),
                    "location": _text(location.group(1)) if location else "",
                    "url": BASE + link.group(1), "description": "",
                    "salary": _text(salary.group(1)) if salary else None,
                    "posted_at": None, "board_size": None,
                }
    return list(jobs.values())


def detail(job: dict) -> str:
    page = get(job["url"], headers=BROWSER, timeout=30)
    body = re.search(r'id="job-description"[^>]*>(.*?)</section>', page, re.S) or \
        re.search(r'class="j-advert__details"[^>]*>(.*)', page, re.S)
    return strip_html(body.group(1))[:12000] if body else ""
