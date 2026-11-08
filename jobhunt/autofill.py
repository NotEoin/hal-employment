"""Proposes a value for every field of an application form, and says why each answer was chosen.

Sources, in order: the private answers (only for the questions `private_question` names),
answers from notes about the job site (`field_answer` and `field_skip`), answers the person has given to
the same question before, the profile (the `you.*` settings), the job's documents, then the AI for what is
left. Each proposal records its source and a `why` in plain words ("Why this answer?" on the Form step),
naming the profile setting, saved answer, note, CV line or record passage it came from; a field nothing
can answer is left for the person; what they type is remembered for the next form that asks it, at the
scope it suits. Diversity questions get the `you.diversity` answer ("Prefer not to say" unless the person
chose to answer them themselves).

Design notes:
- A private answer's `why` is one fixed sentence and nothing else, so neither the value nor which private
  answer was used reaches the page, a log or the form state's words.
- Site notes come from `sites.notes.for_page`, or the `NoteSet` the engine passes in. A note never
  applies to a private question, because those are settled before notes are read. Each note used is listed
  on its field (`notes_used`); the engines record the use and its result through `sites.notes.used`
  so a note is counted once.
- Saved answers live in `answers2` with a scope: `everywhere`, `site` (this site),
  `job` (this job), `never` (kept only as an example of the person's writing) and `review` (not used
  until the person chooses). An answer about the job applied for (salary, start date, notice, office,
  relocation, how they heard) is kept for that job only, and a "why us" answer or an essay is never
  reused, so nothing typed for one employer is offered to another.
- Answers saved before they had a scope came in through migration 0007, and `answers.rescope` placed them.
- Salary comes from the per-job research unless the person set one answer for every form
  (`you.salary_answer`).
- No prompt names the person: `ai/prompts/form_fill.txt` and the others are filled from the settings.
"""
from __future__ import annotations

import difflib
import itertools
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import cv, db, gpu, llm, record, salary
from .ai import prompts
from .writing import spelling, stage

if TYPE_CHECKING:
    from .sites.notes import Note, NoteSet

HOW_HEARD = {"greenhouse": "Company careers page", "lever": "Company careers page", "ashby": "Company careers page",
             "workable": "Company careers page", "smartrecruiters": "Company careers page",
             "recruitee": "Company careers page", "personio": "Company careers page",
             "80k": "80,000 Hours job board", "hn": "Hacker News", "indeed": "Indeed", "adzuna": "Adzuna",
             "reed": "Reed", "workable-search": "Workable job board", "ktp": "KTP website",
             "jobsacuk": "jobs.ac.uk", "yc": "Y Combinator (Work at a Startup)", "linkedin": "LinkedIn"}

DECLINE = re.compile(r"prefer not|don'?t wish|do not wish|decline|rather not|not to (say|disclose|answer)|"
                     r"choose not|not disclose|no answer", re.I)
YES = re.compile(r"^\s*yes\b", re.I)
NO = re.compile(r"^\s*no\b", re.I)
# Health questions, answered only from the private answers store, and only those the person saved.
DISABILITY = re.compile(r"disab|long[- ]term (physical|health|condition)|neurodiver|health condition|impairment", re.I)
WHICH = re.compile(r"\b(which|what|type|nature|kind|specify|describ\w*|categor\w*|details?|tell us)\b", re.I)
SCHEME = re.compile(r"disability confident|guaranteed interview|interview guarantee|guaranteed (an )?interview", re.I)
ADJUST = re.compile(r"reasonable (adjustment|accommodation)|adjustments?\b|access (needs|requirements)|"
                    r"additional (support|needs|requirements)|extra time|support (you )?(need|require)", re.I)
DETAILS = re.compile(r"^(if (yes|so)|please (give|provide|specify)|details|give details)", re.I)
# Caring for someone else, answered with the applicant's `carer` answer. "Cared for" and "care leaver"
# ask whether they were in care themselves, a different question left to the diversity answer.
CARER = re.compile(r"\bcarers?\b|caring|look(s|ing)? after|care for (someone|anyone|another|a |an |family|relatives?|"
                   r"children|dependants?)|unpaid care|provide care", re.I)
# Questions on how a disability affects the applicant, or on a formal status, are theirs to weigh.
OWN_CALL = re.compile(r"diagnos|affect|prevent|stop you|limit|ability to|registered|day-to-day", re.I)
# About the employer's networks or news, not the applicant.
NOT_ASKED = re.compile(r"hear (more )?about|information about|network|newsletter", re.I)
# A disability question about someone the applicant cares for or lives with is a carer question, not theirs:
# an employer's "look after someone ... caused by disability" once got "Yes, 1-19 hours a week" (2026-10-15).
SOMEONE = re.compile(r"look(s|ing)? after|care (for|of)\b|\bcarers?\b|caring|cared for|someone|anyone|another person|"
                     r"other people|family member|relative|household|dependants?|child(ren)?\b|parent|guardian|partner|"
                     r"spouse", re.I)
HEALTH = ("disability", "disability_type", "guaranteed_interview", "adjustments")
# Every key `private_question` can return; none of them is ever in the profile, a prompt or a saved answer.
PRIVATE_KEYS = (*HEALTH, "carer")
CARED_FOR = re.compile(r"care leaver|been in care|cared for", re.I)
YOURSELF = "Answer this yourself"
# A box for the code a site emails: only the code step types it (`apply.driver._type_code`), so a form fill
# never guesses, recalls or remembers one. Same wording as the code wall `browser.CODE_WALL` looks for.
CODE_BOX = re.compile(r"(verification|security|confirmation|one[- ]time|access) code|passcode|enter the code|"
                      r"code (we|was|that was) (sent|emailed)", re.I)
CODE_NOTE = "the code the site emails you; Hal never guesses it"
# Labels longer than this are paragraphs with a question in them; a keyword match there proves little.
RULE_LABEL = 170
# A box asking for a fact about the person has a short label; a longer one is a question for the model.
FACT_LABEL = 75
# Answers at least this long are prose the person should read before it is sent.
WRITTEN = 80
MODEL_BATCH = 10
SAVED_FACTS = 200     # saved answers read for the AI's facts, newest first
SHORT_FACTS = 60      # of those, short answers listed
VOICE_EXAMPLES = 2    # "never" answers shown as examples of the person's writing
# Answers longer than this are essays: never reused as they stand, only shown to the model as the applicant's writing.
ESSAY = 400

# The "Why this answer?" words. The private sentence is the whole of what a private
# answer ever says about itself.
PRIVATE_WHY = "From your private answers. These are never shown to the AI or saved in logs."
LEFT_PRIVATE_WHY = ("Hal leaves questions like this for you. It never guesses them and never sends them to "
                    "the AI.")
YOU_WHY = "You typed this."
LEFT_WHY = "Hal had nothing to answer this with, so it is left for you."
PROFILE_WORDS = {
    "first_name": "your name", "last_name": "your name", "full_name": "your name", "middle_name": "your name",
    "preferred_name": "the name you like to be called", "email": "your email address", "phone": "your phone number",
    "linkedin": "your LinkedIn address", "github": "your GitHub address", "website": "your website",
    "sponsorship": "your right to work in the UK", "right_to_work": "your right to work in the UK",
    "notice": "your notice period", "start": "when you can start", "office": "whether you can work in an office",
    "relocate": "whether you would move for a job", "postcode": "your postcode", "address1": "your address",
    "country": "your country", "phone_code": "your phone's dialling code", "city": "your town or city",
    "location": "where you are based",
    "nationality": "your nationality", "university": "your university", "degree": "your degree",
    "grade": "your grade", "grad_year": "when your course ends or ended", "salary": "the salary answer you set",
}
SCOPE_WORDS = {"everywhere": "Hal may use it on every form", "site": "Hal uses it only on {key}",
               "job": "Hal uses it only for this job"}
ORIGIN_WORDS = {"you": "your note", "builtin": "a note built into Hal", "learned": "a note Hal learned from you",
                "hal": "a note Hal suggested"}
