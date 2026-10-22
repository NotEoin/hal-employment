"""The never-Submit guard: which controls Hal may press, and the record of each press it refused.

Hal never presses Submit, Send or a final Apply, in any mode; the person presses it on the employer's site.
Both engines ask `never_press` before every press and raise `WouldSubmit` when it names a reason. The
helper's content script mirrors these rules with the patterns in `PATTERNS`, sent with every command, so
the words live here once.

Design decisions:
- When in doubt the page is final: a page whose facts do not say otherwise is treated as the last one.
- A `<button>` with no `type` inside a form submits it under HTML, so it counts as submit-like ("Add",
  "Upload" and "Save" included) unless its own words are the site's Next words.
- "Apply" words reach the form from an advert, so they are refused only on a page that holds a form, on
  a final page, or where the person is signed in (where Apply may send a saved CV at once).
- Each refusal is written as a `site_events` row `would_submit`, so a site that tempts Hal is visible.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable

log = logging.getLogger(__name__)

STOPPED = "Hal stopped before pressing Submit"

# Words that always send: Submit, Send, Finish, Confirm and submit, Place application.
ALWAYS_WORDS = re.compile(
    r"\bsubmit|^\s*send\b|\bsend (my |your |the |this )?application|^\s*finish\b|"
    r"\bconfirm (and|&) (submit|send|apply)|\bplace (my |your |the )?application", re.I)
# Words that send on a form's last page; on an advert, Apply leads to the form.
FINAL_WORDS = re.compile(
    r"^\s*(submit|submit (your |my |the )?application|send( (your |my )?application)?|"
    r"((easy|quick|1[- ]click) )?apply( now)?( for this (job|role))?|complete( (your |my |the )?application)?|"
    r"finish( application)?|(confirm|review) (and|&) submit|submit (and|&) (finish|apply))\s*$", re.I)
# The site's own words for moving to the next page of a form. The tail allows the arrows and chevrons
# sites put after "Next" (U+00BB, U+203A, U+2192).
NEXT_WORDS = re.compile(r"^\s*(next|next step|next page|continue|proceed|save (and|&) (continue|next|proceed)|"
                        r"continue to .{0,30}|next: .{0,30}|review( (your |my )?application)?|go to next step)"
                        "[\\s\u00bb\u203a>\u2192]*$", re.I)

KINDS = ("apply", "next", "named")
# Sent with every helper command, so content.js applies the same rules (L content.js:93-109).
PATTERNS = {"always": ALWAYS_WORDS.pattern, "final": FINAL_WORDS.pattern, "nextWords": NEXT_WORDS.pattern}


class WouldSubmit(RuntimeError):
    """Hal was about to press a control that could send the application, and did not."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"{STOPPED}: {reason}")
        self.reason = reason


def _matches(words: Iterable[str], name: str) -> bool:
    low = " ".join(name.split()).lower()
    return any(w and w.lower() in low for w in words)


def never_press(control: dict, page: dict, kind: str = "named") -> str | None:
    """The reason Hal must not press `control` on `page`, or None when the press is safe.

    `control` is a survey control: `name`, and `submit` and `inForm` when known (`type`, `value` and
    `aria` add words). `page` holds `form` (the page holds an application form), `final` (missing means
    final), `signed_in`, and a site note's `submit_words` and `next_words`. `kind` is "apply" for the way
    from an advert to its form, "next" for a form's Next button, "named" for a control chosen by name.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind of press {kind!r}")
    texts = [str(control.get(k) or "") for k in ("name", "value", "aria")]
    name = next((t for t in texts if t.strip()), "")
    said = f'"{" ".join(name.split())[:60]}"' if name.strip() else "an unlabelled button"
    nexty = any(NEXT_WORDS.search(t) for t in texts if t) or _matches(page.get("next_words") or [], name)
    final = page.get("final", True)
    if any(ALWAYS_WORDS.search(t) for t in texts if t):
        return f"{said} would send the application"
    if _matches(page.get("submit_words") or [], name):
        return f"{said} is this site's Submit button"
    if any(FINAL_WORDS.search(t) for t in texts if t):
        if kind != "apply" or page.get("form") or page.get("signed_in"):
            return f"{said} would send the application"
        if kind == "apply" and page.get("final") is True:
            return f"{said} is on the last page"
    submitter = str(control.get("type") or "").lower() in ("submit", "image") or bool(control.get("submit"))
    if submitter and control.get("inForm", True) and not (nexty and not final):
        return f"{said} sends the form it is in"
    if kind == "next" and not nexty:
        return f"{said} is not this form's Next button"
    if kind == "named" and page.get("form") and final and not nexty:
        return f"{said} is on the form's last page"
    return None


def record(job_id: str | None, url: str, reason: str, con=None) -> None:
    """Writes a `site_events` row `would_submit` for a press Hal refused or a submit it cancelled."""
    from .. import db
    from .queries import _site

    log.warning("would_submit on %s: %s", url, reason)
    own = con is None
    con = con or db.connect()
    try:
        con.execute("INSERT INTO site_events (at, job_id, site, url, what, detail) VALUES (?, ?, ?, ?, ?, ?)",
                    (db.now(), job_id, _site(url) if url else "", url, "would_submit", reason[:300]))
        con.commit()
    finally:
        if own:
            con.close()


def refuse(job_id: str | None, url: str, reason: str) -> WouldSubmit:
    """Records the refusal and returns the error to raise."""
    record(job_id, url, reason)
    return WouldSubmit(reason)


def check(job_id: str | None, url: str, control: dict, page: dict, kind: str = "named") -> None:
    """Raises `WouldSubmit`, after recording it, when `control` must not be pressed."""
    reason = never_press(control, page, kind)
    if reason:
        raise refuse(job_id, url, reason)
