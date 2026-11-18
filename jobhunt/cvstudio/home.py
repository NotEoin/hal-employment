"""What My CV puts on Home: text the one-time import could not place in Hal's copy.

The import of today's CV checks that every piece of text arrived; when some did not, the person
should look before Hal sends that CV anywhere, so it is a need until a later version holds the text. Only the
lead CV is looked at: it is the one sent when nothing chooses another.
"""
from __future__ import annotations

import logging

from jobhunt.cvstudio import store

log = logging.getLogger(__name__)

URGENCY = 32            # after redrafts (30), before interrupted actions (40), on Home's scale
SHOWN = 2               # pieces of missing text quoted in the detail line


def needs(con) -> list[dict]:
    """One item when the lead CV still lacks text from the old CV, else none."""
    vid = store.lead_version_id(con)
    if not vid:
        return []       # no CV yet: setup asks for one; Home does not make one by looking
    try:
        doc = store.get(vid, con=con)
    except Exception:  # Home must draw even when the CV cannot be read; the health check says why
        log.exception("could not read the CV for Home")
        return []
    missing = list(doc.extras.get("not_found") or [])
    if not missing:
        return []
    quoted = "; ".join(f'"{m[:60]}"' for m in missing[:SHOWN])
    more = f" and {len(missing) - SHOWN} more" if len(missing) > SHOWN else ""
    return [{"id": "cv:not_found", "icon": "file", "tone": "warn",
             "title": (f"{len(missing)} pieces of your old CV are not in Hal's copy" if len(missing) != 1
                       else "1 piece of your old CV is not in Hal's copy"),
             "detail": f"Check them before Hal sends your CV: {quoted}{more}.", "due": None,
             "act": {"label": "Check your CV", "route": "cv-versions"}, "urgency": URGENCY}]


def sections(con) -> dict:
    """How many CVs the person has, archived ones included, so the menu says "My CVs" once there are several."""
    return {"cv_count": con.execute("SELECT count(*) FROM cv_documents").fetchone()[0]}
