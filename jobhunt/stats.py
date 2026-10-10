"""Figures for the statistics page: applications sent over time, replies, and where they stand."""
from collections import Counter
from datetime import datetime, timedelta
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
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
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
