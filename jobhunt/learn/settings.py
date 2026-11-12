"""The setting learning adds: which areas Hal looks for patterns in.

It lives in Advanced because every suggestion already waits for a yes; switching an area off only stops new
suggestions from it, and what the person already said yes to stays until they switch it off in What Hal
remembers.
"""
from __future__ import annotations

from ..core.settings.registry import Setting, register

ORDER = 140     # after "Quit Hal", the last row of Advanced
AREAS = (("finding", "Finding jobs"), ("cv", "Your CV"), ("writing", "Your writing"))   # `patterns.SOURCES`

register(
    Setting("learn.enabled", "multi", [a for a, _ in AREAS], "Suggestions from your choices", group="advanced",
            choices=AREAS, order=ORDER,
            help="Hal looks for patterns in the jobs you save and hide, the CV lines you keep and the changes "
                 "you make to its drafts, and suggests changes. Nothing changes until you say yes."),
)
