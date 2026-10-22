"""What the Applications area puts on Home (`needs`): the applications waiting for the person.

Each item is one application and the one thing it needs next: documents to approve, answers to check, a
last page to send, or a follow-up that is due. A page that waits for a sign-in or a human check reaches
Home as the driver's own alert, so it is not repeated here.

`urgency` runs from 0 to 100, higher first. A form already filled outranks documents, because the site may
sign the person out; anything closing within `SOON_DAYS` gains `SOON_BONUS`, so a closing job comes first.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import quote

from ..core import clock
from .queries import _app, _busy, _form_counts, column_of

log = logging.getLogger(__name__)

SOON_DAYS = 2           # the reminder window Jobs uses for saved jobs closing soon
SOON_BONUS = 20
URGENCY = {"last_page": 70, "to_check": 60, "approve": 40, "follow_up": 30}


def _route(job_id: str) -> str:
    """The app route of an application, as `router.js` `route("app", id)` writes it."""
    return "app-" + quote(job_id, safe="")


def _closes_soon(closes_at: str | None, now) -> bool:
    if not closes_at:
        return False
    try:
        return clock.parse(closes_at) - now <= timedelta(days=SOON_DAYS)
    except (ValueError, AttributeError):
        return False


def _item(r, kind: str, icon: str, tone: str, title: str, detail: str, label: str, now) -> dict:
    keys = r.keys()
    closes = r["closes_at"] if "closes_at" in keys else None
    urgency = URGENCY[kind] + (SOON_BONUS if kind != "follow_up" and _closes_soon(closes, now) else 0)
    return {"id": f"apply:{kind}:{r['id']}", "icon": icon, "tone": tone, "title": title, "detail": detail,
            "due": closes if kind != "follow_up" else r["follow_up_at"],
            "act": {"label": label, "route": _route(r["id"])}, "urgency": urgency}


def needs(con) -> list[dict]:
    """The applications that need the person now, each with what to press."""
    now = clock.now_utc()
    out = []
    rows = con.execute("""SELECT j.*, a.stage, a.follow_up_at FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
                          WHERE j.status IN ('applying', 'applied')""").fetchall()
    for r in rows:
        if _busy(r["id"]):
            continue
        col = column_of(r["status"], r["stage"])
        org, title = r["company"], r["title"]
        if col == "ready":
            out.append(_item(r, "approve", "file", "info", f"Approve documents for {title}",
                             f"{org}. Hal has your CV and letter ready.", "Open", now))
        elif col == "form":
            form = _app(con, r["id"])["form"] or {}
            _, _, to_check = _form_counts(form)
            if to_check:
                need = "1 answer needs you" if to_check == 1 else f"{to_check} answers need you"
                out.append(_item(r, "to_check", "warn", "warn", f"{need}: {title}",
                                 f"{org}. Check them here or in the form.", "Check", now))
            elif form.get("final"):
                out.append(_item(r, "last_page", "check", "info", f"Press Submit on {org}'s site",
                                 f"{title}. Hal filled the last page and stopped before Submit.", "Open", now))
        elif col == "sent" and r["follow_up_at"]:
            try:
                due = clock.parse(r["follow_up_at"]) <= now
            except (ValueError, AttributeError):
                log.warning("unreadable follow-up date for %s: %r", r["id"], r["follow_up_at"])
                continue
            if due:
                out.append(_item(r, "follow_up", "mail", "why", f"Follow up on {title}",
                                 f"{org} has not replied. Hal can draft a short, polite note for you to send.",
                                 "Open", now))
    return sorted(out, key=lambda i: -i["urgency"])
