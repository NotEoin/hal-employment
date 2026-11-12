"""Settings group "You": who the person is, as forms and documents need it."""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, safe
from jobhunt.core.settings.registry import Setting, register

PRIVATE_HELP = ("Used only to answer these questions on forms. Never sent to the AI, never in logs or exports. "
                "Hal shows only whether they are saved.")


def _preferred_name(get):
    name = (safe(get, "you.name") or "").split()
    return name[0] if name else None


def _location_line(get):
    city = (safe(get, "you.city") or "").strip()
    return f"{city}, United Kingdom" if city else None


EDUCATION = (
    ("degree", "Degree"), ("subject", "Subject"), ("university", "University or college"), ("grade", "Grade"),
    ("start_year", "Started"), ("dissertation", "Dissertation"),
)
LINKS = (("linkedin", "LinkedIn"), ("website", "Website"), ("github", "GitHub"))

register(
    Setting("you.name", "text", "", "Your name", help="As it appears on your CV, letters and forms.", order=10),
    Setting("you.other_names", "text", "", "Other names you go by", advanced=True, order=15,
            help="Usernames, a student or staff number, a maiden name: how Hal knows a file is yours when it "
                 "does not give your full name. Separate them with commas."),
    Setting("you.preferred_name", "text", "", "What Hal calls you", default_fn=_preferred_name, order=20),
    Setting("you.email", "email", "", "Email for applications", order=30),
    Setting("you.phone", "phone", "", "Phone", order=40),
    Setting("you.city", "text", "", "Where you live", rescore=True, order=50,
            help="Used for distances, and on forms that ask for your town."),
    Setting("you.country", "locked", "United Kingdom", "Country", help="Hal works with UK jobs for now.", order=60),
    Setting("you.location_line", "text", "", "How your location is written on forms", advanced=True,
            default_fn=_location_line, order=70),
    Setting("you.right_to_work", "choice", "uk_no_visa", "Right to work", order=80, choices=choices(
        ("uk_no_visa", "UK, no visa needed"), ("uk_visa", "UK, with a visa"),
        ("need_sponsorship", "I need sponsorship"))),
    Setting("you.work_status", "text", "", "How your right to work is written on forms", advanced=True, order=90,
            help="Leave empty and Hal words it from your right to work."),
    Setting("you.visa_detail", "text", "", "About your visa", visible_if="you.right_to_work != 'uk_no_visa'",
            order=100),
    Setting("you.start", "choice", "now", "When you can start", order=110, choices=choices(
        ("now", "Straight away"), ("1w", "After 1 week"), ("1m", "After 1 month"), ("date", "From a date"))),
    Setting("you.start_date", "date", "", "Start date", visible_if="you.start == 'date'", order=120),
    Setting("you.notice", "text", "None, available immediately", "Notice period, as forms should say it",
            advanced=True, order=130),
    Setting("you.relocate", "bool", False, "Willing to move for a job", order=140),
    Setting("you.office", "bool", True, "Happy to work in an office", advanced=True, order=150),
    Setting("you.driving_licence", "choice", "prefer_not", "Full UK driving licence", advanced=True, order=160,
            choices=choices(("yes", "Yes"), ("no", "No"), ("prefer_not", "Prefer not to say"))),
    *(Setting(f"you.links.{key}", "url", "", label, order=170 + n,
              help="Only links you give are checked." if n == 0 else "")
      for n, (key, label) in enumerate(LINKS)),
    *(Setting(f"you.education.{key}", "text", "", label, order=180 + n,
              help="Filled in from your CV; change anything Hal read wrongly." if n == 0 else "")
      for n, (key, label) in enumerate(EDUCATION)),
    Setting("you.nationality", "text", "", "Nationality (only if forms ask)", advanced=True, order=190),
    Setting("you.diversity", "choice", "prefer_not", "Answer for equality questions you have not answered",
            advanced=True, order=200, choices=choices(
                ("prefer_not", "Prefer not to say"), ("leave", "Leave them for me to answer"))),
    Setting("you.salary_answer", "text", "", "One salary answer for every form", advanced=True, order=210,
            help="Leave empty and Hal works out a figure for each job."),
    Setting("you.private", "private", None, "Private answers (equality and adjustments)", help=PRIVATE_HELP,
            order=220),
)
