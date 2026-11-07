"""Company checks: public controversies on Wikipedia and registration at Companies House.

Flags what a reader should look at before applying; it does not judge. Wikipedia section headings such as
"Controversies" or "Working conditions" are the signal, and Companies House shows whether the UK company
exists and is active.

Design decisions, each with its reason:

- A hit is kept only when it plausibly is the advert's employer (`Advert`): its name holds every word of the
  employer's name, and something else ties it to the advert. For a Wikipedia article that is the advert's
  town, the employer's web domain among the article's links, or `INDUSTRY_SHARED` words of the advert's
  trade in its summary. For a Companies House registration it is an active company whose nature of business
  (its SIC codes, with the words of each code's division in `SIC_DIVISIONS`) shares a word with the advert.
  A name alone matched a solar company, a fungicide and a dissolved company for one employer (2026-11-06).
  Anything else reads "No reliable match found".
- Words are compared as `record.words` stems, so "banking" in the advert meets "bank" in a summary.
- Every lookup has a timeout; a lookup that fails says so and never stops a pack.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass

from .http import get

WIKI_API = "https://en.wikipedia.org/w/api.php?"
WIKI_PAGE = "https://en.wikipedia.org/wiki/"
COMPANIES_HOUSE_SITE = "https://find-and-update.company-information.service.gov.uk"
COMPANIES_HOUSE = f"{COMPANIES_HOUSE_SITE}/search/companies?q="
CONCERN = re.compile(r"controvers|criticism|lawsuit|litigation|legal (issues|action)|scandal|investigation|"
                     r"fine[sd]?\b|working conditions|labou?r (practices|issues|dispute)|allegation|"
                     r"regulatory|data breach|privacy (concerns|issues)|safety|sanction|fraud|misconduct", re.I)
LEGAL_WORDS = r"\b(ltd|limited|plc|group|holdings|uk|the)\b"   # words a registration adds to a trading name
BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"}
LOOKUP_TIMEOUT_S = 20
WIKI_HITS = 3             # search results read for an article naming the employer
REGISTRATIONS = 3         # Companies House results named like the employer that are looked at, and shown
NO_MATCH = "No reliable match found"
INDUSTRY_SHARED = 2       # trade words an article's summary must share with the advert; one is often chance
SIC_SHARED = 1            # a registration's nature of business is a few words, so one shared word is a match
SUMMARY_WORDS = 80        # of an article's first section, shown in the pack
# Words in nearly every advert and company summary, which say nothing about the trade.
GENERIC = set("""company companies business businesses service services team teams people role roles work working
experience skills skill opportunity opportunities candidate candidates salary benefits apply application
applications including based limited group international global leading years year support join within
across great looking organisation organization offer offers provide provides provided provider providers
help helps client clients customer customers product products range across further include includes
founded headquartered headquarters known largest large small first public private part member members
employees staff staffing ensure ensuring responsible responsibilities develop development manage
management new well also high level levels make makes made world worldwide british kingdom united""".split())
NOT_PLACES = {"uk", "united kingdom", "england", "scotland", "wales", "northern ireland", "remote", "hybrid",
              "home", "home based", "office", "flexible", "multiple locations", "various", "nationwide",
              "anywhere", "not stated", "gb", "great britain"}
# Hosts of job boards and application systems: an advert's link there names no employer's own site.
JOB_BOARDS = ("greenhouse", "lever", "workable", "ashbyhq", "smartrecruiters", "recruitee", "personio", "indeed",
              "linkedin", "reed", "adzuna", "totaljobs", "jobs.ac.uk", "civilservicejobs", "jobs.nhs", "trac.jobs",
              "ycombinator", "workatastartup", "80000hours", "ktp", "glassdoor", "cv-library", "teamtailor",
              "bamboohr", "myworkdayjobs", "icims", "jobvite", "applytojob", "hirehive", "eploy", "tal.net",
              "jobs.example", "google", "wikipedia")
WEB_ADDRESS = re.compile(r"\b([a-z0-9][a-z0-9.-]{0,200}\.(?:co\.uk|org\.uk|ac\.uk|com|org|net|io|uk|ai))\b")
# The endings of a web host that name no one: "careers.acme.co.uk" is named "acme".
DOMAIN_ENDINGS = {"uk", "com", "org", "net", "io", "co", "ai", "dev", "eu", "ac", "gov", "nhs", "ltd", "plc", "me"}
# The trade of each SIC division (the code's first two digits), from the UK SIC 2007 section titles, in plain
# words an advert uses. Each entry: (first division, last division, words).
SIC_DIVISIONS = (
    (1, 3, "farming agriculture farm crops forestry fishing"),
    (10, 12, "food drink beverages manufacturing"),
    (19, 22, "chemicals chemical pharmaceutical plastics fungicide pesticide medicines"),
    (26, 28, "electronics engineering machinery manufacturing hardware"),
    (29, 30, "automotive vehicles aerospace aircraft"),
    (35, 35, "energy electricity power gas solar renewable wind"),
    (36, 39, "water waste recycling environmental"),
    (41, 43, "construction building civil engineering"),
    (45, 47, "retail shop shops store sales ecommerce wholesale"),
    (49, 53, "transport logistics delivery freight rail aviation shipping"),
    (55, 56, "hospitality hotel restaurant catering"),
    (58, 60, "publishing media broadcasting film television books"),
    (61, 61, "telecommunications network broadband mobile"),
    (62, 63, "software technology digital data computing developer cloud web platform"),
    (64, 66, "bank banking finance financial lending credit payments insurance investment fintech mortgage loans"),
    (68, 68, "property housing estate letting homes"),
    (69, 69, "legal law solicitors accounting accountancy audit tax"),
    (70, 70, "consultancy consulting strategy"),
    (71, 71, "engineering architecture surveying design"),
    (72, 72, "research science scientific laboratory"),
    (73, 73, "advertising marketing"),
    (74, 74, "design photography translation"),
    (75, 75, "veterinary animals"),
    (78, 78, "recruitment employment"),
    (80, 82, "security facilities administration"),
    (84, 84, "government council civil defence"),
    (85, 85, "education school university college teaching training learning"),
    (86, 88, "health healthcare hospital care medical nhs social"),
    (90, 91, "arts museum heritage library archive archives culture collections gallery theatre"),
    (93, 93, "sport fitness leisure"),
    (94, 94, "charity membership union association"),
)


@dataclass(frozen=True)
class Advert:
    """What an advert says about its employer: where the job is, its words and its link."""

    location: str = ""
    text: str = ""
    url: str = ""


def advert_of(job: Mapping | None) -> Advert:
    """The advert of a job row (location, description and link); an empty one without a job."""
    if not job:
        return Advert()
    keys = set(job.keys())
    return Advert(*(str(job[k] or "") if k in keys else "" for k in ("location", "description", "url")))


def _wiki(params: dict) -> dict:
    return json.loads(get(WIKI_API + urllib.parse.urlencode({**params, "format": "json"}), timeout=LOOKUP_TIMEOUT_S))


def _stems(text: str) -> set[str]:
    from . import record

    return set(record.words(text)) - set(record.words(" ".join(GENERIC)))


def trade_words(advert: Advert, company: str) -> set[str]:
    """The advert's words that may name its trade: its stems less common ones and the employer's own name."""
    return _stems(advert.text) - _stems(company)


def towns(advert: Advert) -> list[str]:
    """The towns and regions the advert's location names, without "UK", "Remote" and the like."""
    parts = re.split(r"[,/|;()]| or | and | - ", advert.location or "")
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in NOT_PLACES and len(p.strip()) > 1]


