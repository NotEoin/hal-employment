"""The private-answer guard: the last check before any text goes to an AI.

The first line of defence is that private-question fields never enter a request (`autofill.private_question`
and the form-fill exclusions). This guard is the second: it searches every prompt for the person's saved
free-text private answers and refuses the request when one is there. Option answers ("Yes", "No", "Prefer
not to say") are never searched for: they appear in ordinary text everywhere, so matching them would stop
all AI form filling while catching nothing the field exclusion does not already stop. The values come only
from `settings.private.values_for_guard()`, are compared case-folded with whitespace collapsed, and
are never logged; the Activity row says that a request was stopped, never what was in it.
"""
from __future__ import annotations

import logging

from .base import ChatRequest, PrivateLeak

log = logging.getLogger(__name__)

MIN_CHARS = 8          # shorter free text ("ADHD") is left to the field exclusion
STOPPED = "Hal stopped an AI request that would have included a private answer"
WHY = "A saved private answer was in the text, and private answers are never sent to any AI."
# Option answers typed into a free-text box are still everyday words; searching for them would stop every form.
OPTION_WORDS = ("yes", "no", "prefer not to say", "none", "n/a", "not applicable")


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


def _values() -> list[str]:
    """The saved free-text private answers, from the private answers store."""
    from ..core.settings.private import values_for_guard

    return list(values_for_guard())


def _searched(values: list[str]) -> list[str]:
    return [v for v in (_fold(x) for x in values) if len(v) >= MIN_CHARS and v.strip(" .") not in OPTION_WORDS]


def contains_private(*texts: str) -> bool:
    """True when any of `texts` holds a saved free-text private answer."""
    wanted = _searched(_values())
    if not wanted:
        return False
    hay = _fold("\n".join(texts))
    return any(v in hay for v in wanted)


def _record_stop(req: ChatRequest) -> None:
    from .. import db
    from ..core import clock

    con = db.connect()
    try:
        with con:
            con.execute("""INSERT INTO activity (at, area, kind, icon, tone, text, why, job_id)
                           VALUES (?, 'system', 'ai.private_stopped', 'shield', 'warn', ?, ?, ?)""",
                        (clock.iso(), STOPPED, WHY, req.job_id))
    finally:
        con.close()


def check(req: ChatRequest) -> None:
    """Raises `PrivateLeak` (after an Activity row with no content) when `req` holds a private answer."""
    if not contains_private(req.system, req.user):
        return
    log.warning("stopped an AI request for %r: it held a private answer", req.purpose or "unnamed")
    try:
        _record_stop(req)
    except Exception:
        log.exception("could not record the stopped AI request")
    raise PrivateLeak(STOPPED)
