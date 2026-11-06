"""Which files the ingest keeps out of the record because they look private, and bringing one back.

People drop whole folders and zips into Hal, and those hold bank statements, passport scans, payslips, fit
notes and `.env` files beside their CVs. `reason` reads a file's converted text, before any AI call, and
gives a short reason in plain words or "". A file with a reason gets no note, no copy and no facts, and its
text never reaches the AI; the page lists it by name and reason only, so a false alarm comes back with
"Add it anyway" (`allow`, `requeue`).

Design decisions:
- Precision over recall. Each rule wants a structured hit that checks out (a Luhn, mod-11 or mod-97 check
  digit, a machine-readable zone line, a key with real randomness) or several words of one kind together;
  one weak hit, such as the word "NHS" on a CV, is never enough. A file wrongly kept out costs a click; a
  CV kept out by a loose rule would make the whole check a nuisance that people turn off.
- Teaching and coursework that only discuss these things (a lecture on cryptography, a payroll seminar, a
  nursing essay) pass: the clusters of words and the `password = "..."` rule do not count in a text with
  several teaching or coursework markers. Structured hits still count there.
- The AI's own verdict (`said`, the reply's `sensitive` field) is a reason too, for what rules cannot see;
  by then the text was with the AI, but the note, the copy and the facts are still left out.
- A file allowed once is remembered by its SHA-256 with its path or its name in the folder or zip, so the
  same bytes read again are not held again; a changed file is checked again.
"""
from __future__ import annotations

import re
import sqlite3
import string

BANK = "bank details"
ID_DOCUMENT = "a passport or ID card"
ID_NUMBER = "a National Insurance or social security number"
MEDICAL = "medical information"
PAY = "a payslip or tax form"
BIRTH_AND_ID = "a date of birth with an ID number"
SECRETS = "passwords or secret keys"
SAID_BY_AI = "the AI said it looks private"

NEAR_CHARS = 200          # a label counts for a number this far before or after it
LABEL_GAP = 60            # "Sort code:" to its digits, "Date of birth" to its date, and the like
SAID_CHARS = 80           # the AI's own reason is shown to the person, so it stays one short line
YES_WORDS = {"true", "yes", "1", "sensitive", "private"}

# National Insurance numbers (HMRC National Insurance Manual): two prefix letters, six digits, suffix A to
# D. D, F, I, Q, U and V are never used, O never second, and these prefixes are never issued.
NINO = re.compile(r"\b(?!BG|GB|KN|NK|NT|TN|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b")
NINO_LABEL = re.compile(r"national\s+insurance|\bNI\s*(?:no\b|number)|\bNINO\b", re.I)
# US social security numbers: never 000, 666 or 9xx first, 00 middle or 0000 last (SSA, 2011 randomisation).
SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
SSN_LABEL = re.compile(r"\bSSN\b|social\s+security", re.I)

# Passport and ID card machine-readable zone lines (ICAO 9303): 30 to 44 of A to Z, 0 to 9 and "<".
MRZ_LINE = re.compile(r"^[A-Z0-9<]{30,44}$")
MRZ_PASSPORT = re.compile(r"^P[A-Z<][A-Z<]{3}[A-Z]")
MRZ_FILLER = "<<"
MRZ_LINES_MIN = 2         # two zone lines together are a card even without the passport line

SORT_CODE = re.compile(rf"sort\s*code\b[^\n\d]{{0,{LABEL_GAP}}}(\d{{2}}[- ]?\d{{2}}[- ]?\d{{2}})\b", re.I)
ACCOUNT = re.compile(rf"account\s*(?:number|no\.?|num)[^\n\d]{{0,{LABEL_GAP}}}(\d{{8}})\b", re.I)
# IBANs: country, two check digits, 11 to 30 more (ISO 13616); valid when the number mod 97 is 1.
IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[A-Z0-9]{11,30}|(?: [A-Z0-9]{4}){2,7}(?: [A-Z0-9]{1,4})?))\b")
IBAN_MOD = 97
IBAN_LEN = (15, 34)