# The note kinds `propose` acts on; the engines act on the others.
ANSWER_NOTES = ("field_answer", "field_skip")
# Words shorter than this say little about where an answer came from ("the", "and", "with").
SUPPORT_WORD = 5
SUPPORT_SHARED = 3     # content words an answer shares with a CV line or record passage it is said to use
SUPPORT_QUOTE = 90     # characters of that line or passage quoted in the why
CV_FACTS = "CV facts:\n"
UNSURE_NOTE = "Hal is unsure; check this"
CHECKED_WORDS = 6      # an answer this long makes claims; a shorter one is a fact the form asks for (a yes, a date)
ADVERT_LABEL = "Advert (for context only, not facts about the applicant):"
ADVERT_CHARS = 1800    # of the advert, beside the facts in one form request
STYLE_LABEL = ("Drafts written for this job earlier, as examples of style only. They are not facts about the "
               "applicant: never take a claim from them.")


START_WORDS = {"now": "Immediately", "1w": "In 1 week", "1m": "In 1 month"}
WORK_WORDS = cv.WORK_WORDS
# What a choice in a form is matched against when the profile's answer is plain yes or no.
YES_NO_KEYS = ("sponsorship", "right_to_work", "office", "relocate")


def _yes(value) -> str:
    return "Yes" if value else "No"


def profile() -> dict:
    """The person's standard answers, from the `you.*` settings, in the words forms use; never a private answer."""
    from .core import settings

    you = settings.get_many("you.")
    name = (you.get("you.name") or "").strip()
    first, _, last = name.partition(" ")
    work = you.get("you.right_to_work") or "uk_no_visa"
    start = you.get("you.start") or "now"
    study_end = str(settings.get("goal.study_end") or "")
    links = {k: (you.get(f"you.links.{k}") or "").strip() for k in ("linkedin", "github", "website")}
    p = {
        "first_name": first, "last_name": last.strip(), "full_name": name,
        "preferred_name": (you.get("you.preferred_name") or "").strip() or first,
        "email": (you.get("you.email") or "").strip().lower(), "phone": (you.get("you.phone") or "").strip(),
        **links,
        "city": you.get("you.city") or "", "country": you.get("you.country") or "United Kingdom",
        "phone_code": _dial_code(you.get("you.phone") or "", you.get("you.country") or "United Kingdom"),
        "location": you.get("you.location_line") or "",
        "right_to_work": _yes(work != "need_sponsorship"), "sponsorship": _yes(work == "need_sponsorship"),
        "work_status": (you.get("you.work_status") or "").strip() or WORK_WORDS.get(work, ""),
        "nationality": you.get("you.nationality") or "",
        "notice": you.get("you.notice") or "",
        "start": (you.get("you.start_date") or "") if start == "date" else START_WORDS.get(start, ""),
        "university": you.get("you.education.university") or "", "degree": you.get("you.education.degree") or "",
        "grade": you.get("you.education.grade") or "", "grad_year": study_end[:4],
        "office": _yes(you.get("you.office")), "relocate": _yes(you.get("you.relocate")),
        "diversity": "Prefer not to say" if you.get("you.diversity") != "leave" else "",
        "salary": (you.get("you.salary_answer") or "").strip(),
    }
    return {k: v for k, v in p.items() if k not in PRIVATE_KEYS}


def sign_ups_only(fields: list[dict]) -> bool:
    """True when every field is an optional tick box for news or alerts: a site's sign-up, not an application
    (jobs.ac.uk's email preferences beside an advert that sends you elsewhere, testers 2026-11-05)."""
    return bool(fields) and all(
        f["kind"] == "checkbox" and not f.get("required")
        and MARKETING.search(f"{f.get('label') or ''} {f.get('context') or ''}") for f in fields)


# Facts only the applicant has, which no rule, saved note or model may supply (testers, 2026-11-05): the
# expected salary went into "What is your current salary?", and the model said "Yes" to "Have you interviewed
# with Jane Street before?".
CURRENT_PAY = re.compile(r"\b(current|present|most recent|previous|last)\b[^?]{0,30}\b(salary|pay|compensation|"
                         r"remuneration)", re.I)
PAST = re.compile(r"\b(interviewed|applied|worked|employed)\b[^?]*\b(before|previously|ever)\b|"
                  r"\b(before|previously|ever)\b[^?]*\b(interviewed|applied|worked|employed)\b", re.I)
US = re.compile(r"\b(us|our (company|firm|organi[sz]ation)|this (company|firm|organi[sz]ation|employer))\b", re.I)
MIN_NAME = 3   # letters in the first word of an employer's name before it alone names them ("Jane" of Jane Street)
YOURS_WHY = {"pay": "Only you know your current pay, so Hal leaves it for you.",
             "past": "Only you know whether you have dealt with this employer before, so Hal leaves it for you."}


def left_to_you(field: dict, company: str = "") -> str | None:
    """Why Hal leaves this question to the applicant whatever it knows, or None: a question with two answers in
    one, the applicant's current pay, or their past with this employer (`company`, or "us")."""
    label = field.get("label") or ""
    if two_in_one(field):
        return TWO_IN_ONE_WHY
    if CURRENT_PAY.search(label):
        return YOURS_WHY["pay"]
    name = company.split()[0] if company.split() and len(company.split()[0]) >= MIN_NAME else company
    if PAST.search(label) and (US.search(label) or (name and re.search(rf"\b{re.escape(name)}", label, re.I))):
        return YOURS_WHY["past"]
    return None


def two_in_one(field: dict) -> bool:
    """True for a label that asks two opposite questions under one answer: "Are you legally entitled to work in the
    UK? Will you require sponsorship?" Any one answer is wrong for one of them, so it is left to the person."""
    label = field.get("label") or ""
    return bool(RIGHT_TO_WORK.search(label) and SPONSOR.search(label)) and label.count("?") >= 2


def private_question(field: dict) -> str | None:
    """The private key a form question asks about ("disability", "disability_type", "guaranteed_interview",
    "adjustments", "carer"), or None.

    Built from the DISABILITY, ADJUST, SCHEME, WHICH and CARER patterns applied to the label and, for ADJUST,
    the context, with the NOT_ASKED exclusion of `_health`; a disability question about someone the person
    cares for or lives with (SOMEONE) maps to "carer". Decides by the question only, never by whether an
    answer is saved, so every path (the form, the review, the AI, saved answers) agrees.
    """
    label, context = field.get("label") or "", field.get("context") or ""
    if SCHEME.search(label):
        return "guaranteed_interview"
    if ADJUST.search(label) or (DETAILS.search(label) and ADJUST.search(context)):
        return "adjustments"
    if DISABILITY.search(label) and not NOT_ASKED.search(label):
        if SOMEONE.search(label):
            return "carer"
        yes_no = any(YES.search(o) for o in field.get("options") or [])
        return "disability_type" if WHICH.search(label) and not yes_no else "disability"
    if CARER.search(label) and not CARED_FOR.search(label):
        return "carer"
    return None


def _private(key: str) -> str | None:
    """A saved private answer, for typing into the form only; never for a prompt, a log or the profile."""
    from .core.settings.private import value_for_form

    return value_for_form(key) or None


def _private_answers() -> dict:
    return {k: v for k in PRIVATE_KEYS if (v := _private(k))}


DIVERSITY = re.compile(
    r"gender|\bsex\b|sexual orientation|ethnic|\brace\b|disab|veteran|religio|\bbelief\b|pronoun|trans(gender|\*)|"
    r"socio-?economic|free school meals|type of school|household earner|caring responsib|\bcarer\b|care for someone|"
    r"care leaver|been in (local authority )?care|"
    r"neurodiver|age (range|group|band)|identify (my|as|your)|first (generation|in your family)|marital|"
    r"long[- ]term (physical|health)", re.I)
# Opt-ins that are not part of the application; they are left as the form has them.
MARKETING = re.compile(r"newsletter|marketing|talent (community|pool|network)|hear (more )?about|"
                       r"(future|other) (opportunit|vacanc|roles)|job alerts?|notifications?:?$|keep (me|my)|"
                       r"receive (e-?mail )?(communications?|updates|news|offers)", re.I)
