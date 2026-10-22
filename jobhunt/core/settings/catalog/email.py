"""Settings group "Email": the inbox Hal reads. Hal never sends from it, except the person's own summary."""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, safe
from jobhunt.core.settings.registry import Setting, register

PROVIDER_HELP = "Gmail for now. Outlook and Hotmail need a Microsoft sign-in, which comes later."
# The settings page fills in where the password is kept (Windows Credential Manager, your desktop keyring).
PASSWORD_HELP = ("Kept in {store}. Hal reads your email but never sends from it, except your own daily "
                 "summary.")
FIRST_SYNC_DAYS = 400   # L gmail.py:35; reaches back over a whole graduate recruitment season


def _supported(value):
    return "Outlook and Hotmail come later. Choose Gmail for now." if value == "outlook" else None


register(
    Setting("email.provider", "choice", "gmail", "Email account", help=PROVIDER_HELP, validate=_supported, order=10,
            choices=choices(("gmail", "Gmail"), ("outlook", "Outlook or Hotmail (coming later)"))),
    Setting("email.address", "email", "", "Address", default_fn=lambda get: safe(get, "you.email") or None,
            order=20),
    Setting("email.app_password", "secret", None, "App password", secret_name="email.app_password",
            help=PASSWORD_HELP, order=30),
    Setting("email.todos", "bool", True, "Turn interview and test emails into to-dos", order=40),
    Setting("email.codes", "bool", True, "Read sign-in codes that job sites email you", order=50,
            help="Hal types the code in. You press the button."),
    Setting("email.refine_with_ai", "bool", True, "Ask the AI about emails the rules cannot sort", advanced=True,
            order=60),
    Setting("email.first_sync_days", "int", FIRST_SYNC_DAYS, "How far back the first check reads", advanced=True,
            min=1, unit="days", order=70),
    Setting("email.lookback_days", "int", 21, "Days of email that make to-dos", advanced=True, min=1, unit="days",
            order=80),
    Setting("email.alert_senders", "textlist", [], "Job alert senders", advanced=True, order=90,
            help="Leave empty to use Hal's built-in list. One sender address or part of one a line."),
)
