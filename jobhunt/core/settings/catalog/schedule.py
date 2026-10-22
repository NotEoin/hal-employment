"""Settings group "Schedule": when Hal searches and reads email.

`goal.mode` shapes the plan on top of these: "Getting ready" keeps only the first search time, and "Just
watching" searches on Mondays only.
"""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, same
from jobhunt.core.settings.registry import Setting, register

register(
    Setting("schedule.search_times", "times", ["07:30", "13:30"], "Search for jobs at", order=10,
            help="Type it like 07:30 and 13:30."),
    Setting("schedule.mail_every", "choice", 30, "Check email", order=20, choices=choices(
        (15, "Every 15 minutes"), (30, "Every 30 minutes"), (60, "Every hour"))),
    Setting("schedule.mail_hours", "hours", ["07:00", "23:00"], "Between", order=30),
    Setting("schedule.start_at_login", "bool", True, "Start Hal when the computer starts", machine=True,
            remote="label", order=40),
    Setting("schedule.catch_up", "bool", True, "Catch up on missed searches", order=50),
    Setting("schedule.record_every", "choice", 10, "Read your record folder every", advanced=True, order=60,
            choices=same(10, 30, 60, unit="minutes")),
)