CONSENT = re.compile(r"consent|i agree|agree to|privacy|data (protection|processing)|process(ing)? (of )?(my|your) "
                     r"(personal )?(data|information)|terms|acknowledge|i (confirm|understand|certify)|"
                     r"confirm (that|the)|you understand|declaration", re.I)

# (label pattern, profile key, preferred options for a choice, in order)
# Standard answers to questions, however they are worded.
ANSWERS = {"sponsorship", "right_to_work", "notice", "start", "salary", "how_heard", "office", "relocate"}
# "Legal First and Last Name" asks for both names, and the last-name rule gave it the surname (Harvey, testers
# 2026-11-05).
BOTH_NAMES = re.compile(r"first\s*(and|&|/|\+)\s*last|given\s*(and|&)\s*family", re.I)
RIGHT_TO_WORK = re.compile(r"right to work|legally (entitled|authori[sz]ed|eligible|able)|eligib\w+ to work|"
                           r"authori[sz]ed to work|work permit|immigration status|visa status|work status", re.I)
SPONSOR = re.compile(r"sponsor", re.I)
TWO_IN_ONE_WHY = ("It asks whether you may work here and whether you need sponsorship, with one answer for both, so "
                  "Hal leaves it for you.")
RULES = [
    (r"\bpreferred (first )?name\b", "preferred_name", []),
    (r"\bmiddle name", "middle_name", []),
    (r"\bfirst name|given name|forename", "first_name", []),
    (r"\blast name|surname|family name", "last_name", []),
    (r"^(full )?(legal )?name\b|^your name$|full name|" + BOTH_NAMES.pattern, "full_name", []),
    (r"e-?mail", "email", []),
    # Before the phone rule: "Phone country code" asks for the dialling code, not the number (Totaljobs,
    # testers 2026-11-04).
    (r"country code|dial(l)?ing code|calling code|(phone|mobile|telephone) (country )?(code|prefix)|"
     r"international (code|prefix)", "phone_code", []),
    (r"phone|mobile|telephone", "phone", []),
    (r"linkedin", "linkedin", []),
    (r"github", "github", []),
    (r"website|portfolio|personal (site|url|page)|other links?", "website", []),
    (r"sponsor", "sponsorship", []),
    (RIGHT_TO_WORK.pattern, "right_to_work", []),
    (r"notice period", "notice", []),
    (r"start date|when (can|could) you start|earliest (start|availability)|available to start", "start", []),
    (r"salary|compensation|pay expectation|remuneration", "salary", []),
    (r"how did you (hear|find|learn)|where did you (hear|find|see|learn)|referr?al source|^source$", "how_heard", []),
    (r"\boffice\b|on-?site|in person|hybrid|commut", "office", []),
    (r"relocat", "relocate", []),
    (r"post ?code|\bzip\b", "postcode", []),
    (r"address( line)? ?1|street address|^address:?$", "address1", []),
    (r"^country|country of residence|country/region", "country",
     [re.compile(r"united kingdom|^uk\b|great britain", re.I)]),
    (r"^(town|city)\b|town ?/ ?city", "city", []),
    (r"\bcity\b|where are you (based|located)|current location|^location", "location", []),
    (r"nationality|citizenship", "nationality", []),
    (r"universit|institution|college|\bschool\b", "university", []),
    (r"degree|discipline|\bmajor\b|field of study|subject", "degree", []),
    (r"grade|classification|\bgpa\b|\bresults?\b", "grade", []),
    (r"graduat", "grad_year", []),
]
# Options that say "straight away" or "no notice", for a profile answer that says so.
SOON = re.compile(r"immediate|asap|\bnow\b|none|no notice|^0|less than", re.I)
NOT_NEEDED = re.compile(r"(do not|don'?t|will not|won'?t) (require|need)", re.I)
# Sections about someone or something other than the applicant now: their questions are never
# answered with the applicant's own details, and dates there belong to that course or job.
OTHER_PERSON = re.compile(r"referee|references?\b|emergency|next of kin|employment history|work history|"
                          r"(previous|past|current|former) (employ|job|role|position)|employer details|"
                          r"\bmanager\b|supervisor|dependant|spouse|partner'?s", re.I)
HISTORY = re.compile(r"education|qualification|academic|employment|experience|training|course", re.I)
# Answers about the job applied for, which a dated history section is not asking.
ABOUT_THIS_JOB = {"start", "notice", "salary", "office", "relocate", "how_heard"}
HIGH_SCHOOL = re.compile(r"high ?school|secondary school|sixth form|\bgcses?\b|\ba[- ]?levels?\b", re.I)
# Labels a rule's words match that ask about something else (Jane Street, testers 2026-11-05): "What year did you
# graduate high school?" was given the university, and "Secondary Major" the degree.
NOT_FOR = {"university": HIGH_SCHOOL, "grad_year": HIGH_SCHOOL,
           "degree": re.compile(r"secondary|second|minor|double", re.I),
           "first_name": BOTH_NAMES, "last_name": BOTH_NAMES, "preferred_name": BOTH_NAMES}
HEARD_SITE = [re.compile(r"(company|careers?|our) (web)?(site|page)|^website", re.I)]
HEARD_BOARD = [re.compile(r"job ?board|job (site|website|search)|online (job|search)", re.I)]
# Shortest option or answer matched inside the other; shorter ones ("UK", "No") match too much by accident.
MIN_INSIDE = 4
# Places and institutions are typed into search boxes that list nothing until asked.
SEARCHED = {"city", "location", "country", "university"}


def _norm(label: str, company: str = "") -> str:
    """Reduces a label to its words, with the employer's name taken out so it matches the next employer's."""
    text = (label or "").lower()
    if company:
        text = text.replace(company.lower(), "the employer")
    # Asked of every employer under its own name or initials.
    text = re.sub(r"\b(how did you (first )?(hear|find out|learn) (about|of)) .*", r"\1 the employer", text)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


def _asked(f: dict) -> str:
    """The question a field asks, with its section when that says whose details it wants."""
    section = f.get("section") or ""
    return f"{section}: {f.get('label') or ''}" if OTHER_PERSON.search(section) else f.get("label") or ""


def _key(f: dict, company: str = "") -> str:
    """The question a field asks, reduced for remembering its answer; a referee's "Name" is not the applicant's."""
    return _norm(_asked(f), company)


def _choose(options: list[str], value: str, preferred: list) -> str | None:
    """Returns the option that best expresses `value`, or None if none does."""
    for pattern in preferred:
        for opt in options:
            if pattern.search(opt):
                return opt
    want = str(value).strip().lower()
    exact = [o for o in options if o.strip().lower() == want]
    if exact:
        return exact[0]
    close = difflib.get_close_matches(str(value), options, n=1, cutoff=0.75)
    if close:
        return close[0]
    # "Made-up Studies" for "BA (Hons) Made-up Studies", "Leeds" for "Leeds, United Kingdom".
    inside = [o for o in options if len(o.strip()) >= MIN_INSIDE and (o.strip().lower() in want or
                                                                      (len(want) >= MIN_INSIDE and want in o.lower()))]
    return max(inside, key=len) if inside else None


def _fit(f: dict, value, preferred: list) -> object | None:
    """Shapes a known answer to the field's kind, or returns None when the field cannot take it."""
    kind, options = f["kind"], f.get("options") or []
    several = kind == "checkgroup" or (kind == "select" and f.get("multiple"))
    if kind in ("select", "radio", "combobox") and options and not several:
        return _choose(options, value if not isinstance(value, list) else value[0], preferred)
    if several:
        wanted = value if isinstance(value, list) else [value]
        chosen = [c for c in (_choose(options, w, preferred) for w in wanted) if c]
        return chosen or None
    if kind == "checkbox":
        return value if isinstance(value, bool) else str(value).lower() in ("yes", "true")
    if kind in ("select", "radio") or isinstance(value, (list, bool)):
        return None
    if f.get("type") == "number" and not re.fullmatch(r"\s*-?[\d.,]+\s*", str(value)):
        return None  # a box that takes only digits cannot hold a sentence
    return value


