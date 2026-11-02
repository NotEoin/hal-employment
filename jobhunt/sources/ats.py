"""Public job-board APIs of the common applicant-tracking systems.

These are the feeds the companies' own careers pages read from, so they are
complete, current, need no login, and are meant to be read by third parties.
Each fetcher returns (jobs, board_size); a 404 means the slug is not on that ATS.
"""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from ..http import get, get_json


def strip_html(s: str | None) -> str:
    """Returns the plain text of an HTML fragment."""
    if not s:
        return ""
    s = html.unescape(s)                       # Greenhouse double-encodes
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def _job(source, company, ext_id, title, location, url, description, salary=None, posted_at=None):
    return {
        "id": f"{source}:{company}:{ext_id}",
        "source": source,
        "company": company,
        "title": title.strip(),
        "location": (location or "").strip(),
        "url": url,
        "description": description,
        "salary": salary,
        "posted_at": posted_at,
    }


def greenhouse(slug: str, name: str):
    """Returns an employer's jobs from its Greenhouse board."""
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    jobs = [
        _job("greenhouse", name, j["id"], j["title"],
             (j.get("location") or {}).get("name"), j["absolute_url"],
             strip_html(j.get("content")),
             posted_at=j.get("first_published") or j.get("updated_at"))
        for j in data.get("jobs", [])
    ]
    return jobs, len(jobs)


def _lever(host: str, slug: str, name: str):
    data = get_json(f"https://{host}/v0/postings/{slug}?mode=json")
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location")]
        desc = "\n\n".join(
            [j.get("descriptionPlain") or ""]
            + [f"{l.get('text')}\n{strip_html(l.get('content'))}" for l in j.get("lists", [])]
            + [j.get("additionalPlain") or ""]
        )
        sal = j.get("salaryRange")
        salary = f"{sal.get('currency')} {sal.get('min')}-{sal.get('max')} {sal.get('interval')}" if sal else None
        posted = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).isoformat() if j.get("createdAt") else None
        jobs.append(_job("lever", name, j["id"], j["text"], " / ".join(filter(None, locs)),
                         j["hostedUrl"], desc.strip(), salary, posted))
    return jobs, len(jobs)


def lever(slug: str, name: str):
    """Returns an employer's jobs from its Lever board."""
    return _lever("api.lever.co", slug, name)


def lever_eu(slug: str, name: str):
    """Returns an employer's jobs from its Lever board in the EU region."""
    return _lever("api.eu.lever.co", slug, name)


def ashby(slug: str, name: str):
    """Returns an employer's jobs from its Ashby board."""
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    jobs = []
    for j in data.get("jobs", []):
        if not j.get("isListed", True):
            continue
        locs = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        if j.get("isRemote"):
            locs.append("Remote")
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        jobs.append(_job("ashby", name, j["id"], j["title"], " / ".join(filter(None, locs)),
                         j.get("jobUrl"), j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                         comp, j.get("publishedAt")))
    return jobs, len(jobs)


def workable(slug: str, name: str):
    """Returns an employer's jobs from its Workable board."""
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", retries=3)
    jobs = []
    for j in data.get("jobs", []):
        locs = [", ".join(filter(None, [l.get("city"), l.get("country")])) for l in j.get("locations") or []]
        if j.get("telecommuting"):
            locs.append("Remote")
        jobs.append(_job("workable", name, j["shortcode"], j["title"], " / ".join(filter(None, locs)),
                         j.get("url") or j.get("shortlink"), strip_html(j.get("description")),
                         posted_at=j.get("published_on") or j.get("created_at")))
    return jobs, len(jobs)


def smartrecruiters(slug: str, name: str):
    """UK postings only; the listing has no description (see DETAIL)."""
    jobs, offset, total = [], 0, 1
    while offset < total:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                        f"?country=gb&limit=100&offset={offset}")
        total = data.get("totalFound", 0)
        for j in data.get("content", []):
            loc = j.get("location") or {}
            where = loc.get("fullLocation") or loc.get("city") or ""
            if loc.get("remote"):
                where += " / Remote"
            level = (j.get("experienceLevel") or {}).get("label")
            jobs.append(_job("smartrecruiters", name, j["id"], j["name"], where,
                             f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                             f"Experience level: {level}" if level else "",
                             posted_at=j.get("releasedDate")))
        offset += 100
        if not data.get("content"):
            break
    return jobs, total


def smartrecruiters_detail(job: dict) -> str:
    """Returns the advert text of one SmartRecruiters job."""
    slug, ext = job["url"].rstrip("/").split("/")[-2:]
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{ext}")
    sections = (d.get("jobAd") or {}).get("sections") or {}
    return "\n\n".join(strip_html((sections.get(k) or {}).get("text"))
                        for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))


def recruitee(slug: str, name: str):
    """Returns an employer's jobs from its Recruitee board."""
    data = get_json(f"https://{slug}.recruitee.com/api/offers/")
    jobs = []
    for j in data.get("offers", []):
        locs = [l.get("name") or l.get("city") for l in j.get("locations") or []] or [j.get("location")]
        if j.get("remote"):
            locs.append("Remote")
        sal = j.get("salary") or {}
        salary = f"{sal.get('currency')} {sal.get('min')}-{sal.get('max')} {sal.get('period')}" if sal.get("min") else None
        jobs.append(_job("recruitee", name, j["id"], j["title"], " / ".join(filter(None, locs)),
                         j.get("careers_url"),
                         strip_html((j.get("description") or "") + (j.get("requirements") or "")),
                         salary, j.get("published_at")))
    return jobs, len(jobs)


def personio(slug: str, name: str):
    """Returns an employer's jobs from its Personio feed."""
    root = ET.fromstring(get(f"https://{slug}.jobs.personio.de/xml?language=en"))
    jobs = []
    for p in root.iter("position"):
        f = lambda tag: (p.findtext(tag) or "").strip()
        offices = [f("office")] + [o.text or "" for o in p.findall("additionalOffices/office")]
        desc = "\n\n".join(
            f"{d.findtext('name') or ''}\n{strip_html(d.findtext('value'))}"
            for d in p.findall("jobDescriptions/jobDescription")
        )
        if f("yearsOfExperience"):
            desc = f"Years of experience: {f('yearsOfExperience')}\n\n{desc}"
        jobs.append(_job("personio", name, f("id"), f("name"), " / ".join(filter(None, offices)),
                         f"https://{slug}.jobs.personio.de/job/{f('id')}", desc,
                         posted_at=f("createdAt")))
    return jobs, len(jobs)


FETCHERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "lever_eu": lever_eu,
    "ashby": ashby,
    "workable": workable,
    "smartrecruiters": smartrecruiters,
    "recruitee": recruitee,
    "personio": personio,
}

# Sources whose listing omits the description: fetched per job, and only for
# jobs that pass the cheap title and location checks.
DETAIL = {"smartrecruiters": smartrecruiters_detail}
