"""The Checks tab: how job-site scanners read a CV, the checks of its CV type, a recruiter's read, and the
CV against one job.

- The scanner score uses fixed, published weights (`SCANNER`, 100 in all) and no AI, so the same CV and look
  always get the same number. It reads the printed PDF through `pdfread` when there is one, else the
  document's own text in the order a one-column print puts it.
- Type checks are plain functions keyed by the ids a CV type's `[checks]` table names; a weight of 0
  switches a check off. They run on the CV as the type shows it (`types.arrange`), so a humanities CV is
  judged by humanities limits and a technical CV by technical ones.
- "Fix it" never touches a locked section, entry or line.
- Results are cached in `cv_checks` by version and look, keyed also by the type, the profile and the
  spelling setting, so a GET never redoes the work and any change that matters redoes it.
- A recruiter's read is the AI's opinion, kept apart from every score.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobhunt import cvscore
from jobhunt.cvstudio import types as cvtypes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Part:
    """One part of the scanner score."""

    id: str
    name: str
    points: int


# The published weights; they add up to 100.
SCANNER = (Part("readable", "Text can be read", 30), Part("headings", "Standard headings", 20),
           Part("dates", "Dates on every entry", 15), Part("contact", "Contact details found", 15),
           Part("columns", "One column", 10), Part("symbols", "Symbols scanners keep", 10))
SCANNER_TOTAL = 100
MIN_WORDS = 30                   # fewer words read back than this means the text layer is missing
MAX_SYMBOL_SHARE = 0.05          # Textkernel treats text with more symbols than this as unreadable
WIDE_LINES = 6                   # lines with text in separate columns before a PDF counts as two columns
TOP_CHARS = 700                  # how far down the first page "at the top" reaches
EMAIL_POINTS, PHONE_POINTS = 8, 7
SYMBOL_POINTS = (10, 7, 5, 2)    # points kept with 0, 1, 2 or 3 dropped symbols; none from 4 on
WORDS_PER_PAGE = 500             # a one-column A4 CV at 10 to 11 pt holds about 450 to 550 words
RESULT_SHARE = 0.3               # share of lines with a result before "Bullets show a result" passes
BACKED_SHARE = 0.7               # share of listed skills shown in use before "Each skill is shown in use" passes
DATED_KINDS = ("education", "experience", "volunteering", "research", "teaching")
DOING_KINDS = ("experience", "volunteering", "projects", "research", "portfolio", "achievements", "teaching")
RECRUITER_TIMEOUT_S = 600
RECRUITER_CTX = 16384

# Headings scanners map to a section; the type's own titles are among them.
STANDARD_HEADINGS = cvscore.STANDARD_HEADINGS | {
    "key skills", "strengths", "teaching", "talks", "conference talks", "presentations", "portfolio",
    "selected work", "work and activities", "volunteer experience", "personal statement", "qualifications",
    "activities and positions of responsibility", "certificates", "awards and achievements"}
SAFE_EXTRA = {0x2022}            # a bullet glyph at the start of a line is drawn by the look, not typed
# What scanners drop, and what "Fix it" writes instead; everything else in these classes is removed.
SYMBOL_SWAPS = {0x2022: ",", 0x00B7: ",", 0x25CF: ",", 0x25AA: ",", 0x2192: "to", 0x2794: "to", 0x27A4: "to",
                0x2605: "", 0x2713: "", 0x2714: "", 0x260E: "", 0x2709: ""}
RESULT_WORDS = re.compile(r"\b(so that|which|leading to|resulting in|raised|cut|won|grew|saved|increased|"
                          r"reduced|improved|published|exhibited|commissioned|chosen|selected|awarded|praised)\b",
                          re.I)
DISSERTATION = re.compile(r"\b(dissertation|thesis|final[- ]year project|research project|capstone)\b", re.I)
RIGHT_TO_WORK = re.compile(r"right to work|no (visa )?sponsorship|settled status|british citizen|uk citizen", re.I)
AVAILABLE = re.compile(r"\bavailab|\bfree (to start|from)|\bplacement year\b|\b\d{1,2}[- ]month|\b\d{1,2} weeks\b|"
                       r"\bsummer 20\d\d\b|\bfrom (january|february|march|april|may|june|july|august|september|"
                       r"october|november|december)\b", re.I)
FIRST_PERSON = re.compile(r"\b(I|my|me|we|our)\b")
BRITISH = re.compile(r"\b(optimis\w*|analys(e|ed|ing)|organis\w*|prioritis\w*|colour\w*|behaviour\w*|"
                     r"modelling|modelled|centre|centres|defence|catalogue)\b", re.I)
URL = re.compile(r"(https?://\S+|\bwww\.\S+|\b[\w-]+\.(com|co\.uk|org|net|io|art|design|uk)(/\S*)?)", re.I)
MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
DATE_FORMS = (("month_short", rf"^{MONTHS}\.? \d{{4}}$"), ("month_long", r"^[a-z]{4,9} \d{4}$"),
              ("year", r"^\d{4}$"), ("numeric", r"^\d{1,2}/\d{2,4}$"))
TEXT_FIELDS = ("title", "text", "heading", "org", "location", "grade", "meta", "also", "headline", "closing", "name")

CHECK_NAMES = {
    "profile_evidence": "Profile shows evidence, not qualities",
    "result_bullets": "Bullets show a result where there is one",
    "dissertation_shown": "Dissertation and strong modules shown",
    "modules_strong": "Only strong modules shown",
    "dates_one_format": "Dates in one format",
    "no_personal_data": "No photo, age or marital status",
    "one_spelling": "One spelling throughout",
    "entry_says_what": "Each entry says what you did",
    "contact_at_top": "Contact details at the top",
    "projects_with_evidence": "Each project shows what came of it",
    "education_first": "Education comes first",
    "skills_backed": "Each skill is shown in use",
    "right_to_work_line": "Says you can work in the UK",
    "no_module_codes": "Modules named in words, not codes",
    "no_first_person": "No I, my or we in entries",
    "portfolio_link": "Portfolio link at the top",
    "newest_first": "Jobs listed newest first",
    "availability_line": "Says when you are free to start",
}
PAGES_NAMES = {1: "One page", 2: "Two pages or fewer"}


# Reading a document ------------------------------------------------------------------------------------------

def _sections(doc: Any) -> list:
    return [s for s in doc.sections if s.visible]


def _entries(section: Any) -> list:
    return [e for e in section.entries if not e.hidden]


def entry_lines(entry: Any) -> list[str]:
    """An entry's lines: its bullets, else the sentences of its paragraph."""
    if entry.bullets:
        return [b.text.strip() for b in entry.bullets if b.text.strip()]
    return cvscore.lines_of(entry.text)