def _diversity(f: dict, prof: dict) -> bool:
    """Answers a diversity question with the profile's choice; returns whether it settled the field."""
    if prof.get("diversity") == "":
        # The person chose to answer equality questions themselves.
        f.update(value=None, source="you", note=YOURSELF,
                 why="You chose to answer equality questions yourself (Settings, You).")
        return True
    value = prof.get("diversity") or "Prefer not to say"
    options = f.get("options") or []
    note = "diversity question; change it if you want to answer"
    why = (f"An equality question. Your choice for these (Settings, You) is \"{value}\". Change it here if you "
           "want to answer this one.")
    if f["kind"] in ("select", "radio", "combobox", "checkgroup") and options:
        choice = _choose(options, value, [DECLINE])
        if choice:
            f.update(value=[choice] if f["kind"] == "checkgroup" else choice, source="profile: diversity", note=note,
                     why=why)
        elif not f.get("required"):
            f.update(skip=True, source="skipped", note="optional diversity question with no 'prefer not to say'")
        else:
            return False
        return True
    if f["kind"] in ("text", "textarea"):
        if f.get("required"):
            f.update(value=value, source="profile: diversity", note=note, why=why)
        else:
            f.update(skip=True, source="skipped", note="optional diversity question")
        return True
    return False


def _health(f: dict, prof: dict | None = None) -> bool:
    """Answers a disability, guaranteed-interview or adjustments question from the private answers; True if settled.

    Only the answer to the question `private_question` names is used. Questions on a diagnosis, a registered
    status or how much a disability limits the applicant are left for them; questions about someone they
    care for are not theirs at all.
    """
    asked = private_question(f)
    if asked not in HEALTH:
        return False
    prof = _private_answers()
    label, kind, options = f.get("label") or "", f["kind"], f.get("options") or []
    listed = kind in ("select", "radio", "combobox", "checkgroup") and bool(options)
    if asked == "guaranteed_interview" and prof.get("guaranteed_interview"):
        key, answer = "guaranteed_interview", True if kind == "checkbox" else _fit(f, prof["guaranteed_interview"], [YES])
    elif asked == "adjustments" and prof.get("adjustments"):
        written = kind in ("text", "textarea") and not listed
        key, answer = "adjustments", prof["adjustments"] if written else True if kind == "checkbox" else _fit(f, "Yes", [YES])
    elif asked in ("disability", "disability_type") and prof.get("disability"):
        yeses = [o for o in options if YES.search(o)]
        if OWN_CALL.search(label) or len(yeses) > 1:
            # "Yes, limited a lot" or "a little" is a judgement only the applicant can make.
            f.update(value=None, source="you", note="asks about a diagnosis, a formal status or how your disability "
                                                     "affects you; answer this yourself")
            return True
        named = _choose(options, prof["disability_type"], []) if listed and prof.get("disability_type") else None
        if WHICH.search(label) and prof.get("disability_type") and (named or not listed):
            chosen = [named] if kind == "checkgroup" else named
            key, answer = "disability_type", chosen if listed else prof["disability_type"]
        else:
            # "Do you have a disability? If yes, please give details" with Yes/No options asks the first part.
            key, answer = "disability", True if kind == "checkbox" else _fit(f, prof["disability"], [YES])
    else:
        return False
    if answer in (None, []):
        return False
    f.update(value=answer, source=f"private: {key}", note="from your private answers; check it")
    return True


def _carer(f: dict, prof: dict | None = None) -> bool:
    """Answers a question on caring for someone with the applicant's private `carer` answer; True if settled."""
    label, options = f.get("label") or "", f.get("options") or []
    prof = {"carer": _private("carer")}
    if not prof["carer"] or not CARER.search(label) or CARED_FOR.search(label):
        return False
    no = NO.search(prof["carer"])
    if f["kind"] == "checkbox":
        answer = not no
    elif options:
        answer = _choose(options, prof["carer"], [NO] if no else [YES])
        answer = [answer] if answer and f["kind"] == "checkgroup" else answer
    else:
        answer = prof["carer"]
    if answer in (None, []):
        return False
    f.update(value=answer, source="private: carer", note="from your private answers; check it")
    return True


# Dialling codes by country, for a form that asks for the code apart from the number; a number written with
# its code ("+353 ...") says it itself.
DIAL_CODES = {"united kingdom": "+44", "uk": "+44", "great britain": "+44", "ireland": "+353", "united states": "+1",
              "france": "+33", "germany": "+49", "spain": "+34", "italy": "+39", "netherlands": "+31",
              "india": "+91", "canada": "+1", "australia": "+61"}


def _dial_code(phone: str, country: str) -> str:
    """The dialling code for the person's phone: from the number when it has one, else from their country."""
    written = re.match(r"\s*(\+\d{1,3})[\s-]", phone)
    return written.group(1) if written else DIAL_CODES.get(country.strip().lower(), "")


def _rule(f: dict, prof: dict) -> tuple[object, str] | None:
    label = f.get("label") or ""
    section = f.get("section") or ""
    if OTHER_PERSON.search(section):
        return None
    listed = f["kind"] in ("select", "radio", "combobox", "checkgroup") and bool(f.get("options"))
    rules = RULES
    rtw, sponsor = RIGHT_TO_WORK.search(label), SPONSOR.search(label)
    if rtw and sponsor and rtw.start() < sponsor.start():
        # "Are you authorised to work here without sponsorship?" asks first about the right to work, so it is
        # answered as that; the sponsorship rule answered it "No" (ComplyAdvantage, testers 2026-11-05).
        rules = [r for r in RULES if r[1] != "sponsorship"]
    for pattern, key, preferred in rules:
        if not re.search(pattern, label, re.I) or (key in NOT_FOR and NOT_FOR[key].search(label)):
            continue
        if key in ABOUT_THIS_JOB and HISTORY.search(section):
            return None
        # A fact fits a short label, or a list that holds it; a standard answer fits a question of moderate length.
        if len(label) > (RULE_LABEL if key in ANSWERS else 400 if listed else FACT_LABEL):
            return None
        value = prof.get(key)
        if value in (None, ""):
            return None
        if key == "how_heard":
            # The list rarely words the source as the profile does; the nearest kind of source is chosen.
            preferred = HEARD_SITE if re.search(r"careers page|website", str(value), re.I) else HEARD_BOARD
        elif key in YES_NO_KEYS:
            no = NO.search(str(value))
            preferred = [NO, NOT_NEEDED] if no else [YES]
            if key == "right_to_work" and listed and prof.get("work_status"):
                worded = _choose(f["options"], prof["work_status"], [])
                if worded:
                    return ([worded] if f["kind"] == "checkgroup" else worded, f"profile: {key}")
        elif key == "phone_code":
            # "+44" as a whole code, in "+44" or "United Kingdom (+44)", never inside "+441".
            preferred = [re.compile(rf"(^|[^\d+]){re.escape(str(value))}(?!\d)")]
        elif key in ("notice", "start") and SOON.search(str(value)):
            preferred = [SOON]
        if key == "salary" and prof.get("salary_figure"):
            if f.get("type") == "number":
                value = prof["salary_figure"]
            elif listed:
                choice = salary.band(f["options"], int(prof["salary_figure"]))
                return ([choice] if f["kind"] == "checkgroup" else choice, "profile: salary") if choice else None
        if key == "notice" and (f.get("type") == "number" or re.search(r"\b(weeks|days|months)\b", label, re.I)) \
                and re.search(r"none|immediate", str(value), re.I):
            value = "0"
        if f["kind"] == "combobox" and not f.get("options"):
            # A search box is typed into; any other list that would not open is left to be read again.
            return (value, f"profile: {key}") if key in SEARCHED and f.get("typeable") else None
        fitted = _fit(f, value, preferred)
        return (fitted, f"profile: {key}") if fitted not in (None, []) else None
    return None


def _match_answer(label: str, answers: list[dict]) -> str | None:
    """Returns the drafted answer whose question matches the field's label."""
    best, score_ = None, 0.0
    for a in answers:
        q = a.get("question", "")
        s = difflib.SequenceMatcher(None, label.lower(), q.lower()).ratio()
        if q and (q.lower() in label.lower() or label.lower() in q.lower()):
            s = max(s, 0.9)
        if s > score_:
            best, score_ = a.get("answer"), s
    return best if score_ >= 0.6 else None


