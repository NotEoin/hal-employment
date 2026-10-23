"""What kind of job site is this address? Detection for "Add a job site" and "Watch a company".

First match wins: a known application system by its host; one embedded in the page; an RSS or Atom feed the
page links; schema.org `JobPosting` data in the page (read without AI). Anything else is a page Hal cannot
read by itself yet (reading pages with the AI is not built), so the person is offered
"Add one advert" instead.

Everything here is a pure function of the address and the page text, apart from `probe` and the fetchers it
is handed, so tests run on the practice pages in `tests/site/careers-*.html` without the network.
"""

from __future__ import annotations

import html as html_mod
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

ADVERT_CHARS = 12000        # as much of each advert as a job keeps (find.job_actions does the same)
PROBE_THREADS = 16          # the old probe command's pool size

# Host patterns of application systems: (pattern, ats id, where the board's slug is).
HOSTS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^(job-boards|boards)\.greenhouse\.io$"), "greenhouse", "path"),
    (re.compile(r"^(job-boards|boards)\.eu\.greenhouse\.io$"), "greenhouse", "path"),
    (re.compile(r"^jobs\.lever\.co$"), "lever", "path"),
    (re.compile(r"^jobs\.eu\.lever\.co$"), "lever_eu", "path"),
    (re.compile(r"^jobs\.ashbyhq\.com$"), "ashby", "path"),
    (re.compile(r"^apply\.workable\.com$"), "workable", "path"),
    (re.compile(r"^(jobs|careers)\.smartrecruiters\.com$"), "smartrecruiters", "path"),
    (re.compile(r"^([a-z0-9-]+)\.recruitee\.com$"), "recruitee", "host"),
    (re.compile(r"^([a-z0-9-]+)\.jobs\.personio\.(de|com)$"), "personio", "host"),
    (re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$"), "workday", "host"),
    (re.compile(r"^([a-z0-9-]+)\.teamtailor\.com$"), "teamtailor", "host"),
    (re.compile(r"^([a-z0-9-]+)\.bamboohr\.com$"), "bamboohr", "host"),
    (re.compile(r"^([a-z0-9-]+)\.pinpointhq\.com$"), "pinpoint", "host"),
]
# Application systems embedded in an employer's own careers page.
EMBEDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/)?job_board(?:/js)?\?for=([A-Za-z0-9_-]+)"),
     "greenhouse"),
    (re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"), "lever"),
    (re.compile(r"api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+?)(?:/embed|[\"'/?])"), "ashby"),
    (re.compile(r"apply\.workable\.com/(?:api/v\d/(?:widget/)?accounts/)?([A-Za-z0-9_-]+)"), "workable"),
]
FEED = re.compile(r"<link\b[^>]*type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", re.I)
HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
JSON_LD = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
TAGS = re.compile(r"<[^>]+>")
SKIP_SLUGS = {"embed", "api", "v0", "v1", "jobs", "job_board", "j"}


@dataclass
class Detected:
    """What an address turned out to be, and the jobs on it now."""

    kind: str                       # ats_board | rss | careers_page | ai_page | company_watch
    name: str
    url: str = ""
    ats: str | None = None
    slug: str | None = None
    jobs: list[dict] = field(default_factory=list)
    can_watch: bool = True
    words: str = ""

    def config(self) -> dict:
        """The `sources.config` a saved site gets."""
        if self.ats:
            return {"ats": self.ats, "slug": self.slug, "url": self.url}
        return {"url": self.url}


def text(fragment: str | None) -> str:
    """Page markup as plain text on one line."""
    return re.sub(r"\s+", " ", html_mod.unescape(TAGS.sub(" ", fragment or ""))).strip()


def name_from_slug(slug: str) -> str:
    """A company name from a board slug: "yorkshire-policy-lab" reads "Yorkshire Policy Lab"."""
    words = re.split(r"[-_.]+", slug)
    return " ".join(w if w.isupper() else w.capitalize() for w in words if w)


