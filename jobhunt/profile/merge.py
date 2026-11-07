"""The profile's merge rules: when two found facts are one, and what the merged fact says.

Everything here is plain code over the facts' fields, never a model, so the same files always give the same
profile. `store.add_found` uses `same_item` to pick the item a new fact belongs to and `fold` to work out a
found item's fields from its sources.

Design decisions, each with its reason:

- Dates are kept as `YYYY-MM` or `YYYY`, whatever way a document wrote them ("May 2024", "05/2024",
  "Sept 2023 - present", "Summer 2023", "2023-24"), so two CVs that write one date two ways agree. Text that
  is not a date is kept as written, never guessed at.
- A whole period written with a two-digit second year ("2023-24", "2022-25") runs from September to June,
  as do two four-digit years in a row ("2023-2024"), because UK CVs and transcripts write academic years
  that way. A single point such as "2023-12" is always a month, so a stored date reads back as itself.
- Organisation names are compared without case, punctuation, "the", company endings (Ltd, plc) and word
  order, so "The Fenwick Museum Trust Ltd" and "Fenwick Museum Trust" are one employer, as are
  "University of Nowhere Fen" and "Nowhere Fen University".
- For the Inconsistencies list, titles and grades are compared without case, punctuation, plurals,
  number words ("Thirty" is "30") or common abbreviations ("HE" is "Higher Education"), and a qualification
  by its level, so only facts that differ are raised.
- A project is one with another when they share a link, when one's name is the other's link, or when
  their names match once a tagline after " - " or ":" and words such as "app" or "system" are left out:
  "Hatch", "Hatch - AI desktop companion" and "Hatch - desktop companion and wellbeing app" were three
  cards (2026-11-06). So is a name of two or more words whose words are all in the other's ("Recovery
  drone", "Autonomous Recovery Drone"). "Hal" and "Hal Employment" stay two: one word is too little.
- Skills are compared without case, spaces or punctuation other than "+" and "#" (so "C++" and "C#" stay
  apart), and through a small table of other names for the same skill ("MS Excel" is "Excel"), covering
  office, research and writing skills as well as code. "python-scripting" and "Python scripting" were two
  cards (2026-11-07); one skill shows the name its sources write in normal case (`skill_name`).
- A found item's fields come from its sources in rank order (transcript, certificate, CV, letter, notes):
  each field takes the value of the best-ranked source that gives one, so a transcript's grade beats a CV's.
  An end date before this month makes the item not current, even when another source said "present"; a
  "present" with no end date anywhere stays current.
  Lists (duties, tools) are joined across sources.
- An education item's modules come from its transcripts and certificates when any lists modules, else from
  its CVs, and never from notes, slides or coursework, which name lectures and practicals as well as
  modules. They are one per module code ("CSC1032", "1032 Computer Systems Design" and "Computer Systems
  (CSC1032)" are one), with the fullest name and the best source's mark; modules without a code are one
  when their names are close.
"""
from __future__ import annotations

import difflib
import re

from jobhunt.core import clock

