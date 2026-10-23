"""Read the job inbox, turn recruiting email into application status.

Read-only: the mailbox is opened with EXAMINE and bodies fetched with
BODY.PEEK, so nothing is marked read, moved or deleted. Each message is
classified once, by rules with a local-model fallback, and stored in the
emails table with its job. Applications made outside the pipeline get a job.

Kinds:
  applied     you sent an application (outbound email)
  confirmed   'we received your application'
  assessment  online test / coding challenge / take-home
  interview   invitation to interview or to book a call
  offer
  rejected
  human       a real person wrote about an application: read and reply
  alert       job-alert digest (Indeed, LinkedIn, Gradcracker, ...)
  admin       security codes, GDPR notices, account mail
  other

The inbox is the `email.address` setting and its app password the `email.app_password` secret; nothing
about the person is written here.
"""
import difflib
import email
import email.header
import email.utils
import html
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, llm
from .ai import prompts
from .score import score

log = logging.getLogger(__name__)

# A fixed inbox for the made-up world of the tests (tests/support/world.py patches it); None means the
# `email.address` setting, read when used.
ADDRESS: str | None = None
# Where the old layout kept the app password, read only when no secret is saved yet (before the import).
PASSWORD_FILE = Path("~/.config/jobhunt/gmail-app-password").expanduser()
PASSWORD_SECRET = "email.app_password"
IMAP_TIMEOUT = 60   # seconds; a stalled IMAP connection must not hold the email check for ever
FIRST_SYNC_DAYS = 400
REFINE_TIMEOUT_S = 90   # the fast model answers one email well inside this; past it the rules stand

# Application status each kind moves a job to, and how far along that is.
STATUS_FOR = {"applied": "applied", "confirmed": "applied", "assessment": "assessment",
              "interview": "interview", "offer": "offer", "rejected": "rejected"}
RANK = {"applied": 1, "assessment": 2, "interview": 3, "offer": 4}
ATTENTION = {"assessment", "interview", "offer", "human"}


ALERT_SENDERS = re.compile(r"match\.indeed\.com|jobalerts?-noreply@linkedin|jobs-noreply@linkedin|"
                           r"gradcracker\.com|jobs2web\.com|@e\.jm\.indeed|@jm\.indeed|"
                           r"@trackr|the-trackr|jobs\.ac\.uk|cv-library|welcometothejungle|otta\.com|"
                           r"workinstartups|totaljobs|reed\.co\.uk|glassdoor", re.I)
SOCIAL = re.compile(r"@(e\.|em\.)?linkedin\.com|@facebookmail|bitwarden|github\.com|accounts\.google", re.I)
ADMIN = re.compile(r"security code|verification code|^welcome to |verify your (email|account)|confirm your email|"
                   r"gdpr|privacy (and compliance )?notification|password|account (created|activated)|"
                   r"registration|^\[jobhunt\]", re.I)
APP_CONTEXT = re.compile(r"\b(appl(y|ying|ied|ication)|candida(te|cy)|position|vacanc|role|interview|"
                         r"assessment|recruit|hiring|talent)\b", re.I)

OFFER = re.compile(r"pleased to (offer|extend)|offer of employment|formal offer|"
                   r"delighted to offer|(make|making) you an offer", re.I)
REJECTED = re.compile(
    r"unfortunately|regret to inform|not (be )?(progress|proceed|mov)ing (forward|further|with)|"
    r"(will|shall|are) not (be )?(progressing|moving forward|proceeding|taking)|"
    r"decided not to (progress|proceed|move)|not been (successful|selected)|unsuccessful|"
    r"not (been )?selected for further|not in a position to (pursue|progress|offer)|"
    r"filled (our|the) (open )?position|no longer (interviewing|considering)|position has (now )?been filled|"
    r"other candidates whose|decided to pursue other|won'?t be (moving|progressing)", re.I)
INTERVIEW = re.compile(
    r"invite you (to|for) (an? )?(\w+ )?(interview|call|chat|conversation)|"
    r"(like|love) to (invite|schedule|arrange|set up|book)|interview (invitation|invite)|"
    r"(book|select|choose|pick) (a|your) (time|slot)|calendly\.com|goodtime\.io|"
    r"(send|share|let us know) (us )?your availability|next stage (is|will be) an? (\w+ )?(interview|call)|"
    r"(pleased|happy|delighted) to (let you know|inform you|invite)", re.I)