# Card numbers of 13 to 19 digits, alone or in groups, from the networks' own ranges (Visa 4, Mastercard
# 51 to 55 and 2221 to 2720, Amex 34 and 37, Discover 6011 and 65).
# Never the digits after a decimal point: a results table's "0.2719458330027242" was a card (2026-11-06).
CARD = re.compile(r"(?<![\d.-])(\d{4}(?:[ -]?\d{3,4}){2,3}(?:[ -]?\d{1,3})?)(?![\d-]|\.\d)")
# A run this long of one digit is a rounded ID or a placeholder, such as a tweet's 550335000000000000
CARD_RUN = re.compile(r"(\d)\1{5}")
CARD_PREFIX = re.compile(r"^(?:4|5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]|720)|3[47]|6011|65)")
CARD_DIGITS = (13, 19)
CARD_GROUPED = re.compile(r"^\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}$")
CARD_LABEL = re.compile(r"\bcard\b|\bvisa\b|mastercard|\bamex\b|expir|valid\s+thru|\bcvv\b|\bcvc\b", re.I)
# The networks' published test numbers, found in developers' notes and payment tutorials.
TEST_CARDS = {"4111111111111111", "4242424242424242", "4000056655665556", "5555555555554444",
              "5105105105105100", "378282246310005", "371449635398431", "6011111111111117", "4012888888881881"}

# NHS numbers: ten digits with a mod-11 check digit (NHS Data Dictionary, "NHS number").
NHS_NUMBER = re.compile(r"(?<!\d)(\d{3} ?\d{3} ?\d{4})(?!\d)")
NHS_LABEL = re.compile(r"\bNHS\b")
NHS_WEIGHTS = range(10, 1, -1)
NHS_MOD = 11

BIRTH = re.compile(rf"(?:date\s+of\s+birth|\bD\.?O\.?B\b\.?|\bborn\s+on)[^\n\d]{{0,{LABEL_GAP}}}"
                   r"(\d{1,4}[/.\- ](?:\d{1,2}|[A-Za-z]{3,9})[/.\- ]\d{2,4})", re.I)
ID_LABEL = re.compile(rf"\b(?:student|staff|employee|candidate|passport|licen[cs]e|identity|ID|registration|"
                      rf"matriculation|personnel|payroll)\s*(?:number|no\b\.?|num|#|ID\b)?[^\n\w]{{0,{LABEL_GAP}}}"
                      r"([A-Z]{0,3}\d[A-Z0-9]{5,})\b", re.I)

PAY_TERMS = ("payslip", "pay slip", "pay stub", "p60", "p45", "p11d", "tax code", "net pay", "gross pay",
             "paye", "ni contributions", "national insurance contributions", "tax period", "year to date",
             "pay date", "earnings statement", "w-2", "form 1040", "employee leaving work", "end of year certificate")
PAY_TERMS_MIN = 3
MONEY = re.compile(r"(?<![\d.])\d[\d,]*\.\d{2}\b")       # 1,640.00 with or without its currency sign
MONEY_MIN = 2

# Words about the person's own health; a pharmacy job on a CV says "prescriptions" and "NHS", not these.
MEDICAL_STRONG = ("fit note", "sick note", "statement of fitness for work", "not fit for work",
                  "repeat prescription", "diagnosed with", "medical certificate", "discharge summary",
                  "patient name", "nhs number", "hospital number")
MEDICAL_WORDS = (r"\bGP\b", r"\bNHS\b", r"\bprescri", r"\bhospital\b", r"\bclinic\b", r"\bmedication\b",
                 r"\bsymptoms?\b", r"\breferral\b", r"\bconsultant\b", r"\btreatment\b", r"\bdiagnosis\b",
                 r"\b\d+\s?mg\b", r"\bdosage\b", r"\bsurgery\b", r"\bblood test\b", r"\bmental health\b")
MEDICAL_STRONG_MIN = 2    # two strong words alone, or one with this many of the others
MEDICAL_WORDS_MIN = 3

PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----([A-Za-z0-9+/=\s]+)-----END")
KEY_BODY_MIN = 200        # a real key's body; a slide shows the header with "..." or a few characters
TOKENS = re.compile(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}"
                    r"|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{35})")
ASSIGNED = re.compile(r"\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b"
                      r"[\"']?\s*[:=]\s*[\"']([^\"'\s]+)[\"']", re.I)