def _lines_of_kinds(doc: Any, kinds: tuple[str, ...]) -> list[str]:
    return [line for s in _sections(doc) if s.kind in kinds for e in _entries(s) for line in entry_lines(e)]


def _dates(entry: Any) -> str:
    return " - ".join(x for x in (entry.start, entry.end) if x)


def doc_lines(doc: Any, profile: dict) -> list[str]:
    """The CV's text in the order a one-column print puts it: name and contact details, then each section."""
    contact = [profile.get(k, "") for k in ("email", "phone", "city")] + list((profile.get("links") or {}).values())
    out = [profile.get("name", ""), " | ".join(c for c in contact if c), doc.headline]
    for s in _sections(doc):
        out += [s.title.upper(), s.text]
        for e in _entries(s):
            out += [" | ".join(x for x in (e.heading, e.org, e.location, _dates(e)) if x), e.grade, e.meta, e.link]
            out += [*entry_lines(e), ", ".join(e.items)]
            out += [f"{m.name} {m.mark}".strip() for m in e.modules]
        out.append(s.also)
    out.append(doc.closing)
    return [line for line in (x.strip() for x in out if x) if line]


def _unsafe(ch: str) -> bool:
    cp = ord(ch)
    if cp < 0x80:
        return False
    cat = unicodedata.category(ch)
    return cat in ("So", "Co", "Cn", "Cs") or (cat == "Sm" and cp > 0x2000) or cp in SYMBOL_SWAPS


def symbols_in(text: str) -> list[str]:
    """Characters in the text that some scanners drop: icons, emoji, arrows, ticks and mid-line bullets."""
    return [ch for ch in text if _unsafe(ch)]


# The scanner score ---------------------------------------------------------------------------------------

def _part(p: Part, got: float, note: str, fix_id: str | None = None) -> dict:
    got = max(0, min(p.points, round(got)))
    return {"id": p.id, "name": p.name, "got": got, "max": p.points, "note": note,
            "fix_id": fix_id if got < p.points else None}