def _site_name(host: str) -> str:
    """The name part of a web host: "acme" for "careers.acme.co.uk"; "" for a job board's."""
    host = host.lower().removeprefix("www.")
    if not host or any(board in host for board in JOB_BOARDS):
        return ""
    labels = host.split(".")
    while len(labels) > 1 and labels[-1] in DOMAIN_ENDINGS:
        labels = labels[:-1]
    return labels[-1]


def domains(advert: Advert) -> set[str]:
    """The employer's own web names the advert gives: its link, and addresses in its text, never a job board's."""
    hosts = [urllib.parse.urlparse(advert.url).hostname or "",
             *WEB_ADDRESS.findall((advert.text or "").lower())]
    return {n for n in (_site_name(h) for h in hosts) if n}


def plausible_article(advert: Advert, company: str, summary: str, links: list[str]) -> bool:
    """True when an article named like the employer also names the advert's town, links to the employer's
    site, or shares `INDUSTRY_SHARED` trade words with the advert."""
    if any(re.search(rf"\b{re.escape(p)}\b", summary, re.I) for p in towns(advert)):
        return True
    names = domains(advert)
    if names and any(_site_name(urllib.parse.urlparse(u).hostname or "") in names for u in links):
        return True
    return len(trade_words(advert, company) & _stems(summary)) >= INDUSTRY_SHARED


def _article(title: str) -> tuple[str, list[str]]:
    """An article's first section as plain text, and its external links."""
    found = _wiki({"action": "query", "prop": "extracts|extlinks", "exintro": 1, "explaintext": 1,
                   "ellimit": "max", "titles": title}).get("query", {}).get("pages", {})
    page = next(iter(found.values()), {})
    return page.get("extract", ""), [next(iter(e.values()), "") for e in page.get("extlinks", []) if e]


