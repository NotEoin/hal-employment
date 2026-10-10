"""Proposes a value for every field of an application form.

Sources, in order: the applicant profile (contact details from the CV site and
standard answers from config.toml's [applicant] table), the job's documents,
then the local model for what is left. Each proposal records its source, and a
field nothing can answer is left for Firstname. Diversity questions default to
'prefer not to say' unless the profile gives an answer.
"""
import difflib
import importlib
import re
import sys

from . import cv, gpu, llm
from .config import CONFIG

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
        "notice": "None, available immediately", "start": "Immediately",
        "salary": "Open to the advertised range",
        "university": "University", "degree": "Degree",
        "grade": "Grade", "grad_year": "Year",
        "office": "Yes", "relocate": "Yes",
        "diversity": "Prefer not to say",
    }
    p.update(CONFIG.get("applicant", {}))
    return p


# (label pattern, profile key, preferred options for a choice, in order)
RULES = [
    (r"full legal name", "yes", [YES]),
    (r"\bpreferred (first )?name\b", "preferred_name", []),
    (r"\bfirst name|given name|forename", "first_name", []),
    (r"\blast name|surname|family name", "last_name", []),
    (r"^(full )?name$|^your name$|full name", "full_name", []),
    (r"e-?mail", "email", []),
    (r"phone|mobile|telephone", "phone", []),
    (r"linkedin", "linkedin", []),
    (r"github", "github", []),
    (r"website|portfolio|personal (site|url|page)|other links?", "website", []),
    (r"sponsor", "sponsorship", [NO, re.compile(r"(do not|don'?t|will not|won'?t) (require|need)", re.I)]),
    (r"right to work|legally (entitled|authori[sz]ed|eligible|able)|eligib\w+ to work|authori[sz]ed to work|"
     r"work permit|immigration status|visa status|work status",
     "right_to_work", [re.compile(r"british or irish|british|irish|uk citizen|citizen", re.I), YES]),
    (r"notice period", "notice", [re.compile(r"immediate|none|no notice|^0|less than", re.I)]),
    (r"start date|when (can|could) you start|earliest (start|availability)|available to start", "start",
     [re.compile(r"immediate|asap|now", re.I)]),
    (r"salary|compensation|pay expectation|remuneration", "salary", []),
    (r"how did you (hear|find|learn)|where did you (hear|find|see|learn)|referr?al source|^source$", "how_heard", []),
    (r"office|on-?site|in person|hybrid|commut", "office", [YES]),
    (r"relocat", "relocate", [YES]),
    (r"^country|country of residence", "country", [re.compile(r"united kingdom|^uk\b|great britain", re.I)]),
    (r"\bcity\b|where are you (based|located)|current location|^location", "location",
     [re.compile(r"london", re.I), re.compile(r"united kingdom|\buk\b", re.I)]),
    (r"universit|school|institution|college", "university", []),
    (r"degree|discipline|major|field of study|subject", "degree", []),
    (r"grade|classification|\bgpa\b|result", "grade", [re.compile(r"first|1st", re.I)]),
    (r"graduat", "grad_year", [re.compile(r"2026", re.I)]),
    (r"gender|\bsex\b|ethnic|\brace\b|disab|sexual orientation|veteran|religio|pronoun|transgender|"
     r"socio-?economic|type of school|caring responsib|neurodiver|age range|date of birth|"
     r"identify (my|as)|free school meals|first (generation|in your family)", "diversity", [DECLINE]),
]
CONSENT = re.compile(r"consent|i agree|agree to|privacy|data (protection|processing)|terms|acknowledge|"
                     r"confirm (that|the)|contact you about", re.I)


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


def _rule(f: dict, prof: dict) -> tuple[object, str] | None:
    label = f.get("label") or ""
    for pattern, key, preferred in RULES:
        if not re.search(pattern, label, re.I):
            continue
        value = "Yes" if key == "yes" else prof.get(key)
        if value in (None, ""):
            continue
        if f["kind"] in ("select", "radio", "combobox") and f.get("options"):
            choice = _choose(f["options"], value, preferred)
            if choice:
                return choice, f"profile: {key}"
            continue
        if f["kind"] == "checkbox":
            return (str(value).lower() in ("yes", "true"), f"profile: {key}")
        if f["kind"] == "checkgroup":
            choice = _choose(f.get("options") or [], value, preferred)
            if choice:
                return [choice], f"profile: {key}"
            continue
        return value, f"profile: {key}"
    return None


def _match_answer(label: str, answers: list[dict]) -> str | None:
    """Returns the drafted answer whose question matches the field's label."""
    best, score = None, 0.0
    for a in answers:
        q = a.get("question", "")
        s = difflib.SequenceMatcher(None, label.lower(), q.lower()).ratio()
        if q and (q.lower() in label.lower() or label.lower() in q.lower()):
            s = max(s, 0.9)
        if s > score:
            best, score = a.get("answer"), s
    return best if score >= 0.6 else None


WHY = re.compile(r"why (do you want|are you (interested|applying)|this (role|company|team)|us\b|join)|"
                 r"what (interests|excites|attracts) you|motivat", re.I)