ASSESSMENT = re.compile(
    r"hackerrank|codility|codesignal|coderpad|testgorilla|hirevue|pymetrics|shl\.com|"
    r"cappfinity|arctic shores|sova|talentlens|(online|technical|coding|numerical|aptitude|"
    r"situational judgement) (assessment|test|challenge|exercise)|take[- ]home|"
    r"complete (the|an|our|your) (\w+ )?(assessment|test|challenge)", re.I)
CONFIRMED = re.compile(
    r"(received|receipt of) your application|thank(s| you)[\w ]{0,12} for (your )?(\w+ )?(applying|application|"
    r"interest)|application (has been )?(submitted|received)|application submitted|"
    r"we('ll| will) (be in touch|review|contact)", re.I)

NOREPLY = re.compile(r"no-?reply|donotreply|do-not-reply|notifications?@|mailer|system@|"
                     r"@[\w.-]*(greenhouse|ashbyhq|lever|workday|myworkday|successfactors|icims|"
                     r"smartrecruiters|workable|teamtailor|recruitee|personio|jobs2web)", re.I)


def address() -> str:
    """The job inbox's address, lower case: the `email.address` setting, or "" when none is set."""
    if ADDRESS is not None:
        return ADDRESS.lower()
    from .core import settings

    return (settings.get("email.address") or "").strip().lower()


def password() -> str | None:
    """The inbox's app password: the saved secret, else the old layout's file; None when neither exists."""
    from .core.settings import secrets

    try:
        saved = secrets.get(PASSWORD_SECRET, interactive=False)
    except secrets.SecretsLocked:
        log.info("the email app password is waiting for the keyring to be unlocked")
        saved = None
    if saved:
        return saved.strip().replace(" ", "")
    if PASSWORD_FILE.exists():
        return PASSWORD_FILE.read_text().strip().replace(" ", "")
    return None


def classify(sender: str, rcpt: str, subject: str, body: str) -> str:
    s = f"{subject}\n{body[:4000]}"
    own = address()
    if own and own in sender.lower():
        if re.search(r"^\[jobhunt\]", subject):
            return "admin"
        others = [a for a in re.findall(r"[\w.+-]+@[\w.-]+", rcpt) if a.lower() != own]
        if others and re.search(r"\bappl(y|ication)\b|\bCV\b|r[e\u00e9]sum[e\u00e9]", s, re.I):
            return "applied"
        return "other"
    if ALERT_SENDERS.search(sender) or _from_alert_sender(sender, subject) or re.search(
            r" is hiring\b|new jobs?\b.*\bmore\b|jobs? for you|deadlines this week|new opportunities at",
            subject, re.I):
        return "alert"
    if "indeedapply@indeed" in sender.lower():
        return "confirmed"
    if re.search(r"linkedin", sender, re.I) and re.search(r"new messages?\b", subject, re.I):
        return "human"  # may be a recruiter InMail, readable only on LinkedIn
    if SOCIAL.search(sender):
        return "other"
    if ADMIN.search(subject) and not REJECTED.search(s):
        return "admin"
    if not APP_CONTEXT.search(s):
        return "other"
    if OFFER.search(s):
        return "offer"
    if REJECTED.search(s):
        return "rejected"
    if INTERVIEW.search(s):
        return "interview"
    if ASSESSMENT.search(s):
        return "assessment"
    if CONFIRMED.search(s):
        return "confirmed"
    # Mail from a person about an application needs reading even when no rule fires.
    return "human" if not NOREPLY.search(sender) else "other"



