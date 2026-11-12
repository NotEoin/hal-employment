"""The profile's action kind: sorting the experiences in the profile into tiers (`profile.tiers`), and noting
where each was done (`profile.contexts`).

The ingest sorts them by itself when it has read a batch (`ingest.actions`); this is the same pass when the
person presses Sort them again on Check your record. One at a time (`exclusive="kind"`), as two passes at
once would ask the AI about the same cards twice.

`dedupe` is the pass an ingest runs after its read, before sorting, merging the experiences found twice under
different names (`profile.duplicates`); sorting them again does not merge, so a person's Undo is never
followed by another merge until a new read.
"""
from __future__ import annotations

from jobhunt.core import events, runner
from jobhunt.profile import contexts, duplicates, tiers

KIND = "profile.tiers"


def dedupe(ctx) -> dict[str, int]:
    """One pass merging the experiences found twice under different names (`profile.duplicates`), inside an
    action; says how many."""
    counts = duplicates.refresh(ctx.con)
    if counts["merged"]:
        ctx.changed(f"Merged {counts['merged']} experiences found again under another name into "
                    f"{counts['groups']} cards. Check your record lists them, with Undo.")
    return counts


def sort(ctx) -> dict[str, int]:
    """One sorting pass inside an action: says what changed and tells open pages to read the profile again."""
    counts = tiers.refresh(ctx.con)
    contexts.refresh(ctx.con)
    sorted_now = counts["rules"] + counts["judged"]
    if sorted_now:
        ctx.changed(f"Sorted {sorted_now} of your experiences.")
    if counts["unjudged"]:
        ctx.changed(f"{counts['unjudged']} experiences wait until Hal's AI can look at them.")
    events.publish("profile", tiers=True)
    return counts


@runner.action(KIND, area="documents", label="Sorting your experiences", why="You pressed Sort them again",
               priority=0, exclusive="kind", example=lambda world: {})
def sort_tiers(ctx) -> dict:
    """Puts each job, course, project, achievement and certificate in a tier; says how many."""
    ctx.step("Sorting your experiences")
    return sort(ctx)