FIT = re.compile(r"why (are you|would you be) (a good|the right|suited)|what makes you|tell us about yourself|"
                 r"relevant experience|why should we", re.I)

FILL = """You complete a job application form for Firstname Lastname using only the facts given.
Reply with JSON only: {"answers": [{"hid": ..., "value": ..., "sure": true | false}]}.
For a field with options, the value must be one of the options, copied exactly; for a
checkgroup, a list of options. For free text, write plainly in the first person, within
any word limit the label states, and at most 120 words. Use only the facts; if they do
not answer a field, give "" and sure false. Never claim experience, skills or
circumstances the facts do not state. Set sure false for anything a person should check."""


def propose(job, fields: list[dict], docs: dict, files: dict) -> list[dict]:
    """Fills in `value`, `source` and `note` on each field and returns the list.

    Args:
        job: The job row.
        fields: Fields read from the form by browser.open_form.
        docs: The approved documents (documents.load).
        files: Paths of the CV and cover letter PDFs (documents.files).
    """
    prof = profile()
    prof["how_heard"] = HOW_HEARD.get(job["source"], "Company careers page")
    answers = docs.get("answers") or []
    uploads = [f for f in fields if f["kind"] == "file"]
    named = any(re.search(r"cover|resume|r\u00e9sum\u00e9|\bcv\b", f"{f.get('label')} {f.get('context', '')}", re.I)
                for f in uploads)
    for f in fields:
        f.update(value=None, source="you", note="")
        label = f.get("label") or ""
        text = f"{label} {f.get('context', '')}"
        if f.get("type") == "password":
            # Passwords never pass through Hal or its database.
            f.update(skip=True, source="skipped", note="type this into the form yourself")
            continue
        if f["kind"] == "file":
            if re.search(r"cover", text, re.I) and files.get("letter"):
                f.update(value=files["letter"], source="documents: cover letter")
            elif re.search(r"resume|r\u00e9sum\u00e9|\bcv\b", text, re.I) and files.get("cv"):
                f.update(value=files["cv"], source="documents: CV")
            elif not named and f is uploads[0] and files.get("cv"):
                # A form whose uploads name no document wants the CV in the first.
                f.update(value=files["cv"], source="documents: CV", note="taken to be the CV upload; check this")
            else:
                f.update(skip=True, source="skipped", note="not a CV or cover letter upload")
            continue
        if f["kind"] == "checkbox" and CONSENT.search(text):
            f.update(value=True, source="profile: consent", note="consent to the employer's terms; check them")
            continue
        if f["kind"] in ("text", "textarea") and re.search(r"cover letter", label, re.I) and docs.get("letter"):
            f.update(value=docs["letter"], source="documents: cover letter")
            continue
        hit = _rule(f, prof)
        if hit:
            f["value"], f["source"] = hit
            if hit[1] == "profile: diversity":
                f["note"] = "diversity question; change it if you want to answer"
            continue
        if f["kind"] in ("text", "textarea"):
            drafted = _match_answer(label, answers)
            if drafted is None and WHY.search(label):
                drafted = _match_answer("Why do you want to work here?", answers)
            if drafted is None and FIT.search(label):
                drafted = _match_answer("Why are you a good fit for this role?", answers)
            if drafted:
                f.update(value=drafted, source="documents: drafted answer")
    return fields


def ask_model(job, fields: list[dict], docs: dict) -> tuple[int, str | None]:
    """Asks the local model for the fields still without a value.

    Returns:
        (answered, reason): how many fields it filled, and why it could not run, if so.
    """
    open_fields = [f for f in fields if f.get("value") in (None, "", []) and not f.get("skip")
                   and f["kind"] != "file"]
    if not open_fields:
        return 0, None
    reason = gpu.busy()
    if reason:
        return 0, reason
    listing = "\n".join(
        f"- hid={f['hid']} kind={f['kind']} label={f.get('label')!r}"
        + (f" options={f['options']!r}" if f.get("options") else "")
        + (f" context={f['context']!r}" if f.get("context") else "")
        + (" (required)" if f.get("required") else "")
        for f in open_fields)
    prompt = (f"Job: {job['title']} at {job['company']}\n\nFields:\n{listing}\n\n"
              f"Applicant profile: {profile()}\n\nCV facts:\n{cv.facts_text()}\n\n"
              f"Cover letter:\n{docs.get('letter', '')}")
    try:
        reply = llm.chat_json(FILL, prompt, role="main", timeout=300)
    except llm.LLMUnavailable as e:
        return 0, str(e)
    by_hid = {f["hid"]: f for f in open_fields}
    answered = 0
    for a in reply.get("answers", []):
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
        if f["kind"] == "checkbox":
            value = str(value).lower() in ("true", "yes", "1")
        if isinstance(value, str) and not f.get("options"):
            value = llm.undash(value)
        f.update(value=value, source="hal", note="" if a.get("sure") else "Hal is unsure; check this")
        answered += 1
    return answered, None