def scanner(doc: Any, profile: dict, *, pdf: Path | bytes | None = None, columns: int = 1) -> dict:
    """The scanner score out of 100 and its six parts; no AI, the same CV and look always give the same score."""
    parts = {p.id: p for p in SCANNER}
    lines = doc_lines(doc, profile)
    text = "\n".join(lines)
    raw = cvscore.pdf_text(pdf) if pdf is not None else text
    words = raw.split()
    odd = cvscore.ODD_CHAR.findall(raw)
    share = sum(1 for c in raw if not (c.isalnum() or c.isspace() or c in ".,;:-()&/%'+#@|")) / max(1, len(raw))
    readable = 0.0 if len(words) < MIN_WORDS else 1.0 if not odd and share < MAX_SYMBOL_SHARE else 0.5
    out = [_part(parts["readable"], 30 * readable,
                 "Scanners can read every section." if readable == 1 else
                 f"Only {len(words)} words could be read." if len(words) < MIN_WORDS else
                 "Some letters read back as symbols, so parts of it may be lost.")]

    titles = [s.title.strip() for s in _sections(doc) if s.title.strip()]
    odd_titles = [t for t in titles if t.lower() not in STANDARD_HEADINGS]
    got = 20 * (len(titles) - len(odd_titles)) / len(titles) if titles else 0
    out.append(_part(parts["headings"], got,
                     "Every heading is one scanners know." if not odd_titles else
                     f"Scanners may not know: {', '.join(odd_titles)}." if titles else "There are no section headings.",
                     "headings" if odd_titles else None))

    dated_entries = [e for s in _sections(doc) if s.kind in DATED_KINDS for e in _entries(s)]
    dated = [e for e in dated_entries if re.search(r"\b(19|20)\d{2}\b|present", _dates(e), re.I)]
    got = 15 * len(dated) / len(dated_entries) if dated_entries else 15
    out.append(_part(parts["dates"], got,
                     "Every job and qualification has dates." if len(dated) == len(dated_entries) else
                     f"{len(dated_entries) - len(dated)} of {len(dated_entries)} entries have no dates."))

    top = (raw if pdf is not None else text)[:TOP_CHARS]
    email = bool(cvscore.EMAIL.search(top))
    phone = bool(cvscore.PHONE.search(top)) or bool(re.search(r"\+?\d[\d ]{9,}\d", top))
    missing = [w for w, ok in (("email", email), ("phone number", phone)) if not ok]
    out.append(_part(parts["contact"], EMAIL_POINTS * email + PHONE_POINTS * phone,
                     "Your email and phone number are at the top." if not missing else
                     f"No {' or '.join(missing)} at the top. Add it in Settings, You."))

    wide = 0
    if pdf is not None:
        wide = sum(1 for line in cvscore.pdf_text(pdf, layout=True).splitlines() if re.search(r"\S\s{6,}\S", line))
    one = columns == 1 and wide < WIDE_LINES
    out.append(_part(parts["columns"], 10 if one else 0,
                     "One column, read in order." if one else
                     "Scanners read two columns across the page, mixing them. "
                     "Choose a look that is safe for job sites."))

    dropped = symbols_in(text)
    kept = SYMBOL_POINTS[len(dropped)] if len(dropped) < len(SYMBOL_POINTS) else 0
    out.append(_part(parts["symbols"], kept,
                     "No symbols that scanners drop." if not dropped else
                     f"{len(dropped)} {'character' if len(dropped) == 1 else 'characters'} in your CV may be dropped "
                     "by some scanners.", "symbols" if dropped else None))
    return {"score": sum(p["got"] for p in out), "parts": out}


# Checks for the CV type -------------------------------------------------------------------------------------

@dataclass
class _In:
    doc: Any
    type_: cvtypes.CVType
    profile: dict
    text: str
    pages: int | None
    spelling: str


def _summary(c: _In) -> str:
    return " ".join(s.text for s in _sections(c.doc) if s.kind == "summary").strip()


