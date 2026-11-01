"""Salary expectations for one job: a direct figure, from the advert and the market for comparable roles.

Employers who ask for a figure want one; "open to discussion" reads as dodging the
question. So each application gets a researched answer, with its reasoning kept for the
applicant to check in the form review:

- the advert's own range, when it states one: the answer stays inside it;
- ITJobsWatch's median for the nearest junior or graduate job title, in London or in the
  UK outside London, over the last six months (public pages, no key). ITJobsWatch covers technical jobs
  only, so it is read only for the technical preset (`scoring.preset` = technical_grad);
- the comparable junior adverts already in Hal's database, in the same region.

The market figure is the mean of the medians that rest on at least ten salaries; when the
role's own family has too few, junior software roles in the region stand in, and only when
those are thin too is a small sample used, flagged. Adzuna's own
salary estimates are not adverts and are left out: Adzuna gives them as one odd figure
("GBP 53,622-53,622"), never a round range. Day rates, hourly rates and foreign currencies
are ignored. When nothing is known the answer is the applicant's own floor (`goal.min_pay`, read when
used), flagged.
"""
import html
import re
import statistics
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import db
from .http import get
from .research import BROWSER_UA

TECHNICAL_PRESET = "technical_grad"   # the only preset whose jobs ITJobsWatch describes

ITJW = "https://www.itjobswatch.co.uk/jobs/{place}/{phrase}.do"
ENOUGH = 10  # salaries behind a median before it is trusted; four "junior" adverts at GBP 60k+ once set one
FEW = 4  # below this a median is noise, not used even as a last resort
ANNUAL = (15_000, 150_000)  # outside this a figure is a day rate, a bonus or a typo, not a junior salary
RECENT_DAYS = 180

# Role families, most specific first, each with the ITJobsWatch job titles to read for it.
FAMILIES = [
    ("data engineer", r"data engineer|analytics engineer|etl", ["junior data engineer", "graduate data engineer"]),
    ("data scientist", r"data scien", ["junior data scientist", "graduate data scientist"]),
    ("data analyst", r"data analyst|\banalyst\b", ["junior data analyst", "graduate data analyst"]),
    ("machine learning engineer", r"machine learning|\bml\b|\bai\b|artificial intelligence|research engineer",
     ["junior machine learning engineer", "graduate machine learning engineer", "junior data scientist"]),
    ("security engineer", r"secur|cyber|penetration|soc\b", ["junior security analyst", "graduate cyber security",
                                                            "junior cyber security analyst"]),
    ("devops engineer", r"devops|cloud|platform|infrastructure|site reliability|\bsre\b",
     ["junior devops engineer", "graduate devops engineer", "junior cloud engineer"]),
    ("quantitative developer", r"quant|trading|electronic trading", ["graduate quantitative developer",
                                                                     "graduate software engineer"]),
    ("software engineer", r".", ["graduate software engineer", "junior software engineer", "junior software developer",
                                 "graduate developer"]),
]
JUNIOR = re.compile(r"junior|graduate|entry|trainee|associate|\bgrad\b|early careers?|new grad", re.I)
SENIOR = re.compile(r"senior|\bsr\b|lead|principal|staff|head of|manager|director|architect|mid[- ]level|"
                    r"\b(ii|iii|iv)\b|apprentice", re.I)
NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k)?"
# "GBP 30k", "GBP 40,000-60,000", "GBP 30-35k": the second figure of a range often has no currency mark.
MONEY = re.compile(rf"(?:\u00a3|GBP)\s?{NUMBER}(?:\s?(?:-|\u2013|to)\s?(?:\u00a3|GBP)?\s?{NUMBER})?", re.I)

_PAGES: dict = {}


def family(title: str) -> tuple[str, list[str]]:
    """The role family a job title belongs to, and the ITJobsWatch titles that describe it."""
    for name, pattern, phrases in FAMILIES:
        if re.search(pattern, title or "", re.I):
            return name, phrases
    return FAMILIES[-1][0], FAMILIES[-1][2]


