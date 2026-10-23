"""What the engines do on a page, worked out from its notes.

Each function here takes a `NoteSet` and answers one question an engine asks at one hook: which address
holds the form, which words mark a closed advert, how long to wait, which frames to read, what to type
into a date box. With only the built-in notes active each answer is exactly what the engines had written
into them before notes existed; `tests/test_site_notes.py` proves that against the old constants, so each
engine hook can move onto these functions without changing what Hal does.

A note can only add to the Submit words of `apply/guard.py`, never take one away: submit words only ever
stop Hal.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from jobhunt.apply import guard
from jobhunt.sites.notes import NoteSet

PAGE_WORDS = ("apply", "success", "applied", "closed", "bot_check", "blocked", "code_wall")
DEFAULT_ANSWER = "Company careers page"   # the old HOW_HEARD fallback, kept if every how-heard note is off


def _words(pattern: str, extra: list) -> re.Pattern:
    """One pattern from a built-in pattern and a site's own words (each matched as a whole button name)."""
    parts = [p for p in [pattern] if p] + [rf"^\s*{re.escape(str(w).strip())}\s*$" for w in extra if str(w).strip()]
    return re.compile("|".join(parts) if parts else r"(?!)", re.I)


def application_url(job, notes: NoteSet, boards: list[dict] | None = None) -> str:
    """The page holding a job's application form, as best the notes know it (the old `application_url`)."""
    url = job["url"] or ""
    note = notes.first("open_url")
    if note is None:
        return url
    act = note.act
    base = url.split("?")[0].rstrip("/")
    if "append" in act:
        return url if base.endswith(act["append"]) else base + act["append"]
    rewrite = act.get("rewrite") or {}
    m = re.search(rewrite.get("from", r"(?!)"), url)
    if not m:
        return url
    to = rewrite.get("to", "")
    if "{board}" in to:
        if boards is None:
            from jobhunt.config import boards as listed

            boards = listed()
        slug = next((b["slug"] for b in boards if b.get("ats") == act.get("board") and b.get("name") == job["company"]),
                    None)
        if not slug:
            return url
        to = to.replace("{board}", slug)
    return m.expand(to)


def page_words(notes: NoteSet) -> dict[str, re.Pattern]:
    """The patterns for Apply buttons, thanks pages, closed adverts, human checks, blocks and code walls,
    plus Next and Submit from the guard; every `buttons` note on the page adds its words."""
    found = notes.all("buttons")
    base = {key: "" for key in PAGE_WORDS}
    extra: dict[str, list] = {key: [] for key in (*PAGE_WORDS, "next", "submit")}
    for n in reversed(found):   # least specific first, so a built-in pattern is the base
        for key in PAGE_WORDS:
            value = n.act.get(key)
            if isinstance(value, str) and not base[key]:
                base[key] = value
            elif isinstance(value, list):
                extra[key].extend(value)
        for key in ("next", "submit"):
            if isinstance(n.act.get(key), list):
                extra[key].extend(n.act[key])
    out = {key: _words(base[key], extra[key]) for key in PAGE_WORDS}
    out["next"] = guard.NEXT_WORDS if not extra["next"] else _words(guard.NEXT_WORDS.pattern, extra["next"])
    out["submit"] = guard.FINAL_WORDS if not extra["submit"] else _words(guard.FINAL_WORDS.pattern, extra["submit"])
    return out


def closed(survey: dict, status: int | None, notes: NoteSet) -> bool:
    """True when the page says the advert has closed (the old `_closed`)."""
    words = page_words(notes)
    head = f"{survey['title']} {' '.join(survey['headings'][:4])}"
    marks = [m for n in notes.all("buttons") for m in n.act.get("closed_url", [])]
    return words["closed"].search(head) is not None or status in (404, 410) \
        or any(m in survey["url"] for m in marks)


def wait_redirect(url: str, notes: NoteSet) -> dict | None:
    """The holding page to wait out: {host, max_s}, or None (the old Adzuna wait in `_settle`)."""
    note = notes.first("wait_redirect")
    if note is None:
        return None
    return {"host": note.act.get("host", ""), "until": note.act.get("until", "host_changes"),
            "max_s": float(note.act.get("max_s", 15))}