# Rank of each kind of document; a higher rank's value wins. The person's own edits are above all of these
# and are never folded at all (see store.py).
RANKS = {"transcript": 5, "certificate": 4, "cv": 3, "letter": 2, "notes": 1}
DOC_TYPES = {  # other names the ingest may give a document type
    "hear": "transcript", "degree_transcript": "transcript", "marks": "transcript",
    "degree_certificate": "certificate", "award": "certificate", "certificates": "certificate",
    "resume": "cv", "curriculum_vitae": "cv",
    "cover_letter": "letter", "personal_statement": "letter", "reference": "letter", "statement": "letter",
    "note": "notes", "study_notes": "notes", "slides": "notes",
}
UNKNOWN_RANK = 0           # a document type Hal has no rank for is below notes
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
MONTH_MIN = 3              # "Sep" and "Sept" are months; "ma" is not
# (first month, last month) of each season; winter runs into the next year
SEASONS = {"spring": (3, 5), "summer": (6, 8), "autumn": (9, 11), "fall": (9, 11), "winter": (12, 2)}
ACADEMIC_START, ACADEMIC_END = 9, 6        # a UK academic year runs September to June
NOW_WORDS = {"present", "now", "current", "ongoing", "to date", "date", "today"}
DASHES = "-\u2013\u2014"                    # hyphen, en dash, em dash, as documents write ranges
CENTURY = 100
FIRST_MONTH, LAST_MONTH = 1, 12
ORG_ENDINGS = {"ltd", "limited", "plc", "llp", "llc", "inc", "co", "cic", "uk"}
ORG_SKIP = {"the", "of", "and", "at", "for"}
# Other names for one skill, by their compared form (see `skill_key`); the value is the shared key.
SKILL_ALIASES = {
    "msexcel": "excel", "microsoftexcel": "excel", "excelspreadsheets": "excel", "spreadsheets": "excel",
    "msword": "word", "microsoftword": "word", "mspowerpoint": "powerpoint", "microsoftpowerpoint": "powerpoint",
    "msoffice": "microsoftoffice", "office365": "microsoftoffice", "microsoft365": "microsoftoffice",
    "archiveresearch": "archivalresearch", "researchinarchives": "archivalresearch",
    "oralhistories": "oralhistory", "oralhistoryinterviews": "oralhistory",
    "proofreading": "proofreading", "copyediting": "copyediting", "editing": "editing",
    "customerservices": "customerservice", "publicspeaking": "presenting", "presentations": "presenting",
    "js": "javascript", "py": "python", "postgres": "postgresql", "gsheets": "googlesheets",
}
LIST_FIELDS = {"duties", "achievements", "tools", "modules"}


def rank(doc_type: str) -> int:
    """How much a document type is trusted: transcript 5, certificate 4, CV 3, letter 2, notes 1, else 0."""
    key = (doc_type or "").strip().lower().replace(" ", "_").replace("-", "_")
    return RANKS.get(DOC_TYPES.get(key, key), UNKNOWN_RANK)


def doc_group(doc_type: str) -> str:
    """The ranked group a document type belongs to (`transcript`, `cv` and so on), or the type itself."""
    key = (doc_type or "").strip().lower().replace(" ", "_").replace("-", "_")
    return DOC_TYPES.get(key, key)


# Dates ---------------------------------------------------------------------------------------------------

def _month_word(word: str) -> int | None:
    word = word.lower().rstrip(".")
    if len(word) < MONTH_MIN:
        return None
    return next((i for i, name in enumerate(MONTHS, 1) if name.startswith(word)), None)


def _year(text: str) -> int | None:
    return int(text) if re.fullmatch(r"(19|20)\d\d", text) else None


def parse_point(text: str, side: str = "start") -> str | None:
    """One date as `YYYY-MM` or `YYYY`; None when it is not a date. A season is its first or last month.

    "May 2024", "05/2024", "1/5/2024" (UK day first), "2024-05", "2024-05-01", "2024" and "Summer 2023" are
    understood; `side` ("start" or "end") picks a season's first or last month.
    """
    t = " ".join((text or "").replace(",", " ").split()).lower()
    if not t:
        return None
    if m := re.fullmatch(r"((19|20)\d\d)", t):
        return m.group(1)
    if m := re.fullmatch(r"((19|20)\d\d)[-/.](\d{1,2})(?:[-/.]\d{1,2})?", t):
        month = int(m.group(3))
        return f"{m.group(1)}-{month:02d}" if FIRST_MONTH <= month <= LAST_MONTH else None
    if m := re.fullmatch(r"(?:\d{1,2}[/.-])?(\d{1,2})[/.-]((19|20)\d\d)", t):
        month = int(m.group(1))
        return f"{m.group(2)}-{month:02d}" if FIRST_MONTH <= month <= LAST_MONTH else None
    words = t.split()
    if len(words) == 3 and re.fullmatch(r"\d{1,2}(st|nd|rd|th)?", words[0]):
        words = words[1:]                  # "1st May 2024": the day is dropped
    if len(words) == 2 and (year := _year(words[-1])):
        first = words[-2]
        if first in SEASONS:
            start, end = SEASONS[first]
            if side == "end" and end < start:
                return f"{year + 1}-{end:02d}"
            return f"{year}-{(start if side == 'start' else end):02d}"
        if (month := _month_word(first)) is not None:
            return f"{year}-{month:02d}"
    return None


