"""Company research from the public web: a search, then the pages it finds.

Uses DuckDuckGo's HTML endpoint, which needs no key. Returns plain text for
the local model to summarise; nothing here calls a model.
"""
import html
import re
import urllib.parse

from .http import get

SEARCH = "https://html.duckduckgo.com/html/?q="
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}
# Aggregators and job boards describe the listing, not the company.
SKIP_HOSTS = re.compile(r"linkedin|glassdoor|indeed|crunchbase|tracxn|wikipedia|greenhouse|lever\.co|"
                        r"ashbyhq|workable|smartrecruiters|ycombinator|facebook|twitter|x\.com|youtube")


def search(query: str, limit: int = 5) -> list[str]:
    """Returns result URLs for a query, excluding aggregator sites."""
    page = get(SEARCH + urllib.parse.quote_plus(query), headers=BROWSER_UA, timeout=15)
    urls = []
    for href in re.findall(r'class="result__a" href="([^"]+)"', page):
        target = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(href)).query).get("uddg")
        if target and not SKIP_HOSTS.search(target[0]):
            urls.append(target[0])
    return urls[:limit]


def page_text(url: str, limit: int = 6000) -> str:
    """Returns the readable text of a web page, truncated to `limit` characters."""
    raw = get(url, headers=BROWSER_UA, timeout=15)
    raw = re.sub(r"<(script|style|nav|footer|svg|noscript)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def company_pages(company: str, max_pages: int = 2) -> list[tuple[str, str]]:
    """Returns (url, text) for the company's own site, best effort."""
    found = []
    for url in search(f"{company} company about"):
        try:
            found.append((url, page_text(url)))
        except Exception:  # noqa: BLE001 - research is best effort; one bad page is skipped
            continue
        if len(found) >= max_pages:
            break
    return found
