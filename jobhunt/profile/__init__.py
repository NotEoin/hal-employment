"""The profile: the facts about the person that Hal finds in their record, checked and corrected by them.

`store` keeps the facts and their sources, `merge` decides when two facts are one, `conflicts` finds what
does not add up, `notes` writes the Profile notes into the record folder and `writing` gives document
prompts a Profile block. `write_notes` and `context_block` are the two entry points other areas call.
"""
from jobhunt.profile.notes import write_notes
from jobhunt.profile.writing import context_block

__all__ = ["context_block", "write_notes"]
