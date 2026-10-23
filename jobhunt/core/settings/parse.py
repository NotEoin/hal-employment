"""Lenient readers for what people type into settings, and the checks every value passes.

`check(setting, raw)` is the whole pipeline the store runs before a write: the setting's own `parse` or the
reader for its kind, then its `validate`, then the range and choice checks. Every problem is a
`SettingError` whose words are shown under the field; nothing here raises a bare `ValueError`, because
setup once crashed on "22,000" (U setupcfg.py:173-174).

Design notes:
- Pay is always stored as whole pounds a year. Hourly pay is converted at 37.5 hours a week and 52 weeks,
  the same figures `find/pay.py` uses for adverts, so a typed minimum and an advert compare.
- Readers accept the value already in its stored form too (an int for money, a list for tags), so the API,
  the importers and the page can all call the same function.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date as Date
from typing import Any

from jobhunt.core.settings.registry import CHOICE_KINDS, Setting, SettingError

POUND = "\u00a3"
HOURS_A_WEEK = 37.5   # a UK full-time week, as find/pay.py converts adverts
WEEKS_A_YEAR = 52
DAYS_A_WEEK = 5
MONTHS_A_YEAR = 12
MIN_PAY = 1_000       # under this a typed pay is a slip, not a salary
MAX_PAY = 500_000
PAY_ROUNDING = 100    # converted pay is shown to the nearest hundred pounds
KM_PER_MILE = 1.609344
MONEY_HELP = "Hal could not read that. Try 24000 or 24k."

_PER = {
    "hour": HOURS_A_WEEK * WEEKS_A_YEAR,
    "day": DAYS_A_WEEK * WEEKS_A_YEAR,
    "week": WEEKS_A_YEAR,
    "month": MONTHS_A_YEAR,
    "year": 1,
}
_PER_WORDS = re.compile(
    r"(?:an?|per|/|each|every)\s*(?P<per>hour|hr|h|day|week|wk|month|mo|year|yr|annum)\b|"
    r"\b(?P<short>ph|pa|pw|pcm|hourly|daily|weekly|monthly|yearly|annually)\b", re.I)
_SHORT_PER = {"ph": "hour", "hourly": "hour", "daily": "day", "pw": "week", "weekly": "week", "pcm": "month",
              "monthly": "month", "pa": "year", "yearly": "year", "annually": "year"}
_LONG_PER = {"hr": "hour", "h": "hour", "wk": "week", "mo": "month", "yr": "year", "annum": "year"}
_AMOUNT = re.compile(r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<mult>k|thousand|grand)?$", re.I)
_TIME = re.compile(r"^(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ampm>am|pm)?$", re.I)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?$|^localhost(:\d+)?$|^\d{1,3}(\.\d{1,3}){3}(:\d+)?$", re.I)
_OFF = {"", "off", "none", "no", "never"}
_TRUE = {"true", "yes", "on", "1", "y"}
_FALSE = {"false", "no", "off", "0", "n", ""}


class Unreadable(Exception):
    """Raised by a reader; `check()` turns it into a `SettingError` for the field."""


def _text(raw: Any) -> str:
    return "" if raw is None else str(raw).strip()


def money(raw: Any) -> int:
    """Whole pounds a year from "24000", "24,000", "GBP 24k", "24 thousand" or "12.50 an hour"; 0 for none."""
    if isinstance(raw, bool):
        raise Unreadable(MONEY_HELP)
    if isinstance(raw, int | float):
        value, per = float(raw), "year"
    else:
        text = _text(raw).lower()
        if text in _OFF or text in ("0", "any"):
            return 0
        per = "year"
        m = _PER_WORDS.search(text)
        if m:
            word = (m["per"] or m["short"]).lower()
            per = _SHORT_PER.get(word) or _LONG_PER.get(word) or word
            text = text[:m.start()] + text[m.end():]
        text = text.replace(POUND, " ").replace(",", "")
        text = re.sub(r"\b(gbp|pounds?|salary|about|around|at least|a year|per year|or more)\b", " ", text)
        m = _AMOUNT.match(text.strip())
        if not m:
            raise Unreadable(MONEY_HELP)
        value = float(m["num"]) * (1000 if m["mult"] else 1)
    if value == 0:
        return 0
    yearly = round(value * _PER[per])
    if not MIN_PAY <= yearly <= MAX_PAY:
        raise Unreadable(MONEY_HELP)
    return yearly


SHOWN_PREFIX = "Hal reads this as "   # the field's own note; history lines leave it out


def show_money(value: Any) -> str:
    """Words for a yearly pay: "Hal reads this as 22,000 pounds a year" ("about" when it was converted)."""
    if not value:
        return "No lowest pay: Hal shows jobs whatever they pay."
    if value % PAY_ROUNDING:
        shown = round(value / PAY_ROUNDING) * PAY_ROUNDING
        return f"{SHOWN_PREFIX}about {shown:,} pounds a year"
    return f"{SHOWN_PREFIX}{value:,} pounds a year"


def miles(raw: Any) -> int:
    """Whole miles from "15", "15 miles", "15mi" or "25 km" (16)."""
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return round(raw)
    text = _text(raw).lower().replace(",", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(miles?|mi|km|kms|kilometres?|kilometers?)?$", text)
    if not m:
        raise Unreadable("Hal could not read that distance. Try 15 or 15 miles.")
    number = float(m[1])
    return round(number / KM_PER_MILE) if (m[2] or "").startswith("k") else round(number)


def _clock(text: str) -> str:
    m = _TIME.match(text.strip())
    if not m:
        raise Unreadable(f"Hal could not read the time {text.strip()!r}. Try 07:30.")
    hour, minute = int(m["h"]), int(m["m"] or 0)
    if m["ampm"]:
        if not 1 <= hour <= 12:
            raise Unreadable(f"Hal could not read the time {text.strip()!r}. Try 07:30.")
        hour = hour % 12 + (12 if m["ampm"].lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        raise Unreadable(f"{text.strip()!r} is not a time of day. Try 07:30.")
    return f"{hour:02d}:{minute:02d}"


def times(raw: Any) -> list[str]:
    """Times of day, earliest first: "07:30 and 13:30", "7.30, 13:30" or "7:30am" -> ["07:30", "13:30"]."""
    parts = raw if isinstance(raw, list | tuple) else re.split(r"\s*(?:,|;|\band\b|&|\n)\s*", _text(raw))
    found = sorted({_clock(str(p)) for p in parts if str(p).strip()})
    if not found:
        raise Unreadable("Give at least one time, such as 07:30.")
    return found


def hours(raw: Any) -> list[str] | None:
    """A span of the day: "22:00 to 07:30" -> ["22:00", "07:30"]; "Off" -> None."""
    if raw is None:
        return None
    if isinstance(raw, list | tuple):
        parts = [str(p) for p in raw]
    else:
        text = _text(raw)
        if text.lower() in _OFF:
            return None
        parts = re.split(r"\s*(?:\bto\b|\buntil\b|\btill\b|-|\u2013)\s*", text, flags=re.I)
    if len(parts) != 2:
        raise Unreadable("Hal could not read those hours. Try 22:00 to 07:30, or Off.")
    start, end = _clock(parts[0]), _clock(parts[1])
    if start == end:
        raise Unreadable("The start and end are the same time. Try 22:00 to 07:30, or Off.")
    return [start, end]


def show_hours(value: Any) -> str:
    """Words for a span of the day: "22:00 to 07:30", or "Off"."""
    return "Off" if not value else f"{value[0]} to {value[1]}"


def tags(raw: Any) -> list[str]:
    """Items split on commas and new lines, stripped, de-duplicated ignoring case, first spelling kept."""
    items = raw if isinstance(raw, list | tuple) else re.split(r"[,\n]", _text(raw))
    seen, out = set(), []
    for item in items:
        word = re.sub(r"\s+", " ", str(item)).strip()
        if word and word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    return out


def textlist(raw: Any) -> list[str]:
    """Non-empty lines, stripped; "# Section" lines are kept as they are."""
    lines = raw if isinstance(raw, list | tuple) else _text(raw).splitlines()
    return [str(line).strip() for line in lines if str(line).strip()]


def date(raw: Any) -> str:
    """An ISO date from "2026-09-01" or the UK "01/09/2026"; "" for none."""
    text = _text(raw)
    if not text:
        return ""
    try:
        m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", text)
        day = Date(int(m[3]), int(m[2]), int(m[1])) if m else Date.fromisoformat(text)
    except ValueError as exc:
        raise Unreadable("Hal could not read that date. Try 01/09/2026.") from exc
    return day.isoformat()


def phone(raw: Any) -> str:
    """A UK number spaced for reading ("07700 900123", "020 7946 0018"); other numbers kept as typed."""
    text = _text(raw)
    digits = re.sub(r"[\s().-]", "", text)
    if digits.startswith("+44"):
        digits = "0" + digits[3:].lstrip("0")
    if not re.fullmatch(r"0\d{10}", digits):
        if text and not re.fullmatch(r"\+?[\d\s().-]{6,20}", text):
            raise Unreadable("That does not look like a phone number.")
        return text
    if digits.startswith("02"):
        return f"{digits[:3]} {digits[3:7]} {digits[7:]}"
    return f"{digits[:5]} {digits[5:]}"


def email(raw: Any) -> str:
    """An email address, or "" for none."""
    text = _text(raw)
    if text and not _EMAIL.match(text):
        raise Unreadable("That does not look like an email address. Check it has an @ and a dot after it.")
    return text


def url(raw: Any) -> str:
    """A web address; "https://" is added when it is missing. "" for none."""
    text = _text(raw)
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    host = re.sub(r"^https?://", "", text, flags=re.I).split("/", 1)[0]
    if not _HOST.match(host):
        raise Unreadable("That does not look like a web address. Try https://example.org.")
    return text


def integer(raw: Any) -> int:
    """A whole number from "12", "12 points" or 12."""
    if isinstance(raw, bool):
        raise Unreadable("Give a whole number.")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    m = re.match(r"^(-?\d+)(?:\s*[a-z ]+)?$", _text(raw).replace(",", ""), re.I)
    if not m:
        raise Unreadable("Give a whole number.")
    return int(m[1])


def boolean(raw: Any) -> bool:
    """On or off from a bool, "yes"/"no", "on"/"off" or "true"/"false"."""
    if isinstance(raw, bool):
        return raw
    text = _text(raw).lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise Unreadable("Choose on or off.")


def text_value(raw: Any) -> str:
    """Plain text, trimmed."""
    if isinstance(raw, dict | list | tuple):
        raise Unreadable("Type plain words here.")
    return _text(raw)


READERS: dict[str, Callable[[Any], Any]] = {
    "text": text_value, "path": text_value, "email": email, "phone": phone, "url": url, "int": integer,
    "bool": boolean, "money": money, "times": times, "hours": hours, "date": date, "tags": tags,
    "ordered_tags": tags, "textlist": textlist,
}


def _choice(setting: Setting, raw: Any) -> Any:
    """The stored value for a choice, given either the stored value or the words on screen."""
    if raw == setting.default:
        return raw   # the declared default ("" for "not chosen yet") is always allowed
    if not setting.choices:
        value = _text(raw)
        if not value:
            raise SettingError(setting.key, "Choose one of the options.")
        return value
    for value, words in setting.choices:
        if raw == value or _text(raw) == str(value) or _text(raw).lower() == words.lower():
            return value
    raise SettingError(setting.key, "Choose one of the options.")


def _range_value(setting: Setting, raw: Any) -> int:
    if setting.unit == "miles":
        return miles(raw)
    return integer(raw)


def read(setting: Setting, raw: Any) -> Any:
    """The stored value for what was typed, without range checks; raises `SettingError`."""
    try:
        if setting.parse is not None:
            return setting.parse(raw)
        if setting.kind in CHOICE_KINDS:
            if setting.kind == "multi":
                items = raw if isinstance(raw, list | tuple) else tags(raw)
                return list(dict.fromkeys(_choice(setting, item) for item in items))
            return _choice(setting, raw)
        if setting.kind == "range":
            return _range_value(setting, raw)
        reader = READERS.get(setting.kind)
        if reader is None:
            raise SettingError(setting.key, "This setting cannot be changed here.")
        return reader(raw)
    except Unreadable as exc:
        raise SettingError(setting.key, str(exc)) from None


def check(setting: Setting, raw: Any) -> Any:
    """Parses, validates and range-checks a value for `setting`; returns what to store or raises `SettingError`."""
    value = read(setting, raw)
    if setting.validate is not None:
        problem = setting.validate(value)
        if problem:
            raise SettingError(setting.key, problem)
    if isinstance(value, int | float) and not isinstance(value, bool):
        unit = f" {setting.unit}" if setting.unit else ""
        low = f"{setting.min:g}" if setting.min is not None else ""
        high = f"{setting.max:g}" if setting.max is not None else ""
        if (setting.min is not None and value < setting.min) or (setting.max is not None and value > setting.max):
            words = f"between {low} and {high}" if low and high else f"at least {low}" if low else f"at most {high}"
            raise SettingError(setting.key, f"Choose a number {words}{unit}.")
    return value


def describe(setting: Setting, value: Any) -> str:
    """Words for a stored value, as the page shows them under the field."""
    if setting.show is not None:
        return setting.show(value)
    if setting.kind == "money":
        return show_money(value)
    if setting.kind == "hours":
        return show_hours(value)
    if setting.kind in ("choice", "seg"):
        return next((words for v, words in setting.choices if v == value), str(value))
    if setting.kind == "bool":
        return "On" if value else "Off"
    if setting.kind == "multi" and isinstance(value, list):
        words = dict(setting.choices)
        return ", ".join(words.get(v, str(v)) for v in value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if setting.kind != "times" else " and ".join(value)
    return "" if value is None else str(value)
