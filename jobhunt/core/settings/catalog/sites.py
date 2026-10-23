"""Settings group "Job sites": where and how Hal searches."""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import same
from jobhunt.core.settings.registry import Setting, register

KEY_HELP = "Hal works without it. With it, Hal searches one more large site."
MAX_THREADS = 16

register(
    Setting("sites.where", "link", None, "Where Hal looks", to="jobs-where", order=10,
            help="The job sites Hal searches, and which are switched on."),
    Setting("sites.max_age_days", "choice", 21, "Ignore adverts older than", rescore=True,
            choices=same(7, 14, 21, 45, unit="days"), order=20),
    Setting("sites.check_open", "bool", True, "Check an advert is still open before showing it", order=30),
    Setting("sites.read_alerts", "bool", True, "Read job alert emails (Indeed, LinkedIn and others)", order=40),
    Setting("sites.adzuna_key", "secret", None, "A free Adzuna key", secret_name="sites.adzuna_key", order=50,
            help=KEY_HELP + " Paste it as id:key."),
    Setting("sites.reed_key", "secret", None, "A free Reed key", secret_name="sites.reed_key", help=KEY_HELP,
            order=60),
    Setting("sites.detail_fetch", "bool", True, "Read the full advert for promising jobs", advanced=True, order=70),
    Setting("sites.threads", "int", 12, "Sites searched at once", min=1, max=MAX_THREADS, advanced=True, order=80),
    # Empty sends no address at all: the old http.py sent the owner's email to every site (L http.py:7).
    Setting("sites.contact", "email", "", "Contact address sent to job sites", advanced=True, order=90,
            help="Some sites like to know who to contact about heavy use. Leave empty to send none."),
)
