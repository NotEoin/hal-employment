"""Finds who to message about an application.

Candidates come from three places: people the advert names, people who wrote
to the job inbox about the job, and public search results for the employer's
engineering and hiring leads. The local model picks the best few; without it,
rules on job titles do. LinkedIn itself is never opened: profile links come
from a search engine's result page, read in Hal's browser, and are for the
applicant to visit by hand.
"""
import email.utils
import json
import re
import time

from . import browser, db, documents, gpu, llm

SEARCH_PAUSE = 8  # seconds between queries, so the search engine does not refuse them
_last_query = 0.0
KEEP = 3          # people suggested for one job
QUERIES = (
    'site:linkedin.com/in "{company}" "engineering manager" OR "head of engineering" OR "CTO" OR "team lead"',
    'site:linkedin.com/in "{company}" "talent acquisition" OR "recruiter" OR "early careers"',
)
LEAD = re.compile(r"engineering manager|head of (engineering|software|technology|development)|\bcto\b|"
                  r"chief technology|director of engineering|vp,? (of )?engineering|"
                  r"(team|tech|technical|engineering) lead|founder", re.I)
TALENT = re.compile(r"talent|recruit|early careers|people (partner|team)|human resources|resourcing", re.I)
SUFFIX = re.compile(r"[\s,]+(ltd|limited|plc|llp|inc)\.?$", re.I)

PICK = """You choose who a new graduate should message about a job application.
Reply with JSON only: {"picks": [{"n": number from the list, "why": one short sentence,
"sure": true or false}]}, best first, at most 3. Prefer, in order: a person the advert
names or who emailed about the job; the manager of the team hiring (engineering manager,
team lead, head of engineering, or the CTO or a founder at a small company); a recruiter
or talent partner at the employer. Leave out anyone who does not appear to work at the
employer now, or whose job is unrelated to hiring for this role. Use only the list. Set
sure false unless the listing itself shows the person fits. Give an empty list if nobody fits."""


def _employer(company: str) -> str:
    """Returns the company name without its legal suffix, as people write it on a profile."""
    return SUFFIX.sub("", company.strip())


def _profile(result: dict, company: str) -> dict | None:
    """Reads a search result for a LinkedIn profile into a person, or None if it is not one at the employer."""
    if not re.match(r"https://([a-z]{2,3}\.)?linkedin\.com/in/", result["url"]):
        return None
    title = re.sub(r"\s*[|\-\u2013]\s*LinkedIn\s*$", "", result["title"])
    name, _, role = title.partition(" - ")
    # A surname that matches the company is not evidence of working there.
    if not re.search(rf"\b{re.escape(_employer(company))}\b", f"{role} {result['snippet']}", re.I):
        return None
    return {"name": name.strip(), "role": role.strip(" ."), "url": result["url"].split("?")[0],
            "source": "web search", "evidence": result["snippet"][:240]}


def from_advert(job) -> list[dict]:
    """Returns the people or addresses the advert names, as the pack recorded them."""
    to = documents.drafts(job).get("message_to", "")
    if not to or to.startswith("find the engineering lead"):
        return []
    people = []
    for entry in to.split(";"):
        address = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", entry)
        people.append({"name": entry.strip(), "role": "named in the advert", "source": "advert",
                       "email": address.group(0) if address else ""})
    return people


def from_inbox(con, job) -> list[dict]:
    """Returns the people who wrote to the job inbox about this job."""
    rows = con.execute("""SELECT sender, MAX(subject) subject FROM emails
                          WHERE job_id = ? AND kind = 'human' GROUP BY sender""", (job["id"],))
    people = []
    for r in rows:
        name, address = email.utils.parseaddr(" ".join((r["sender"] or "").split()))
        people.append({"name": name or address, "role": "emailed you about this job", "source": "email",
                       "email": address, "evidence": r["subject"] or ""})
    return people