def wikipedia(company: str, advert: Advert | None = None) -> dict:
    """The article that is the advert's employer, with its summary and any section headings that suggest
    concerns; {} when no article is plausibly the employer."""
    advert = advert or Advert()
    hits = _wiki({"action": "query", "list": "search", "srsearch": f"{company} company", "srlimit": WIKI_HITS})
    for title in [h["title"] for h in hits.get("query", {}).get("search", []) if names(company, h["title"])]:
        summary, links = _article(title)
        if not plausible_article(advert, company, summary, links):
            continue
        sections = _wiki({"action": "parse", "page": title, "prop": "sections"}).get("parse", {}).get("sections", [])
        return {
            "article": title,
            "url": WIKI_PAGE + urllib.parse.quote(title.replace(" ", "_")),
            "summary": " ".join(summary.split()[:SUMMARY_WORDS]),
            "concerns": [s["line"] for s in sections if CONCERN.search(s["line"])],
        }
    return {}


def sic_words(codes: list[str]) -> str:
    """The trade a registration's SIC codes name: each code's own words and its division's (`SIC_DIVISIONS`)."""
    out = []
    for code in codes:
        out.append(code)
        m = re.match(r"\s*(\d{2})", code)
        if m:
            out += [words for first, last, words in SIC_DIVISIONS if first <= int(m.group(1)) <= last]
    return " ".join(out)


def registration(number: str) -> dict:
    """A company's status and nature of business (SIC codes) from its Companies House page."""
    page = get(f"{COMPANIES_HOUSE_SITE}/company/{urllib.parse.quote(number)}", headers=BROWSER,
               timeout=LOOKUP_TIMEOUT_S)
    status = re.search(r'id="company-status"[^>]*>(.*?)<', page, re.S)
    sic = [html.unescape(" ".join(s.split())) for s in re.findall(r'id="sic\d+"[^>]*>(.*?)<', page, re.S)]
    return {"status": " ".join(status.group(1).split()) if status else "", "sic": sic}


def companies_house(company: str, advert: Advert | None = None) -> list[str]:
    """The Companies House registrations that are plausibly the advert's employer, as one-line descriptions:
    named like it, active, and in a trade the advert's words share."""
    advert = advert or Advert()
    page = get(COMPANIES_HOUSE + urllib.parse.quote_plus(company), headers=BROWSER, timeout=LOOKUP_TIMEOUT_S)
    key, trade, results = _key(company), trade_words(advert, company), []
    for block in re.findall(r'<li class="type-company">(.*?)</li>', page, re.S)[:REGISTRATIONS * 3]:
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", block))).strip()
        number = re.search(r'href="/company/([A-Z0-9]+)"', block)
        # Only registrations whose name starts with the employer's name may be the employer.
        if "{{" in text or not key or not _key(text).startswith(key) or not number:
            continue
        found = registration(number.group(1))
        if found["status"].lower() == "active" and len(trade & _stems(sic_words(found["sic"]))) >= SIC_SHARED:
            results.append(f"{text[:160]} ({found['status']}; {'; '.join(found['sic'])})")
        if len(results) == REGISTRATIONS:
            break
    return results


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


def check(company: str, advert: Advert | None = None) -> tuple[list[str], str]:
    """Returns (flags, markdown section) for the employer of `advert`."""
    flags, lines = [], ["## Company check", ""]
    try:
        wiki = wikipedia(company, advert)
    except Exception:  # an unavailable lookup is reported, not fatal
        wiki = None
    if wiki is None:
        lines.append("- Wikipedia: lookup failed")
    elif not wiki:
        lines.append(f"- Wikipedia: {NO_MATCH} (usual for small companies)")
    else:
        lines.append(f"- Wikipedia: [{wiki['article']}]({wiki['url']}): {wiki['summary']}")
        if wiki["concerns"]:
            lines.append(f"- Sections to read: {', '.join(wiki['concerns'])}")
            flags.append(f"company check: Wikipedia sections on {', '.join(wiki['concerns'][:4])}")
    try:
        registered = companies_house(company, advert)
    except Exception:  # a failed registry lookup leaves the company check without it
        registered = None
    if registered is None:
        lines.append("- Companies House: lookup failed")
    elif not registered:
        lines.append(f"- Companies House: {NO_MATCH}; the employer may trade under another legal name")
    else:
        lines += [f"- Companies House: {r}" for r in registered]
    return flags, "\n".join(lines) + "\n"