ENV_LINE = re.compile(r"^\s*(?:export\s+)?[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY)[A-Z0-9_]*"
                      r"=([^\s\"'#]+)\s*$", re.M)
SECRET_MIN = 8            # shorter values are examples such as "abc123" or "secret"
SECRET_DISTINCT_MIN = 5   # "aaaaaaaa" or "12121212" is a placeholder
TOKEN_DISTINCT_MIN = 10
PLACEHOLDERS = ("your", "example", "xxxx", "changeme", "change_me", "placeholder", "dummy", "redacted", "<", "{",
                "$", "%", "*", "...", "password", "secret", "token", "here", "fake", "test", "sample")

TEACHING = ("lecture", "seminar", "tutorial", "learning outcomes", "in this module", "this week", "for example",
            "e.g.", "students", "assignment", "essay", "coursework", "reflection", "word count", "bibliography",
            "case study", "slide")
TEACHING_MIN = 2


def _lower_count(text: str, words) -> int:
    low = text.lower()
    return sum(1 for w in words if w in low)


def _teaching(rel: str, text: str) -> bool:
    """True when the text reads as teaching or coursework about a subject, not a person's own papers."""
    return _lower_count(f"{rel}\n{text}", TEACHING) >= TEACHING_MIN


def _near(label: re.Pattern, text: str, at: int) -> bool:
    return bool(label.search(text[max(0, at - NEAR_CHARS): at + NEAR_CHARS]))


def luhn(digits: str) -> bool:
    """True when `digits` passes the Luhn check card numbers carry."""
    total = 0
    for n, d in enumerate(reversed(digits)):
        v = int(d) * (2 if n % 2 else 1)
        total += v - 9 if v > 9 else v
    return total % 10 == 0


def iban_ok(iban: str) -> bool:
    """True when `iban` has an IBAN's length and its check digits hold (mod 97 is 1)."""
    s = iban.replace(" ", "").upper()
    if not IBAN_LEN[0] <= len(s) <= IBAN_LEN[1] or not s[:2].isalpha():
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % IBAN_MOD == 1


def nhs_ok(number: str) -> bool:
    """True when ten digits carry a valid NHS number check digit."""
    d = number.replace(" ", "")
    if len(d) != len(NHS_WEIGHTS) + 1 or len(set(d)) == 1:
        return False
    check = NHS_MOD - sum(int(c) * w for c, w in zip(d, NHS_WEIGHTS, strict=False)) % NHS_MOD
    check = 0 if check == NHS_MOD else check
    return check != NHS_MOD - 1 and check == int(d[-1])


def _random_enough(value: str, distinct: int) -> bool:
    """True when `value` looks like a real key or password, not a placeholder."""
    low = value.lower()
    if any(p in low for p in PLACEHOLDERS) or len(set(value)) < distinct:
        return False
    return bool(set(value) & set(string.digits)) or bool(set(value) & set(string.punctuation))


def _secrets(rel: str, text: str) -> bool:
    for m in PRIVATE_KEY.finditer(text):
        if len(re.sub(r"\s", "", m.group(1))) >= KEY_BODY_MIN:
            return True
    if any(_random_enough(t, TOKEN_DISTINCT_MIN) and "EXAMPLE" not in t for t in TOKENS.findall(text)):
        return True
    if _teaching(rel, text):
        return False
    values = ASSIGNED.findall(text) + ENV_LINE.findall(text)
    return any(len(v) >= SECRET_MIN and _random_enough(v, SECRET_DISTINCT_MIN) for v in values)


def _passport(text: str) -> bool:
    lines = [re.sub(r"\s", "", ln) for ln in text.splitlines()]
    zone = [ln for ln in lines if MRZ_LINE.match(ln) and MRZ_FILLER in ln and re.search(r"[A-Z0-9]{3}", ln)]
    return any(MRZ_PASSPORT.match(ln) for ln in zone) or len(zone) >= MRZ_LINES_MIN


def _pay(rel: str, text: str) -> bool:
    if _teaching(rel, text):
        return False
    return _lower_count(text, PAY_TERMS) >= PAY_TERMS_MIN and len(MONEY.findall(text)) >= MONEY_MIN


