"""Daily digest email, sent from the job inbox to itself (or to digest_to)."""
import smtplib
from datetime import date
from email.message import EmailMessage

from . import gmail
from .config import CONFIG
from .vault import actions


def build(con) -> tuple[str, str]:
    today = date.today().isoformat()
    acts = actions(con)
    new = con.execute("""SELECT company, title, location, score, score_notes, url FROM jobs
                         WHERE first_seen >= datetime('now','-1 day') AND score >= 55 AND active=1
                         ORDER BY score DESC LIMIT 12""").fetchall()
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
    out += ["", "PIPELINE  " + "  ".join(f"{k} {v}" for k, v in counts.items()),
            f"Unreviewed matches scoring 60+: {queue}   (python -m jobhunt list -m 60)"]
    subject = f"[jobhunt] {today}: {len(acts)} need you, {len(new)} new matches"
    return subject, "\n".join(out)


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