STOP = r"(?:\s+[-\u2013|:]|\s*[!.,]|\s+(?:for|and|we|is|has|team|in)\b|\s*$)"
COMPANY_PATTERNS = [
    r"items were sent to (.+?)\. ?Good luck",                                   # Indeed Apply
    r"(?:applying|application|applied) (?:to|at|with) (?!the |our |a |this )([A-Z][\w&.'\u2019 -]{1,40}?)" + STOP,
    r"(?:interest in|working for|work for|career(?: opportunity)? (?:at|with)|position at|role at|joining|join) "
    r"(?!the |our |a |this )([A-Z][\w&.'\u2019 -]{1,40}?)" + STOP,
    r"^Dear ([A-Z][\w&.' -]{1,40}?) (?:team|hiring)",                         # outbound application email
]
ROLE_PATTERNS = [
    r"Indeed Application: (.+)$",
    r"position of (.+?)(?:\s+\d{6,}|[.!]|,\s|$)",
    r"(?:for|of|to) the (?:role|position) of (.+?)(?:\s+\d{6,}|[.!(]|,\s|\s+at\b|$)",
    r"(?:for|of|to) the (.+?) (?:role|position)\b",
    r"application for (?:the )?(.+?)(?: role| position|[.,!]| at |$)",
    r"Application at [^-]+ - (.+)$",
    r"Application to (.+?) position",
    r"^(.+?) Status Update$",
    r"^Application: (.+?) - ",
    r"apply for the (.+?) role",
    r"^(.+?)\.? - (?:Application Received|[A-Z][\w ]+ University)$",
]
SENDER_NOISE = re.compile(r"\b(recruitment|recruiting|hiring team|careers?|talent( acquisition)?|"
                          r"jobs|team|hr|notifications?|no-?reply|via \w+|from)\b", re.I)
GENERIC = {"group", "department", "office", "service", "services", "indeed", "indeed apply", "greenhouse", "workday", "lever", "ashby", "linkedin", "us",
           "you", "the team", "our team", "your application"}


def _generic() -> set[str]:
    """Names that are never an employer: GENERIC, plus the person's own first name (their sent mail)."""
    from .core import settings

    name = (settings.get("you.preferred_name") or settings.get("you.name") or "").split()
    return GENERIC | ({name[0].lower()} if name else set())


def _first(patterns, *texts):
    for p in patterns:
        for t in texts:
            m = re.search(p, t, re.M)
            if m:
                v = re.sub(r"^(our|the|a|an)\s+", "", m.group(1).strip(" -\u2013:'\u2019\""), flags=re.I)
                if v and v.lower() not in _generic() and len(v) <= 80:
                    return v
    return None


def company_of(sender: str, rcpt: str, subject: str, body: str, kind: str) -> str | None:
    if kind == "applied":
        found = _first(COMPANY_PATTERNS[3:], body[:300])
        if found:
            return found
        dom = re.search(r"@([\w-]+)\.", rcpt or "")
        return dom.group(1).title() if dom else None
    display = email.utils.parseaddr(sender)[0]
    if re.search(r"\b(recruitment|recruiting|hiring team|careers|talent)\b", display, re.I):
        org = SENDER_NOISE.sub("", display).strip(" -\u2013|\"")
        if org and org.lower() not in _generic():
            return org
    third_party = re.match(r"^([A-Z][\w&.]+) (?:Assessment|Interview|Online Test|Video Interview)\b", subject)
    if third_party:  # assessment vendors name the employer in the subject
        return third_party.group(1)
    found = _first(COMPANY_PATTERNS, subject, body[:1500])
    if found:
        return found
    name = display
    name = SENDER_NOISE.sub("", name).strip(" -\u2013|\"")
    if name and name.lower() not in _generic() and "@" not in name:
        return name
    dom = re.search(r"@([\w-]+)\.", sender)
    if dom and dom.group(1).lower() not in ("myworkday", "greenhouse", "ashbyhq", "gmail", "us", "eu"):
        return dom.group(1).title()
    local = re.search(r"([\w-]+)@myworkday", sender)  # Workday tenants send as <tenant>@myworkday.com
    return local.group(1).upper() if local else None


def role_of(subject: str, body: str) -> str | None:
    return _first(ROLE_PATTERNS, subject, body[:1500])


def refine(sender, subject, body, kind, company, role):
    """Ask the local model when the rules are unsure. Rules win if it's down."""
    try:
        out = llm.chat_json(prompts.text("email_refine"), f"From: {sender}\nSubject: {subject}\n\n{body[:3000]}",
                            role="fast", timeout=REFINE_TIMEOUT_S, purpose="email_refine")
    except llm.LLMUnavailable:
        return kind, company, role
    k = out.get("kind")
    if k in STATUS_FOR or k in ("human", "other"):
        kind = k if kind in ("human", "other") else kind  # rule-based status calls take precedence
    return kind, company or out.get("company"), role or out.get("role")



