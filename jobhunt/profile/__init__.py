"""The profile: the facts about the person that Hal finds in their record, checked and corrected by them.

`store` keeps the facts and their sources, `merge` decides when two facts are one, `conflicts` finds what
does not add up and `writing` gives document prompts a Profile block. `context_block` is the entry point
other areas call.
"""
from jobhunt.profile.writing import context_block

__all__ = ["context_block"]