def _profile_evidence(c: _In) -> tuple[bool, str]:
    words = _summary(c)
    if not words:
        return True, "There is no profile."
    banned = [b for b in c.type_.writing.get("banned", []) if re.search(rf"\b{re.escape(b)}\b", words, re.I)]
    found = sorted({m.group(0).lower() for m in cvscore.CLICHES.finditer(words)} | {b.lower() for b in banned})
    most = (c.type_.sections.get("summary") or {}).get("max_words")
    if found:
        return False, f"It describes qualities ({', '.join(found)}) instead of what you did."
    if most and len(words.split()) > most:
        return False, f"{len(words.split())} words; this type keeps it to {most}."
    return True, "It says what you have done."


def _result_bullets(c: _In) -> tuple[bool, str]:
    lines = _lines_of_kinds(c.doc, DOING_KINDS)
    if not lines or c.type_.writing.get("results") == "rarely":
        return True, ""
    shown = [x for x in lines if cvscore.NUMBER.search(x) or RESULT_WORDS.search(x)]
    ok = len(shown) / len(lines) >= RESULT_SHARE
    return ok, f"{len(shown)} of {len(lines)} lines say what came of the work."


def _dissertation_shown(c: _In) -> tuple[bool, str]:
    edu = [e for s in _sections(c.doc) if s.kind == "education" for e in _entries(s)]
    if not edu:
        return False, "There is no education section."
    first = edu[0]
    if not DISSERTATION.search(" ".join([first.heading, first.meta, first.text, *entry_lines(first)])):
        return False, "Your dissertation or final-year project is not on your CV."
    return True, ""


def _modules_strong(c: _In) -> tuple[bool, str]:
    floor = (c.type_.sections.get("education") or {}).get("min_mark", 0)
    weak = [m.name for s in _sections(c.doc) if s.kind == "education" for e in _entries(s) for m in e.modules
            if cvtypes._mark(m) is not None and cvtypes._mark(m) < floor]
    return not weak, f"Under {floor}: {', '.join(weak)}." if weak else ""


def _date_form(value: str) -> str:
    v = value.strip().lower()
    if not v or v in ("present", "now", "current"):
        return ""
    return next((name for name, rx in DATE_FORMS if re.match(rx, v)), "other")


def _dates_one_format(c: _In) -> tuple[bool, str]:
    forms = {f for s in _sections(c.doc) for e in _entries(s) for f in (_date_form(e.start), _date_form(e.end)) if f}
    return len(forms) <= 1, "" if len(forms) <= 1 else "Some dates have months and some do not, or use numbers."


def _no_personal_data(c: _In) -> tuple[bool, str]:
    found = sorted({m.group(0).lower() for m in cvscore.PRIVATE.finditer(c.text)})
    return not found, f"Remove: {', '.join(found)}." if found else ""