def norm(company: str | None) -> str:
    c = (company or "").lower()
    c = re.sub(r"\.(com|io|ai|co\.uk|co)\b", "", c)
    c = re.sub(r"\b(ltd|limited|inc|plc|llc|group|technologies|technology|tech|data|uk|holdings|"
               r"careers|the|labs?)\b", "", c)
    c = re.sub(r"[^a-z0-9]", "", c)
    return c


def _similar(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_job(con, company: str | None, role: str | None, kind: str) -> str | None:
    if not company:
        return None
    key = norm(company)
    if not key:
        return None
    rows = [r for r in con.execute("SELECT id, company, title, status FROM jobs").fetchall()
            if (k := norm(r["company"])) and (k == key or (len(key) >= 4 and (k.startswith(key) or key.startswith(k))))]
    if not rows:
        return None
    if role:
        best = max(rows, key=lambda r: _similar(r["title"], role))
        if _similar(best["title"], role) >= 0.75:
            return best["id"]
    # An unmatched status update belongs to the live application at that
    # company, or the most recent one if there are several.
    live = [r for r in rows if r["status"] in RANK]
    if len(live) == 1 and (kind != "confirmed" or not role):
        return live[0]["id"]
    if live and kind in ("rejected", "assessment", "interview", "offer"):
        return max(live, key=lambda r: r["id"])["id"]
    return None


def ensure_job(con, company: str, role: str | None, when: str, url: str | None = None) -> str:
    """An application made outside the pipeline still gets a tracked job."""
    job_id = f"email:{norm(company)}:{re.sub(r'[^a-z0-9]+', '-', (role or 'unknown role').lower()).strip('-')}"
    if not con.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
        con.execute(
            """INSERT INTO jobs (id, source, company, title, url, first_seen, last_seen, active, status)
               VALUES (?, 'email', ?, ?, ?, ?, ?, 1, 'new')""",
            (job_id, company, role or "(role not stated)", url, when, when))
    return job_id


def advance(con, job_id: str, kind: str, detail: str, at: str | None = None) -> bool:
    """Move a job's status forward; returns True if it changed."""
    new = STATUS_FOR.get(kind)
    if not new:
        return False
    cur = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()["status"]
    if new == "rejected":
        ok = cur not in ("rejected", "offer", "withdrawn")
    else:
        ok = RANK[new] > RANK.get(cur, 0) or cur in ("rejected", "closed")
    if ok:
        db.set_status(con, job_id, new, detail, at)
    return ok



def _dec(s: str | None) -> str:
    return "".join(t.decode(c or "utf-8", "replace") if isinstance(t, bytes) else t
                   for t, c in email.header.decode_header(s or "")).strip()


def body_text(msg) -> str:
    """Prefer HTML (it often names the company where the plain part does not)."""
    best = ""
    for p in msg.walk():
        ct = p.get_content_type()
        if ct not in ("text/plain", "text/html") or p.get("Content-Disposition", "").startswith("attachment"):
            continue
        raw = p.get_payload(decode=True) or b""
        t = raw.decode(p.get_content_charset() or "utf-8", "replace")
        if ct == "text/html":
            t = re.sub(r"<(style|script|head)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
            t = re.sub(r"<[^>]+>", " ", t)
        t = html.unescape(t)
        t = re.sub(r"[\u034f\u00ad\u200b-\u200d\ufeff]+", "", t)  # preheader padding
        t = re.sub(r"\s+", " ", t).strip()
        if ct == "text/html" or not best:
            best = t
    return best


def connect() -> imaplib.IMAP4_SSL:
    """Opens the job inbox read-only; exits with the old message when no app password is saved."""
    secret = password()
    if not secret:
        # The words are the command line's (gate G4); Settings, Email shows where to add the password.
        raise SystemExit(f"No Gmail app password at {PASSWORD_FILE}")
    M = imaplib.IMAP4_SSL("imap.gmail.com", timeout=IMAP_TIMEOUT)
    M.login(address(), secret)
    M.select('"[Gmail]/All Mail"', readonly=True)
    return M


CODE_SUBJECT = re.compile(r"(security|verification|confirmation|one[- ]time|access) code|verify your", re.I)
CODE_LEAD = re.compile(r"code\b[^.]{0,120}?[:\s]\s*([A-Za-z0-9]{6,10})\b")


def code_in(subject: str, body: str) -> str | None:
    """Finds the security code in an email: the first run of letters and digits after the word 'code'."""
    for m in CODE_LEAD.finditer(f"{subject}. {body}"):
        token = m.group(1)
        # A code mixes digits or cases; an ordinary word after "code" does not.
        if re.search(r"\d", token) or (token != token.lower() and token != token.upper() and not token.istitle()):
            return token
    return None


def security_code(since: float) -> str | None:
    """Returns the code an application site emailed after `since` (epoch seconds), or None.

    Read-only, like every other use of the job inbox.
    """
    try:
        M = connect()
    except (SystemExit, OSError, imaplib.IMAP4.error):
        return None
    try:
        uids = _search(M, "newer_than:1d code")
        for _msgid, _thrid, msg in reversed(list(_fetch(M, uids[-8:]))):
            sent = email.utils.parsedate_to_datetime(msg["Date"]).timestamp() if msg["Date"] else 0
            subject = _dec(msg["Subject"])
            if sent < since or not CODE_SUBJECT.search(subject):
                continue
            code = code_in(subject, body_text(msg))
            if code:
                return code
        return None
    except (OSError, imaplib.IMAP4.error, ValueError, TypeError):
        return None
    finally:
        try:
            M.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def _search(M, query: str) -> list[bytes]:
    M.literal = query.encode("utf-8")
    typ, data = M.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
    return data[0].split() if data and data[0] else []


def _unseen(M, uids, known: set[str]) -> list[bytes]:
    out = []
    for i in range(0, len(uids), 500):
        typ, data = M.uid("FETCH", b",".join(uids[i:i + 500]), "(X-GM-MSGID)")
        for line in data:
            line = line.decode() if isinstance(line, bytes) else line[0].decode()
            m = re.search(r"X-GM-MSGID (\d+) UID (\d+)|UID (\d+) X-GM-MSGID (\d+)", line)
            if m:
                msgid, uid = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
                if msgid not in known:
                    out.append(uid.encode())
    return out


def _fetch(M, uids):
    for i in range(0, len(uids), 50):
        typ, data = M.uid("FETCH", b",".join(uids[i:i + 50]), "(X-GM-MSGID X-GM-THRID BODY.PEEK[])")
        for part in data:
            if not isinstance(part, tuple):
                continue
            head = part[0].decode()
            msgid = re.search(r"X-GM-MSGID (\d+)", head).group(1)
            thrid = re.search(r"X-GM-THRID (\d+)", head).group(1)
            yield msgid, thrid, email.message_from_bytes(part[1])


def sync(con, days: int | None = None) -> list[dict]:
    """Fetch new mail, classify, update jobs. Returns the emails needing attention."""
    last = con.execute("SELECT v FROM meta WHERE k='mail_synced'").fetchone()
    if days is None:
        days = FIRST_SYNC_DAYS if not last else max(
            2, (datetime.now(timezone.utc) - datetime.fromisoformat(last["v"])).days + 2)
    M = connect()
    try:
        known = {r["msgid"] for r in con.execute("SELECT msgid FROM emails")}
        uids = _unseen(M, _search(M, f"newer_than:{days}d"), known)
        fresh = []
        for msgid, thrid, msg in _fetch(M, uids):
            if msgid in known:
                continue
            when = email.utils.parsedate_to_datetime(msg["Date"]) if msg["Date"] else datetime.now(timezone.utc)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            fresh.append((when.astimezone(timezone.utc).isoformat(timespec="seconds"), msgid, thrid, msg))
    finally:
        M.logout()

    attention = []
    for when, msgid, thrid, msg in sorted(fresh, key=lambda x: x[0]):  # status must advance in order
        sender, rcpt, subject = _dec(msg["From"]), _dec(msg["To"]), _dec(msg["Subject"])
        body = body_text(msg)
        kind = classify(sender, rcpt, subject, body)
        company = role = job_id = None
        if kind in STATUS_FOR or kind == "human":
            company = company_of(sender, rcpt, subject, body, kind)
            role = role_of(subject, body)
            if kind == "human" or not company or not role:
                kind, company, role = refine(sender, subject, body, kind, company, role)
            job_id = match_job(con, company, role, kind)
            if not job_id and norm(company) and kind in STATUS_FOR:
                job_id = ensure_job(con, company, role, when)
            if job_id:
                db.log(con, job_id, "email", f"{kind}: {subject}", when)
                advance(con, job_id, kind, f"email: {subject}", when)
        elif kind == "alert":
            ingest_alert(con, sender, subject, body, when, links=alert_links(msg))
        con.execute(
            """INSERT INTO emails (msgid, thrid, date, sender, rcpt, subject, kind, company, role, job_id, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msgid, thrid, when, sender, rcpt, subject, kind, company, role, job_id, body[:600]))
        recent = datetime.fromisoformat(when) > datetime.now(timezone.utc) - timedelta(days=3)
        if kind in ATTENTION and recent:
            attention.append({"kind": kind, "company": company, "role": role, "subject": subject,
                              "sender": sender, "date": when, "job_id": job_id})
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('mail_synced', ?)", (db.now(),))
    con.commit()
    return attention



INDEED_SUBJECT = re.compile(r"^(?P<title>.+?) (?:@|at) (?P<company>.+?)(?: in (?P<loc>.+?) and \d+ more new jobs?)?$")
SENDERS_SEED = Path(__file__).resolve().parent / "find" / "seed" / "alert_senders.toml"
ALERTS_SOURCE = "alerts"            # the sources row for email job alerts (find/seed/sources.toml)
DETAIL_PER_EMAIL = 5                # adverts read in full from one alert; the rest wait for the job page
DETAIL_TIMEOUT = 15                 # seconds per advert page
CONTEXT_CHARS = 240                 # text after a link that may name the employer and place
# the words after a link are read ahead without being consumed, so the next link is still found
ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>(?=(.{0,"
                    + str(CONTEXT_CHARS) + "}))", re.I | re.S)
# A link's words read as a job when they name a kind of role; the fallback when a board is not known.
JOB_WORDS = re.compile(
    r"\b(assistant|officer|intern(ship)?s?|placements?|graduates?|trainee|apprentice(ship)?|analyst|engineer|"
    r"developer|designer|coordinator|co-ordinator|manager|executive|researcher|research|associate|scheme|"
    r"programme|program|administrator|editor|writer|curator|archivist|librarian|fellow(ship)?|consultant|"
    r"technician|advis[eo]r|worker|producer|lecturer|teacher|tutor|nurse|scientist|specialist|accountant|"
    r"caseworker|campaigner|fundraiser|spring week|insight|year in industry)\b", re.I)
NOT_JOBS = re.compile(r"unsubscribe|manage (your )?alerts?|view (all|more|in browser)|privacy|preferences|"
                      r"see all|sign in|log in|help centre|terms|edit (this )?alert|download the app|"
                      r"https?://|^www\.|update your|cookie", re.I)
MAX_TITLE = 120
APPLICATION_NEWS = re.compile(r"your application|thank you for (applying|your application)|interview|assessment|"
                              r"application (received|update|status|outcome)|offer|unsuccessful|next steps?", re.I)


def alert_senders() -> list[tuple[str, str]]:
    """(name, part of an address) for every job alert sender: the person's own list, else Hal's."""
    from .core import settings

    own = [line.strip() for line in settings.get("email.alert_senders") or [] if line.strip()]
    if own:
        return [(match, match.lower()) for match in own]
    import tomllib

    data = tomllib.loads(SENDERS_SEED.read_text(encoding="utf-8"))
    return [(s["name"], m.lower()) for s in data.get("sender", []) for m in s.get("match", [])]


def alert_sender(sender: str) -> str | None:
    """The name of the alert sender an address belongs to, else None."""
    low = (sender or "").lower()
    return next((name for name, match in alert_senders() if match in low), None)


def _from_alert_sender(sender: str, subject: str) -> bool:
    """True for mail from a listed alert sender that is not about one of the person's applications.

    Several senders (Civil Service Jobs, NHS Jobs, Reed) also send application news from the same address,
    and those must reach the application, not become leads.
    """
    return alert_sender(sender) is not None and not APPLICATION_NEWS.search(subject or "")


def alert_links(msg) -> list[tuple[str, str, str]]:
    """(address, link words, the words after the link) of each link in a message's HTML part."""
    out = []
    for p in msg.walk():
        if p.get_content_type() != "text/html":
            continue
        raw = (p.get_payload(decode=True) or b"").decode(p.get_content_charset() or "utf-8", "replace")
        raw = re.sub(r"<(style|script|head)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        for href, words, after in ANCHOR.findall(raw):
            text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", words))).strip()
            tail = html.unescape(re.sub(r"<[^>]+>", "\n", after.split("<a", 1)[0]))
            out.append((html.unescape(href).strip(), text, tail))
    return out


def lead_from_link(url: str, words: str, after: str, sender_name: str) -> dict | None:
    """A lead from one alert link when its words read as a job title (the fallback), else None."""
    if not url.startswith(("http://", "https://")) or not words or len(words) > MAX_TITLE:
        return None
    if NOT_JOBS.search(words) or not JOB_WORDS.search(words):
        return None
    lines = [ln.strip(" \t-|,") for ln in after.splitlines() if ln.strip(" \t-|,")]
    company = lines[0] if lines and len(lines[0]) <= 60 and not re.search(r"\d{2},?\d{3}", lines[0]) else ""
    location = lines[1] if len(lines) > 1 and len(lines[1]) <= 60 else ""
    key = __import__("hashlib").sha1(url.encode("utf-8")).hexdigest()[:12]
    return {"id": f"alert:{key}", "source": "alert", "company": company or sender_name, "title": words,
            "location": location, "url": url, "description": " ".join([words, *lines[:4]]), "salary": None,
            "posted_at": None, "board_size": None}


def _detail_page(url: str) -> str:
    from . import http

    return http.get(url, timeout=DETAIL_TIMEOUT, retries=0)


def _read_detail(job: dict) -> None:
    """Fills a lead's advert from its page, when it can be read; a page that fails leaves the lead as it is."""
    from .find import job_actions

    try:
        found = job_actions.read_advert(job["url"], _detail_page(job["url"]))
    except Exception as e:  # the lead keeps the alert's words; the reason is logged
        log.info("alert advert not read for %s: %s", job["id"], e)
        return
    job["description"] = found.get("description") or job["description"]
    for k in ("location", "salary", "posted_at"):
        if found.get(k) and not job.get(k):
            job[k] = found[k]


def ingest_alert(con, sender: str, subject: str, body: str, when: str, links: list | None = None) -> int:
    """Keeps the jobs an alert email names as leads and returns how many were new.

    Indeed's match emails name one job in the subject. Every other alert is read by the generic parser: each
    link whose words read as a job title becomes a lead with the email's employer words, and the first few
    get their advert read in full.
    """
    from .core import settings
    from .find import rules

    new = 0
    m = INDEED_SUBJECT.match(subject) if "indeed" in sender.lower() else None
    if m:
        title, company, loc = m.group("title"), m.group("company"), m.group("loc")
        if not match_job(con, company, title, "confirmed"):
            job = {
                "id": f"indeed:{norm(company)}:{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')}",
                "source": "indeed", "company": company, "title": title, "location": loc or "",
                "url": "https://uk.indeed.com/jobs?" + __import__("urllib.parse").parse.urlencode(
                    {"q": f'"{title}" {company}'}),
                "description": body[:4000], "salary": None, "posted_at": when, "board_size": None,
            }
            if db.upsert(con, job):
                s, why = score(job)
                con.execute("UPDATE jobs SET score=?, score_notes=? WHERE id=?", (s, why, job["id"]))
                new += 1
    name = alert_sender(sender) or (email.utils.parseaddr(sender)[0] or "a job alert")
    detail = bool(settings.get("sites.detail_fetch"))
    read = 0
    for url, words, after in links or []:
        job = lead_from_link(url, words, after, name)
        if job is None or match_job(con, job["company"], job["title"], "confirmed"):
            continue
        if con.execute("SELECT 1 FROM jobs WHERE id = ?", (job["id"],)).fetchone():
            continue
        if detail and read < DETAIL_PER_EMAIL:
            read += 1
            _read_detail(job)
        job["posted_at"] = job["posted_at"] or when
        if db.upsert(con, job):
            con.execute("UPDATE jobs SET source_id = ? WHERE id = ?", (ALERTS_SOURCE, job["id"]))
            rules.score_and_save(con, job)
            new += 1
    if new:
        con.execute("""UPDATE sources SET seen = seen + ?, kept = kept + ?, last_run = ?, last_ok = ?
                       WHERE id = ?""", (new, new, db.now(), db.now(), ALERTS_SOURCE))
    return new
