"""Daily digest email, sent from the job inbox to itself (or to digest_to)."""
import smtplib
from datetime import date, datetime
from email.message import EmailMessage

from . import gmail
from .config import CONFIG
from .vault import actions


def build(con) -> tuple[str, str]:
    today = date.today().isoformat()
    acts = actions(con)
    new = con.execute("""SELECT company, title, location, score, score_notes, url FROM jobs
                         WHERE first_seen >= datetime('now','-1 day') AND score >= 55 AND active=1
                         AND score_notes NOT LIKE '%graduate internship%'
                         ORDER BY score DESC LIMIT 12""").fetchall()
    interns = con.execute("""SELECT company, title, location, score, url FROM jobs
                             WHERE first_seen >= datetime('now','-1 day') AND score > 0 AND active=1
                             AND score_notes LIKE '%graduate internship%' ORDER BY score DESC LIMIT 6""").fetchall()
    moved = con.execute("""SELECT j.company, j.title, ev.detail FROM events ev JOIN jobs j ON j.id = ev.job_id
                           WHERE ev.kind='status' AND ev.at >= datetime('now','-1 day')
                           AND j.status IN ('applied','assessment','interview','offer','rejected')
                           ORDER BY ev.at""").fetchall()
    counts = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, COUNT(*) n FROM jobs WHERE status NOT IN ('new','closed','skipped') GROUP BY status")}
    queue = con.execute("""SELECT COUNT(*) FROM jobs WHERE status='new' AND active=1 AND score >= 60""").fetchone()[0]

    out = [f"Job search - {today}", ""]
    out += ["NEEDS YOU"] + ([f"  - {t}  (due {d})" for t, d in acts] or ["  Nothing waiting on you."]) + [""]
    if moved:
        out += ["CHANGED IN THE LAST DAY"] + [f"  - {r['company']} - {r['title']}: {r['detail']}" for r in moved] + [""]
    out += ["NEW STRONG MATCHES"]
    out += [f"  {r['score']:.0f}  {r['company']} - {r['title']} | {r['location']}\n      {r['url']}" for r in new] or ["  None today."]
    if interns:
        out += ["", "GRADUATE INTERNSHIPS"]
        out += [f"  {r['score']:.0f}  {r['company']} - {r['title']} | {r['location']}\n      {r['url']}" for r in interns]
    out += ["", "PIPELINE  " + "  ".join(f"{k} {v}" for k, v in counts.items()),
            f"Unreviewed matches scoring 60+: {queue}   (python -m jobhunt list -m 60)"]
    subject = f"[jobhunt] {today}: {len(acts)} need you, {len(new)} new matches"
    return subject, "\n".join(out)


def _names(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def spoken(con) -> str:
    """Returns a short briefing written to be read aloud."""
    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    out = [f"{greeting}, Firstname."]
    urgent = con.execute("""SELECT company, status FROM jobs WHERE status IN ('assessment','interview','offer')""").fetchall()
    for kind in ("offer", "interview", "assessment"):
        names = [r["company"] for r in urgent if r["status"] == kind]
        if names:
            noun = {"offer": "an offer", "interview": "an interview", "assessment": "an assessment"}[kind]
            out.append(f"You have {noun} pending with {_names(names)}." if len(names) == 1
                       else f"You have {kind}s pending with {_names(names)}.")
    follow = [t for t, _ in actions(con) if t.startswith("Follow up")]
    if follow:
        out.append(f"{len(follow)} application{'s' * (len(follow) > 1)} {'have' if len(follow) > 1 else 'has'} "
                   "gone ten days without a reply.")
    new = con.execute("""SELECT company, title FROM jobs WHERE first_seen >= datetime('now','-1 day')
                         AND score >= 60 AND active = 1 ORDER BY score DESC LIMIT 3""").fetchall()
    if new:
        out.append(f"I found {len(new)} strong new match{'es' * (len(new) > 1)}. The best is "
                   f"{new[0]['title']} at {new[0]['company']}.")
    ready = con.execute("SELECT COUNT(*) FROM jobs WHERE status = 'ready'").fetchone()[0]
    if ready:
        out.append(f"{ready} application pack{'s are' if ready > 1 else ' is'} drafted and waiting for you.")
    if len(out) == 1:
        out.append("Nothing needs your attention.")
    return " ".join(out)


def send(con, dry_run: bool = False) -> str:
    subject, body = build(con)
    if dry_run:
        return f"{subject}\n\n{body}"
    msg = EmailMessage()
    msg["From"] = gmail.ADDRESS
    msg["To"] = CONFIG.get("digest_to") or gmail.ADDRESS
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(gmail.ADDRESS, gmail.PASSWORD_FILE.read_text().strip().replace(" ", ""))
        s.send_message(msg)
    return subject