# Answers the person has given before ------------------------------------------

def site_key(job) -> str:
    """The site a job's form is on, for answers kept for one site: the host of its address, without "www."."""
    url = (job.get("url") if isinstance(job, dict) else job["url"]) if job is not None else ""
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def _scope(f: dict, label: str, value, job) -> tuple[str, str]:
    """Where an answer the person typed may be used again: (scope, scope key)."""
    if (isinstance(value, str) and len(value) > ESSAY) or WHY.search(label) or FIT.search(label):
        return "never", ""          # an essay or a "why us" answer is only ever an example of their writing
    rule = next((key for pattern, key, _ in RULES if re.search(pattern, f.get("label") or "", re.I)), None)
    if rule in ABOUT_THIS_JOB or "the employer" in _norm(label):
        # about this job or this employer: for this job only, or for the person to choose when the job is unknown
        return ("job", str(job["id"])) if job is not None else ("review", "")
    return "everywhere", ""


def remember(con, fields: list[dict], company: str = "", job=None) -> int:
    """Stores the answers the person typed themselves, each at the scope it suits, so a question is asked once.

    `job` is the job the form is for; without it, answers about a job are kept for the person to place.
    What the person types into a private question is never kept.
    """
    kept = 0
    for f in fields:
        value = f.get("value")
        if f.get("source") != "you" or value in (None, "", []) or f["kind"] == "file" or f.get("type") == "password":
            continue
        if private_question(f) is not None or CODE_BOX.search(f.get("label") or ""):
            continue
        question = _key(f, company)
        if len(question) < 3:
            continue
        scope, scope_key = _scope(f, _asked(f), value, job)
        con.execute("""INSERT INTO answers2 (question, label, answer, scope, scope_key, kind, source, updated)
                       VALUES (?, ?, ?, ?, ?, 'fixed', 'you', ?)
                       ON CONFLICT(question, scope, scope_key) DO UPDATE SET answer = excluded.answer,
                         label = excluded.label, updated = excluded.updated""",
                    (question, _asked(f), db.json_text(value), scope, scope_key, db.now()))
        kept += 1
    con.commit()
    from .writing import edits

    edits.from_fields(con, fields, job)
    return kept


def _usable(con, job) -> list:
    """The saved answers this job's form may use: everywhere, this site's and this job's."""
    if job is None:
        return con.execute("SELECT * FROM answers2 WHERE scope = 'everywhere' ORDER BY updated DESC").fetchall()
    return con.execute("""SELECT * FROM answers2 WHERE scope = 'everywhere'
                          OR (scope = 'site' AND scope_key = ?) OR (scope = 'job' AND scope_key = ?)
                          ORDER BY updated DESC""", (site_key(job), str(job["id"]))).fetchall()


def _recall(con, f: dict, job=None):
    """The answer the person gave to this question before, where it may be used for this job, or None."""
    found = _recall_row(con, f, job)
    return found[0] if found else None


def _recall_row(con, f: dict, job=None) -> tuple[object, object] | None:
    """(answer fitted to the field, its `answers2` row) for this question where this job may use it, or None."""
    if con is None:
        return None
    question = _key(f, job["company"] if job is not None else "")
    rows = [r for r in _usable(con, job) if private_question({"label": r["label"]}) is None]
    by_question = {}
    for r in rows:   # the narrowest scope wins: this job, then this site, then everywhere
        rank = {"job": 0, "site": 1, "everywhere": 2}[r["scope"]]
        if r["question"] not in by_question or rank < by_question[r["question"]][0]:
            by_question[r["question"]] = (rank, r)
    found = by_question.get(question)
    if found is None:
        close = difflib.get_close_matches(question, list(by_question), n=1, cutoff=0.93)
        if not close:
            return None
        found = by_question[close[0]]
    value = db.json_value(found[1]["answer"])
    if isinstance(value, str) and len(value) > ESSAY:
        return None  # an essay answers one employer's question; it shows the AI the person's voice instead
    fitted = _fit(f, value, [])
    return (fitted, found[1]) if fitted not in (None, []) else None


def _saved_why(row) -> str:
    """Why a saved answer was used: the question it answered and where it may be used."""
    where = SCOPE_WORDS.get(row["scope"], SCOPE_WORDS["everywhere"]).format(key=row["scope_key"])
    return (f"You gave this answer before, to \"{row['label']}\". {where} (What Hal remembers, Saved "
            "answers).")


def _count_use(con, row) -> None:
    """Counts one more use of a saved answer, for "Used N times" on the Saved answers tab."""
    con.execute("UPDATE answers2 SET uses = uses + 1, last_used = ? WHERE id = ?", (db.now(), row["id"]))
    con.commit()


# Notes about the job site ------------------------------------------------------

def page_notes(job, notes: NoteSet | None = None) -> NoteSet | None:
    """The notes for this job's form: the `NoteSet` the engine passed, else `sites.notes.for_page` for its address."""
    if notes is not None:
        return notes
    url = _get(job, "url")
    if not url:
        return None
    from .sites import notes as site_notes

    return site_notes.for_page(url, job_id=str(_get(job, "id") or "") or None)


def _get(job, key: str):
    """A job's value by key, from a dict or a database row; None when it has none."""
    if job is None:
        return None
    try:
        return job[key]
    except (KeyError, IndexError):
        return None


def _note_words(note: Note) -> str:
    """Whose note it is and where it applies: "your note for acme.example", "a note built into Hal"."""
    whose = ORIGIN_WORDS.get(note.origin, "a note about this job site")
    if note.scope_kind in ("site", "tenant", "job") and note.scope_key:
        return f"{whose} for {note.scope_key}"
    if note.scope_kind == "ats" and note.scope_key:
        return f"{whose} for every {note.scope_key.title()} site"
    return whose


def _used(note: Note) -> dict:
    """The note as a field's `notes_used` entry."""
    return {"id": note.id, "text": note.text, "origin": note.origin}


def _note_answer(f: dict, notes: NoteSet | None, source: str | None = None) -> bool:
    """Answers or skips the field as the most specific `field_answer` or `field_skip` note says; True if settled.

    Never called for a private question. A note whose answer the field cannot take (a choice its
    list does not offer) is passed over, so the next source gets its turn. `source` is where Hal found the job,
    for the built-in notes that answer "How did you hear" by job board.
    """
    if notes is None:
        return False
    label = f.get("label") or ""
    for note in sorted((n for n in notes.for_field(label, source=source) if n.kind in ANSWER_NOTES),
                       key=lambda n: NOTE_RANK.get(n.scope_kind, len(NOTE_RANK))):
        if note.kind == "field_skip":
            f.update(skip=True, source="note: field_skip", note=note.text, notes_used=[_used(note)],
                     why=f"Left as it is because of {_note_words(note)}: \"{note.text}\".")
            return True
        value = note.act.get("value", note.cond.get("value"))
        if value in (None, "", []):
            continue
        key = next((k for pattern, k, _ in RULES if re.search(pattern, label, re.I)), None)
        preferred = (HEARD_SITE if re.search(r"careers page|website", str(value), re.I) else HEARD_BOARD) \
            if key == "how_heard" else []
        fitted = _fit(f, value, preferred)
        if fitted in (None, []):
            continue
        shown = ", ".join(map(str, fitted)) if isinstance(fitted, list) else fitted
        f.update(value=fitted, source="note: field_answer", notes_used=[_used(note)],
                 why=f"Answered with \"{shown}\" because of {_note_words(note)}: \"{note.text}\".")
        return True
    return False


# The most specific note of a kind wins; `NoteSet` keeps that order, and this keeps it here too.
NOTE_RANK = {"job": 0, "tenant": 1, "site": 2, "ats": 3, "global": 4}


def _site_facts(notes: NoteSet | None) -> list[str]:
    """The free-words notes (`note` kind) the AI is given as facts; never one that reads as a private question."""
    if notes is None:
        return []
    return [n.text for n in notes.all("note") if n.text and private_question({"label": n.text}) is None]


WHY = re.compile(r"why (do you want|are you (interested|applying)|this (role|company|team)|us\b|join)|"
                 r"what (interests|excites|attracts) you|motivat", re.I)
