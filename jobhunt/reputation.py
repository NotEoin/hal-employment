"""Company checks: public controversies on Wikipedia and registration at Companies House.

Flags what a reader should look at before applying; it does not judge. Wikipedia
section headings such as "Controversies" or "Working conditions" are the signal,
and the Companies House search shows whether a UK entity exists and is active.
"""
import html
import json
import re
import urllib.parse

from .http import get

WIKI_API = "https://en.wikipedia.org/w/api.php?"
COMPANIES_HOUSE = "https://find-and-update.company-information.service.gov.uk/search/companies?q="
CONCERN = re.compile(r"controvers|criticism|lawsuit|litigation|legal (issues|action)|scandal|investigation|"
                     r"fine[sd]?\b|working conditions|labou?r (practices|issues|dispute)|allegation|"
                     r"regulatory|data breach|privacy (concerns|issues)|safety|sanction|fraud|misconduct", re.I)
LEGAL_WORDS = r"\b(ltd|limited|plc|group|holdings|uk|the)\b"   # words a registration adds to a trading name
BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}


def _wiki(params: dict) -> dict:
    return json.loads(get(WIKI_API + urllib.parse.urlencode({**params, "format": "json"}), timeout=20))


def wikipedia(company: str) -> dict:
    """Returns the matching article, its summary, and any section headings that suggest concerns."""
    hits = _wiki({"action": "query", "list": "search", "srsearch": f"{company} company", "srlimit": 3})
    hits = hits.get("query", {}).get("search", [])
    article = next((h["title"] for h in hits if names(company, h["title"])), None)
    if not article:
        return {}
    sections = _wiki({"action": "parse", "page": article, "prop": "sections"}).get("parse", {}).get("sections", [])
    summary = _wiki({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                     "titles": article}).get("query", {}).get("pages", {})
    extract = next(iter(summary.values()), {}).get("extract", "")
    return {
        "article": article,
        "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(article.replace(" ", "_")),
        "summary": " ".join(extract.split()[:80]),
        "concerns": [s["line"] for s in sections if CONCERN.search(s["line"])],
    }


def companies_house(company: str) -> list[str]:
    """Returns the top Companies House search results as one-line descriptions."""
    page = get(COMPANIES_HOUSE + urllib.parse.quote_plus(company), headers=BROWSER, timeout=20)
    results = []
    key = _key(company)
    for block in re.findall(r'<li class="type-company">(.*?)</li>', page, re.S):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", block))).strip()
        # Only registrations whose name starts with the employer's name are the employer.
        if "{{" not in text and key and _key(text).startswith(key):
            results.append(text[:160])
    return results[:3]


def _key(name: str) -> str:
    # the whole name: a 12-letter key matched "Riverside Heritage Joinery" for "Riverside Heritage Trust"
    # (made-up employer, 2026-10-23)
    return "".join(_name_words(name))


def _name_words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(LEGAL_WORDS, " ", name.lower()))


def names(company: str, title: str) -> bool:
    """True when an article title holds every word of the company's name (legal words aside)."""
    # a 6-letter key matched "Riverside Cemetery (Denver)" for "Riverside Heritage Trust" (2026-10-23)
    wanted, held = _name_words(company), set(_name_words(title))
    return bool(wanted) and all(w in held for w in wanted)


def check(company: str) -> tuple[list[str], str]:
    """Returns (flags, markdown section) for a company."""
    flags, lines = [], ["## Company check", ""]
    try:
        wiki = wikipedia(company)
    except Exception:  # an unavailable lookup is reported, not fatal
        wiki = None
    if wiki is None:
        lines.append("- Wikipedia: lookup failed")
    elif not wiki:
        lines.append("- Wikipedia: no article (usual for small companies)")
    else:
        lines.append(f"- Wikipedia: [{wiki['article']}]({wiki['url']}): {wiki['summary']}")
        if wiki["concerns"]:
            lines.append(f"- Sections to read: {', '.join(wiki['concerns'])}")
            flags.append(f"company check: Wikipedia sections on {', '.join(wiki['concerns'][:4])}")
    try:
        registered = companies_house(company)
    except Exception:  # a failed registry lookup leaves the company check without it
        registered = None
    if registered is None:
        lines.append("- Companies House: lookup failed")
    elif not registered:
        lines.append("- Companies House: no registration found under this exact name; "
                     "the employer may trade under another legal name")
    else:
        lines += [f"- Companies House: {r}" for r in registered]
        if all(re.search(r"dissolved|liquidation|administration", r, re.I) for r in registered):
            flags.append("company check: every Companies House match is dissolved or insolvent")
    return flags, "\n".join(lines) + "\n"
