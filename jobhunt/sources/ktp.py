"""Knowledge Transfer Partnership associate posts (iuk-ktp.org.uk).

Two-to-three-year graduate posts embedding a university project in a small
company: salaried, often with a training budget, rarely advertised elsewhere.
The WordPress REST API lists them; the location, salary and closing date live
only on each job page, so those are read from its text.
"""
import html
import re

from ..http import get, get_json

API = "https://iuk-ktp.org.uk/wp-json/wp/v2/job?per_page=100&_fields=id,date,title,link"


def _page_text(url: str) -> str:
    h = get(url)
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", h, flags=re.S)
    return html.unescape(re.sub(r"\s*\n\s*", "\n", re.sub(r"<[^>]+>", "\n", h)))


def fetch():
    jobs = []
    for j in get_json(API):
        title = html.unescape(j["title"]["rendered"]).strip()
        text = _page_text(j["link"])
        # Location and salary are the two lines before the first "Apply now".
        start = text.find("\nApply now\n")
        above = text[:start].split("\n")[-2:] if start != -1 else ["", ""]
        location = above[0] if "\u00a3" not in above[0] else ""
        salary = next((l for l in above if "\u00a3" in l), None)
        closing = re.search(r"Application closing date\n([\d/]+)", text)
        body_at = text.find("Job description\n", start)
        end = text.find("Share this job", body_at + 20)
        desc = text[body_at:end if end > body_at else body_at + 6000].strip()
        if closing:
            desc = f"Closing date: {closing.group(1)}\n\n{desc}"
        jobs.append({
            "id": f"ktp:{j['id']}",
            "source": "ktp",
            "company": "KTP",           # the host company is named inside the description
            "title": title,
            "location": location,
            "url": j["link"],
            "description": desc,
            "salary": salary,
            "posted_at": j["date"],
        })
    return jobs
