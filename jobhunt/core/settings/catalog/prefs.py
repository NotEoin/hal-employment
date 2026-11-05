"""Settings group "Preferences": how Hal behaves while you work, each a choice someone may want either way.

Testers asked for one place for these (2026-11-04). Rows that belong to an area keep that area's key
(`applying.window`, `cvdocs.check_open`), so saved values stay where they were; `applying.window`,
`applying.press_next` and `applying.wait_minutes` are registered in `applying.py` with `group="prefs"`.
Every default is how Hal behaved before the row existed.
"""

from __future__ import annotations

from jobhunt.core.settings.registry import Setting, register

register(
    Setting("applying.bring_forward", "bool", True, "Bring Hal's form window to the front when a form opens",
            group="prefs", order=15,
            help="Off: the form opens behind the window you are using, and you switch to it yourself."),
    Setting("applying.close_popups", "bool", True, "Close job-alert and newsletter pop-ups on the way to a form",
            group="prefs", order=20,
            help="Hal presses the pop-up's own close button, such as \"No, thanks\". It never closes one that "
                 "could be part of your application."),
    Setting("applying.keep_site_answers", "bool", True, "Keep answers a site has already filled in",
            group="prefs", order=25,
            help="When Hal has no answer of its own, what the site filled in stays, marked \"Filled in by the "
                 "site\". Off: those questions are left for you to check."),
    Setting("cvdocs.check_open", "bool", True, "Check an advert is still open before writing documents",
            group="prefs", order=50,
            help="One look at the advert first, so Hal does not write for a job that has closed."),
    Setting("updates.reload_pages", "bool", True, "Reload Hal's page when Hal is updated", group="prefs", order=60,
            help="While you are typing, Hal offers a Reload button instead. Off: always the button."),
)
