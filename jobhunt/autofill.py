"""Proposes a value for every field of an application form.

Sources, in order: answers Firstname has given to the same question before, the
applicant profile (contact details from the CV site and standard answers from
config.toml's [applicant] table), the job's documents, then the local model
for what is left. Each proposal records its source, and a field nothing can
answer is left for Firstname; what he enters is remembered for the next form that
asks it. Diversity questions default to 'prefer not to say' unless the
profile gives an answer.
"""
import difflib
import importlib
import re
import sys
from datetime import date

from . import cv, db, gpu, llm, record, salary
from .config import CONFIG, private

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
# Health questions, answered from data/private.toml: the applicant chose to disclose a disability on
# equality forms, take up guaranteed-interview schemes and ask for adjustments, and nothing else.
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
# Nomura's "look after someone ... caused by disability" once got "Yes, 1-19 hours a week" (2026-10-15).
SOMEONE = re.compile(r"look(s|ing)? after|care (for|of)\b|\bcarers?\b|caring|cared for|someone|anyone|another person|"
                     r"other people|family member|relative|household|dependants?|child(ren)?\b|parent|guardian|partner|"
                     r"spouse", re.I)
# How lists name ADHD, most specific first.
NEURO = [re.compile(p, re.I) for p in (r"adhd|attention deficit", r"neuro", r"specific learning|learning (difference|difficult)",
                                       r"cognitive|developmental")]
HEALTH = ("disability", "disability_type", "guaranteed_interview", "adjustments")
# Every key `private_question` can return; none of them is ever in the profile, a prompt or a saved answer.
PRIVATE_KEYS = (*HEALTH, "carer")
CARED_FOR = re.compile(r"care leaver|been in care|cared for", re.I)
YOURSELF = "Answer this yourself"
# Labels longer than this are paragraphs with a question in them; a keyword match there proves little.
RULE_LABEL = 170
# A box asking for a fact about Firstname has a short label; a longer one is a question for the model.
FACT_LABEL = 75
# Answers at least this long are prose Firstname should read before it is sent.
WRITTEN = 80
MODEL_BATCH = 10
# Answers longer than this are essays: never reused as they stand, only shown to the model as the applicant's writing.
ESSAY = 400


def contact() -> tuple[str, str, str]:
    """Returns (name, email, phone) from the CV site, which keeps them out of this repository."""
    tools = str(cv.SITE / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    mod = importlib.import_module("cv_contact")
    return mod.NAME, mod.EMAIL, mod.PHONE


def profile() -> dict:
    """Returns the applicant's standard answers, merged with config.toml's [applicant]."""
    name, email, phone = contact()
    first, _, last = name.partition(" ")
    data = cv.load()
    c = data["contact"]
    p = {
        "first_name": first, "last_name": last, "full_name": name, "preferred_name": first,
        "email": email.lower(), "phone": phone,
        "linkedin": f"https://www.{c['linkedin']}", "github": f"https://{c['github']}",
        "website": f"https://{c['site']}",
        "city": "London", "country": "United Kingdom", "location": "London, United Kingdom",
        "right_to_work": "Yes", "work_status": "British or Irish citizen", "sponsorship": "No",
        "nationality": "British and Irish",
        "notice": "None, available immediately", "start": "Immediately",
        "university": "University", "degree": "Degree",
        "grade": "Grade", "grad_year": "Year",
        "office": "Yes", "relocate": "Yes",
        "diversity": "Prefer not to say",
    }
    p.update({k: v for k, v in CONFIG.get("applicant", {}).items() if k not in PRIVATE_KEYS})
    return p


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
    try:
        from .core.settings.private import value_for_form
    except ModuleNotFoundError as e:
        if e.name != "jobhunt.core.settings.private":
            raise
        # Without the private answers store the answers are read from the old answers file.
        value = private().get(key)
        if value in (None, "") and key == "carer":
            value = CONFIG.get("applicant", {}).get("carer")
        return value or None
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
                       r"(future|other) (opportunit|vacanc|roles)|job alerts?|notifications?:?$|keep (me|my)", re.I)
