"""University, research, library and teaching posts from jobs.ac.uk.

Research assistant, library, archive and KTP posts suit graduates of every subject and are rarely advertised
elsewhere. The searches are the goal's phrases; a student, or someone who chose internships or
placements, also gets the source's `student_terms`. The search's location filter is loose, so the scorer
does the location work.
"""
import html
import re
import urllib.parse

from ..http import get
from . import places
from .ats import strip_html

BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}
# Used only when a caller passes no source and goal (older callers); searches ask the goal.
SEARCHES = ["graduate", "research assistant"]
STUDENT_KINDS = ("internship", "placement")
BASE = "https://www.jobs.ac.uk"


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _searches(source: dict | None, goal) -> list[str]:
    if source is None or goal is None:
        return list(SEARCHES)
    from ..find import sources_registry

    student = goal.is_student() or any(k in goal.kinds for k in STUDENT_KINDS)
    extra = tuple(source.get("config", {}).get("student_terms", [])) if student else ()
    return sources_registry.queries(source, goal, extra=extra)


def fetch(source: dict | None = None, goal=None):
    jobs = {}
    for keywords in _searches(source, goal):
        for place in places():
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