def _split(text: str) -> tuple[str, str] | None:
    """The two ends of a written range, or None when `text` is one date."""
    t = " ".join((text or "").split())
    for sep in (" to ", " until ", " till "):
        if sep in t.lower():
            i = t.lower().index(sep)
            return t[:i], t[i + len(sep):]
    m = re.fullmatch(rf"(.+?)\s*[{DASHES}]\s*(.+)", t)
    if m and not re.fullmatch(r"(19|20)\d\d[-/.]\d{1,2}([-/.]\d{1,2})?", t) \
            and not re.fullmatch(r"\d{1,2}[-/.]\d{1,2}[-/.](19|20)\d\d|\d{1,2}[-/.](19|20)\d\d", t):
        return m.group(1), m.group(2)
    return None


def _academic(text: str) -> tuple[str, str] | None:
    m = re.fullmatch(r"((19|20)\d\d)\s*[/\-\u2013]\s*(\d\d|(19|20)\d\d)", (text or "").strip())
    if not m:
        return None
    first = int(m.group(1))
    second = int(m.group(3))
    short = second < CENTURY
    if short:
        second = first - first % CENTURY + second
    if second <= first or (not short and second != first + 1):
        return None
    return f"{first}-{ACADEMIC_START:02d}", f"{second}-{ACADEMIC_END:02d}"


def is_now(text: str) -> bool:
    """True when `text` says the period has not ended ("present", "now", "to date")."""
    return " ".join((text or "").lower().split()).strip(".") in NOW_WORDS


def parse_period(text: str) -> tuple[str, str, bool]:
    """A written period as (start, end, current): "Sept 2023 - present" is ("2023-09", "", True).

    An end or start that is not a date is kept as written; "2023-24" is the academic year 2023-09 to 2024-06.
    """
    t = " ".join((text or "").split())
    if not t:
        return "", "", False
    if year := _academic(t):
        return year[0], year[1], False
    if is_now(t):
        return "", "", True
    parts = _split(t)
    if parts is None:
        start = parse_point(t, "start")
        if start is None:
            return t, "", False
        season = t.lower().split()[0] in SEASONS
        return start, (parse_point(t, "end") or "") if season else "", False
    start_text, end_text = parts
    if is_now(end_text):
        return parse_point(start_text, "start") or start_text.strip(), "", True
    return (parse_point(start_text, "start") or start_text.strip(),
            parse_point(end_text, "end") or end_text.strip(), False)


def clean_date(text: str, side: str = "start") -> str:
    """A date field as stored: `YYYY-MM` or `YYYY` when it is a date, else the text as written."""
    t = " ".join(str(text or "").split())
    return parse_point(t, side) or t


def month_index(point: str, side: str = "start") -> int | None:
    """A stored date as a count of months, for comparing; a bare year is January (start) or December (end)."""
    m = re.fullmatch(r"((19|20)\d\d)-(\d\d)", point or "")
    if m and FIRST_MONTH <= int(m.group(3)) <= LAST_MONTH:
        return int(m.group(1)) * 12 + int(m.group(3)) - 1
    if re.fullmatch(r"(19|20)\d\d", point or ""):
        return int(point) * 12 + (0 if side == "start" else LAST_MONTH - 1)
    return None


def this_month() -> int:
    """Today as a count of months, so "present" can be compared."""
    today = clock.local_day()
    return today.year * 12 + today.month - 1


def ended(fields: dict) -> bool:
    """True when the item's end date is a month before this one, so it is not current whatever a source said."""
    end = month_index(fields.get("end") or "", "end")
    return end is not None and end < this_month()