CONSENT = re.compile(r"consent|i agree|agree to|privacy|data (protection|processing)|process(ing)? (of )?(my|your) "
                     r"(personal )?(data|information)|terms|acknowledge|i (confirm|understand|certify)|"
                     r"confirm (that|the)|you understand|declaration", re.I)

# (label pattern, profile key, preferred options for a choice, in order)
# Standard answers to questions, however they are worded.
ANSWERS = {"sponsorship", "right_to_work", "notice", "start", "salary", "how_heard", "office", "relocate"}
RULES = [
    (r"\bpreferred (first )?name\b", "preferred_name", []),
    (r"\bmiddle name", "middle_name", []),
    (r"\bfirst name|given name|forename", "first_name", []),
    (r"\blast name|surname|family name", "last_name", []),
    (r"^(full )?(legal )?name\b|^your name$|full name", "full_name", []),
    (r"e-?mail", "email", []),
    (r"phone|mobile|telephone", "phone", []),
    (r"linkedin", "linkedin", []),
    (r"github", "github", []),
    (r"website|portfolio|personal (site|url|page)|other links?", "website", []),
    (r"sponsor", "sponsorship", [NO, re.compile(r"(do not|don'?t|will not|won'?t) (require|need)", re.I)]),
    (r"right to work|legally (entitled|authori[sz]ed|eligible|able)|eligib\w+ to work|authori[sz]ed to work|"
     r"work permit|immigration status|visa status|work status",
     "right_to_work", [re.compile(r"british or irish|(british|irish|uk) (citizen|passport|national)|citizen", re.I), YES]),
    (r"notice period", "notice", [re.compile(r"immediate|none|no notice|^0|less than", re.I)]),
    (r"start date|when (can|could) you start|earliest (start|availability)|available to start", "start",
     [re.compile(r"immediate|asap|now", re.I)]),
    (r"salary|compensation|pay expectation|remuneration", "salary", []),
    (r"how did you (hear|find|learn)|where did you (hear|find|see|learn)|referr?al source|^source$", "how_heard", []),
    (r"\boffice\b|on-?site|in person|hybrid|commut", "office", [YES]),
    (r"relocat", "relocate", [YES]),
    (r"post ?code|\bzip\b", "postcode", []),
    (r"address( line)? ?1|street address|^address:?$", "address1", []),
    (r"^country|country of residence|country/region", "country",
     [re.compile(r"united kingdom|^uk\b|great britain", re.I)]),
    (r"^(town|city)\b|town ?/ ?city", "city", [re.compile(r"london", re.I)]),
    (r"\bcity\b|where are you (based|located)|current location|^location", "location",
     [re.compile(r"london", re.I), re.compile(r"united kingdom|\buk\b", re.I)]),
    (r"nationality|citizenship", "nationality", [re.compile(r"^british", re.I), re.compile(r"^irish", re.I)]),
    (r"universit|institution|college|\bschool\b", "university", []),
    (r"degree|discipline|\bmajor\b|field of study|subject", "degree",
     [re.compile(r"^computer science", re.I), re.compile(r"bachelor of science|\bbsc\b", re.I)]),
    (r"grade|classification|\bgpa\b|\bresults?\b", "grade", [re.compile(r"first|1st", re.I)]),
    (r"graduat", "grad_year", [re.compile(r"2026", re.I)]),
]
# Sections about someone or something other than the applicant now: their questions are never
# answered with the applicant's own details, and dates there belong to that course or job.
OTHER_PERSON = re.compile(r"referee|references?\b|emergency|next of kin|employment history|work history|"
                          r"(previous|past|current|former) (employ|job|role|position)|employer details|"
                          r"\bmanager\b|supervisor|dependant|spouse|partner'?s", re.I)
