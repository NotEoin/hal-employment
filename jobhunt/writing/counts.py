"""A number in a draft that counts something the facts do not count that way: a check that needs no AI.

Real letters counted a number of tests as a number of the things tested, and claimed a span of years no fact
gave, and the one AI check let both through (2026-11-19). `writing.check` already lists a number in digits
that no source holds; this module reads what each number counts.

Design decisions, each with its reason:

- A draft's number and the words after it in its clause must meet the same number in a clause of the facts
  with a word of the same stem beside it: "nine scripted intruders" fails when the facts say one intruder was
  contained in all 9 break-in tests. Windows never cross a clause or a fact, as the next fact's words would
  count for the number before them.
- It reads only plain counts, never a year, sum, mark, share, ordinal, rough number ("two or three"), small
  count of a list the facts spell out ("two operating systems") or the letter's length: each line it adds to
  "Check before sending" costs the person a look, so it stays quiet when unsure. The AI check weighs the rest
  (`verify`).
"""
from __future__ import annotations

import re

from jobhunt.writing import check
from jobhunt.writing.drafts import LITTLE_WORDS

# Numbers a draft may write as words, to their digits: "nine" is the facts' 9.
NUMBER_WORDS = {w: str(n) for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())} | {"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
                                                      "seventy": "70", "eighty": "80", "ninety": "90",
                                                      "hundred": "100", "thousand": "1000"}
# A number in brackets, with a currency sign, a "%", or a ":" or "/" inside ("(74)", "2:1", "72/100", "2023/24") is
# one token that is never read as a count: a mark, a sum, a share or a date.
COUNT_TOKEN = re.compile(r"\(\d[\d,.]*\)|[$\u00a3\u20ac]?\d[\d,.:/]*\d%?|[$\u00a3\u20ac]?\d%?|[a-z]+", re.I)
COUNTED_AFTER = 3          # words after a number a draft names what it counts in ("nine scripted intruders")
# Words of the facts' own sentence before and after the same number that may name what it counts: "a team of
# five", "9 tests", "from 40 minutes to 8". Wider let "intruder ... in all 9 tests" count intruders.
FACT_BEFORE, FACT_AFTER = 2, 3
CLAUSE = re.compile(r"[;()]|[,:](?!\d)")   # a number counts nothing past the end of its clause
COUNT_STEM = 4             # letters two words share to be one counted thing ("intruder" and "intruders")
YEAR = re.compile(r"^(?:19|20)\d\d$")
SINGLES = frozenset({"0", "1"})   # "one tool", "no errors": too common in plain prose to hold to the facts
# Words after a number that count nothing a fact must hold: the length the person asked for, a share, an order.
NOT_COUNTED = frozenset("words word percent per cent st nd rd th am pm".split())
ROUGH = frozenset({"or", "to"})   # "two or three pieces", "five to ten": a rough number, not a count of the facts
RANGE_BACK = 3             # words back to the first number of a range: "from 40 minutes to 8"
MARKED = frozenset("scored score scoring mark marked grade graded average averaged averaging".split())  # "scored 74 in"
# A small count written out ("two operating systems" for "Linux and Windows") is the writer counting a list the
# facts give, which they rarely number (a false alarm in a real pack, 2026-11-19); a span of time never is.
LISTED_MOST = 4
TIME_UNITS = frozenset("year years month months week weeks day days hour hours decade decades".split())
COUNT_SAID = "says {said}; your facts give {number} only of {theirs}"
COUNT_NONE = "says {said}; your facts give no such number"


def _count_tokens(text: str) -> list[str]:
    """The words and numbers of a text, lower case, a number word as its digits and digits without commas."""
    out = []
    for tok in COUNT_TOKEN.findall(text or ""):
        low = tok.lower().replace(",", "").rstrip(".")
        out.append(NUMBER_WORDS.get(low, low))
    return out


def _counted(tokens: list[str], k: int, span: int) -> list[str]:
    """The content words within `span` words after `tokens[k]`."""
    return [t for t in tokens[k + 1:k + 1 + span] if not t.isdigit() and t not in LITTLE_WORDS]