def span(fields: dict) -> tuple[int, int] | None:
    """An item's (first month, last month), "present" as this month and a missing end as its start; or None."""
    start = month_index(fields.get("start") or "", "start")
    if start is None:
        return None
    if fields.get("current"):
        return start, max(start, this_month())
    end = month_index(fields.get("end") or "", "end")
    return start, (end if end is not None else start)


def shared_months(a: dict, b: dict) -> int:
    """How many months the two periods share (0 when either has no start)."""
    sa, sb = span(a), span(b)
    if sa is None or sb is None:
        return 0
    return max(0, min(sa[1], sb[1]) - max(sa[0], sb[0]) + 1)


def overlap_or_touch(a: dict, b: dict) -> bool:
    """True when the two periods share a month or one starts the month after the other ends."""
    sa, sb = span(a), span(b)
    if sa is None or sb is None:
        return False
    return sa[0] <= sb[1] + 1 and sb[0] <= sa[1] + 1


def date_words(point: str) -> str:
    """A stored date for people: "2024-05" is "May 2024"; anything else is shown as it is."""
    m = re.fullmatch(r"((19|20)\d\d)-(\d\d)", point or "")
    if m and FIRST_MONTH <= int(m.group(3)) <= LAST_MONTH:
        return f"{MONTHS[int(m.group(3)) - 1].capitalize()} {m.group(1)}"
    return point or ""


# Names ---------------------------------------------------------------------------------------------------