def _pages_within(c: _In) -> tuple[bool, str]:
    if not c.type_.pages:
        return True, ""
    pages = c.pages if c.pages is not None else max(1, -(-len(c.text.split()) // WORDS_PER_PAGE))
    how = "" if c.pages is not None else "about "
    return pages <= c.type_.pages, f"{how}{pages} {'page' if pages == 1 else 'pages'}."


def _one_spelling(c: _In) -> tuple[bool, str]:
    wrong = cvscore.AMERICAN if c.spelling == "en-GB" else BRITISH
    found = sorted({m.group(0).lower() for m in wrong.finditer(c.text)})
    side = "US" if c.spelling == "en-GB" else "UK"
    return not found, f"{side} spellings: {', '.join(found)}." if found else ""


def _entry_says_what(c: _In) -> tuple[bool, str]:
    empty = [e.heading for s in _sections(c.doc) if s.kind in ("experience", "volunteering", "projects")
             for e in _entries(s) if not entry_lines(e)]
    duties = [x for x in _lines_of_kinds(c.doc, DOING_KINDS)
              if cvscore.DUTIES.search(x) or cvscore.WEAK_VERBS.search(x)]
    if empty:
        return False, f"Nothing under: {', '.join(empty)}."
    return not duties, f"{len(duties)} lines list duties instead of what you did." if duties else ""


def _contact_at_top(c: _In) -> tuple[bool, str]:
    top = c.text[:TOP_CHARS]
    ok = bool(cvscore.EMAIL.search(top)) and bool(c.profile.get("phone") or cvscore.PHONE.search(top))
    return ok, "" if ok else "Add your email and phone number in Settings, You."


def _projects_with_evidence(c: _In) -> tuple[bool, str]:
    projects = [e for s in _sections(c.doc) if s.kind == "projects" for e in _entries(s)]
    weak = [e.heading for e in projects
            if not (e.link or any(cvscore.NUMBER.search(x) or RESULT_WORDS.search(x) for x in entry_lines(e)))]
    return not weak, f"No result or link for: {', '.join(weak)}." if weak else ""


def _education_first(c: _In) -> tuple[bool, str]:
    order = [s.kind for s in _sections(c.doc) if s.kind != "summary"]
    return bool(order) and order[0] == "education", "" if order and order[0] == "education" else \
        "Put education straight after your profile while you are studying or a recent graduate."


def _skills_backed(c: _In) -> tuple[bool, str]:
    items = [i for s in _sections(c.doc) if s.kind == "skills" for e in _entries(s) for i in e.items]
    if not items:
        return True, ""
    rest = " ".join(_lines_of_kinds(c.doc, (*DOING_KINDS, "education"))).lower()
    backed = [i for i in items if any(w in rest for w in re.findall(r"[a-z+#]{3,}", i.lower()))]
    ok = len(backed) / len(items) >= BACKED_SHARE
    return ok, f"{len(backed)} of {len(items)} skills appear in an entry."


def _right_to_work_line(c: _In) -> tuple[bool, str]:
    return bool(RIGHT_TO_WORK.search(c.text)), ""


def _no_module_codes(c: _In) -> tuple[bool, str]:
    codes = sorted(set(cvscore.MODULE_CODE.findall(c.text)))
    return not codes, f"Codes: {', '.join(codes)}." if codes else ""


def _no_first_person(c: _In) -> tuple[bool, str]:
    found = [x for x in _lines_of_kinds(c.doc, DOING_KINDS) if FIRST_PERSON.search(x)]
    return not found, f"{len(found)} lines use I, my or we." if found else ""


def _portfolio_link(c: _In) -> tuple[bool, str]:
    links = list((c.profile.get("links") or {}).values())
    links += [e.link for s in _sections(c.doc) if s.kind == "portfolio" for e in _entries(s) if e.link]
    ok = bool([x for x in links if x]) or bool(URL.search(_summary(c)))
    return ok, "" if ok else "Add your portfolio address in Settings, You, Website."


def _year(value: str) -> int:
    found = re.findall(r"(19|20)(\d{2})", value or "")
    return int("".join(found[-1])) if found else 0


def _newest_first(c: _In) -> tuple[bool, str]:
    years = [9999 if re.search(r"present|now", e.end or "", re.I) else _year(e.end or e.start)
             for s in _sections(c.doc) if s.kind == "experience" for e in _entries(s)]
    years = [y for y in years if y]
    return years == sorted(years, reverse=True), ""


def _availability_line(c: _In) -> tuple[bool, str]:
    ok = bool(AVAILABLE.search(c.text))
    return ok, "" if ok else "Say when you can start and for how long, in your profile."


CHECKS = {"profile_evidence": _profile_evidence, "result_bullets": _result_bullets,
          "dissertation_shown": _dissertation_shown, "modules_strong": _modules_strong,
          "dates_one_format": _dates_one_format, "no_personal_data": _no_personal_data,
          "pages_within": _pages_within, "one_spelling": _one_spelling, "entry_says_what": _entry_says_what,
          "contact_at_top": _contact_at_top, "projects_with_evidence": _projects_with_evidence,
          "education_first": _education_first, "skills_backed": _skills_backed,
          "right_to_work_line": _right_to_work_line, "no_module_codes": _no_module_codes,
          "no_first_person": _no_first_person, "portfolio_link": _portfolio_link, "newest_first": _newest_first,
          "availability_line": _availability_line}


def check_name(check_id: str, t: cvtypes.CVType) -> str:
    """What a check is called on screen; the length check names the type's page limit."""
    if check_id == "pages_within":
        return PAGES_NAMES.get(t.pages, f"{t.pages} pages or fewer")
    return CHECK_NAMES[check_id]


def type_checks(doc: Any, t: cvtypes.CVType, profile: dict, *, pages: int | None = None,
                spelling: str = "en-GB") -> dict:
    """The type's checks on the CV as the type shows it: {type_id, name, items, passed, total}."""
    shown = cvtypes.arrange(doc, t)
    c = _In(shown, t, profile, "\n".join(doc_lines(shown, profile)), pages, spelling)
    items = []
    for cid, weight in t.checks.items():
        if not weight or (cid == "pages_within" and not t.pages):
            continue
        ok, note = CHECKS[cid](c)
        items.append({"id": cid, "name": check_name(cid, t), "ok": ok, "weight": weight, "note": note,
                      "suggestion_id": None})
    return {"type_id": t.id, "name": t.name, "items": items, "passed": sum(i["ok"] for i in items),
            "total": len(items)}


def for_type(doc: Any, t: cvtypes.CVType) -> list[dict]:
    """`[{id, name, ok}]` for each check `t` weighs above 0, with the person's own details and spelling."""
    from jobhunt.core import settings

    found = type_checks(doc, t, profile_now(), spelling=settings.get("cvdocs.spelling"))
    return [{"id": i["id"], "name": i["name"], "ok": i["ok"]} for i in found["items"]]


# Fixes --------------------------------------------------------------------------------------------------

def _swap_symbols(text: str) -> str:
    out = "".join(SYMBOL_SWAPS.get(ord(ch), "") if _unsafe(ch) else ch for ch in text)
    return re.sub(r"\s+([,.;:])", r"\1", re.sub(r"[ \t]{2,}", " ", out)).strip()


def _map_text(obj: Any, fn) -> Any:
    """`obj` with `fn` applied to every text field, leaving locked parts and their children alone."""
    if getattr(obj, "locked", False):
        return obj
    changes: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if isinstance(value, str) and f.name in TEXT_FIELDS:
            new = fn(value)
        elif isinstance(value, list) and f.name == "items":
            new = [fn(v) for v in value]
        elif isinstance(value, list) and value and dataclasses.is_dataclass(value[0]):
            new = [_map_text(v, fn) for v in value]
        else:
            continue
        if new != value:
            changes[f.name] = new
    return dataclasses.replace(obj, **changes) if changes else obj


FIXES = ("symbols", "headings")


def fix(fix_id: str, doc: Any, t: cvtypes.CVType) -> tuple[Any, str]:
    """(the fixed document, what changed in words); LookupError for a fix Hal does not have."""
    if fix_id == "symbols":
        n = len(symbols_in("\n".join(doc_lines(doc, {}))))
        new = _map_text(doc, _swap_symbols)
        return new, f"Replaced {n} {'symbol' if n == 1 else 'symbols'} job-site scanners drop"
    if fix_id == "headings":
        renamed, sections = [], []
        for s in doc.sections:
            want = t.title_of(s.kind)
            if not s.locked and s.title.strip().lower() not in STANDARD_HEADINGS and want.lower() in STANDARD_HEADINGS:
                renamed.append(f"{s.title} to {want}")
                s = dataclasses.replace(s, title=want)
            sections.append(s)
        if not renamed:
            raise LookupError("Every heading Hal can rename is already one scanners know.")
        what = "Renamed headings for job-site scanners: " + "; ".join(renamed)
        return dataclasses.replace(doc, sections=sections), what
    raise LookupError(f"There is no fix called {fix_id!r}.")


# Cache ------------------------------------------------------------------------------------------------------

def cache_key(t: cvtypes.CVType, profile: dict, spelling: str, pdf_sha: str | None) -> str:
    """What the cached results depend on besides the version and look."""
    body = json.dumps([t.id, t.source, profile, spelling, pdf_sha], sort_keys=True)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def for_version(con: sqlite3.Connection, version_id: str, doc: Any, t: cvtypes.CVType, look_id: str, profile: dict,
                *, spelling: str = "en-GB", pdf: Path | None = None, columns: int = 1) -> dict:
    """The scanner score and type checks of one version and look, from `cv_checks` when nothing changed."""
    from jobhunt import db

    pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf is not None else None
    key = cache_key(t, profile, spelling, pdf_sha)
    row = con.execute("SELECT scanner, type_checks FROM cv_checks WHERE version_id = ? AND look_id = ?",
                      (version_id, look_id)).fetchone()
    if row and row["scanner"] and row["type_checks"]:
        cached = json.loads(row["type_checks"])
        if cached.get("key") == key:
            return {"scanner": json.loads(row["scanner"]), "type": cached["result"]}
    pages = cvscore.pdf_pages(pdf) if pdf is not None else None
    scan = scanner(doc, profile, pdf=pdf, columns=columns)
    checked = type_checks(doc, t, profile, pages=pages, spelling=spelling)
    con.execute("INSERT INTO cv_checks (version_id, look_id, pdf_sha, scanner, type_checks, at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (version_id, look_id) DO UPDATE SET pdf_sha = excluded.pdf_sha,"
                " scanner = excluded.scanner, type_checks = excluded.type_checks, at = excluded.at",
                (version_id, look_id, pdf_sha, json.dumps(scan), json.dumps({"key": key, "result": checked}), db.now()))
    con.execute("UPDATE cv_versions SET scanner_score = ? WHERE id = ?", (scan["score"], version_id))
    con.commit()
    return {"scanner": scan, "type": checked}


def link_suggestions(con: sqlite3.Connection, checked: dict) -> dict:
    """Each failing check gets the id of the pending suggestion it led to, when there is one ("See suggestion")."""
    for item in checked["items"]:
        if item["ok"]:
            continue
        row = con.execute("SELECT id FROM cv_suggestions WHERE status = 'pending' AND json_extract(trigger, '$.id') = ?"
                          " ORDER BY created DESC LIMIT 1", (item["id"],)).fetchone()
        item["suggestion_id"] = row["id"] if row else None
    return checked


# A recruiter's read ---------------------------------------------------------------------------------------

def recruiter_read(cv_text: str, t: cvtypes.CVType) -> dict:
    """The AI's opinion as a recruiter for the people the type fits; raises `ai.LLMUnavailable` when it cannot."""
    from jobhunt import ai
    from jobhunt.ai import prompts
    from jobhunt.writing import critique

    persona = f"Read it as a recruiter would for this kind of candidate: {t.name} ({t.fits})."
    be = ai.backend()
    reply = ai.chat_json(prompts.text("cv_judge"), critique.cv_request(persona, cv_text), role="checker",
                         timeout=RECRUITER_TIMEOUT_S, ctx=RECRUITER_CTX, purpose="cv_judge", priority=0)
    read = critique.parse(reply if isinstance(reply, dict) else {}, cv_text)
    grades = [{k: g[k] for k in ("criterion", "grade", "why", "fix")} for g in read["grades"]]
    words = read["summary"] or " ".join(g["why"] for g in grades if g["why"]).strip()
    if not words:
        raise ai.LLMUnavailable("The AI's answer had nothing Hal could show.", kind="unavailable")
    try:
        ai_words = ai.model_for(be, "checker")
    except ai.LLMUnavailable:
        ai_words = be.name
    return {"text": words, "ai_words": ai_words, "grades": grades, "screen": read["screen"],
            "requirements": read["requirements"], "top_fixes": read["top_fixes"]}


# What the routes and actions read ----------------------------------------------------------------------------

PROFILE_KEYS = ("name", "email", "phone", "city")
LINK_KEYS = ("linkedin", "website", "github")
TWO_COLUMN_LOOKS = ("two",)      # the looks that print two columns


def profile_now() -> dict:
    """The name and contact block every document is drawn with (`you.*`)."""
    from jobhunt.core import settings

    out = {k: str(settings.get(f"you.{k}") or "") for k in PROFILE_KEYS}
    out["links"] = {k: v for k in LINK_KEYS if (v := str(settings.get(f"you.links.{k}") or ""))}
    return out


def columns_of(look_id: str) -> int:
    """How many columns a look prints in."""
    look = Path(__file__).resolve().parent / "looks" / look_id / "look.toml"
    if look.is_file():
        import tomllib

        try:
            return int(tomllib.loads(look.read_text(encoding="utf-8")).get("columns", 1))
        except (tomllib.TOMLDecodeError, TypeError, ValueError):
            log.warning("look %s has no readable column count", look_id)
    return 2 if look_id in TWO_COLUMN_LOOKS else 1


def current_cv() -> tuple[str, Any, cvtypes.CVType, dict]:
    """(version id, document, its CV type, the `cv_documents` row); LookupError when there is no CV yet."""
    from jobhunt.cvstudio import store

    row = store.doc()
    version_id, doc = store.current()
    try:
        t = cvtypes.load(row["type_id"])
    except LookupError:
        log.warning("CV type %s is gone; showing %s", row["type_id"], cvtypes.FALLBACK)
        t = cvtypes.load(cvtypes.FALLBACK)
    return version_id, doc, t, row
