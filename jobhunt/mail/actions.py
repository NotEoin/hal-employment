"""The email area's background work: checking the inbox and the daily summary.

`mail.check` reads new emails (read only), sorts them, moves jobs on and makes to-dos; the work itself is
`gmail.sync` and `tasks.sync`, which the command line also calls. `notify.digest` sends the person's own daily
summary to their own address, the only email Hal ever sends. The scheduler starts both.

Desktop pop-ups are skipped in test mode, because a test run must never put a notification on the screen of
the computer it runs on.
"""
from __future__ import annotations

import logging
import os

from jobhunt.core import paths, runner

log = logging.getLogger(__name__)


def _alert(item: dict) -> None:
    if os.environ.get(paths.ENV_TEST) == "1":
        return
    from jobhunt.notify import notify

    notify(f"{item['kind'].title()}: {item['company'] or item['sender']}", item["subject"],
           urgent=item["kind"] != "human")


def _check_example(world) -> dict:
    return {}


@runner.action("mail.check", area="email", label="Checking your email", why="Email check, every {every} minutes",
               priority=2, example=_check_example)
def check(ctx, every: int | str = "few") -> dict:
    """Reads new emails, sorts them and makes to-dos for the ones that need you."""
    from jobhunt import gmail, tasks

    ctx.step("Reading new emails")
    ctx.used("email", "Your email inbox, read only")
    before = ctx.con.execute("SELECT count(*) FROM emails").fetchone()[0]
    attention = gmail.sync(ctx.con)
    new = ctx.con.execute("SELECT count(*) FROM emails").fetchone()[0] - before
    ctx.checkpoint()
    ctx.step("Making to-dos for the emails that need you")
    made = tasks.sync(ctx.con)
    for item in attention:
        try:
            _alert(item)
        except OSError:
            log.warning("could not show an alert for %s", item["subject"])
    ctx.changed(f"Sorted {new} new emails. {len(attention)} need you. {len(made)} new to-dos.")
    return {"new": new, "need_you": len(attention), "todos": len(made)}


def _digest_example(world) -> dict:
    return {"dry_run": True}


@runner.action("notify.digest", area="email", label="Sending your daily summary", why="Daily summary at {time}",
               priority=2, example=_digest_example)
def digest(ctx, time: str = "the time you chose", dry_run: bool = False) -> dict:
    """Writes the daily summary and emails it to your own address."""
    from jobhunt import digest as daily

    ctx.step("Writing your daily summary")
    subject = daily.send(ctx.con, dry_run=dry_run).splitlines()[0]
    ctx.changed(("Wrote" if dry_run else "Sent you") + f" your daily summary: {subject}")
    return {"subject": subject, "sent": not dry_run}
