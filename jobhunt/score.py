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
    name = re.sub(r"\(.*?\)|https?://\S+", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", _SUFFIXES.sub("", name))


# Course and placement providers recruit trainees, often for a fee or a bond.
TRAINING_PROVIDER = re.compile(r"\btraining\b|\bacademy\b|bootcamp|career switch|\bupskill", re.I)
# Recruiters advertise for unnamed clients, so the employer can't be checked.
AGENCY = re.compile(r"recruit|staffing|resourcing|talent|search|consulting\b|\bnoir\b|hays|reed specialist|"
                    r"harvey nash|robert walters|michael page|computer futures|jefferson frank", re.I)
# Sectors Firstname will not work in, matched in the advert text.
DISREPUTABLE_WORK = re.compile(r"\b(tobacco|vap(e|ing) products|e-cigarettes?|payday loans?|high-cost (short-term )?"
                               r"credit|multi-level marketing|network marketing|firearms|ammunition)\b", re.I)
# Adverts for adult content, gambling work or bonded training schemes, at any employer.
ADULT_WORK = re.compile(r"\badult (content|entertainment|industry|creators?)\b|\b18\+ (content|platform)|"
                        r"\bnsfw\b|\bporn", re.I)
GAMBLING_WORK = re.compile(r"\b(igaming|sportsbook|online casino|casino games|betting (company|operator|"
                           r"platform|exchange|industry)|gambling (company|operator|industry))\b", re.I)
TRAINING_BOND = re.compile(r"training bond|repay (the )?(cost of )?(your )?training|training (cost|fee)s? "
                           r"(will be|are) (repayable|recoverable)|exit fee|minimum (service|commitment) "
                           r"(period|of \d+ (months|years))", re.I)
# Wording that means prior work experience is required. Negations are checked separately.
EXPERIENCE_REQUIRED = re.compile(
    r"\b(proven|significant|solid|demonstrable|substantial|extensive|previous|prior|relevant) "
    r"(commercial |professional |industry |hands-on |production )?experience (in|with|of|as|building|developing)|"
    r"\b(commercial|professional|industry) experience (in|with|of|as|building|developing|is required)|"
    r"\bextensive \w+ experience|\b(strong|solid),? proven experience|\btrack record of|"
    r"meaningful production ownership|\b(several|many|a few|multiple) years\b|\bmid[- ]level role|"
    r"experience level: (mid|senior|mid-senior)", re.I)
# Postgraduate degrees required; a BSc graduate does not qualify.
POSTGRAD_REQUIRED = re.compile(r"\b(msc|ph\.?d|master'?s( degree)?)\b[^.\n]{0,30}\b(in|required|degree)\b", re.I)
UNDERGRAD_OK = re.compile(r"\b(bsc|bachelor'?s?|undergraduate|first[- ]class|2[.:]1)\b", re.I)
NO_EXPERIENCE_NEEDED = re.compile(r"\b(no|without) (prior |previous |commercial |professional )*experience|"
                                  r"experience (is )?(not required|not necessary|a plus|a bonus|desirable|"
                                  r"is nice to have)", re.I)
# Employers that name themselves after defence work, whether or not they are listed.
DEFENCE_NAME = re.compile(r"defen[cs]e|military|weapons?|munitions|armaments|\barmy\b|\bnavy\b|"
                          r"royal air force|\braf\b|royal marines|armed forces|\bmod\b", re.I)
# Roles that need a security clearance the candidate would already have to hold.
CLEARANCE_HELD = re.compile(r"\b(sc|dv|ctc|esc)[- ]clear(ed|ance)\b|active (sc|dv) clearance|"
                            r"(hold|holding|have) (a |an )?(current |active |valid )?(sc|dv|security) clearance", re.I)
EXCLUDED = {company_key(line) for line in EXCLUDED_FILE.read_text().splitlines()
            if line.strip() and not line.startswith("#")} if EXCLUDED_FILE.exists() else set()

LONDON = r"\blondon\b|\bcity of london\b|\bcanary wharf\b|\bshoreditch\b|\bkings? cross\b"
# City and anywhere on or near the local tram.
CITY_AREA = r"\bcity\b|\btown\b|\bregion\b"
# Firstname wants an office: on-site is preferred, hybrid accepted, fully remote excluded.
FULLY_REMOTE = re.compile(r"fully remote|100% remote|remote[- ]first|remote[- ]only|work from anywhere|"
                          r"remote[- ]based|this is a remote (role|position)|workplace: remote|"
                          r"distributed (team|company)", re.I)
HYBRID = re.compile(r"\bhybrid\b|days? (a|per) week in (the |our )?office|days? in (the |our )?office|"
                    r"workplace: hybrid|flexible working", re.I)
ON_SITE = re.compile(r"\bon-?site\b|\bin[- ]office\b|office[- ]based|\bin[- ]person\b|"
                     r"5 days (a week )?in|five days (a week )?in|workplace: on_site", re.I)
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
    r"office|facilities|procurement|buyer|warehouse|driver|rigger|animator|artist|producer|"
    r"menswear|womenswear|fashion|garment|apparel|textile|footwear|product developer|geotechnical|"
    r"geological|surveyor|quantity surveyor|estimator)\b", re.I)
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
# Internships are listed separately, and only when graduates may apply.
INTERNSHIP = re.compile(r"\bintern(ship)?\b", re.I)
GRADUATES_WELCOME = re.compile(r"recent(ly)? graduat|have graduated|open to graduates|graduates (are )?welcome|"
                               r"off-cycle|graduate internship|graduated (in|within)|no longer (a )?student", re.I)
