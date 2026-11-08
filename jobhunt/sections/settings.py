"""The setting the sectioned record adds: how often Hal checks the projects you added for changes.

It sits with the other schedule rows. Six hours is the default the person approved (sections plan,
2026-11-09): often enough that a pushed project shows within a working day, rarely enough to stay well
inside GitHub's limit for an account with dozens of projects.
"""
from __future__ import annotations

from ..core.settings.catalog._shared import choices
from ..core.settings.registry import Setting, register

EVERY_DEFAULT = 6       # hours
ORDER = 70              # after "Read your record folder every"

register(
    Setting("schedule.projects_every", "choice", EVERY_DEFAULT, "Check your projects for changes every",
            group="schedule", advanced=True, order=ORDER,
            choices=choices((1, "Every hour"), (3, "Every 3 hours"), (6, "Every 6 hours"), (12, "Every 12 hours"),
                            (24, "Every day"), (0, "Never")),
            help="Hal looks at your GitHub, folders and pages for changes and reads again only what changed. "
                 "What you changed on a project's card is never overwritten."),
)
