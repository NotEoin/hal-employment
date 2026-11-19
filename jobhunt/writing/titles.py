"""A job's title as a person would say it: an advert's title without the board's padding.

`spoken(title, location="")` is what every document Hal writes calls the job: the letter, the message and the
answers (their requests), the message's opening, the letter's subject line, the pack's heading, the
recruiter's read and the interview notes. "Trainee DevOps Engineer | No experience needed (Ref: 7501)" is
"Trainee DevOps Engineer".

Design decisions, each with its reason:

- One letter said it was applying for "Trainee DevOps Engineer | No experience needed (Ref: 7501)", the
  board's raw title (owner's trial, 2026-11-19), so the title is cleaned once, here, with no AI.
- Conservative: only what is clearly not the job's name goes. A tail after " | " goes, as boards and agencies
  put their padding there; a tail after a dash, a comma or in brackets goes only when the whole tail is
  padding: a reference number, a salary, the advert's own location or a way of working ("Remote", "Hybrid"),
  or an agency's phrase ("No experience needed", "Immediate start", "Apply now"). "Software Engineer -
  Python" and "Research Assistant (Fixed Term)" keep their words.
- A title in capitals throughout ("SOFTWARE DEVELOPER") is put in title case, keeping the acronyms a job title
  uses (`ACRONYMS`) and any word holding a digit; a title with lower-case letters keeps its capitals as the
  employer wrote them ("DevOps", "iOS").
- When nothing would be left, the title is kept as it was given.
"""
from __future__ import annotations

import re

# Words a job title writes in capitals that title case must keep.
ACRONYMS = frozenset("""AI API AWS BI CAD CNC CRM CSS DBA EU GCP GIS HGV HR HTML ICT IOS IT ML NHS PA PHP PR QA
QC RAF SAP SEN SEO SQL SRE UI UK US UX II III IV""".split())
SMALL_WORDS = frozenset("a an and at for in of on or the to with".split())
SHOUTING_SHARE = 0.8      # share of a title's letters in capitals from which it reads as shouting
# A reference number: "Ref: 7501", "Job ref 12-34", "Req #8812", "ID 4471", "Vacancy no. 77".
REFERENCE = re.compile(r"^(?:job\s+)?(?:ref(?:erence)?|req(?:uisition)?|id|vacancy)\b(?:\s*(?:no|number))?\.?\s*"
                       r"[:#.]?\s*[\w/-]*\d[\w/-]*$", re.I)
CODE = re.compile(r"^[A-Z]{0,4}[-/]?\d{3,}[\w/-]*$")             # a bare code: "7501", "JR-12345"
SALARY = re.compile(r"(?:\u00a3|GBP|\$|\u20ac)\s*\d|\b\d+(?:\.\d+)?\s*k\b|\b(?:salary|per annum|p\.?a\.?|"
                    r"per hour|an hour|DOE|competitive|OTE|up to)\b", re.I)
WORKING = re.compile(r"^(?:fully\s+|100%\s+)?(?:remote|hybrid|on-?site|office[- ]based|home[- ]based|"
                     r"work from home|wfh)(?:\s+working)?$", re.I)
PADDING = re.compile(r"^(?:no (?:prior |previous )?experience (?:needed|required|necessary)|immediate start|"
                     r"start (?:asap|immediately)|apply (?:now|today)|urgent(?:ly)?(?: required| hiring)?|"
                     r"hiring now|now hiring|new|hot job|full training(?: provided| given)?|"
                     r"training provided|great benefits|excellent benefits|entry level|graduates? welcome|"
                     r"(?:full|part)[- ]time|permanent|temporary|contract)$", re.I)
LEAD = re.compile(r"^(?:urgent(?:ly)?(?: required)?|new|hot job|hiring|now hiring|apply now)\s*[:!-]\s*", re.I)
BAR = re.compile(r"\s+\|\s+|\s*\|\s*$")
TAIL = re.compile(r"\s+[-\u2013\u2014]\s+|\s*,\s+")
BRACKETED = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]")


def _padding(part: str, location: str) -> bool:
    """True when a part of a title (a tail or a bracket's words) says nothing of the job's name."""
    words = part.strip(" .:;-")
    if not words:
        return True
    if REFERENCE.match(words) or CODE.match(words) or SALARY.search(words) or WORKING.match(words) \
            or PADDING.match(words):
        return True
    place, said = location.strip().lower(), words.lower()
    # "Newcastle" of "Newcastle upon Tyne, Tyne and Wear"; "London" of "London, UK"
    return bool(place) and (said == place or said in [p.strip() for p in place.split(",")]
                            or re.match(rf"{re.escape(said)}\b", place) is not None)


def _without_brackets(title: str, location: str) -> str:
    return BRACKETED.sub(lambda m: "" if _padding(m.group(1), location) else m.group(0), title)


def _without_tails(title: str, location: str) -> str:
    """The title without its trailing parts that are padding, the last first, so a real part before one stays."""
    parts = TAIL.split(title)
    seps = TAIL.findall(title)
    while len(parts) > 1 and _padding(parts[-1], location):
        parts.pop()
        seps.pop()
    return "".join(p + s for p, s in zip(parts, [*seps, ""], strict=True))


def _shouting(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    return len(letters) > 3 and sum(c.isupper() for c in letters) >= SHOUTING_SHARE * len(letters)


def _cased(title: str) -> str:
    """A title in capitals throughout, in title case with its acronyms kept."""
    out = []
    for k, word in enumerate(title.split()):
        bare = word.strip("()[],.:;&/")
        if bare.upper() in ACRONYMS or any(c.isdigit() for c in word):
            out.append(word)
        elif k and bare.lower() in SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in word.split("-")))
    return " ".join(out)


def spoken(title: str, location: str = "") -> str:
    """`title` as a person would say it: without reference numbers, a tail after " | ", salary, location and
    agency padding, and in title case when it was in capitals; `location` is the advert's own, so a tail naming
    it goes. The title as given when nothing would be left (see the module notes)."""
    given = " ".join(str(title or "").split())
    said = BAR.split(given, maxsplit=1)[0] if BAR.search(given) else given
    said = LEAD.sub("", said)
    for _ in range(2):        # a bracket inside a tail, then the tail, or the other way round
        said = _without_tails(_without_brackets(said, location), location)
    said = said.strip(" -\u2013\u2014,|:;")
    if not said:
        return given
    return _cased(said) if _shouting(said) else said


def of_job(job) -> str:
    """A job row's title as a person would say it (`spoken`), with its advert's location."""
    keys = job.keys()
    return spoken(str(job["title"] or ""), str((job["location"] if "location" in keys else "") or ""))