HISTORY = re.compile(r"education|qualification|academic|employment|experience|training|course", re.I)
# Answers about the job applied for, which a dated history section is not asking.
ABOUT_THIS_JOB = {"start", "notice", "salary", "office", "relocate", "how_heard"}
HEARD_SITE = [re.compile(r"(company|careers?|our) (web)?(site|page)|^website", re.I)]
HEARD_BOARD = [re.compile(r"job ?board|job (site|website|search)|online (job|search)", re.I)]
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
    exact = [o for o in options if o.strip().lower() == str(value).strip().lower()]
    if exact:
        return exact[0]
    close = difflib.get_close_matches(str(value), options, n=1, cutoff=0.75)
    return close[0] if close else None


def _fit(f: dict, value, preferred: list) -> object | None:
    """Shapes a known answer to the field's kind, or returns None when the field cannot take it."""
    kind, options = f["kind"], f.get("options") or []
    if kind in ("select", "radio", "combobox") and options:
        return _choose(options, value if not isinstance(value, list) else value[0], preferred)
    if kind == "checkgroup":
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
    value = prof.get("diversity") or "Prefer not to say"
    options = f.get("options") or []
    note = "diversity question; change it if you want to answer"
    if f["kind"] in ("select", "radio", "combobox", "checkgroup") and options:
        choice = _choose(options, value, [DECLINE])
        if choice:
            f.update(value=[choice] if f["kind"] == "checkgroup" else choice, source="profile: diversity", note=note)
        elif not f.get("required"):
            f.update(skip=True, source="skipped", note="optional diversity question with no 'prefer not to say'")
        else:
            return False
        return True
    if f["kind"] in ("text", "textarea"):
        if f.get("required"):
            f.update(value=value, source="profile: diversity", note=note)
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
        neuro = next((o for pattern in NEURO for o in options if pattern.search(o)), None)
        if WHICH.search(label) and prof.get("disability_type") and (neuro or not listed):
            key, answer = "disability_type", ([neuro] if kind == "checkgroup" else neuro) if listed else prof["disability_type"]
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


def _rule(f: dict, prof: dict) -> tuple[object, str] | None:
    label = f.get("label") or ""
    section = f.get("section") or ""
    if OTHER_PERSON.search(section):
        return None
    listed = f["kind"] in ("select", "radio", "combobox", "checkgroup") and bool(f.get("options"))
    for pattern, key, preferred in RULES:
        if not re.search(pattern, label, re.I):
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


# Answers Firstname has given before --------------------------------------------------

def remember(con, fields: list[dict], company: str = "") -> int:
    """Stores the answers Firstname entered himself, so the same question is not put to him twice."""
    kept = 0
    for f in fields:
        value = f.get("value")
        if f.get("source") != "you" or value in (None, "", []) or f["kind"] == "file" or f.get("type") == "password":
            continue
        if private_question(f) is not None:
            continue   # what the person types into a private question is never kept
        question = _key(f, company)
        if len(question) < 3:
            continue
        con.execute("""INSERT INTO answers (question, label, answer, updated) VALUES (?, ?, ?, ?)
                       ON CONFLICT(question) DO UPDATE SET answer = excluded.answer, updated = excluded.updated""",
                    (question, _asked(f), db.json_text(value), db.now()))
        kept += 1
    con.commit()
    return kept


def _recall(con, f: dict, company: str = ""):
    """Returns the answer Firstname gave to this question before, shaped to the field, or None."""
    if con is None:
        return None
    question = _key(f, company)
    row = con.execute("SELECT answer FROM answers WHERE question = ?", (question,)).fetchone()
    if row is None:
        known = [r["question"] for r in con.execute("SELECT question FROM answers")]
        close = difflib.get_close_matches(question, known, n=1, cutoff=0.93)
        if not close:
            return None
        row = con.execute("SELECT answer FROM answers WHERE question = ?", (close[0],)).fetchone()
    value = db.json_value(row["answer"])
    if isinstance(value, str) and len(value) > ESSAY:
        return None  # an essay answers one employer's question; it shows the model the applicant's voice instead
    return _fit(f, value, [])