def from_search(job) -> tuple[list[dict], str]:
    """Returns the employer's people found by public search, and a note if the search was cut short."""
    global _last_query
    time.sleep(max(0.0, _last_query + SEARCH_PAUSE - time.monotonic()))
    queries = [q.format(company=_employer(job["company"])) for q in QUERIES]
    try:
        pages, refused = browser.search(queries, SEARCH_PAUSE)
        note = "the search engine refused some queries; search again later" if refused else ""
    except Exception as e:  # noqa: BLE001 - the advert and the inbox may still name someone
        pages, note = [], f"the search did not run: {str(e).splitlines()[0][:160]}"
    _last_query = time.monotonic()
    people, seen = [], set()
    for result in (r for page in pages for r in page):
        person = _profile(result, job["company"])
        if person and person["url"] not in seen:
            seen.add(person["url"])
            people.append(person)
    return people, note


def _by_rules(people: list[dict]) -> list[dict]:
    def weight(p: dict) -> int:
        if p["source"] in ("advert", "email"):
            return 3
        return 2 if LEAD.search(p["role"]) else 1 if TALENT.search(p["role"]) else 0

    ranked = sorted((p for p in people if weight(p)), key=weight, reverse=True)
    return [{**p, "why": "", "sure": p["source"] != "web search"} for p in ranked[:KEEP]]


def _by_model(job, people: list[dict]) -> list[dict]:
    listing = "\n".join(f"{n}. {p['name']} | {p['role']} | from: {p['source']} | {p.get('evidence', '')}"
                        for n, p in enumerate(people, 1))
    reply = llm.chat_json(PICK, f"Job: {job['title']} at {job['company']} ({job['location'] or 'UK'})\n\n"
                                f"People:\n{listing}", role="main", timeout=180)
    picked = {}
    for pick in reply.get("picks") or []:
        n = pick.get("n")
        # The model may only choose from the list; anything else it names is dropped.
        if isinstance(n, int) and 1 <= n <= len(people) and n not in picked:
            picked[n] = {**people[n - 1], "why": str(pick.get("why") or "")[:200], "sure": bool(pick.get("sure"))}
    return list(picked.values())[:KEEP]


def rank(job, people: list[dict]) -> tuple[list[dict], str]:
    """Returns the people worth messaging, best first, and whether the model or rules chose them."""
    if not people:
        return [], "rules"
    if not gpu.busy():
        try:
            return _by_model(job, people), "model"
        except llm.LLMUnavailable:
            pass
    return _by_rules(people), "rules"


def _store(con, job, found: dict) -> None:
    con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                (job["id"], db.now()))
    con.execute("UPDATE applications SET contacts = ? WHERE job_id = ?", (json.dumps(found), job["id"]))
    con.commit()


def find(con, job) -> dict:
    """Finds and stores who to message about a job, and points the job's to-do at the best match."""
    searched, note = from_search(job)
    people, by = rank(job, from_advert(job) + from_inbox(con, job) + searched)
    found = {"at": db.now(), "people": people, "by": by, "note": note}
    _store(con, job, found)
    if people:
        top = people[0]
        detail = (f"Suggested: {top['name']}" + (f", {top['role']}" if top["role"] else "")
                  + ". Check they are the right person; the drafted message is on the application page.")
    else:
        detail = "Hal found nobody to suggest. The drafted message is on the application page."
    con.execute("UPDATE todos SET detail = ?, url = ? WHERE source = ? AND done IS NULL",
                (detail, people[0].get("url") or None if people else None, f"message:{job['id']}"))
    con.commit()
    return found


def step(con) -> bool:
    """Finds contacts for one sent application that has none yet; returns whether there was one."""
    job = con.execute("""SELECT j.* FROM todos t JOIN jobs j ON j.id = t.job_id
                         LEFT JOIN applications a ON a.job_id = j.id
                         WHERE t.kind = 'message' AND t.done IS NULL AND a.contacts IS NULL
                         ORDER BY t.created LIMIT 1""").fetchone()
    if job is None:
        return False
    try:
        find(con, job)
    except Exception as e:  # noqa: BLE001 - recorded, so one bad job is not retried every minute
        _store(con, job, {"at": db.now(), "people": [], "by": "rules", "note": f"the search failed: {e}"})
    return True
