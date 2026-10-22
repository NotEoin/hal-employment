"""Pay in an advert, as pounds a year.

Adverts give pay by the year, month, week, day or hour. Each figure is turned into pounds a year so that a
paid internship at an hourly rate can be compared with a graduate salary and with the person's lowest pay:
hourly x 37.5 x 52, daily x 5 x 52, weekly x 52, monthly x 12. `per` keeps what the advert said.

Only figures marked as pounds count (a pound sign or "GBP" before the number), as L score.py MONEY and
L salary.py did: a bare number in an advert is far more often a team size, a year or a bonus than pay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

POUND = "\u00a3"
HOURS_A_WEEK = 37.5        # a UK full-time week
WEEKS_A_YEAR = 52
DAYS_A_WEEK = 5
MONTHS_A_YEAR = 12
PER_YEAR = {"year": 1, "month": MONTHS_A_YEAR, "week": WEEKS_A_YEAR, "day": DAYS_A_WEEK * WEEKS_A_YEAR,
            "hour": HOURS_A_WEEK * WEEKS_A_YEAR}
# Below these a figure with no period word is read as hourly, daily or monthly rather than annual.
HOURLY_BELOW = 100
DAILY_BELOW = 1_000
MONTHLY_BELOW = 6_000
# Annual figures outside this are a typo, a bonus or a company's turnover, not someone's pay.
ANNUAL_RANGE = (5_000, 300_000)

NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,2})?)\s?(k)?"
MONEY = re.compile(rf"(?:{POUND}|\bGBP)\s?{NUMBER}(?:\s?(?:-|\u2013|to)\s?(?:{POUND}|GBP)?\s?{NUMBER})?", re.I)
PERIOD = re.compile(
    r"^\W{0,3}(?:(?:per|an|a|/)\s?(hour|hr|day|week|wk|month|annum|year|yr)\b|(hourly|daily|weekly|monthly|"
    r"annually|pro rata|p\.?a\.?|pcm|ph|p/h)\b)", re.I)
PERIOD_WORDS = {"hour": "hour", "hr": "hour", "hourly": "hour", "ph": "hour", "p/h": "hour", "day": "day",
                "daily": "day", "week": "week", "wk": "week", "weekly": "week", "month": "month",
                "monthly": "month", "pcm": "month", "annum": "year", "year": "year", "yr": "year",
                "annually": "year", "pa": "year", "p.a.": "year", "p.a": "year", "pro rata": "year"}
UNPAID = re.compile(r"\bunpaid\b|\bexpenses only\b|\bvoluntary (role|position|basis)\b|\bvolunteer role\b", re.I)
PAY_WINDOW = 4000          # characters of the advert read for pay, as for the kind


@dataclass(frozen=True)
class Pay:
    """Pay found in an advert: per-year figures and the period the advert used."""

    min: int | None
    max: int | None
    per: str | None          # year | month | week | day | hour
    paid: int | None         # 1 a figure was found, 0 unpaid, None not stated
    quote: str = ""


NO_PAY = Pay(None, None, None, None)


def _number(raw: str, k: str | None, thousands: bool) -> float:
    n = float(raw.replace(",", ""))
    if k or (thousands and n < 1000):
        n *= 1000
    return n


def _period(after: str, amount: float) -> str:
    m = PERIOD.match(after)
    if m:
        word = (m.group(1) or m.group(2)).lower()
        return PERIOD_WORDS.get(word, PERIOD_WORDS.get(word.rstrip("."), "year"))
    if amount < HOURLY_BELOW:
        return "hour"
    if amount < DAILY_BELOW:
        return "day"
    if amount < MONTHLY_BELOW:
        return "month"
    return "year"


def figures(text: str) -> list[tuple[int, str, str]]:
    """Each pay figure in `text` as (pounds a year, period the advert used, the words quoted)."""
    out = []
    for m in MONEY.finditer(text or ""):
        thousands = bool(m.group(2) or m.group(4))   # "30-35k": the k covers both figures
        amounts = [_number(m.group(i), m.group(i + 1), thousands) for i in (1, 3) if m.group(i)]
        per = _period(text[m.end():m.end() + 20], max(amounts))
        for amount in amounts:
            yearly = round(amount * PER_YEAR[per])
            if ANNUAL_RANGE[0] <= yearly <= ANNUAL_RANGE[1]:
                out.append((yearly, per, m.group(0).strip()))
    return out


def parse(salary: str | None, text: str | None) -> Pay:
    """The pay an advert states: the salary field first, then the advert text."""
    head = (text or "")[:PAY_WINDOW]
    found = figures(salary or "") or figures(head)
    if found:
        values = [f[0] for f in found]
        return Pay(min(values), max(values), found[0][1], 1, found[0][2])
    unpaid = UNPAID.search(f"{salary or ''}\n{head}")
    if unpaid:
        return Pay(None, None, None, 0, unpaid.group(0))
    return NO_PAY


def words(pounds: int) -> str:
    """A yearly figure in words for the screen: "24,000 pounds"."""
    return f"{pounds:,} pounds"