def _plain(text: str) -> str:
    t = (text or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", t).split())


def org_key(name: str) -> str:
    """An employer's or institution's name as compared: no case, punctuation, "the", Ltd or word order."""
    words = [w for w in _plain(name).replace("univ ", "university ").split() if w not in ORG_SKIP]
    while words and words[-1] in ORG_ENDINGS:
        words.pop()
    return " ".join(sorted(words))


def text_key(text: str) -> str:
    """A title or name as compared: no case, punctuation or extra spaces."""
    return _plain(text)


def skill_key(name: str) -> str:
    """A skill's name as compared: no case, spaces, hyphens or dots, and other names folded to one."""
    parts = sorted(re.sub(r"[^\w+#]|_", "", part) for part in (name or "").lower().split("/"))
    key = "/".join(p for p in parts if p)
    return SKILL_ALIASES.get(key, key)       # "TypeScript/JavaScript" is "JavaScript/TypeScript"


ACRONYM_MAX = 5            # "SQL" and "HTML5" are written in capitals; "PYTHON SCRIPTING" is shouting


def _name_shape(name: str) -> tuple[bool, bool, bool, bool]:
    """How well a skill's name is written, for choosing one: higher is better."""
    return ("_" not in name, not (name.isupper() and len(name) > ACRONYM_MAX), any(c.isupper() for c in name),
            "-" not in name or " " in name)


def skill_name(names: list[str]) -> str:
    """The name to show for one skill its sources write in several ways, best-ranked source first.

    The best-written name wins ("Python scripting" over "python-scripting" or "PYTHON SCRIPTING"); on a
    tie, the earlier source's. A name written only in lower case starts with a capital, and underscores
    become spaces; hyphens stay, as in "scikit-learn".
    """
    written = [" ".join(n.split()) for n in names if n and n.strip()]
    if not written:
        return ""
    best = max(enumerate(written), key=lambda p: (_name_shape(p[1]), -p[0]))[1].replace("_", " ")
    best = " ".join(best.split())
    return best[:1].upper() + best[1:] if best == best.lower() else best


# Wording that differs while the fact is the same: "Thirty units" and "30 units", "GCSE" and "GCSEs",
# "Access to HE Diploma" and "Access to Higher Education Diploma" were raised as inconsistencies (2026-11-05).
NUMBER_WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
TENS_WORDS = {w: n * 10 for n, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
ABBREVIATIONS = {"he": "higher education", "hons": "honours", "uni": "university", "dept": "department",
                 "mgr": "manager", "asst": "assistant", "snr": "senior", "sr": "senior", "jnr": "junior",
                 "jr": "junior", "intl": "international", "admin": "administration", "1st": "first"}
# UK degree classes written in words or figures ("Upper Second", "2:1", "2.1")
GRADE_PHRASES = (("upper second", "2 1"), ("lower second", "2 2"), ("second upper", "2 1"),
                 ("second lower", "2 2"))
GRADE_WORDS = {"distinction", "merit", "pass", "fail", "credit", "first", "1st", "2nd", "3rd", "starred"}
FILLER_WORDS = {"the", "a", "an", "at", "of", "and", "with", "in"}
GRADE_FILLER = {"class", "honours", "degree", "grade", "overall", "classification"}
PLURAL_MIN = 4             # "units" is "unit"; "is", "bus" and "class" are left alone


def _number(words: list[str]) -> list[str]:
    """Number words as digits: "thirty five units" is "35 units"."""
    out: list[str] = []
    for w in words:
        if w in NUMBER_WORDS and out and out[-1].isdigit() and int(out[-1]) in TENS_WORDS.values():
            out[-1] = str(int(out[-1]) + NUMBER_WORDS[w])
        elif w in NUMBER_WORDS or w in TENS_WORDS:
            out.append(str(NUMBER_WORDS.get(w, TENS_WORDS.get(w))))
        else:
            out.append(w)
    return out


def fact_key(text: str, grade: bool = False) -> str:
    """A title or grade as compared for conflicts: wording, number words, plurals and abbreviations aside.

    With `grade`, UK degree classes in words are their figures and "class" or "honours" are left out.
    """
    t = _plain(text)
    if grade:
        for phrase, figure in GRADE_PHRASES:
            t = t.replace(phrase, figure)
    words = " ".join(ABBREVIATIONS.get(w, w) for w in t.split()).split()
    skip = FILLER_WORDS | (GRADE_FILLER if grade else set())
    words = [w[:-1] if len(w) >= PLURAL_MIN and w.endswith("s") and not w.endswith("ss") else w
             for w in _number(words) if w not in skip]
    return " ".join(words)


def grade_marks(text: str) -> list[str]:
    """The figures and grade words of a grade, in order: "Year 1: 81.5% (1st)" gives 1, 81, 5, 1st."""
    return [w for w in fact_key(text, grade=True).split() if any(c.isdigit() for c in w) or w in GRADE_WORDS]


def says_less(field: str, a: str, b: str) -> bool:
    """True when value `a` of `field` is `b` told with less detail, so the two are no conflict.

    A grade whose figures and grade words come, in order, within the other's ("1st" in "Year 1: 81.5%
    (1st); Year 2: 73.3% (1st)"); a qualification naming no level whose words are all in the other's
    ("Computer Science" in "Computer Science BSc").
    """
    if field == "grade":
        small, big = grade_marks(a), iter(grade_marks(b))
        return bool(small) and all(w in big for w in small)
    if field == "qualification":
        words = set(fact_key(a).split())
        return not level(a) and bool(words) and words <= set(fact_key(b).split())
    return False


def qualification_key(text: str) -> str:
    """A qualification as compared for conflicts: its level and level number, or its words if it has no level."""
    found = level(text)
    if not found:
        return fact_key(text)
    number = re.search(r"\blevel (\d)", _plain(text))
    return f"{found} {number.group(1)}" if number else found


def value_key(field: str, value) -> str:
    """A field's value as compared for conflicts: dates by month, a qualification by level, wording aside."""
    if field in ("start", "end", "date", "expires"):
        return clean_date(str(value or ""), "end" if field == "end" else "start")
    if field in ("employer", "institution", "issuer"):
        return org_key(str(value or ""))
    if field == "qualification":
        return qualification_key(str(value or ""))
    if field in ("title", "grade"):
        return fact_key(str(value or ""), grade=field == "grade")
    if field == "value" and re.fullmatch(r"[\d\s()+-]{7,}", str(value or "")):
        return re.sub(r"\D", "", str(value))      # "07700 900123" and "07700900123" are one number
    if isinstance(value, bool):
        return "yes" if value else "no"
    return _plain(str(value or ""))


# Same item -----------------------------------------------------------------------------------------------

def _same(key, a: dict, b: dict, field: str) -> bool:
    """True when both facts give `field` and it compares equal under `key`."""
    ka, kb = key(a.get(field, "")), key(b.get(field, ""))
    return bool(ka) and ka == kb


# Qualification levels, most specific first: a "BSc", a "Bachelor of Science with Honours" and a "Computer
# Science BSc" are one degree, and the model writes it a different way in each document (2026-11-05).
LEVELS = (("pgce", r"\bpgce\b|postgraduate certificate in education"),
          ("doctorate", r"\bph ?d\b|\bd ?phil\b|doctorate"),
          ("master", r"\bm(?:sc|a|res|phil|ba|eng|st|ed|sci)\b|master"),
          ("bachelor", r"\bb(?:sc|a|eng|ed|mus|sci)\b|bachelor|\bllb\b|\bhons\b|honours degree|"
                       r"undergraduate degree"),
          ("foundation", r"foundation degree|\bhn[cd]\b"),
          ("access", r"access to (?:he|higher education)|\baccess (?:course|diploma)"),
          ("a_level", r"\bas? ?levels?\b"),
          ("btec", r"\bbtec\b"),
          ("gcse", r"\bi?gcses?\b"),
          ("diploma", r"\bdiploma\b"))
# Words that say what a qualification is rather than what it is in: left out when subjects are compared.
LEVEL_WORDS = {"bsc", "ba", "beng", "bed", "bmus", "msc", "ma", "mres", "mphil", "mba", "meng", "phd", "honours",
               "hons", "degree", "bachelor", "bachelors", "master", "masters", "of", "in", "with", "and", "science",
               "arts", "studies", "student", "diploma", "certificate", "first", "class", "1st", "2", "1", "the"}
# "Newcastle University" is "University of Newcastle upon Tyne"; "University" alone is not
ORG_SUBSET_MIN = 2
# "Fire Marshal" and "The Fire Marshall" are one certificate; "Food Safety Level 2" and "Level 3" are not
NAME_CLOSE = 0.9
# A tagline after a spaced dash or a colon: "Hatch - AI desktop companion", "QuizMaster: a Java quiz app"
TAGLINE = re.compile(r"\s+[-\u2013\u2014]\s+.*$|:\s.*$")
PROFILE_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "huggingface.co", "kaggle.com", "linkedin.com"}
PROJECT_WORDS_MIN = 2      # words a shorter project name needs to match a longer one by its words
PROJECT_WORDS = {"app", "application", "system", "project", "tool", "program", "programme", "website", "site"}


def level(qualification: str) -> str:
    """The level of a qualification ("bachelor", "gcse", ...), or "" when its name does not say."""
    text = _plain(qualification)
    return next((name for name, pattern in LEVELS if re.search(pattern, text)), "")


def _subject_words(fields: dict) -> set[str]:
    words = set(_plain(f"{fields.get('subject', '')} {fields.get('qualification', '')}").split())
    return words - LEVEL_WORDS


def _org_same(a: str, b: str) -> bool:
    """True when two institutions are one: equal, or one's name inside the other's ("Newcastle University")."""
    ka, kb = set(org_key(a).split()), set(org_key(b).split())
    if not ka or not kb:
        return False
    small = min(ka, kb, key=len)
    return ka == kb or (len(small) >= ORG_SUBSET_MIN and (ka <= kb or kb <= ka))


def _name_close(a: str, b: str) -> bool:
    ka, kb = (re.sub(r"^the ", "", text_key(x)) for x in (a, b))
    if not ka or re.findall(r"\d+", ka) != re.findall(r"\d+", kb):
        return False                       # Level 2 and Level 3 differ by one letter and are two certificates
    return ka == kb or difflib.SequenceMatcher(None, ka, kb).ratio() >= NAME_CLOSE


def _same_education(a: dict, b: dict) -> bool:
    ia, ib = a.get("institution", ""), b.get("institution", "")
    if ia and ib and not _org_same(ia, ib):
        return False
    la, lb = level(a.get("qualification", "")), level(b.get("qualification", ""))
    shared = bool(_subject_words(a) & _subject_words(b))
    if ia and ib and not (a.get("qualification") and b.get("qualification")):
        return True     # modules from course notes, with no qualification named, belong to the one studied there
    if la and lb:
        return la == lb and (bool(ia and ib) or shared)
    if ia and ib:
        return shared or shared_months(a, b) > 0
    return False


def same_item(kind: str, a: dict, b: dict) -> bool:
    """True when the facts `a` and `b` of one kind are the same thing (see the module notes)."""
    if kind == "job":
        if not (a.get("employer") or b.get("employer")):
            return _same(text_key, a, b, "title") and overlap_or_touch(a, b)
        if not _same(org_key, a, b, "employer"):
            return False
        # a job named with no dates at all is the dated one there ("Staff" and "Supervisor" at one bar)
        undated = not (a.get("start") or a.get("end")) or not (b.get("start") or b.get("end"))
        return _same(text_key, a, b, "title") or overlap_or_touch(a, b) or undated
    if kind == "education":
        return _same_education(a, b)        # a BA and then an MA at one university stay two: their levels differ
    if kind == "skill":
        return _same(skill_key, a, b, "name")
    if kind == "basic":
        if not _same(text_key, a, b, "key"):
            return False
        return text_key(a["key"]) in SINGLE_BASICS or value_key("value", a.get("value")) == value_key(
            "value", b.get("value"))
    if kind == "link":
        return _same(_url_key, a, b, "url")
    if kind == "project":
        return _same_project(a, b)
    field = "title" if kind == "achievement" else "name"
    return _name_close(a.get(field, ""), b.get(field, ""))


def project_key(name: str) -> str:
    """A project's name as compared: no tagline after " - " or ":", no words such as "app" or "system"."""
    words = _plain(TAGLINE.sub("", name or "")).split()
    return " ".join(w for w in words if w not in PROJECT_WORDS) or " ".join(words)


def _project_link(url: str) -> str:
    """A link that names one project; "" for none or a profile page ("github.com/alexdoe") many share."""
    key = _url_key(url)
    host, _, path = key.partition("/")
    return "" if host in PROFILE_HOSTS and "/" not in path.strip("/") else key


def _same_project(a: dict, b: dict) -> bool:
    la, lb = _project_link(a.get("link", "")), _project_link(b.get("link", ""))
    if la and la == lb:
        return True
    if (lb and _url_key(a.get("name", "")) == lb) or (la and _url_key(b.get("name", "")) == la):
        return True                        # "alexdoe.dev" is the site whose link it is
    ka, kb = project_key(a.get("name", "")), project_key(b.get("name", ""))
    small, big = sorted((set(ka.split()), set(kb.split())), key=len)
    return _name_close(ka, kb) or (len(small) >= PROJECT_WORDS_MIN and small <= big)


SINGLE_BASICS = {"name"}      # basics a person has one of; others (emails, phones, links) can be several


def _url_key(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.rstrip("/")


# Folding sources into fields -----------------------------------------------------------------------------

# Lines from several versions of one CV say the same duty in other words ("Served 400+ customers a shift",
# "serving 400+ covers a shift during peak periods"); a job collected twenty of them (2026-11-05).
LINE_CLOSE = 0.6


def _close_line(key: str, kept: list[str]) -> bool:
    words = set(key.split())
    for other in kept:
        if key in other or other in key:
            return True
        theirs = set(other.split())
        if words and theirs and len(words & theirs) / min(len(words), len(theirs)) >= LINE_CLOSE + 0.2:
            return True
        if difflib.SequenceMatcher(None, key, other).ratio() >= LINE_CLOSE:
            return True
    return False


def _join(lists: list) -> list:
    """The lists joined best source first; a line that says what one already kept says (other words) is left."""
    out, seen = [], []
    for items in lists:
        for x in items or []:
            key = text_key(x.get("name", "")) if isinstance(x, dict) else text_key(str(x))
            if not key or key in seen or (not isinstance(x, dict) and _close_line(key, seen)):
                continue
            seen.append(key)
            out.append(x)
    return out


# A module code as universities write them: two to four letters and three or four digits ("CSC1032",
# "his2010", "MATH 101"). A space or hyphen is allowed only after capitals, so "Year 2023" is not a code.
MODULE_CODE = re.compile(r"\b(?:([A-Za-z]{2,4})(\d{3,4})|([A-Z]{2,4})[ -](\d{3,4}))\b")
# The bare number of a known code ("1032 Computer Systems Design" beside "CSC1032")
BARE_CODE = re.compile(r"\b(\d{4})\b")
# Lecture notes and slides named a degree's modules in many spellings, beside 120 lecture titles ("Lesson 14
# Cascading Style Sheets", "tut8 vectors and matrices") (2026-11-05): modules come from the official papers
# when they list any, else from CVs, and never from notes.
OFFICIAL_RANK = RANKS["certificate"]
CV_RANK = RANKS["cv"]


def module_code(text: str) -> str:
    """The module code a name holds, in capitals ("CSC1032"), or "" when it holds none."""
    m = MODULE_CODE.search(text or "")
    if not m:
        return ""
    letters, digits = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return f"{letters.upper()}{digits}"


def _module_words(name: str) -> str:
    """A module's name without its code, as compared: "Computer Systems (CSC1032)" is "computer systems"."""
    return text_key(BARE_CODE.sub(" ", MODULE_CODE.sub(" ", name or "")))


def _module_sources(sources: list[tuple[int, dict]]) -> list[dict]:
    """The sources whose modules count: official papers when any lists modules, else CVs, never notes."""
    listing = [(r, said) for r, said in sources if said.get("modules")]
    official = [said for r, said in listing if r >= OFFICIAL_RANK]
    return official or [said for r, said in listing if r == CV_RANK]


def _modules(sources: list[dict]) -> list[dict]:
    """Modules one per code (else per close name), with the fullest name and the best-ranked source's mark.

    Sources come best first. A bare four-digit number is a code only when it is the number of exactly one
    code already in the list.
    """
    mods = [m for said in sources for m in said.get("modules") or [] if text_key(m.get("name", ""))]
    by_digits: dict[str, set[str]] = {}
    for m in mods:
        if code := module_code(m["name"]):
            by_digits.setdefault(re.sub(r"\D", "", code), set()).add(code)
    groups: list[list] = []                # [code or "", members]
    for m in mods:
        code = module_code(m["name"])
        if not code and (bare := BARE_CODE.search(m["name"])) and len(by_digits.get(bare.group(1), ())) == 1:
            (code,) = by_digits[bare.group(1)]
        for group in groups:
            if code and group[0]:
                same = group[0] == code    # two codes are two modules, however alike their names
            else:
                a, b = _module_words(group[1][0]["name"]), _module_words(m["name"])
                same = bool(a) and (a == b or _name_close(a, b))
            if same:
                group[0] = group[0] or code
                group[1].append(m)
                break
        else:
            groups.append([code, [m]])
    out = []
    for _, members in groups:
        name = max(members, key=lambda m: len(_module_words(m["name"])))["name"]
        mark = next((m.get("mark") for m in members if m.get("mark")), "")
        out.append({"name": name, "mark": mark})
    return out


def fold(sources: list[tuple[int, dict]]) -> dict:
    """A found item's fields from its sources' (rank, fields): best rank first, earlier sources on a tie."""
    ranked = [pair for _, pair in sorted(enumerate(sources), key=lambda p: (-p[1][0], p[0]))]
    ordered = [said for _, said in ranked]
    out: dict = {}
    for said in ordered:
        for field, value in said.items():
            if field in LIST_FIELDS or value in (None, "", []):
                continue
            out.setdefault(field, value)
    for field in LIST_FIELDS:
        joined = _modules(_module_sources(ranked)) if field == "modules" else _join([s.get(field) for s in ordered])
        if joined:
            out[field] = joined
    if out.get("current") and ended(out):
        # an old CV's "2023 - present" beside the transcript's June 2026 end showed "to now" (2026-11-05)
        del out["current"]
    return out
