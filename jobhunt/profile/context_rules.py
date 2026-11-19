"""The contexts a card can have and the context a shaped item shows (`shown`); `profile.contexts` explains them.

Kept apart from `profile.contexts`, which reads and writes the store, because the store shapes every item with
`shown`: this module imports nothing that imports the store, so the imports run one way.
"""
from __future__ import annotations

from jobhunt.writing import stage

BY_HAL, BY_PERSON = "hal", "person"
CONTEXT_KINDS = ("job", "education", "project", "achievement", "certificate")   # as `tiers.TIERED_KINDS`
# The person's choice on a card, in the words the Check your record page shows; the keys are
# `writing.stage.CONTEXTS`, written out because `stage` is still loading when the profile package is.
CHOICE_WORDS = {"degree": "Part of my degree", "team": "University team project", "work": "At work",
                "personal": "In my own time", "school": "At school"}
BY_KIND = {"job": "work", "education": "degree"}   # unless the person says otherwise


def shown(item: dict, stored) -> dict:
    """The context a shaped `item` carries: {context, context_by, context_words}; `stored` holds its columns.

    The person's choice wins, then Hal's stored reading, then the kind's own (a job is work); a kind with no
    context has none.
    """
    if item["kind"] not in CONTEXT_KINDS:
        return {"context": "", "context_by": "", "context_words": ""}
    context, by = stored["context"], stored["context_by"]
    if context not in stage.CONTEXTS or by not in (BY_HAL, BY_PERSON):
        context = BY_KIND.get(item["kind"], "")
        by = BY_HAL if context else ""
    return {"context": context, "context_by": by, "context_words": CHOICE_WORDS.get(context, "")}
