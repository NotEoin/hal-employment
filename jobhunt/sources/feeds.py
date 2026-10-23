"""Job feeds and careers pages: RSS or Atom feeds, pages with schema.org job data, and Guardian Jobs.

A feed item becomes a job with the feed's name as the employer unless the item names one; its summary is
the advert until a detail fetch reads more. Careers pages are read through `find.detect.postings`, the same
reading "Add a job site" shows the person, so what they saw is what every search finds.

Guardian Jobs is searched through its own RSS search, with the goal's phrases and places. The
feed's address pattern is the board software's usual one and has not been checked against the live site.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import xml.etree.ElementTree as ET

from ..http import get
from . import places
from .ats import strip_html

FETCH_TIMEOUT = 30          # seconds per feed or page
FEED_ITEMS = 200            # a feed longer than this is a site's whole archive; the newest are enough
# "Policy Officer at Fernvale Council": a dash is not used, because titles such as "Researcher - Fixed term"
# have one too.
AT_EMPLOYER = re.compile(r"^(?P<title>.+?)\s+(?:at|@)\s+(?P<company>[^|]+)$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(fields: dict, *names: str):
    """The first of `names` the item has; an element with no children is falsy, so `or` cannot be used."""
    return next((fields[n] for n in names if fields.get(n) is not None), None)


def parse_feed(xml_text: str, feed_url: str, name: str, source: str = "rss") -> list[dict]:
    """The items of an RSS or Atom feed as jobs in upsert shape."""
    root = ET.fromstring(xml_text)
    items = [e for e in root.iter() if _local(e.tag) in ("item", "entry")][:FEED_ITEMS]
    host = (urllib.parse.urlparse(feed_url).hostname or "").removeprefix("www.")
    jobs = []
    for item in items:
        fields = {_local(c.tag): c for c in item}
        title = (fields["title"].text or "").strip() if "title" in fields else ""
        link = fields.get("link")
        url = ""
        if link is not None:
            url = (link.get("href") or link.text or "").strip()
        summary = _first(fields, "description", "summary", "content")
        posted = _first(fields, "pubDate", "published", "updated")
        company = name
        m = AT_EMPLOYER.match(title)
        if m and len(m.group("company")) < 60:
            title, company = m.group("title").strip(), m.group("company").strip()
        if not title or not url:
            continue
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        jobs.append({"id": f"{source}:{host}:{key}", "source": source, "company": company, "title": title,
                     "location": "", "url": url, "description": strip_html(summary.text if summary is not None
                                                                           else ""),
                     "salary": None, "posted_at": (posted.text or "").strip() if posted is not None else None,
                     "board_size": None})
    return jobs


def read_feed_url(url: str, name: str) -> list[dict]:
    """Fetches and reads one feed."""
    return parse_feed(get(url, timeout=FETCH_TIMEOUT), url, name)


def read_feed(source: dict) -> list[dict]:
    """The jobs of an rss source."""
    return read_feed_url(source["config"]["url"], source["name"])


def read_careers_page(source: dict) -> list[dict]:
    """The jobs a careers page lists in its schema.org data."""
    from ..find import detect

    url = source["config"]["url"]
    return detect.postings(get(url, timeout=FETCH_TIMEOUT), url, source["name"])


def guardian(source: dict, goal) -> list[dict]:
    """Guardian Jobs' RSS search for the goal's phrases in each of the goal's places."""
    from ..find import sources_registry

    pattern = source["config"]["feed"]
    jobs: dict[str, dict] = {}
    for query in sources_registry.queries(source, goal):
        for place in places():
            url = pattern.format(query=urllib.parse.quote_plus(query), place=urllib.parse.quote_plus(place))
            for j in parse_feed(get(url, timeout=FETCH_TIMEOUT), url, "Guardian Jobs", source="guardian"):
                jobs[j["url"]] = j
    return list(jobs.values())