FIT = re.compile(r"why (are you|would you be) (a good|the right|suited)|what makes you|tell us about yourself|"
                 r"relevant experience|why should we", re.I)

# Sources of answers chosen by keyword, which the review pass checks against the question.
REVIEWED = ("profile", "your earlier answer", "documents: drafted answer")


def _prompt(prompt_id: str) -> str:
    """A form prompt filled in with the person's name from the settings."""
    from . import pack

    return pack.prompt(prompt_id)


def profile_for(job, con=None) -> dict:
    """Returns the profile with the answers that depend on the job settled for this one."""
    prof = profile()
    prof["how_heard"] = HOW_HEARD.get(job["source"], "Company careers page")
    if not prof.get("salary"):
        # A direct figure: employers who ask want one, and "open to discussion" reads as dodging it.
        expected = salary.expect(job, con)
        if expected["figure"] > 0:   # nothing known and no lowest pay set: the person answers it
            prof.update(salary=expected["text"], salary_figure=str(expected["figure"]),
                        salary_basis=expected["basis"])
    return prof


def propose(job, fields: list[dict], docs: dict, files: dict, con=None, notes: NoteSet | None = None) -> list[dict]:
    """Fills in `value`, `source`, `note` and `why` on each field and returns the list.

    Args:
        job: The job row.
        fields: Fields read from the form by browser.open_form.
        docs: The approved documents (documents.load).
        files: Paths of the CV and cover letter PDFs (documents.files).
        con: Database connection, for the answers the person has given before.
        notes: The notes for this page, as the engine read them; None reads them for the job's address.
    """
    prof = profile_for(job, con)
    notes = page_notes(job, notes)
    answers = docs.get("answers") or []
    uploads = [f for f in fields if f["kind"] == "file"]
    named = any(CV_UPLOAD.search(_upload_text(f)) for f in uploads)
    # With no upload named for the CV, the first upload named for nothing else takes it: StudySmarter's form
    # named its cover letter and certificate boxes but left the CV's as "Eingabefeld" (testers, 2026-11-04).
    free = next((f for f in uploads if not OTHER_UPLOAD.search(_upload_text(f))), None)
    for f in fields:
        f.update(value=None, source="you", note="", skip=False, why=LEFT_WHY, notes_used=[])
        label = f.get("label") or ""
        text = f"{label} {f.get('context', '')}"
        if f.get("type") == "password":
            # Passwords never pass through Hal or its database.
            f.update(skip=True, source="skipped", note="type this into the form yourself",
                     why="Hal never types passwords. Type it into the form yourself.")
            continue
        if CODE_BOX.search(label):
            f.update(skip=True, source="skipped", note=CODE_NOTE,
                     why="The site emails you this code. Hal never guesses it.")
            continue
        if f["kind"] == "file":
            _upload(f, text, files, named, f is free)
            continue
        if private_question(f) is not None:
            if not (_health(f) or _carer(f)) or f.get("value") is None:
                # Never guessed, never recalled, never sent to the AI.
                note = f.get("note") if f.get("source") == "you" and f.get("note") else YOURSELF
                f.update(value=None, source="you", note=note, why=LEFT_PRIVATE_WHY)
            else:
                f["why"] = PRIVATE_WHY
            continue
        yours = left_to_you(f, _get(job, "company") or "")
        if yours:
            f.update(note=YOURSELF, why=yours)
            continue
        if _note_answer(f, notes, _get(job, "source")):
            continue
        recalled = _recall_row(con, f, job)
        if recalled is not None:
            f.update(value=recalled[0], source="your earlier answer", why=_saved_why(recalled[1]))
            _count_use(con, recalled[1])
            continue
        if DIVERSITY.search(label) and _diversity(f, prof):
            continue
        single = f["kind"] in ("radio", "checkgroup") and len(f.get("options") or []) == 1
        if f["kind"] == "checkbox" or single:
            if MARKETING.search(text) and not f.get("required"):
                f.update(skip=True, source="skipped", note="optional sign-up, left as it is",
                         why="An optional sign-up, so Hal leaves it as it is.")
                continue
            if CONSENT.search(text) or (single and f.get("required")):
                only = f["options"][0] if single else True
                f.update(value=[only] if f["kind"] == "checkgroup" else only, source="profile: consent",
                         note="consent to the employer's terms; check them",
                         why="Hal agrees to the employer's terms so the form can go on. Read them before you send it.")
                continue
        if f["kind"] in ("text", "textarea") and re.search(r"cover letter", label, re.I) and docs.get("letter"):
            f.update(value=docs["letter"], source="documents: cover letter",
                     why="Your cover letter for this job, as you approved it.")
            continue
        hit = _rule(f, prof)
        if hit:
            f["value"], f["source"] = hit
            f["why"] = _profile_why(hit[1].removeprefix("profile: "), prof, job)
            if hit[1] == "profile: salary" and prof.get("salary_basis"):
                f["note"] = f"researched for this job: {prof['salary_basis']}"
            continue
        if f["kind"] in ("text", "textarea"):
            drafted = _match_answer(label, answers)
            if drafted is None and WHY.search(label):
                drafted = _match_answer("Why do you want to work here?", answers)
            if drafted is None and FIT.search(label):
                drafted = _match_answer("Why are you a good fit for this role?", answers)
            if drafted:
                f.update(value=drafted, source="documents: drafted answer",
                         why="From the answers you approved with this job's documents.")
    _skip_follow_ups(fields)
    return mark_written(fields)


# "If yes, please give details", "If you selected other to the above, please specify": a question about the answer
# to the one before it.
FOLLOW_UP = re.compile(r"^\s*if\s+(?:you\s+(?:have\s+)?(?:answered|selected|chose|chosen|ticked|said|picked)\s+)?"
                       r"[\"']?(yes|no|other)\b", re.I)
FOLLOW_UP_WHY = "It asks about a different answer to the question before it, so Hal leaves it empty."


def _skip_follow_ups(fields: list[dict]) -> None:
    """Leaves an optional follow-up empty when the answer just before it is not the one it asks about.

    The model filled "If you answered yes to the above, confirm your visa" with "A" after a "No" (Ashby, testers
    2026-11-04). A follow-up whose question went unanswered is left to the model as before.
    """
    for before, f in itertools.pairwise(fields):
        m = FOLLOW_UP.match(f.get("label") or "")
        given = before.get("value")
        if not m or f.get("required") or f.get("value") not in (None, "", []) or given in (None, "", []):
            continue
        if before["kind"] not in ("radio", "select", "combobox", "checkbox"):
            continue
        said = ("yes" if given else "no") if isinstance(given, bool) else str(given).strip().lower()
        if not said.startswith(m.group(1).lower()):
            f.update(skip=True, source="skipped", note="", why=FOLLOW_UP_WHY)


CV_UPLOAD = re.compile(r"resume|r\u00e9sum\u00e9|\bcv\b|curriculum vitae|lebenslauf", re.I)
LETTER_UPLOAD = re.compile(r"cover|anschreiben|motivation", re.I)
# Uploads for something other than the CV: the letter, or papers Hal does not hold.
OTHER_UPLOAD = re.compile(LETTER_UPLOAD.pattern + r"|autofill|photo|picture|avatar|certificate|zeugnis|"
                          r"transcript|portfolio|passport|reference", re.I)


def _upload_text(f: dict) -> str:
    return f"{f.get('label') or ''} {f.get('context', '')}"


def _upload(f: dict, text: str, files: dict, named: bool, first: bool) -> None:
    """Puts the CV or the cover letter into a file upload, or leaves an upload that wants neither.

    `named` says some upload on the page names the CV; `first` says this is the upload that takes the CV when
    none does.
    """
    if re.search(r"autofill|photo|picture|avatar", text, re.I):
        f.update(skip=True, source="skipped", note="not a CV or cover letter upload")
    elif LETTER_UPLOAD.search(text) and files.get("letter"):
        f.update(value=files["letter"], source="documents: cover letter",
                 why="Your cover letter for this job, as you approved it.")
    elif CV_UPLOAD.search(text) and files.get("cv"):
        f.update(value=files["cv"], source="documents: CV", why="Your CV for this job, as you approved it.")
    elif not named and first and files.get("cv"):
        # A form whose uploads name no CV wants it in the first upload named for nothing else.
        f.update(value=files["cv"], source="documents: CV", note="taken to be the CV upload; check this",
                 why="Your CV for this job. The form does not say which upload is the CV, so Hal used the first "
                     "one not named for another document. Check this.")
    else:
        f.update(skip=True, source="skipped", note="not a CV or cover letter upload")