def figures(text: str) -> list[int]:
    """Annual salaries in pounds named in a text; day rates, hourly rates and other currencies are not."""
    out = []
    for m in MONEY.finditer(text or ""):
        thousands = bool(m.group(2) or m.group(4))  # "GBP 30-35k": the k covers both
        for number, k in ((m.group(1), m.group(2)), (m.group(3), m.group(4))):
            if number:
                n = float(number.replace(",", "")) * (1000 if k or (thousands and float(number.replace(",", "")) < 1000) else 1)
                if ANNUAL[0] <= n <= ANNUAL[1]:
                    out.append(int(n))
    return out


def advertised(job) -> tuple[int, int] | None:
    """The annual range an advert states, or None; Adzuna's estimates are not adverts."""
    stated = job["salary"] or ""
    found = figures(stated)
    if found and len(set(found)) == 1 and found[0] % 500 and job["source"] == "adzuna":
        return None  # Adzuna's predicted salary: one exact figure that no employer wrote
    if not found:
        found = figures(job["description"] or "")
    return (min(found), max(found)) if found else None


def adverts(con, job, name: str) -> list[int]:
    """Mid-points of the recent junior adverts of one role family that Hal has seen in the job's region."""
    pattern = next(p for n, p, _ in FAMILIES if n == name)
    london = "london" in (job["location"] or "").lower()
    since = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).isoformat()
    seen, mids = set(), []
    for r in con.execute("""SELECT id, source, company, title, location, salary, description FROM jobs
                            WHERE (salary LIKE '%GBP%' OR salary LIKE '%\u00a3%') AND first_seen >= ?""",
                         (since,)).fetchall():
        title = r["title"] or ""
        if r["id"] == job["id"] or not JUNIOR.search(title) or SENIOR.search(title):
            continue
        if family(title)[0] != name and not re.search(pattern, title, re.I):
            continue
        if ("london" in (r["location"] or "").lower()) != london:
            continue
        span = advertised(r) if r["salary"] else None
        if not span or span[1] > 2 * span[0]:
            continue  # "GBP 25,000 to GBP 78,000" says nothing about the level
        key = ((r["company"] or "").lower(), title.lower())
        if key not in seen:
            seen.add(key)
            mids.append((span[0] + span[1]) // 2)
    return mids


def parse_itjobswatch(page: str, london: bool) -> tuple[int, int] | None:
    """(median, salaries quoted) for the last six months from an ITJobsWatch job title page, or None."""
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))
    quotes = re.search(r"Number of salaries quoted (\d[\d,]*)", text)
    # The table's first column is the six months to date; outside London the page's own
    # "UK excluding London" row is the one that fits.
    row = r"Median annual salary \(50 th Percentile\) \u00a3([\d,]+)" if london else \
        r"UK excluding London median annual salary \u00a3([\d,]+)"
    median = re.search(row, text) or re.search(r"Median annual salary \(50 th Percentile\) \u00a3([\d,]+)", text)
    if not (quotes and median):
        return None
    return int(median.group(1).replace(",", "")), int(quotes.group(1).replace(",", ""))


def itjobswatch(phrase: str, london: bool) -> tuple[int, int] | None:
    """ITJobsWatch's (median, salaries quoted) for a job title; None when the page has no figures or cannot be read."""
    url = ITJW.format(place="london" if london else "uk", phrase=urllib.parse.quote(phrase))
    if url not in _PAGES:
        try:
            _PAGES[url] = parse_itjobswatch(get(url, headers=BROWSER_UA, timeout=20), london)
        except Exception:  # noqa: BLE001 - a page that will not load is a source missing, not a failure
            _PAGES[url] = None
    return _PAGES[url]


def _round(n: float, to: int = 1000) -> int:
    return int(round(n / to) * to)