def frame_ok(frame_url: str, notes: NoteSet) -> bool:
    """True when a frame could hold the form (the old NOT_FORM_FRAME test in `_frames`)."""
    for n in notes.all("ignore_frame"):
        if any(e in frame_url for e in n.act.get("except", [])):
            continue
        if n.act.get("pattern") and re.search(n.act["pattern"], frame_url, re.I):
            return False
        if any(h in frame_url for h in n.act.get("host_contains", [])):
            return False
    return True


def form_hosts(notes: NoteSet) -> tuple[str, ...]:
    """The hosts whose frames hold an employer's embedded form (the old ATS_FRAME)."""
    return tuple(dict.fromkeys(h for n in reversed(notes.all("form_frame")) for h in n.act.get("host_contains", [])))


def cookie_patterns(notes: NoteSet) -> list[re.Pattern]:
    """The cookie buttons to try, in order: a site's own button first, then the built-in refusals and
    acceptances (the old COOKIE_BUTTONS). A site's `never` words are left out of every pattern."""
    found = notes.all("cookie_button")
    never = {str(w).strip().lower() for n in found for w in n.act.get("never", [])}
    out = [re.compile(rf"^\s*{re.escape(str(n.act['text']))}\s*$", re.I) for n in found if n.act.get("text")]
    for n in reversed(found):
        out += [re.compile(p, re.I) for p in n.act.get("patterns", [])]
    if never:
        out = [_without(p, never) for p in out]
    return out


def _without(pattern: re.Pattern, never: set[str]) -> re.Pattern:
    """A pattern that no longer matches the words in `never`."""
    guard_words = "|".join(re.escape(w) for w in sorted(never))
    return re.compile(rf"^(?!\s*({guard_words})\s*$)(?:{pattern.pattern})", re.I)


def no_escape(notes: NoteSet) -> bool:
    """True when open lists are closed by clicking the form, never with Escape."""
    return any(n.act.get("no_escape") for n in notes.all("widget"))


def timing(notes: NoteSet) -> dict:
    """Seconds to wait for a page, to let it settle and for a fill, and the Apply presses allowed; a more
    specific note's values win key by key over the built-in defaults."""
    out: dict = {}
    for n in reversed(notes.all("timing")):
        out.update(n.act)
    return out


def date_value(value: str, notes: NoteSet) -> str:
    """A date as a date box takes it, when it reads as one (the old `_date`)."""
    note = notes.first("field_transform", field_kind="date")
    if note is None:
        return value
    want = str(note.act.get("transform", "date:%Y-%m-%d")).removeprefix("date:")
    for fmt in note.act.get("read", []):
        try:
            return datetime.strptime(value.strip(), fmt).strftime(want)
        except ValueError:
            pass
    return value


def search_terms(value, notes: NoteSet) -> list[str]:
    """What to type into a searched list, in order (the old comma-split fallback in `_pick`)."""
    note = notes.first("field_transform", field_kind="combobox")
    steps = note.act.get("search", ["whole"]) if note else ["whole"]
    text = str(value)
    terms: list[str] = []
    for step in steps:
        if step == "whole":
            terms.append(text)
        elif step == "comma_parts":
            terms += [part.strip() for part in text.split(",") if part.strip()]
        elif step == "first_word" and text.split():
            terms.append(text.split()[0].strip(","))
    return list(dict.fromkeys(terms))


def transform(value: str, how: str) -> str:
    """Reshapes an answer for one field: city only, digits only, a UK phone number, a date or a length."""
    text = str(value)
    if how == "city_only":
        return text.split(",")[0].strip()
    if how == "digits_only":
        return re.sub(r"\D", "", text)
    if how == "uk_phone":
        digits = re.sub(r"\D", "", text)
        return "0" + digits[2:] if digits.startswith("44") else digits
    if how.startswith("date:"):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(text.strip(), fmt).strftime(how.removeprefix("date:"))
            except ValueError:
                pass
        return text
    if how.startswith("max_len:"):
        return text[: int(how.removeprefix("max_len:"))]
    return text


def how_heard(source: str, notes: NoteSet, label: str = "How did you hear about this job?") -> str:
    """The answer to "How did you hear about this job?" for a job from `source` (the old HOW_HEARD)."""
    note = notes.first("field_answer", label=label, source=source) or notes.first("field_answer", label=label)
    return str(note.act["value"]) if note else DEFAULT_ANSWER


def job_board(url: str, notes: NoteSet) -> bool:
    """True when the page is on a job board that shows other employers' adverts (the old AGGREGATORS)."""
    host = urlparse(url).netloc.lower()
    return any(n.act.get("job_board") for n in notes.all("note")) and bool(host)
