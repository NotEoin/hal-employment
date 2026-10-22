"""Hacker News 'Ask HN: Who is hiring?' - the latest monthly thread.

Posts are written by founders and engineers at small companies, usually with a
direct email, so a reply reaches a person rather than an ATS. Only top-level
comments mentioning the UK or one of your goal's places are kept; the scorer does the
finer location filtering.
"""
import html
import re

from ..http import get_json
from . import places

SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10"
UK_WORDS = ("uk", "united kingdom", "england", "scotland", "wales")


def uk_pattern() -> re.Pattern:
    """The UK, or any of the goal's places, as whole words."""
    words = [*UK_WORDS, *(p.lower() for p in places())]
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


def _text(h: str) -> str:
    h = re.sub(r"<p>", "\n\n", h or "")
    h = re.sub(r'<a href="([^"]+)"[^>]*>.*?</a>', r"\1", h)
    return html.unescape(re.sub(r"<[^>]+>", "", h)).strip()


def fetch():
    hits = get_json(SEARCH)["hits"]
    story = next(h for h in hits if h["title"].startswith("Ask HN: Who is hiring?"))
    thread = get_json(f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
    jobs = []
    uk = uk_pattern()
    for c in thread.get("children", []):
        text = _text(c.get("text"))
        if not text or not uk.search(text):
            continue
        header = text.split("\n", 1)[0]
        parts = [p.strip() for p in header.split("|")]
        company = parts[0][:80] or "HN poster"
        # Headers are loosely 'Company | Role | Location | ...', so keep the
        # whole line as the title; the scorer reads the full text anyway.
        title = header[:200]
        jobs.append({
            "id": f"hn:{c['id']}",
            "source": "hn",
            "company": company,
            "title": title,
            "location": header,
            "url": f"https://news.ycombinator.com/item?id={c['id']}",
            "description": text,
            "salary": None,
            "posted_at": c.get("created_at"),
        })
    return jobs