WHY = re.compile(r"why (do you want|are you (interested|applying)|this (role|company|team)|us\b|join)|"
                 r"what (interests|excites|attracts) you|motivat", re.I)
FIT = re.compile(r"why (are you|would you be) (a good|the right|suited)|what makes you|tell us about yourself|"
                 r"relevant experience|why should we", re.I)

FILL = """You complete a job application form for Firstname Lastname using only the facts given.
Reply with JSON only: {"answers": [{"hid": ..., "value": ..., "sure": true | false}]}.
Give one answer for every field listed.
For a field with options, the value must be one of the options, copied exactly; for a
checkgroup, a list of options. A yes/no question about his eligibility, studies, skills
or history is answered from the facts: if the facts settle it, answer and set sure true.
For free text, write plainly in the first person, within any limit the label or help
states, and at most 120 words; short factual questions get short answers. If a field
asks for something only when another answer applies and it does not apply, give "N/A"
when the field is required and "" otherwise.
Fields are listed in page order with the section heading they sit under. A field in a
section about another person or another job (a referee, an emergency contact, a previous
employer or manager) asks about that person or job, never about Firstname: answer it only from
facts about them, else give "". Dates in an education or employment history are the dates
of that course or job, not Firstname's availability. Salary: if the advert states no pay, never
mention an advertised range.
Passages from the applicant's employment record are facts too: use them for anything the CV leaves out.
Never invent personal details (addresses, dates of birth, reference numbers, names of
people) or claim experience, skills or circumstances the facts do not state: if the
facts do not answer a field, give "" and sure false. Set sure false for anything a
person should check."""

REVIEW = """You check answers that simple keyword rules put into a job application form for
Firstname Lastname. Each line gives a field's id, the section it sits under, its question, and the
answer chosen. Reply with JSON only: {"wrong": [{"hid": ..., "why": a few words}]}.
List a field only when its answer plainly does not answer that question where it sits: Firstname's
own name or email in a referee's details, an availability date as the start date of a
course, an answer to some other question, a choice that contradicts the question. Leave out
anything that is plausibly right; most answers are."""
# Sources of answers chosen by keyword, which the review pass checks against the question.
REVIEWED = ("profile", "your earlier answer", "documents: drafted answer")


def profile_for(job, con=None) -> dict:
    """Returns the profile with the answers that depend on the job settled for this one."""
    prof = profile()
    prof["how_heard"] = HOW_HEARD.get(job["source"], "Company careers page")
    if not prof.get("salary"):
        # A direct figure: employers who ask want one, and "open to discussion" reads as dodging it.
        expected = salary.expect(job, con)
        prof.update(salary=expected["text"], salary_figure=str(expected["figure"]), salary_basis=expected["basis"])
    return prof


