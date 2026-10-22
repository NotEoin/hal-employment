"""Job sources: each module fetches adverts from one kind of site and returns them as job dicts.

`places()` is where the place-based searches look: the person's own goal places, so no source names a place
of its own (the source queries). The sources registry replaces these modules.
"""
from __future__ import annotations

DEFAULT_PLACES = ("London",)   # the UK's largest job market, for a goal with no places yet


def places() -> list[str]:
    """The goal's places in the person's order, else `DEFAULT_PLACES`."""
    from ..find import goal

    return [p.name for p in goal.current().places] or list(DEFAULT_PLACES)