def _fact_counts(facts: str) -> dict[str, list[tuple[list[str], list[str]]]]:
    """Each number of the facts, to the words around it in its own sentence: (`FACT_BEFORE` words before and
    `FACT_AFTER` after, the content words after it). A window never crosses into the next fact or clause, as
    "9 tests.\n- Logs" counts no logs and "9 tests, kept logs" no logs."""
    out: dict[str, list[tuple[list[str], list[str]]]] = {}
    for line in (facts or "").splitlines():
        for clause in (c for s in check.sentences(line) for c in CLAUSE.split(s)):
            toks = _count_tokens(clause)
            for j, t in enumerate(toks):
                if t.isdigit():
                    around = [w for w in toks[max(0, j - FACT_BEFORE):j + FACT_AFTER + 1] if not w.isdigit()]
                    out.setdefault(t, []).append((around, _counted(toks, j, FACT_AFTER)))
    return out


def miscounted(draft: str, facts: str) -> list[str]:
    """The sentences giving a number of something the facts do not count that way, each with why: the number is
    in no fact ("four years" of work no fact gives), or the facts give it only of other things ("nine scripted
    intruders" where they say one intruder was contained in all 9 break-in tests, 2026-11-19).

    Conservative: only a number with a word naming what it counts within `COUNTED_AFTER` words of its clause is
    read, so "where I scored 74, and ..." is not; a year, a sum, a mark, a share, a number of words (the length the
    person asked for), an ordinal, one, zero, a rough number ("two or three", "from 40 minutes to 8") and a small
    count of anything but time (`LISTED_MOST`) are never read; and the facts' number counts the same thing when a
    word of its own clause within `FACT_BEFORE` before or `FACT_AFTER` after it shares `COUNT_STEM` letters with
    what the draft counts. A number in digits no source holds is already listed by `writing.check`."""
    theirs = _fact_counts(facts)
    out = []
    for sentence in check.sentences(draft):
        why = next((w for clause in CLAUSE.split(sentence) if (w := _miscount(clause, sentence, theirs))), "")
        if why:
            out.append(f"{sentence} ({why})")
    return out


def _miscount(clause: str, sentence: str, theirs: dict[str, list[tuple[list[str], list[str]]]]) -> str:
    """Why the first number of a clause the facts do not count that way is wrong, or "" (see `miscounted`)."""
    mine = _count_tokens(clause)
    for k, t in enumerate(mine):
        counted = _counted(mine, k, COUNTED_AFTER)
        if not t.isdigit() or t in SINGLES or YEAR.match(t) or not counted or counted[0] in NOT_COUNTED:
            continue
        if _rough(mine, k) or (k and mine[k - 1] in MARKED) or (int(t) <= LISTED_MOST
                                                                 and counted[0] not in TIME_UNITS):
            continue
        said = " ".join([t, *counted])
        # "a team of five students" counts the team as much as the students
        before = [mine[k - 2]] if k >= 2 and mine[k - 1] == "of" else []
        stems = {c[:COUNT_STEM] for c in (*counted, *before)}
        places = theirs.get(t, [])
        if any(w[:COUNT_STEM] in stems for around, _ in places for w in around):
            continue
        if places:
            what = ", ".join(dict.fromkeys(" ".join(after) or "something else" for _, after in places))
            return COUNT_SAID.format(said=said, number=t, theirs=what)
        if not _in_digits(sentence, t):   # digits no source holds are listed by `writing.check` already
            return COUNT_NONE.format(said=said)
    return ""


def _rough(mine: list[str], k: int) -> bool:
    """True when the number at `k` is one end of a range or a choice ("two or three", "from 40 minutes to 8")."""
    if k + 1 < len(mine) and mine[k + 1] in ROUGH:
        return True
    return k >= 2 and mine[k - 1] in ROUGH and any(w.isdigit() for w in mine[max(0, k - 1 - RANGE_BACK):k - 1])


def _in_digits(sentence: str, number: str) -> bool:
    """True when `number` stands in `sentence` in digits ("9"), not only as a word ("nine")."""
    return number in {tok.replace(",", "") for tok in re.findall(r"\d[\d,]*", sentence)}