UNPAID = re.compile(r"\bunpaid\b|\bexpenses only\b|\bvoluntary\b", re.I)
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
    r"(?:\w+[\s,/-]+){0,4}?(?:experience|building|developing|of (?:software|engineering|industry|"
    r"commercial|professional|production|hands-on))", re.I)
MONEY = re.compile(r"\u00a3\s?(\d{2,3})(?:,(\d{3})|(k))", re.I)


def graduate_internship(title: str, text: str) -> bool:
    """True for a paid internship that the advert explicitly opens to graduates."""
    return bool(INTERNSHIP.search(title) and GRADUATES_WELCOME.search(text)
                and not STUDENTS_ONLY.search(text) and not UNPAID.search(text))


def place(location: str, text: str) -> tuple[str | None, int]:
    loc = (location or "").lower()
    if re.search(CITY_AREA, loc):
        return "City", 10      # smaller market, far fewer applicants per role
    if re.search(LONDON, loc):
        return "London", 6
    if not loc.strip() or (re.search(REMOTE_ANY, loc) and not re.search(ELSEWHERE, loc)):
        # With no usable location field, fall back to an office named in the description.
        body = (text or "")[:3000].lower()
        if re.search(CITY_AREA, body):
            return "City (from text)", 6
        if re.search(LONDON, body):
            return "London (from text)", 3
    return None, 0


def workplace(location: str, text: str) -> tuple[str | None, int | None]:
    """Returns the working pattern and its score adjustment; None means fully remote."""
    head = f"{location or ''}\n{(text or '')[:4000]}"
    if FULLY_REMOTE.search(head) and not (ON_SITE.search(head) or HYBRID.search(head)):
        return "remote", None
    if ON_SITE.search(head) and not HYBRID.search(head):
        return "on-site", 5
    if HYBRID.search(head) or re.search(REMOTE_ANY, (location or "").lower()):
        return "hybrid", -3
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
    if CLEARANCE_HELD.search(f"{title}\n{text}"):
        return 0, "needs existing security clearance"
    if TRAINING_PROVIDER.search(job.get("company") or ""):
        return 0, "training provider"

    where, loc_pts = place(job.get("location"), text)
    if not where:
        return 0, f"location: {job.get('location') or 'unknown'}"
    notes.append(where)

    if job["source"] != "hn":       # HN 'titles' are free text; judged on the body below
        if NOT_FIELD.search(title) or not FIELD.search(title):
            return 0, f"not an engineering role: {title}"
        if TOO_SENIOR.search(title):
            return 0, "too senior"
        if NOT_FOR_GRADS.search(title) and not graduate_internship(title, text):
            return 0, "internship/placement/apprenticeship/PhD"

    if DISREPUTABLE_WORK.search(text):
        return 0, "excluded sector"
    if ADULT_WORK.search(text):
        return 0, "adult content"
    if GAMBLING_WORK.search(text):
        return 0, "gambling"
    if TRAINING_BOND.search(text):
        return 0, "training bond"

    pattern, pattern_pts = workplace(job.get("location"), text)
    if pattern_pts is None:
        return 0, "fully remote"
    if pattern:
        notes.append(pattern)

    s = 40 + loc_pts + pattern_pts
    if graduate_internship(title, text):
        s -= 10
        notes.append("graduate internship")
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

    if AGENCY.search(job.get("company") or ""):
        notes.append("recruitment agency, employer unnamed")
    if job["source"] == "indeed":
        s -= 10
        notes.append("alert only, find the advert")

    yrs = min_years(text)
    if yrs is not None and yrs >= 1:
        return 0, f"asks {yrs}+ years"
    if EXPERIENCE_REQUIRED.search(text) and not NO_EXPERIENCE_NEEDED.search(text):
        return 0, "asks for prior experience"
    if POSTGRAD_REQUIRED.search(text) and not UNDERGRAD_OK.search(text):
        return 0, "asks for a postgraduate degree"

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
