"""Who a pack's message and letter go to: the hiring contacts the advert names, never a contact or mailbox
for questions about applying."""
import re

from ..writing import check as writing_check

FIND_PREFIX = "find the person hiring"   # the start of the To line when the advert names nobody
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# A shared mailbox for applications, never a person hiring: careers@, recruitment@, jobs@, hr@ and the like.
PROCESS_MAILBOX = re.compile(r"^(careers?|recruit\w*|jobs?|talent\w*|hr|hiring|apply|applications?|vacanc\w*|"
                             r"resourcing|people|graduates?|early[-_.]?careers|internships?|enquiries|info)"
                             r"([-_.][\w.-]*)?@", re.I)
# Words an advert uses for a contact who answers questions about applying, not about the work.
PROCESS_WORDS = re.compile(r"\b(application process|the process|questions about applying|how to apply|questions "
                           r"about (the|your|this|an) application|application (queries|questions|enquiries)|"
                           r"recruitment|recruiter|careers? team|talent (team|acquisition|partner)|human resources|"
                           r"hr|technical (issues|problems|difficulties)|reasonable adjustments)\b", re.I)


def _contact(c) -> dict:
    """One contact the advert reading gave, as {name, email, role}; a bare string is a name or an address."""
    if isinstance(c, dict):
        text = {k: str(c.get(k) or "").strip() for k in ("name", "email", "role")}
    else:
        first, _, rest = str(c).partition(",")
        text = {"name": first.strip(), "email": "", "role": rest.strip()}
    found = EMAIL.search(" ".join(text.values()))
    email = found.group(0) if found else ""
    name = "" if "@" in text["name"] else text["name"]
    return {"name": name, "email": email, "role": text["role"]}


def _for_the_process(contact: dict, advert: str) -> bool:
    """True when the advert gives this contact for questions about applying rather than about the work."""
    if PROCESS_WORDS.search(contact["role"]):
        return True
    said = [s for s in writing_check.sentences(advert or "")
            if any(k and k.lower() in s.lower() for k in (contact["email"], contact["name"]))]
    return any(PROCESS_WORDS.search(s) for s in said)


def hiring_contacts(analysis: dict, advert: str = "") -> list[dict]:
    """The named people the advert gives as hiring contacts, each {name, email, role}: never an address or a
    person it gives for questions about the application process, and never a careers or recruitment
    mailbox. A pack's "To:" line was the advert's address for questions about applying (2026-11-07)."""
    out = []
    for c in (_contact(c) for c in analysis.get("contacts") or []):
        if c["name"] and not _for_the_process(c, advert):
            out.append({**c, "email": "" if PROCESS_MAILBOX.match(c["email"]) else c["email"]})
    return out


def message_to(job, analysis: dict, who: dict) -> str:
    """Who the message goes to: the hiring contacts the advert names (`hiring_contacts`), else where to find
    a title the CV type names."""
    advert = str(job["description"] or "") if "description" in job.keys() else ""
    named = [", ".join(p for p in (c["name"], c["role"], c["email"]) if p) for c in hiring_contacts(analysis, advert)]
    if named:
        return "; ".join(named)
    title = (who.get("hiring_titles") or ["hiring manager"])[0]
    return f"{FIND_PREFIX} on LinkedIn (search \"{job['company']} {title}\")"


def contact_names(analysis: dict) -> list[str]:
    """The names of the people the advert names; an email address is not a name."""
    names = [(str(c.get("name") or "") if isinstance(c, dict) else str(c).split(",")[0]).strip()
             for c in analysis.get("contacts") or []]
    return [n for n in names if n and "@" not in n]


def salutation(analysis: dict) -> str:
    """The letter's greeting: the first person the advert names, else the hiring team."""
    # the letter said "Dear Hiring Team" while the message beside it greeted the person the advert names
    # (made-up humanities advert, 2026-10-23)
    names = contact_names(analysis)
    return f"Dear {names[0]}," if names else "Dear Hiring Team,"