def _bank(text: str) -> bool:
    if SORT_CODE.search(text) and ACCOUNT.search(text):
        return True
    for found in IBAN.findall(text):
        groups = found.split(" ")
        # a grouped IBAN may have taken the next word in capitals ("... 32 SORT") as one more group
        if any(iban_ok(" ".join(groups[:n])) for n in range(len(groups), 0, -1)):
            return True
    return False


def _card(text: str) -> bool:
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if not CARD_DIGITS[0] <= len(digits) <= CARD_DIGITS[1] or digits in TEST_CARDS or CARD_RUN.search(digits):
            continue
        if CARD_PREFIX.match(digits) and luhn(digits) and (CARD_GROUPED.match(m.group(1))
                                                           or _near(CARD_LABEL, text, m.start())):
            return True
    return False


def _medical(rel: str, text: str) -> bool:
    if any(nhs_ok(m.group(1)) and _near(NHS_LABEL, text, m.start()) for m in NHS_NUMBER.finditer(text)):
        return True
    if _teaching(rel, text):
        return False
    strong = _lower_count(text, MEDICAL_STRONG)
    words = sum(1 for w in MEDICAL_WORDS if re.search(w, text, re.I))
    return strong >= MEDICAL_STRONG_MIN or (strong >= 1 and words >= MEDICAL_WORDS_MIN)


def _birth_and_id(text: str) -> bool:
    return bool(BIRTH.search(text)) and bool(ID_LABEL.search(text))


def _id_number(text: str) -> bool:
    if any(_near(NINO_LABEL, text, m.start()) for m in NINO.finditer(text)):
        return True
    return any(_near(SSN_LABEL, text, m.start()) for m in SSN.finditer(text))


def _said(said: str) -> str:
    words = " ".join(str(said or "").split())
    if not words or words.lower() in {"false", "no", "0", "none", "null"}:
        return ""
    if words.lower() in YES_WORDS or len(words) > SAID_CHARS:
        return SAID_BY_AI
    return words


def reason(rel: str, text: str, said: str = "") -> str:
    """Why the file at `rel` with this text looks private, in a few plain words, or "" when it does not.

    `said` is the AI's own verdict on the file; anything but empty or a plain "no" counts as a reason.
    """
    checks = ((SECRETS, lambda: _secrets(rel, text)), (ID_DOCUMENT, lambda: _passport(text)),
              (PAY, lambda: _pay(rel, text)), (BANK, lambda: _bank(text) or _card(text)),
              (MEDICAL, lambda: _medical(rel, text)), (BIRTH_AND_ID, lambda: _birth_and_id(text)),
              (ID_NUMBER, lambda: _id_number(text)))
    if text.strip():
        for why, hit in checks:
            if hit():
                return why
    return _said(said)


def held_back(row: dict, text: str, said: str = "") -> str:
    """The reason to keep an ingest item out of the record, or "": none when it was allowed or the check is off."""
    from jobhunt.core import settings

    if row.get("allowed") or not settings.get("record.privacy_check"):
        return ""
    return reason(row["rel"], text, said)


def hold(con: sqlite3.Connection, item_id: int, why: str) -> None:
    """Marks an item private with its reason: it gets no note, no copy of the file and no facts."""
    from jobhunt.ingest import items

    items.update(con, item_id, state="private", private=why)


def private_item(con: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """The item kept out as private with this id, unless it was allowed since."""
    return con.execute("SELECT * FROM ingest_items WHERE id = ? AND state = 'private' AND allowed = 0",
                       (item_id,)).fetchone()


def allow(con: sqlite3.Connection, item_id: int) -> None:
    """Remembers that the person wants this file in their record, so its bytes are not held again."""
    con.execute("UPDATE ingest_items SET allowed = 1 WHERE id = ?", (item_id,))


def requeue(con: sqlite3.Connection, batch: str, ids) -> int:
    """Moves the private items `ids` into `batch` to be read, allowed; returns how many moved.

    Like `engine.mark_again`, the row itself moves, so the page's count and its id stay one file.
    """
    n = 0
    for item_id in ids:
        n += con.execute("""UPDATE ingest_items SET batch = ?, state = 'pending', stage = 'discovered', allowed = 1
                            WHERE id = ? AND state = 'private'""", (batch, int(item_id))).rowcount
    con.commit()
    return n