def ats_from_url(url: str) -> tuple[str, str] | None:
    """(ats, slug) when the address is a board on a known application system."""
    u = urlparse(url.strip())
    host = (u.hostname or "").lower().removeprefix("www.")
    for pattern, ats, where in HOSTS:
        m = pattern.match(host)
        if not m:
            continue
        if where == "host":
            return ats, m.group(1)
        parts = [p for p in u.path.split("/") if p and p.lower() not in SKIP_SLUGS]
        if parts:
            return ats, parts[0]
        q = re.search(r"(?:^|&)for=([A-Za-z0-9_-]+)", u.query)
        if q:
            return ats, q.group(1)
    return None


def ats_from_page(page: str) -> tuple[str, str] | None:
    """(ats, slug) for an application system embedded in a careers page."""
    for pattern, ats in EMBEDS:
        m = pattern.search(page or "")
        if m and m.group(1).lower() not in SKIP_SLUGS:
            return ats, m.group(1)
    return None


def feed_link(page: str, base: str) -> str | None:
    """The address of the first RSS or Atom feed a page links."""
    for tag in FEED.findall(page or ""):
        if m := HREF.search(tag):
            return urljoin(base, html_mod.unescape(m.group(1)))
    return None


def page_title(page: str) -> str:
    """The page's title, without the site name after a bar or dash."""
    m = TITLE.search(page or "")
    if not m:
        return ""
    return re.split(r"\s+[|\u2013\u2014-]\s+", text(m.group(1)))[0].strip()


def _items(data) -> list:
    if isinstance(data, list):
        return [i for d in data for i in _items(d)]
    if not isinstance(data, dict):
        return []
    if "@graph" in data:
        return _items(data["@graph"])
    if data.get("@type") == "ItemList":
        return [i for e in data.get("itemListElement", []) for i in _items(e.get("item", e) if isinstance(e, dict)
                                                                          else e)]
    return [data]


def _is_posting(item: dict) -> bool:
    kind = item.get("@type")
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def _place(posting: dict) -> str:
    place = posting.get("jobLocation") or {}
    places = place if isinstance(place, list) else [place]
    names = []
    for p in places:
        address = p.get("address", {}) if isinstance(p, dict) else {}
        if isinstance(address, dict) and address.get("addressLocality"):
            names.append(address["addressLocality"])
    if posting.get("jobLocationType") == "TELECOMMUTE":
        names.append("Remote")
    return " / ".join(names)


def _pay(posting: dict) -> str | None:
    salary = posting.get("baseSalary") or {}
    value = salary.get("value", {}) if isinstance(salary, dict) else {}
    if not isinstance(value, dict) or not (value.get("minValue") or value.get("value")):
        return None
    pay = f"GBP {value.get('minValue') or value.get('value')}"
    if value.get("maxValue"):
        pay += f" - {value['maxValue']}"
    return pay + f" per {str(value.get('unitText') or 'year').lower()}"