def _profile_why(key: str, prof: dict, job) -> str:
    """Why a profile answer was used, naming the setting it came from."""
    if key == "how_heard":
        where = prof.get("how_heard") or "the employer's careers page"
        if re.fullmatch(r"company careers page", where, re.I):
            where = "the employer's own careers page"
        return f"Hal found this job on {where}. This answer is worked out per job site."
    if key == "salary" and prof.get("salary_basis"):
        return f"Researched for this job: {prof['salary_basis']}. Please check you are happy with it."
    words = PROFILE_WORDS.get(key)
    return f"From your profile (Settings, You): {words}." if words else "From your profile (Settings, You)."


def by_you(f: dict, value, note: str = "") -> dict:
    """Marks a field as answered by the person, so its words and "Why this answer?" say so; returns it.

    Hal's own answer is kept as `hal_value`, so `remember` can learn from what the person changed.
    """
    if f.get("source") != "you" and isinstance(f.get("value"), str) and f["value"]:
        f.setdefault("hal_value", f["value"])
    f.update(value=value, source="you", note=note, skip=False, why=YOU_WHY, notes_used=[])
    return f


SITE_WHY = "The site had already filled this in, and Hal left it as it was."


def keep_site_answers(fields: list[dict]) -> list[dict]:
    """Keeps what the site already filled in where Hal has no answer of its own.

    A dialling code the site set to "+44" was counted as a required question Hal could not answer (Totaljobs,
    testers 2026-11-04). Tick boxes and uploads are left out: an unticked box is no answer, and a file the
    site holds is not one Hal can check. A question Hal left for the person on purpose, such as a private one,
    carries a note and stays theirs.
    """
    for f in fields:
        current = f.get("current")
        if f.get("value") in (None, "", []) and not f.get("skip") and not f.get("note") \
                and f["kind"] not in ("file", "checkbox") and current not in (None, "", [], False) \
                and f.get("type") != "password":
            f.update(value=current, source="site: already filled", why=SITE_WHY, note="")
    return fields


def mark_written(fields: list[dict]) -> list[dict]:
    """Flags the answers that are prose, which the person reads before anything is sent."""
    for f in fields:
        value = f.get("value")
        f["written"] = f["kind"] in ("text", "textarea") and isinstance(value, str) and len(value) >= WRITTEN \
            and not f.get("source", "").startswith("profile")
    return fields


def _conventions(job) -> str:
    """The conventions for answers in this kind of job (`writing.fields`): STAR for a Civil Service form."""
    from .writing import fields

    return fields.block(fields.for_job(job), "answer")


def _facts(job, docs: dict, con, notes: NoteSet | None = None) -> str:
    """What the AI may know when it fills a form: the profile, the CV, saved answers this job may use, and the
    free-words notes about this job site."""
    hidden = ("salary_figure", "salary_basis", "preferred_name", *PRIVATE_KEYS)
    prof = {k: v for k, v in profile_for(job, con).items() if k not in hidden and v not in ("", None)}
    parts = [stage.today_lines(stage.current(con)),
             f"Job: {job['title']} at {job['company']} ({job['location'] or 'location not stated'}).",
             f"Applicant profile: {prof}",
             "The applicant has never worked for, interviewed with or been referred to this employer "
             "unless stated below.",
             f"{CV_FACTS}{cv.facts_text()}"]
    if con is not None:
        before = con.execute("""SELECT title, status FROM jobs WHERE lower(company) = lower(?) AND id != ?
                                AND status IN ('applied','assessment','interview','offer','rejected','withdrawn')""",
                             (job["company"], job["id"])).fetchall()
        if before:
            parts.append("Earlier applications to this employer: "
                         + "; ".join(f"{r['title']} ({r['status']})" for r in before))
        told = [(r["label"], db.json_value(r["answer"])) for r in _usable(con, job)[:SAVED_FACTS]
                if private_question({"label": r["label"]}) is None]
        short = [(q, a) for q, a in told if not (isinstance(a, str) and len(a) > ESSAY)]
        voice = [(r["label"], db.json_value(r["answer"])) for r in con.execute(
                     "SELECT label, answer FROM answers2 WHERE scope = 'never' ORDER BY length(answer) DESC LIMIT ?",
                     (VOICE_EXAMPLES,))
                 if private_question({"label": r["label"]}) is None]
        if short:
            parts.append("Answers the applicant has given on other forms:\n"
                         + "\n".join(f"- {q}: {a}" for q, a in short[:SHORT_FACTS]))
        if voice:
            parts.append("Answers the applicant wrote for other employers, as examples of their voice and phrasing "
                         "(write fresh answers for this job; never copy their claims about other employers):\n"
                         + "\n\n".join(f"Q: {q}\nA: {str(a)[:1500]}" for q, a in voice))
    told_notes = _site_facts(notes)
    if told_notes:
        parts.append("Notes about this job site (follow them):\n" + "\n".join(f"- {t}" for t in told_notes))
    parts.append(_conventions(job))
    if job["description"]:
        parts.append(f"{ADVERT_LABEL}\n{job['description'][:ADVERT_CHARS]}")
    return "\n\n".join(parts)


def _style_examples(docs: dict) -> str:
    """The answers and letter drafted for this job earlier, as examples of style only; "" when there are none.

    Never facts: a claim a draft made up, fed back in as a fact, came back in the form's answers (2026-11-06).
    """
    parts = []
    if docs.get("answers"):
        parts.append("Answers:\n" + "\n".join(f"- {a['question']}: {a['answer']}" for a in docs["answers"]))
    if docs.get("letter"):
        parts.append(f"Cover letter:\n{docs['letter']}")
    return f"\n\n{STYLE_LABEL}\n" + "\n\n".join(parts) if parts else ""


def _cv_lines(facts: str) -> list[str]:
    """The CV's lines as the AI was given them in `facts`, for naming the one an answer draws on."""
    _, found, rest = facts.partition(CV_FACTS)
    return [line for line in rest.split("\n\n", 1)[0].splitlines() if line.strip()] if found else []


def _content_words(text: str) -> set[str]:
    return set(re.findall(rf"[a-z]{{{SUPPORT_WORD},}}", str(text).lower()))


def _best_support(value, passages: list[str]) -> str | None:
    """The passage sharing the most content words with the answer, when it shares enough to have been used."""
    words = _content_words(value)
    best, shared = None, 0
    for passage in passages:
        n = len(words & _content_words(passage))
        if n > shared:
            best, shared = passage, n
    return best if shared >= SUPPORT_SHARED else None