def propose(job, fields: list[dict], docs: dict, files: dict, con=None) -> list[dict]:
    """Fills in `value`, `source` and `note` on each field and returns the list.

    Args:
        job: The job row.
        fields: Fields read from the form by browser.open_form.
        docs: The approved documents (documents.load).
        files: Paths of the CV and cover letter PDFs (documents.files).
        con: Database connection, for the answers Firstname has given before.
    """
    prof = profile_for(job, con)
    answers = docs.get("answers") or []
    uploads = [f for f in fields if f["kind"] == "file"]
    named = any(re.search(r"cover|resume|résumé|\bcv\b", f"{f.get('label')} {f.get('context', '')}", re.I)
                for f in uploads)
    for f in fields:
        f.update(value=None, source="you", note="", skip=False)
        label = f.get("label") or ""
        text = f"{label} {f.get('context', '')}"
        if f.get("type") == "password":
            # Passwords never pass through Hal or its database.
            f.update(skip=True, source="skipped", note="type this into the form yourself")
            continue
        if f["kind"] == "file":
            if re.search(r"autofill|photo|picture|avatar", text, re.I):
                f.update(skip=True, source="skipped", note="not a CV or cover letter upload")
            elif re.search(r"cover", text, re.I) and files.get("letter"):
                f.update(value=files["letter"], source="documents: cover letter")
            elif re.search(r"resume|résumé|\bcv\b", text, re.I) and files.get("cv"):
                f.update(value=files["cv"], source="documents: CV")
            elif not named and f is uploads[0] and files.get("cv"):
                # A form whose uploads name no document wants the CV in the first.
                f.update(value=files["cv"], source="documents: CV", note="taken to be the CV upload; check this")
            else:
                f.update(skip=True, source="skipped", note="not a CV or cover letter upload")
            continue
        if private_question(f) is not None:
            if not (_health(f) or _carer(f)) or f.get("value") is None:
                # Never guessed, never recalled, never sent to the AI.
                note = f.get("note") if f.get("source") == "you" and f.get("note") else YOURSELF
                f.update(value=None, source="you", note=note)
            continue
        recalled = _recall(con, f, job["company"])
        if recalled not in (None, []):
            f.update(value=recalled, source="your earlier answer")
            continue
        if DIVERSITY.search(label) and _diversity(f, prof):
            continue
        single = f["kind"] in ("radio", "checkgroup") and len(f.get("options") or []) == 1
        if f["kind"] == "checkbox" or single:
            if MARKETING.search(text) and not f.get("required"):
                f.update(skip=True, source="skipped", note="optional sign-up, left as it is")
                continue
            if CONSENT.search(text) or (single and f.get("required")):
                only = f["options"][0] if single else True
                f.update(value=[only] if f["kind"] == "checkgroup" else only, source="profile: consent",
                         note="consent to the employer's terms; check them")
                continue
        if f["kind"] in ("text", "textarea") and re.search(r"cover letter", label, re.I) and docs.get("letter"):
            f.update(value=docs["letter"], source="documents: cover letter")
            continue
        hit = _rule(f, prof)
        if hit:
            f["value"], f["source"] = hit
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
                f.update(value=drafted, source="documents: drafted answer")
    return mark_written(fields)


def mark_written(fields: list[dict]) -> list[dict]:
    """Flags the answers that are prose, which Firstname reads before anything is sent."""
    for f in fields:
        value = f.get("value")
        f["written"] = f["kind"] in ("text", "textarea") and isinstance(value, str) and len(value) >= WRITTEN \
            and not f.get("source", "").startswith("profile")
    return fields


def _facts(job, docs: dict, con) -> str:
    hidden = ("salary_figure", "salary_basis", "preferred_name", *HEALTH)
    prof = {k: v for k, v in profile_for(job, con).items() if k not in hidden}
    parts = [f"Today is {date.today():%d %B %Y}.",
             f"Job: {job['title']} at {job['company']} ({job['location'] or 'location not stated'}).",
             f"Applicant profile: {prof}",
             "He has never worked for, interviewed with or been referred to this employer unless stated below.",
             f"CV facts:\n{cv.facts_text()}"]
    if con is not None:
        before = con.execute("""SELECT title, status FROM jobs WHERE lower(company) = lower(?) AND id != ?
                                AND status IN ('applied','assessment','interview','offer','rejected','withdrawn')""",
                             (job["company"], job["id"])).fetchall()
        if before:
            parts.append("Earlier applications to this employer: "
                         + "; ".join(f"{r['title']} ({r['status']})" for r in before))
        told = [(r["label"], db.json_value(r["answer"]))
                for r in con.execute("SELECT label, answer FROM answers ORDER BY updated DESC LIMIT 200")
                if private_question({"label": r["label"]}) is None]
        short = [(q, a) for q, a in told if not (isinstance(a, str) and len(a) > ESSAY)][:60]
        essays = [(q, a) for q, a in told if isinstance(a, str) and len(a) > ESSAY][:2]
        if short:
            parts.append("Answers he has given on other forms:\n" + "\n".join(f"- {q}: {a}" for q, a in short))
        if essays:
            parts.append("Answers Firstname wrote for other employers, as examples of Firstname's voice and phrasing "
                         "(write fresh answers for this job; never copy their claims about other employers):\n"
                         + "\n\n".join(f"Q: {q}\nA: {a[:1500]}" for q, a in essays))
    if docs.get("answers"):
        parts.append("Drafted answers:\n" + "\n".join(f"- {a['question']}: {a['answer']}" for a in docs["answers"]))
    if docs.get("letter"):
        parts.append(f"Cover letter:\n{docs['letter']}")
    if job["description"]:
        parts.append(f"Advert (for context only, not facts about him):\n{job['description'][:1800]}")
    return "\n\n".join(parts)


