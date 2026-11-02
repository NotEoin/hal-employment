"""Figures for the statistics page and Home's "This week": applications sent over time, replies, and where they
stand."""
from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import median

SENT = ("applied", "assessment", "interview", "offer", "rejected", "withdrawn")
REPLIES = ("assessment", "interview", "offer", "rejected")
STANDING = (("applied", "Waiting for a reply"), ("assessment", "Assessment"), ("interview", "Interview"),
            ("offer", "Offer"), ("rejected", "Rejected"), ("withdrawn", "Withdrawn"))
WEEKS = 12


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone()


def summary(con) -> dict:
    """Returns the counts the statistics page shows, with days and weeks in local time."""
    jobs = {r["id"]: r for r in con.execute(
        f"SELECT id, company, title, source, status FROM jobs WHERE status IN ({','.join('?' * len(SENT))})", SENT)}
    sent, replied = {}, {}
    for e in con.execute("SELECT job_id, at, detail FROM events WHERE kind = 'status' ORDER BY at"):
        status = e["detail"].split(":")[0]
        if e["job_id"] not in jobs:
            continue
        if status == "applied":
            sent.setdefault(e["job_id"], _local(e["at"]))
        elif status in REPLIES:
            replied.setdefault(e["job_id"], _local(e["at"]))

    today = datetime.now().astimezone().date()
    days = Counter(at.date() for at in sent.values())
    last7 = sum(days[today - timedelta(d)] for d in range(7))
    monday = today - timedelta(today.weekday())
    weeks = [{"start": (start := monday - timedelta(weeks=w)).isoformat(),
              "count": sum(days[start + timedelta(d)] for d in range(7))} for w in reversed(range(WEEKS))]

    heard = [j for j in jobs if jobs[j]["status"] in REPLIES]
    waits = [(replied[j] - sent[j]).days for j in replied if j in sent and replied[j] >= sent[j]]
    now = datetime.now().astimezone()
    waiting = sorted(({"id": j, "company": jobs[j]["company"], "title": jobs[j]["title"],
                       "days": (now - sent[j]).days} for j in sent if jobs[j]["status"] == "applied"),
                     key=lambda w: -w["days"])
    sources = {}
    for j in jobs.values():
        row = sources.setdefault(j["source"] or "unknown", {"source": j["source"] or "unknown", "sent": 0,
                                                             "heard": 0, "rejected": 0})
        row["sent"] += 1
        row["heard"] += j["status"] in REPLIES
        row["rejected"] += j["status"] == "rejected"
    statuses = Counter(j["status"] for j in jobs.values())
    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    return {
        "today": days[today], "last7": last7,
        "previous7": sum(days[today - timedelta(d)] for d in range(7, 14)),
        "total": len(jobs), "undated": len(jobs) - len(sent),
        "heard": len(heard), "reply_days": median(waits) if waits else None,
        "weeks": weeks,
        "standing": [{"status": s, "label": label, "count": statuses[s]} for s, label in STANDING if statuses[s]],
        "waiting": waiting[:8],
        "sources": sorted(sources.values(), key=lambda r: -r["sent"]),
        "ready": one("SELECT COUNT(*) FROM jobs WHERE status = 'ready' AND active = 1"),
        "applying": one("SELECT COUNT(*) FROM jobs WHERE status = 'applying'"),
    }


WEEK_DAYS = 7
# Home's pipeline bar, in board order, with the statuses each stage counts.
PIPELINE = (("Getting ready", "ready"), ("Form filled", "form"), ("Sent", "sent"), ("Interview", "interview"),
            ("Offer", "offer"), ("Closed", "closed"))
INTERVIEWS = ("assessment", "interview")


def week(con, now: datetime | None = None) -> dict:
    """Home's "This week": new matches, applications sent, replies and interviews in the last 7 days, and
    where every application stands. Old (+00:00) and new (Z) times compare on their first 19 characters."""
    from .docs.routes import column

    now = now or datetime.now().astimezone()
    since_utc = (now - timedelta(days=WEEK_DAYS)).astimezone(UTC).isoformat()[:19]

    def events(statuses) -> int:
        marks = ",".join("?" * len(statuses))
        return con.execute(f"""SELECT count(DISTINCT job_id) FROM events WHERE kind = 'status'
                               AND substr(detail, 1, instr(detail || ':', ':') - 1) IN ({marks})
                               AND substr(at, 1, 19) >= ?""", (*statuses, since_utc)).fetchone()[0]

    # only jobs that Matches or Saved shows: counting hidden jobs said "255 new matches" beside an empty
    # Matches tab after the first search
    from .find import routes as find_routes

    matches = 0
    for tab in ("matches", "saved"):
        where, args = find_routes._shown_where(tab)
        matches += con.execute(f"SELECT count(*) FROM jobs j WHERE {where} AND substr(j.first_seen, 1, 19) >= ?",
                               (*args, since_utc)).fetchone()[0]
    # From job statuses, as the board reads them, because applications sent before Hal kept an application
    # row for every job still count.
    stages = Counter(column(r["status"], r["stage"]) for r in con.execute(
        """SELECT j.status, a.stage FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status IN ('applying', 'applied', 'assessment', 'interview', 'offer', 'rejected', 'withdrawn')
               OR (j.status IN ('closed', 'skipped') AND a.job_id IS NOT NULL)"""))
    return {"matches": matches, "sent": events(("applied",)), "replies": events(REPLIES),
            "interviews": events(INTERVIEWS),
            "pipeline": [{"stage": label, "key": key, "n": stages[key]} for label, key in PIPELINE]}
