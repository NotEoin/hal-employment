"""Settings group "Applying": how Hal fills forms. Hal never presses Submit.

`applying.max_steps` and `applying.page_timeout` show in the Advanced group, as few people need them.
"""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, same
from jobhunt.core.settings.registry import Setting, register

BROWSERS = choices(
    ("opera", "Opera"), ("chrome", "Google Chrome"), ("edge", "Microsoft Edge"), ("brave", "Brave"),
    ("vivaldi", "Vivaldi"), ("chromium", "Chromium"), ("firefox", "Firefox (not supported yet)"))
UNSUPPORTED_BROWSERS = ("firefox",)  # the helper is a Chromium extension; Firefox needs its own build
FILL_TIMEOUT = 600   # seconds per page; L extension.py:182 allowed 180, too short for long forms
MAX_FIX_ROUNDS = 4
MAX_WATCH_HOURS = 12
# The boards of L server.py:39-40, copied rather than imported so reading the catalogue loads no apply code.
AGGREGATORS = ("indeed.", "adzuna.", "reed.co", "linkedin.", "glassdoor.", "totaljobs.", "ziprecruiter.",
               "cv-library.", "monster.", "jooble.", "talent.com", "simplyhired.", "jobrapido.", "whatjobs.")


def _first_browser(get):
    """The first supported browser the platform layer finds, or None before that layer exists."""
    try:
        from jobhunt.core import platform
        found = platform.current().browsers()
    except (ImportError, AttributeError, OSError):
        return None
    return next((b.id for b in found if b.id not in UNSUPPORTED_BROWSERS), None)


def _supported(value):
    return "Hal cannot fill forms in Firefox yet. Choose another browser." if value in UNSUPPORTED_BROWSERS else None


register(
    Setting("applying.browser", "choice", "", "Browser Hal fills forms in", choices=BROWSERS, order=10,
            default_fn=_first_browser, validate=_supported,
            help="The browsers Hal found on this computer are marked (found)."),
    Setting("applying.mode", "choice", "helper", "How Hal fills forms", platforms=("linux",), dev_only=True,
            remote="local_only", order=20, choices=choices(
                ("helper", "In your browser, with the helper"), ("window", "In Hal's own window"),
                ("headless", "In a hidden window (for tests)"))),
    Setting("applying.window", "seg", "separate", "Open forms in", order=30,
            choices=choices(("separate", "A separate window"), ("this", "This window")),
            help="\"This window\" opens each form as a tab beside yours, brought forward while Hal fills it in."),
    Setting("applying.press_next", "bool", True, "Press Next on pages with nothing for you to check", order=40),
    Setting("applying.wait_minutes", "choice", 15, "Wait for you to sign in or pass a check for up to", order=50,
            choices=same(5, 15, 30, unit="minutes")),
    Setting("applying.notes", "link", None, "Notes about job sites", to="memory-sites", order=60,
            help="What works on each site, and what to avoid."),
    Setting("applying.submit", "locked", "Never", "Press Submit for you", order=70,
            help="Hal always stops before Submit. You press it yourself on the employer's site."),
    Setting("applying.find_contacts", "bool", True, "Find who to message about each application", advanced=True,
            order=80),
    Setting("applying.max_steps", "choice", 12, "Most form pages Hal moves through", group="advanced", order=30,
            choices=same(6, 12, 20)),
    Setting("applying.page_timeout", "choice", 45, "Give up on a slow page after", group="advanced", order=40,
            choices=same(20, 45, 90, unit="seconds")),
    Setting("applying.fill_timeout", "int", FILL_TIMEOUT, "Time allowed to fill one page, in seconds",
            advanced=True, min=30, order=90),
    Setting("applying.fix_rounds", "int", 2, "Times Hal refills answers a form refused", advanced=True, min=0,
            max=MAX_FIX_ROUNDS, order=100),
    Setting("applying.watch_hours", "int", 3, "Keep watching an application tab for, in hours", advanced=True,
            min=1, max=MAX_WATCH_HOURS, order=110),
    Setting("applying.aggregators", "textlist", list(AGGREGATORS), "Job boards that carry other employers' adverts",
            advanced=True, order=120, help="One a line: part of the site's address, such as indeed."),
)