def _shortlist(options: list[str], facts: str, limit: int = 60) -> list[str]:
    """Cuts a long option list to those sharing most words with the facts, so the right one is in view."""
    if len(options) <= limit:
        return options
    known = set(re.findall(r"[a-z]{4,}", facts.lower()))
    ranked = sorted(options, key=lambda o: -len(set(re.findall(r"[a-z]{4,}", o.lower())) & known))
    keep = set(ranked[:limit - 5]) | {o for o in options if re.search(r"^(other|none|not applicable|n/a)", o, re.I)}
    return [o for o in options if o in keep]


def ask_model(job, fields: list[dict], docs: dict, con=None, problems: dict | None = None) -> tuple[int, str | None]:
    """Asks the local model for the fields still without a value.

    Args:
        problems: For fields the form refused, the site's message by field id; those are answered again.

    Returns:
        (answered, reason): how many fields it filled, and why it could not run, if so.
    """
    problems = problems or {}
    if not problems and not gpu.busy():
        _review(job, fields)
    open_fields = [f for f in fields if f["kind"] != "file" and f.get("type") != "password"
                   and private_question(f) is None
                   and (f["hid"] in problems or (f.get("value") in (None, "", []) and not f.get("skip")))]
    if not open_fields:
        return 0, None
    reason = gpu.busy()
    if reason:
        return 0, reason
    facts = _facts(job, docs, con)
    answered = 0
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
            kept = f"\n\nFrom the applicant's employment record (as true as the CV):\n{found}" if found else ""
            reply = llm.chat_json(FILL, f"Fields:\n{listing}\n\n{facts}{kept}", role="main", timeout=300)
        except llm.LLMUnavailable as e:
            return answered, str(e)
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
                value = llm.undash(value)
            if f.get("type") == "number" and not re.fullmatch(r"\s*-?[\d.,]+\s*", str(value)):
                continue
            f.update(value=value, source="hal", skip=False, note="" if a.get("sure") else "Hal is unsure; check this")
            answered += 1
    mark_written(fields)
    return answered, None


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
        reply = llm.chat_json(REVIEW, f"Job: {job['title']} at {job['company']}\n\nAnswers:\n{listing}",
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
            f.update(value=None, source="you", note=f"Hal's first answer looked wrong{': ' + why if why else ''}")


NAVIGATE = """You help a job applicant reach an application form. Given a web page's title and
the names of its buttons and links, choose the one control that leads towards applying for the
job named. Reply with JSON only: {"press": the control's name copied exactly, or "" if none
leads to an application form, "why": a few words}. Never choose a control that signs in with
another service (LinkedIn, Indeed, Google), shares the job, or leaves for an unrelated page."""


def choose_control(job, found: dict) -> str:
    """Asks the model which control on a page without a recognised Apply button leads to the form."""
    names = list(dict.fromkeys(c["name"] for c in found.get("controls", [])
                               if private_question({"label": c.get("name")}) is None))
    if not names or gpu.busy():
        return ""
    prompt = (f"Job: {job['title']} at {job['company']}\nPage title: {found['title']}\n"
              f"Page address: {found['url']}\nControls:\n" + "\n".join(f"- {n}" for n in names[:60]))
    try:
        reply = llm.chat_json(NAVIGATE, prompt, role="main", timeout=120)
    except llm.LLMUnavailable:
        return ""
    press = str(reply.get("press") or "")
    return press if press in names else ""
