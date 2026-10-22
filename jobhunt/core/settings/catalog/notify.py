"""Settings group "Notifications": pop-ups, phone alerts and the daily summary."""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, safe
from jobhunt.core.settings.registry import Setting, register

FOLLOW_UP_DAYS = 10     # L vault.py:15
DIGEST_NEW_MIN = 55     # L digest.py:15
DIGEST_REVIEW_MIN = 60  # L digest.py:27

register(
    Setting("notify.desktop", "bool", True, "Pop-ups on this computer", order=10,
            help="In a window opened from another computer, pop-ups show in that browser."),
    Setting("notify.phone", "bool", False, "Alerts on your phone", help="Uses the free ntfy app. Set it up from Home.",
            order=20),
    Setting("notify.ntfy_topic", "secret", None, "Phone alert code", secret_name="notify.ntfy_topic", advanced=True,
            help="Shown only as a QR code. Hal makes a new one for you.", order=30),
    Setting("notify.alert_score", "range", 75, "Tell me about matches scoring at least", min=50, max=95, order=40),
    Setting("notify.quiet_hours", "hours", ["22:00", "07:30"], "Quiet hours", order=50,
            help="Type it like 22:00 to 07:30, or Off."),
    Setting("notify.daily_summary", "choice", "18:00", "Daily summary by email", order=60,
            choices=choices(("off", "Off"), ("08:00", "08:00"), ("12:00", "12:00"), ("18:00", "18:00"))),
    Setting("notify.daily_to", "email", "", "Send the summary to", advanced=True, order=70,
            default_fn=lambda get: safe(get, "you.email") or None),
    Setting("notify.follow_up_days", "int", FOLLOW_UP_DAYS, "Suggest a follow-up after this many days with no reply",
            advanced=True, min=1, unit="days", order=80),
    Setting("notify.digest_new_min", "int", DIGEST_NEW_MIN, "Lowest score for new jobs in the summary",
            advanced=True, min=0, max=100, order=90),
    Setting("notify.digest_review_min", "int", DIGEST_REVIEW_MIN, "Lowest score for jobs to review in the summary",
            advanced=True, min=0, max=100, order=100),
    # Developer only until the weekly review is built.
    Setting("notify.weekly_review", "choice", "off", "Weekly review", dev_only=True, order=110, choices=choices(
        ("off", "Off"), ("monday_0900", "Monday 09:00"), ("friday_1600", "Friday 16:00"))),
)
