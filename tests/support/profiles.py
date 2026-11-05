"""A made-up profile for tests: the humanities graduate of `people.py` as an ingest of her files leaves it.

`humanities_profile(con)` adds her degree (confirmed, with modules and marks from a transcript), a part-time
museum job, research skills, her name and email, and a hidden paper round, and returns the item ids by name.
Every file, employer and module is invented.
"""
from __future__ import annotations

from jobhunt.profile import store

from .people import HUMANITIES

CV = "Sources/CVs and letters/Marnie Quillfeather CV.md"
TRANSCRIPT = "Sources/Education/Nowhere Fen transcript.md"
NOTES = "Sources/Study notes/Archive week.md"
LETTER = "Sources/CVs and letters/Museum letter.md"


def ev(source: str, doc_type: str, quote: str = "") -> dict:
    """Evidence as the ingest gives it."""
    return {"source_rel": source, "quote": quote, "field": "", "doc_type": doc_type}


def humanities_profile(con) -> dict:
    """Marnie's profile; the degree is confirmed and the paper round hidden."""
    you = HUMANITIES.you
    ids = {
        "degree": store.add_found(con, "education", {
            "qualification": you["education.degree"], "subject": you["education.subject"],
            "institution": you["education.university"], "period": "2022-25", "grade": you["education.grade"],
            "modules": [{"name": "Early Modern Letters", "mark": "72"},
                        {"name": "Oral History in Practice", "mark": "68"}]},
            ev(TRANSCRIPT, "transcript", "Early Modern Letters 72")),
        "job": store.add_found(con, "job", {
            "title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust",
            "period": "Sept 2023 - present", "hours": "part-time",
            "duties": ["Catalogued a donated collection of letters"]}, ev(CV, "cv", "Collections volunteer")),
        "archives": store.add_found(con, "skill", {"name": "Archival research", "category": "research"},
                                    ev(NOTES, "notes", "read the parish records in the county archive")),
        "oral": store.add_found(con, "skill", {"name": "Oral history interviewing"},
                                ev(CV, "cv", "recorded six oral history interviews")),
        "email": store.add_found(con, "basic", {"key": "email", "value": you["email"]}, ev(CV, "cv")),
        "name": store.add_found(con, "basic", {"key": "name", "value": you["name"]}, ev(CV, "cv")),
        "old": store.add_found(con, "job", {"title": "Paper round", "employer": "Harrowgate Vale News",
                                            "period": "2015 - 2017"}, ev(NOTES, "notes")),
    }
    store.confirm(con, ids["degree"])
    store.hide(con, ids["old"])
    return ids