def postings(page: str, base: str, company: str = "") -> list[dict]:
    """Every schema.org `JobPosting` in a page, as jobs in upsert shape (no AI needed)."""
    host = (urlparse(base).hostname or "").removeprefix("www.")
    jobs = []
    for block in JSON_LD.findall(page or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for item in _items(data):
            if not isinstance(item, dict) or not _is_posting(item):
                continue
            org = item.get("hiringOrganization") or {}
            org_name = org.get("name", "") if isinstance(org, dict) else str(org)
            url = urljoin(base, item.get("url") or item.get("sameAs") or base)
            ident = item.get("identifier")
            ident = ident.get("value") if isinstance(ident, dict) else ident
            key = str(ident or url)
            jobs.append({"id": f"page:{host}:{re.sub(r'[^a-z0-9]+', '-', key.lower()).strip('-')[-60:]}",
                         "source": "careers_page", "company": text(org_name) or company or host,
                         "title": text(item.get("title", "")), "location": _place(item), "url": url,
                         "description": text(item.get("description", ""))[:ADVERT_CHARS], "salary": _pay(item),
                         "posted_at": item.get("datePosted"), "board_size": None,
                         "closes_at": (item.get("validThrough") or "")[:10] or None})
    return jobs


def detect(url: str, page: str | None, *, fetch_ats: Callable[[str, str, str], tuple[list[dict], int]],
           read_feed: Callable[[str, str], list[dict]] | None = None) -> Detected:
    """Works out what `url` is; `page` is its text (None when the address alone decided it).

    `fetch_ats(ats, slug, name)` reads a board, and `read_feed(url, name)` a feed, so the caller decides how
    the network is reached.
    """
    found = ats_from_url(url)
    title = page_title(page or "")
    if found is None and page:
        found = ats_from_page(page)
    if found is not None:
        ats, slug = found
        name = name_from_slug(slug)
        from jobhunt.sources import ats as ats_mod

        if ats not in ats_mod.FETCHERS:
            label = _ats_label(ats)
            return Detected("ai_page", name, url, ats=ats, slug=slug, can_watch=False,
                            words=f"This is a {label} careers page for {name}. Hal cannot read {label} pages by "
                                  "itself yet. Add one advert instead.")
        jobs, _size = fetch_ats(ats, slug, name)
        label = _ats_label(ats)
        return Detected("ats_board", name, url, ats=ats, slug=slug, jobs=jobs,
                        words=f"This is a {label} careers page for {name}. Hal can check it at every search. "
                              f"It has {_jobs_words(len(jobs))} now.")
    host_name = name_from_slug((urlparse(url).hostname or "").removeprefix("www.").split(".")[0])
    name = title or host_name
    feed = feed_link(page or "", url)
    if feed and read_feed is not None:
        jobs = read_feed(feed, name)
        return Detected("rss", name, feed, jobs=jobs,
                        words=f"This page has a job feed for {name}. Hal can check it at every search. "
                              f"It has {_jobs_words(len(jobs))} now.")
    jobs = postings(page or "", url, name)
    if jobs:
        company = jobs[0]["company"] or name
        return Detected("careers_page", company, url, jobs=jobs,
                        words=f"This is a careers page for {company}. Hal can read its jobs at every search. "
                              f"It has {_jobs_words(len(jobs))} now.")
    return Detected("ai_page", name, url, can_watch=False,
                    words="Hal cannot read this page by itself yet. Add one advert instead.")


def _ats_label(ats: str) -> str:
    from jobhunt.find.sources_registry import ATS_NAMES

    return ATS_NAMES.get(ats, "job board")


def _jobs_words(n: int) -> str:
    return "1 job" if n == 1 else f"{n} jobs"


# ---------------------------------------------------------------------------------------------- probe


def slugs(name: str) -> list[str]:
    """Board slugs to try for a company: 'Name=slug' pins one; else the joined and hyphenated forms.

    Never the first word alone: 'Bloom & Wild' must not match some 'bloom'.
    """
    if "=" in name:
        return [name.split("=", 1)[1].strip()]
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    base = re.sub(r"\s+", " ", base)
    return list(dict.fromkeys([base.replace(" ", ""), base.replace(" ", "-")]))


@dataclass(frozen=True)
class Probed:
    """A board found for a company name."""

    name: str
    ats: str
    slug: str
    size: int
    in_scope: int
    jobs: tuple[dict, ...] = ()


def probe(names: list[str], *, fetchers: dict[str, Callable] | None = None,
          in_scope: Callable[[dict], bool] = lambda j: True) -> dict[str, Probed]:
    """The best board on any application system for each company name (most jobs in scope, then most jobs)."""
    from jobhunt.http import NotFound
    from jobhunt.sources import ats

    table = fetchers if fetchers is not None else ats.FETCHERS

    def attempt(name: str, kind: str, slug: str) -> Probed | None:
        try:
            jobs, size = table[kind](slug, name.split("=")[0])
        except NotFound:
            return None
        except Exception:  # any failure on one system means "not there", as the old probe did
            return None
        if not size:
            return None
        return Probed(name.split("=")[0], kind, slug, size, sum(1 for j in jobs if in_scope(j)), tuple(jobs))

    best: dict[str, Probed] = {}
    with ThreadPoolExecutor(max_workers=PROBE_THREADS) as pool:
        futs = [pool.submit(attempt, n, k, s) for n in names for s in slugs(n) for k in table]
        for f in as_completed(futs):
            r = f.result()
            if r is None:
                continue
            if r.name not in best or (r.in_scope, r.size) > (best[r.name].in_scope, best[r.name].size):
                best[r.name] = r
    return best
