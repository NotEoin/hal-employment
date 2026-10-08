"""Rank jobs for Firstname: 0-100, with the reasons kept alongside.

A score of 0 means filtered out (wrong place, wrong level, wrong field); the
reason is still recorded, so a wrongly filtered job can be found and the rule
fixed. The tunable preferences are the constants at the top of this module.
"""
import re
from datetime import datetime, timezone
from pathlib import Path


SALARY_FLOOR = 30_000

EXCLUDED_FILE = Path(__file__).resolve().parent.parent / "excluded-companies.txt"
_SUFFIXES = re.compile(r"\b(ltd|limited|plc|inc|llc|group|holdings|technologies|technology|"
                       r"systems|uk|international|corporation|corp)\b")


def company_key(name: str) -> str:
    """Normalises a company name for exclusion matching."""
    return re.sub(r"[^a-z0-9]", "", _SUFFIXES.sub("", (name or "").lower()))


# Employers that name themselves after defence work, whether or not they are listed.
DEFENCE_NAME = re.compile(r"defen[cs]e|military|weapons?|munitions|armaments", re.I)
EXCLUDED = {company_key(line) for line in EXCLUDED_FILE.read_text().splitlines()
            if line.strip() and not line.startswith("#")} if EXCLUDED_FILE.exists() else set()

LONDON = r"\blondon\b|\bcity of london\b|\bcanary wharf\b|\bshoreditch\b|\bkings? cross\b"
# City and anywhere on or near the local tram.
CITY_AREA = r"\bcity\b|\btown\b|\bregion\b"
UK_REMOTE = r"remote[^/]{0,25}\b(uk|united kingdom|gb|england)\b|\b(uk|united kingdom)[^/]{0,10}remote|\bremote\s*\(?(uk|emea|europe)\)?"
# Remote with no country at all: worth a look, but often US-only.
REMOTE_ANY = r"\bremote\b|\banywhere\b"
# A remote role pinned to somewhere else is not open to a UK resident.
ELSEWHERE = (r"\b(north america|americas|usa?|united states|canada|latam|apac|asia|india|australia|"
             r"germany|france|spain|poland|portugal|netherlands|ireland|singapore|japan|brazil|mexico|uae|dubai|"
             r"israel|switzerland|sweden|denmark|paris|berlin|amsterdam|madrid|lisbon|warsaw|dublin|"
             r"new york|san francisco|toronto|[a-z]+, (ca|ny|tx|wa|ma|co|il))\b")

FIELD = re.compile(
    r"\b(software|developer|programmer|engineer(ing)?|backend|back[- ]end|full[- ]?stack|"
    r"platform|infrastructure|devops|site reliability|sre|systems|machine learning|ml|ai|"
    r"artificial intelligence|data|research software|rse|security|cyber|computer vision|"
    r"deep learning|llm|ktp associate|quant|python|java|golang|go|c\+\+|rust)\b", re.I)
NOT_FIELD = re.compile(
    r"\b(sales|account (executive|manager)|business development|recruit(er|ment)|talent|marketing|"
    r"designer|legal|counsel|finance|accountant|payroll|hr|people partner|customer (success|support)|"
    r"mechanical|electrical|civil|chemi(st|cal)|structural|hvac|field (service|engineer)|"
    r"technician|nurse|clinical|content|copywriter|writer|operations manager|executive assistant|"
    r"office|facilities|procurement|buyer|warehouse|driver|rigger|animator|artist|producer)\b", re.I)
TOO_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|head|director|manager|vp|vice president|chief|"
    r"architect|distinguished|fellow)\b|\b(iii|iv|l[5-9]|e[5-9])\b", re.I)
NOT_FOR_GRADS = re.compile(r"\b(intern(ship)?|placement|summer|spring week|apprentice(ship)?|"
                           r"year in industry|industrial placement|work experience|phd|professor|"
                           r"post-?doc(toral)?|lecturer|reader in)\b", re.I)
# Schemes for current students; a graduate of 2026 is usually ineligible.
STUDENTS_ONLY = re.compile(
    r"currently (enrolled|studying)|enrolled in a (full-time )?(bachelor|master|degree|undergraduate)|"
    r"graduating in 202[7-9]|expected to graduate in 202[7-9]|penultimate year|final[- ]year students?|"
    r"(class|cohort) of 202[7-9]|depending on (your )?graduation date", re.I)
ENTRY = re.compile(r"\b(graduate|grad|junior|jr\.?|entry[- ]level|early careers?|new grad|"
                   r"associate (software|engineer|developer)|engineer i\b|level 1|"
                   r"ktp associate|trainee|0-2 years|recent graduates?)\b", re.I)

# Weighted to what the portfolio and degree actually evidence.
SKILLS = {
    r"\bpython\b": 3, r"\bpytorch\b": 3, r"\bnumpy\b": 2, r"\bmachine learning\b": 2,
    r"\bcomputer vision\b": 3, r"\b(llm|rag|retrieval)\b": 3, r"\bmcp\b": 2,
    r"\bdistributed systems?\b": 3, r"\bconcurren(cy|t)\b": 2, r"\bcryptograph": 3,
    r"\b(security|secure)\b": 2, r"\bnetwork(ing)?\b": 1, r"\bjava\b": 2, r"\btypescript\b": 2,
    r"\breact\b": 1, r"\bfastapi|flask\b": 2, r"\bsql(ite)?\b|\bpostgres": 1, r"\blinux\b": 2,
    r"\bc\+\+\b|\b(in|and|or) c\b": 2, r"\bgo(lang)?\b": 1, r"\brest(ful)? api": 1,
    r"\bembedded|real[- ]time\b": 2, r"\brobotic|autonomous": 2, r"\bresearch\b": 1,
    r"\bpytest|testing\b": 1, r"\bgit\b": 1, r"\bmedical imaging|healthcare\b": 2,
}


YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|to|\u2013)?\s*(?:\d{1,2})?\s*\+?\s*years?['\u2019]?\s*(?:of\s+)?"
    r"(?:\w+[\s,/-]+){0,4}?experience", re.I)
MONEY = re.compile(r"\u00a3\s?(\d{2,3})(?:,(\d{3})|(k))", re.I)


def place(location: str, text: str) -> tuple[str | None, int]:
    loc = (location or "").lower()
    if re.search(CITY_AREA, loc):
        return "City", 10      # smaller market, far fewer applicants per role
    if re.search(LONDON, loc):
        return "London", 6
    if re.search(UK_REMOTE, loc):
        return "UK remote", 4
    if not loc.strip() or (re.search(REMOTE_ANY, loc) and not re.search(ELSEWHERE, loc)):
        # With no usable location field, fall back to places named in the description.
        body = (text or "")[:3000].lower()
        if re.search(CITY_AREA, body):
            return "City (from text)", 6
        if re.search(LONDON, body):
            return "London (from text)", 3
        if re.search(UK_REMOTE, body) or re.search(r"\b(uk|united kingdom)\b", body):
            return "UK remote (from text)", 2
    return None, 0


def min_years(text: str) -> int | None:
    found = [int(m.group(1)) for m in YEARS.finditer(text or "") if int(m.group(1)) < 20]
    return min(found) if found else None


def max_salary(text: str) -> int | None:
    vals = []
    for m in MONEY.finditer(text or ""):
        n = int(m.group(1)) * 1000 + (int(m.group(2)) if m.group(2) else 0)
        if 10_000 <= n <= 300_000:
            vals.append(n)
    return max(vals) if vals else None


def score(job: dict) -> tuple[float, str]:
    title = job["title"] or ""
    text = job.get("description") or ""
    notes = []

    if company_key(job.get("company")) in EXCLUDED or DEFENCE_NAME.search(job.get("company") or ""):
        return 0, "excluded employer"

    where, loc_pts = place(job.get("location"), text)
    if not where:
        return 0, f"location: {job.get('location') or 'unknown'}"
    notes.append(where)

    if job["source"] != "hn":       # HN 'titles' are free text; judged on the body below
        if NOT_FIELD.search(title) or not FIELD.search(title):
            return 0, f"not an engineering role: {title}"
        if TOO_SENIOR.search(title):
            return 0, "too senior"
        if NOT_FOR_GRADS.search(title):
            return 0, "internship/placement/apprenticeship/PhD"

    s = 40 + loc_pts
    if job["source"] == "hn" and TOO_SENIOR.search(title) and not ENTRY.search(text):
        s -= 15
        notes.append("seems senior-only")
    if ENTRY.search(title):
        s += 25
        notes.append("entry-level title")
    elif ENTRY.search(text[:4000]):
        s += 12
        notes.append("open to grads")

    if STUDENTS_ONLY.search(text):
        s -= 25
        notes.append("may require current students")

    yrs = min_years(text)
    if yrs is not None:
        if yrs >= 5:
            return 0, f"asks {yrs}+ years"
        if yrs >= 3:
            s -= 20
            notes.append(f"asks {yrs}+ yrs")
        elif yrs == 2:
            s -= 6
            notes.append("asks 2 yrs")

    skill_pts = sum(w for pat, w in SKILLS.items() if re.search(pat, text, re.I))
    s += min(skill_pts, 20)
    notes.append(f"skills +{min(skill_pts, 20)}")

    pay = max_salary(f"{job.get('salary') or ''}\n{text}")
    if pay and pay < SALARY_FLOOR:
        s -= 12
        notes.append(f"pay <= GBP {pay:,}")
    elif pay:
        notes.append(f"pay to GBP {pay:,}")

    # Small employers: fewer applicants, and a message reaches a decision-maker.
    size = job.get("board_size")
    if job["source"] in ("hn", "ktp"):
        s += 6
        notes.append("niche source")
    elif size is not None and size <= 15:
        s += 7
        notes.append(f"small ({size} roles open)")
    elif size is not None and size <= 40:
        s += 3

    if job.get("posted_at"):
        try:
            posted = datetime.fromisoformat(
                job["posted_at"].replace("Z", "+00:00").replace(" UTC", "+00:00"))
            if posted.tzinfo is None:  # KTP dates are naive UK local time
                posted = posted.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - posted).days
            if age <= 3:
                s += 6
                notes.append("fresh")
            elif age > 180:             # evergreen ad or stale board: rarely a live vacancy
                s -= 15
                notes.append(f"{age}d old")
            elif age > 45:
                s -= 8
                notes.append(f"{age}d old")
        except ValueError:
            pass

    return max(1, min(100, s)), ", ".join(notes)


def prefilter(job: dict) -> bool:
    """Cheap check used before fetching per-job details."""
    title = job["title"] or ""
    return bool(FIELD.search(title)) and not NOT_FIELD.search(title) and not (
        TOO_SENIOR.search(title)) and bool(
        place(job.get("location"), "")[0] or not job.get("location"))