def _quote(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SUPPORT_QUOTE else text[:SUPPORT_QUOTE].rsplit(" ", 1)[0] + "..."


def _written_why(value, cv_lines: list[str], found: str, *, sure: bool) -> str:
    """Why the AI's answer reads as it does: the CV line and the record passage it draws on, when one shows."""
    used = []
    line = _best_support(value, cv_lines)
    if line:
        used.append(f"your CV (\"{_quote(line)}\")")
    passages = re.split(r"\n\n(?=\[)", found) if found else []
    passage = _best_support(value, passages)
    if passage:
        head = re.match(r"\[(.+?)\]", passage)
        used.append(f"your record ({head.group(1) if head else _quote(passage)})")
    source = f" from {' and '.join(used)}" if used else " from your profile, your CV and the advert"
    check = "Please read it." if sure else "Hal is unsure, so please check it."
    return f"Written for this job by Hal{source}. {check}"


def _shortlist(options: list[str], facts: str, limit: int = 60) -> list[str]:
    """Cuts a long option list to those sharing most words with the facts, so the right one is in view."""
    if len(options) <= limit:
        return options
    known = set(re.findall(r"[a-z]{4,}", facts.lower()))
    ranked = sorted(options, key=lambda o: -len(set(re.findall(r"[a-z]{4,}", o.lower())) & known))
    keep = set(ranked[:limit - 5]) | {o for o in options if re.search(r"^(other|none|not applicable|n/a)", o, re.I)}
    return [o for o in options if o in keep]


def ask_model(job, fields: list[dict], docs: dict, con=None, problems: dict | None = None,
              notes: NoteSet | None = None) -> tuple[int, str | None]:
    """Asks the local model for the fields still without a value.

    Args:
        problems: For fields the form refused, the site's message by field id; those are answered again.
        notes: The notes for this page, as for `propose`; their free words are given to the AI as facts.

    Returns:
        (answered, reason): how many fields it filled, and why it could not run, if so.
    """
    problems = problems or {}
    if not problems and not gpu.busy():
        _review(job, fields)
    open_fields = [f for f in fields if f["kind"] != "file" and f.get("type") != "password"
                   and private_question(f) is None and left_to_you(f, _get(job, "company") or "") is None
                   and (f["hid"] in problems or (f.get("value") in (None, "", []) and not f.get("skip")))]
    if not open_fields:
        return 0, None
    reason = gpu.busy()
    if reason:
        return 0, reason
    facts = _facts(job, docs, con, notes=page_notes(job, notes))
    style = _style_examples(docs)
    cv_lines = _cv_lines(facts)
    spelt_in = spelling.chosen()
    answered, written, passages = 0, [], []
    for start in range(0, len(open_fields), MODEL_BATCH):
        batch = open_fields[start:start + MODEL_BATCH]
        listing = "\n".join(
            f"- hid={f['hid']} kind={f['kind']}"
            + (f" section={f['section']!r}" if f.get("section") else "")
            + f" label={f.get('label')!r}"
            + (f" options={_shortlist(f['options'], facts)!r}" if f.get("options") else "")
            + (f" help={f['help']!r}" if f.get("help") else "")
            + (f" context={f['context']!r}" if f.get("context") else "")
            + (f" max_characters={f['maxlength']}" if f.get("maxlength") else "")
            + (" (required)" if f.get("required") else " (optional)")
            + (f" REFUSED BY THE FORM: {problems[f['hid']]!r}; previous answer {f.get('value')!r}"
               if f["hid"] in problems else "")
            for f in batch)
        try:
            # The employment record holds what the CV leaves out: certificates, the full story of each job.
            found = record.context([f"{f.get('label') or ''} {f.get('help') or ''}" for f in batch])
            kept = f"\n\nEmployment record ({record.OWN_DOCUMENTS}):\n{found}" if found else ""
            reply = llm.chat_json(_prompt("form_fill"), f"Fields:\n{listing}\n\n{facts}{kept}{style}", role="main",
                                  timeout=300)
        except llm.LLMUnavailable as e:
            return answered, str(e)
        passages.append(found)
        by_hid = {f["hid"]: f for f in batch}
        for a in reply.get("answers", []):
            if not isinstance(a, dict):
                continue
            f = by_hid.get(str(a.get("hid")))
            value = a.get("value")
            if f is None or value in (None, "", []):
                continue
            if f.get("options"):
                pool = f["options"]
                value = [v for v in (value if isinstance(value, list) else [value]) if v in pool]
                if not value:
                    continue
                if f["kind"] != "checkgroup":
                    value = value[0]
            elif f["kind"] in ("radio", "select", "checkgroup"):
                continue
            if f["kind"] == "checkbox":
                value = str(value).lower() in ("true", "yes", "1")
            if isinstance(value, str) and not f.get("options"):
                value = spelling.respell(llm.undash(value), spelt_in)
            if f.get("type") == "number" and not re.fullmatch(r"\s*-?[\d.,]+\s*", str(value)):
                continue
            f.update(value=value, source="hal", skip=False, note="" if a.get("sure") else UNSURE_NOTE,
                     why=_written_why(value, cv_lines, found, sure=bool(a.get("sure"))))
            answered += 1
            written.append(f)
    check_written(job, written, facts, "\n\n".join(p for p in passages if p))
    mark_written(fields)
    return answered, None


def _check_this(f: dict, what: str) -> None:
    f["note"] = f"{UNSURE_NOTE}: {what}"


def _makes_claims(f: dict) -> bool:
    value = f.get("value")
    return isinstance(value, str) and not f.get("options") and len(value.split()) >= CHECKED_WORDS


def _answer_claiming(claim: str, prose: list[dict]) -> dict | None:
    """The answer a quoted claim comes from: the one sharing most of its words; None when none shares any."""
    words = _content_words(claim)
    best = max(prose, key=lambda f: (claim.lower() in f["value"].lower(), len(words & _content_words(f["value"]))))
    return best if claim.lower() in best["value"].lower() or words & _content_words(best["value"]) else None


def check_written(job, answered: list[dict], facts: str, found: str) -> None:
    """Marks the AI's written answers that hold a number no fact holds, or a claim about the person their
    facts do not support, for the person to check.

    The same checks as a pack's drafts (`pack.unsupported_numbers`, `pack.unsupported_claims`), with one
    verify call for the whole form. The advert is the employer's own words, never flagged for being
    restated. A marked answer is not rewritten: the person reads it on the Form step.
    """
    from . import pack

    prose = [f for f in answered if _makes_claims(f)]
    if not prose:
        return
    for f in prose:
        numbers = pack.unsupported_numbers(f["value"], facts, found, f.get("label") or "", f.get("help") or "")
        if numbers:
            _check_this(f, f"{', '.join(numbers)} is in none of your facts")
    drafts = "\n".join(f"- {f.get('label') or ''}: {f['value']}" for f in prose)
    try:
        claims = pack.unsupported_claims(pack.person(), drafts, facts=facts, kept=found,
                                         employer=(str(_get(job, "description") or ""),))
    except llm.LLMUnavailable:
        return   # the numbers check stands alone; each answer already says to read it
    for claim in claims:
        f = _answer_claiming(claim, prose)
        if f is not None:
            _check_this(f, f"not in your facts: {claim}")


def _review(job, fields: list[dict]) -> None:
    """Has the model look over the keyword answers; any it says miss the question are cleared for it to answer."""
    chosen = [f for f in fields if not f.get("reviewed") and not f.get("skip") and f["kind"] != "file"
              and private_question(f) is None
              and str(f.get("source", "")).startswith(REVIEWED) and f.get("value") not in (None, "", [])]
    if not chosen:
        return
    listing = "\n".join(f"- hid={f['hid']} section={f.get('section') or ''!r} question={f.get('label')!r} "
                        f"answer={f['value']!r}" for f in chosen)
    try:
        reply = llm.chat_json(_prompt("form_review"), f"Job: {job['title']} at {job['company']}\n\nAnswers:\n{listing}",
                              role="main", timeout=180)
    except llm.LLMUnavailable:
        return
    by_hid = {f["hid"]: f for f in chosen}
    for f in chosen:
        f["reviewed"] = True
    for w in reply.get("wrong", []):
        f = by_hid.get(str(w.get("hid"))) if isinstance(w, dict) else None
        if f is not None:
            why = str(w.get("why") or "").strip()
            f.update(value=None, source="you", note=f"Hal's first answer looked wrong{': ' + why if why else ''}",
                     why="Hal's first answer did not seem to fit the question, so it is left for you"
                         + (f": {why}" if why else "."))


def choose_control(job, found: dict) -> str:
    """Asks the model which control on a page without a recognised Apply button leads to the form."""
    names = list(dict.fromkeys(c["name"] for c in found.get("controls", [])
                               if private_question({"label": c.get("name")}) is None))
    if not names or gpu.busy():
        return ""
    prompt = (f"Job: {job['title']} at {job['company']}\nPage title: {found['title']}\n"
              f"Page address: {found['url']}\nControls:\n" + "\n".join(f"- {n}" for n in names[:60]))
    try:
        reply = llm.chat_json(prompts.text("choose_control"), prompt, role="main", timeout=120)
    except llm.LLMUnavailable:
        return ""
    press = str(reply.get("press") or "")
    return press if press in names else ""