def _sources(con, job, name: str, phrases: list[str], london: bool, where: str) -> list[tuple[int, int, str]]:
    """(median, salaries behind it, description) from ITJobsWatch and from Hal's adverts, for one role family."""
    out, best = [], None
    for phrase in phrases if _technical() else []:
        found = itjobswatch(phrase, london)
        if found and (best is None or found[1] > best[1][1]):
            best = (phrase, found)
        if found and found[1] >= ENOUGH:
            break
    if best:
        phrase, (median, quotes) = best
        out.append((median, quotes, f"ITJobsWatch median for {phrase} in {where}: \u00a3{median:,} "
                                    f"({quotes} salaries, last 6 months)"))
    mids = adverts(con, job, name)
    if mids:
        median = int(statistics.median(mids))
        out.append((median, len(mids), f"{len(mids)} similar junior {name} adverts Hal has seen in {where}: "
                                       f"median \u00a3{median:,}"))
    return out


def _technical() -> bool:
    """True when the person's jobs are technical ones, which ITJobsWatch's figures describe."""
    from .core import settings

    return settings.get("scoring.preset") == TECHNICAL_PRESET


def _floor() -> int:
    """The lowest pay the person will consider (`goal.min_pay`), 0 when they set none."""
    from .core import settings

    return int(settings.get("goal.min_pay") or 0)


def expect(job, con=None) -> dict:
    """The salary to ask for: `figure`, the form answer `text`, and the `basis` it rests on."""
    own = con is None
    con = con or db.connect()
    try:
        name, phrases = family(job["title"])
        london = "london" in (job["location"] or "").lower()
        where = "London" if london else "the UK outside London"
        found = _sources(con, job, name, phrases, london, where)
        solid = [f for f in found if f[1] >= ENOUGH]
        basis = []
        if not solid and name != FAMILIES[-1][0]:
            # Too few salaries for this kind of role: junior software roles as a whole are the nearest market.
            general = _sources(con, job, FAMILIES[-1][0], FAMILIES[-1][2], london, where)
            solid = [f for f in general if f[1] >= ENOUGH]
            if solid:
                basis.append(f"too few salaries for junior {name} roles, so junior software roles as a whole")
        if not solid:
            solid = [f for f in found if f[1] >= FEW]
            if solid:
                basis.append("few salaries to go on; check this")
        basis += [f[2] for f in solid]
        market = statistics.mean(f[0] for f in solid) if solid else None
        span = advertised(job)
        floor = _floor()
        if span:
            low, high = span
            basis.insert(0, f"the advert states \u00a3{low:,}" + (f" to \u00a3{high:,}" if high != low else ""))
            figure = _round(min(max(market, low), high), 500) if market else _round((low + high) / 2, 500)
            bottom, top = max(low, _round(figure * 0.95)), min(high, _round(figure * 1.05))
        elif market:
            figure = _round(market)
            bottom, top = _round(figure * 0.95), _round(figure * 1.05)
        else:
            figure = bottom = floor
            top = _round(floor * 1.1)
            basis.append(f"no salary data found, so your own floor of \u00a3{floor:,}; check this" if floor
                         else "no salary data found and no lowest pay set; check this")
        if figure < floor and not span:
            basis.append(f"below your \u00a3{floor:,} floor")
        text = f"\u00a3{figure:,}" if top <= bottom else f"\u00a3{bottom:,} to \u00a3{top:,}"
        return {"figure": figure, "text": text, "basis": "; ".join(basis)}
    finally:
        if own:
            con.close()


def band(options: list[str], figure: int) -> str | None:
    """The option of a salary list whose band holds `figure`: "GBP 30,000 - 34,999", "35k+", "Up to 25,000"."""
    for opt in options:
        found = figures(opt)
        if not found:
            continue
        low, high = min(found), max(found)
        if len(found) == 1:
            if re.search(r"\+|above|over|more than|or more", opt, re.I):
                high = ANNUAL[1]
            elif re.search(r"up to|under|below|less than", opt, re.I):
                low = 0
        if low <= figure <= high:
            return opt
    return None
