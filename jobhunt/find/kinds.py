"""What kind of role an advert is, its hours and contract, its pay and who may apply.

`classify` reads the title first, then the start of the advert, with word-bounded rules; the first kind in
`ORDER` that matches wins, so "Graduate Internship" is an internship and "Spring Insight Week" is an insight
programme. `eligible` then compares what the advert asks (current students, a year of study, a graduation
year) with the person's own course end. Both are pure: no database and no network.

The students-only wording is L score.py STUDENTS_ONLY without its hard-coded years: the years an advert names
are kept and compared with the person's own, so the rule never goes out of date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from jobhunt.find import pay as pay_mod

TEXT_WINDOW = 4000          # characters of the advert read after the title
PART_TIME_HOURS = 30        # under this many hours a week counts as part-time


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


# Primary kinds, first match wins. Each is (title pattern, text pattern); None means the title alone decides.
KIND_RULES = {
    "insight": (_rx(r"\bspring (week|insight)\b|\binsight (day|week|programme|program|event)s?\b|"
                    r"\bdiscovery (day|week|programme|program)s?\b|\bopen day for students\b|\bwork experience\b"),
                _rx(r"\bspring (week|insight)\b|\binsight (week|programme)\b")),
    "apprenticeship": (_rx(r"\bapprentice(ship)?s?\b|\bdegree apprenticeship\b|\blevel [2-7] apprenticeship\b"),
                       _rx(r"\b(degree|level [2-7]) apprenticeship\b")),
    "placement": (_rx(r"\bplacement( year)?\b|\byear in industry\b|\bindustrial placement\b|"
                      r"\bsandwich (year|placement)\b|\b12[- ]month placement\b"),
                  _rx(r"\bplacement year\b|\byear in industry\b|\bindustrial placement\b|"
                      r"\bsandwich (year|placement)\b")),
    "internship": (_rx(r"\bintern(ship)?s?\b|\bsummer\b|\boff[- ]cycle\b"),
                   _rx(r"\b(summer|graduate|paid) internship\b")),
    "graduate_scheme": (_rx(r"\bgraduate (scheme|programme|program|development programme|leadership programme)\b|"
                            r"\bfast stream\b|\bgraduate trainee scheme\b|\brotational programme\b"),
                        _rx(r"\bgraduate (scheme|programme|development programme)\b|\bfast stream\b")),
    "graduate_job": (_rx(r"\b(graduate|grad|junior|jr\.?|entry[- ]level|trainee|early careers?|new grad)\b|"
                         r"\b(assistant|associate)\b(?!.*\bsenior\b)"),
                     None),
    "experienced": (_rx(r"\b(senior|sr\.?|staff|principal|lead|head|director|manager|vp|vice president|chief|"
                        r"architect|distinguished|fellow)\b|\b(iii|iv|l[5-9]|e[5-9])\b|"
                        r"\b(phd|professor|post-?doc(toral)?|lecturer|reader in)\b"),
                    None),
}
ORDER = (*tuple(KIND_RULES), "other")
# Kinds a person chooses in the goal that are not primary kinds: they are flags on any primary kind.
FLAG_KINDS = ("part_time", "fixed_term")
LABELS = {"graduate_job": "Graduate and entry-level jobs", "graduate_scheme": "Graduate schemes",
          "internship": "Paid internships", "placement": "Placement years",
          "insight": "Insight weeks and spring weeks", "apprenticeship": "Apprenticeships",
          "experienced": "Jobs that ask for experience", "part_time": "Part-time jobs",
          "fixed_term": "Fixed-term jobs", "other": "Other jobs"}
ONE = {"graduate_job": "A graduate or entry-level job", "graduate_scheme": "A graduate scheme",
       "internship": "A paid internship", "placement": "A placement year",
       "insight": "An insight or spring week", "apprenticeship": "An apprenticeship",
       "experienced": "A job that asks for experience", "other": "A job"}

PART_TIME = _rx(r"\bpart[- ]time\b|\b0\.[1-7] fte\b|\b(\d{1,2}) hours (a|per) week\b")
FIXED = _rx(r"\bfixed[- ]term\b|\b\d{1,2}[- ]month (contract|role|position)\b|\bmaternity (cover|leave cover)\b|"
            r"\bsecondment\b")
TEMP = _rx(r"\btemporary\b|\btemp role\b")

YEAR = r"(20\d\d)"
CURRENT_STUDENTS = _rx(r"currently (enrolled|studying)|must be a current student|enrolled (in|on) an? [\w -]{0,40}"
                       r"(degree|course)|returning to (university|study|your studies)|current students? only|"
                       r"for current students")
YEAR_OF_STUDY = {
    "penultimate_year": _rx(r"\bpenultimate[- ]year (students?|undergraduates?)\b"),
    "first_year": _rx(r"\bfirst[- ]year (students?|undergraduates?)\b"),
    "final_year": _rx(r"\bfinal[- ]year (students?|undergraduates?)\b"),
}
GRADUATING = _rx(rf"(?:graduating|expected to graduate|due to graduate|finishing your (?:degree|studies)) "
                 rf"(?:in |by )?(?:summer |june |july )?{YEAR}(?:\s?(?:-|to|or|and)\s?{YEAR})?|"
                 rf"\bclass of {YEAR}\b|\bcohort of {YEAR}\b|\b{YEAR} (?:or|and|/) {YEAR} graduates\b|"
                 rf"\b{YEAR} graduates\b")
GRADUATES_WELCOME = _rx(r"recent(ly)? graduat|have graduated|open to graduates|graduates (are )?welcome|"
                        r"off-cycle|graduate internship|graduated (in|within)|no longer (a )?student|"
                        r"recent graduates")


@dataclass(frozen=True)
class Kind:
    """What one advert is: its primary kind, flags, pay and who may apply."""

    kind: str
    kind_quote: str
    hours: str | None             # part | None (full time unless stated)
    contract: str | None          # fixed | temp | None
    paid: int | None              # 1 paid, 0 unpaid, None not stated
    students_only: bool
    eligibility: dict = field(default_factory=dict)   # {requires, years, quote}; `eligible` adds the verdict
    pay: pay_mod.Pay = pay_mod.NO_PAY


def _kind(title: str, text: str) -> tuple[str, str]:
    for name, (in_title, _in_text) in KIND_RULES.items():
        m = in_title.search(title)
        if m:
            return name, m.group(0)
    for name, (_in_title, in_text) in KIND_RULES.items():
        m = in_text.search(text) if in_text is not None else None
        if m:
            return name, m.group(0)
    return "other", ""


def _hours(text: str) -> str | None:
    for m in PART_TIME.finditer(text):
        if m.group(1) is None or int(m.group(1)) < PART_TIME_HOURS:
            return "part"
    return None


def requirements(text: str) -> dict:
    """Who the advert says may apply: {requires, years, quote}, or {} when it says nothing."""
    years: list[int] = []
    for m in GRADUATING.finditer(text):
        years += [int(y) for y in m.groups() if y]
    if years:
        span = sorted(set(years))
        if len(span) == 2 and span[1] - span[0] > 1:
            span = list(range(span[0], span[1] + 1))
        quote = GRADUATING.search(text).group(0)
        return {"requires": "graduating_in", "years": span, "quote": quote}
    m = CURRENT_STUDENTS.search(text)
    if m:
        return {"requires": "current_students", "years": [], "quote": m.group(0)}
    for name, rx in YEAR_OF_STUDY.items():
        m = rx.search(text)
        if m:
            return {"requires": name, "years": [], "quote": m.group(0)}
    m = GRADUATES_WELCOME.search(text)
    if m:
        return {"requires": "graduates_welcome", "years": [], "quote": m.group(0)}
    return {}


def classify(title: str | None, text: str | None, salary: str | None = None) -> Kind:
    """The kind, hours, contract, pay and stated eligibility of one advert."""
    title = title or ""
    head = (text or "")[:TEXT_WINDOW]
    both = f"{title}\n{head}"
    kind, quote = _kind(title, head)
    contract = "fixed" if FIXED.search(both) else "temp" if TEMP.search(both) else None
    found = pay_mod.parse(salary, head)
    req = requirements(both)
    welcome = bool(GRADUATES_WELCOME.search(both))
    if welcome and req:
        req = {**req, "graduates_welcome": True}
    students_only = req.get("requires") in ("current_students", *YEAR_OF_STUDY) and not welcome
    return Kind(kind, quote, _hours(both), contract, found.paid, students_only, req, found)


def finished_year(study_end: str | None) -> int | None:
    """The year a course ends or ended, from "2026" or "2026-06"."""
    m = re.match(r"\s*(\d{4})", study_end or "")
    return int(m.group(1)) if m else None


def eligible(req: dict, *, student: bool, end_year: int | None) -> tuple[bool | None, str]:
    """Whether the person may apply, with the reason in plain words; None when the advert says nothing."""
    if not req or req.get("requires") == "graduates_welcome":
        return (True if req else None), ""
    welcome = req.get("graduates_welcome", False)
    requires = req["requires"]
    finished = f" You finished your course in {end_year}." if end_year else " You have finished your course."
    if requires == "graduating_in":
        years = req.get("years") or []
        if end_year is None or end_year in years:
            return True, ""
        if not student and welcome:
            return True, ""
        if not student and end_year < min(years):
            return False, f"For current students only. It is for people finishing in {years[0]}.{finished}"
        return False, f"For people finishing in {', '.join(map(str, years))}. Your course ends in {end_year}."
    if student or welcome:
        return True, ""
    return False, f"For current students only.{finished}"


def is_student(study_status: str, study_end: str | None, today: date) -> bool:
    """True while the person is studying and their course has not ended."""
    if study_status != "at_uni":
        return False
    end = finished_year(study_end)
    if end is None:
        return True
    month = re.match(r"\s*\d{4}-(\d{2})", study_end or "")
    return (end, int(month.group(1)) if month else 12) >= (today.year, today.month)
