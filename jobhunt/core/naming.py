"""File and folder names that are safe on every system and every application site.

Some application sites mangle or refuse an upload whose name has accents, and Windows refuses
`<>:"/\\|?*` and trailing dots, so names are folded to plain ASCII here and nowhere else. The person's name
in a file name comes from the `cvdocs.file_name` template, never from code.
"""
from __future__ import annotations

import re
import unicodedata

UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
STEM_UNSAFE = re.compile(r"[^A-Za-z0-9' -]")
MAX_FOLDER = 80                    # also keeps Windows paths well under 260 characters
FALLBACK_STEM = "Applicant"
TEMPLATE_KEYS = {"cv": "cvdocs.file_name", "letter": "cvdocs.letter_file_name"}


def ascii_fold(text: str) -> str:
    """`text` with accents dropped and other non-ASCII letters removed: "Sean O Briain" from its Irish form."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def safe_part(text: str, limit: int | None = None) -> str:
    """One path part: ASCII, no characters Windows refuses, no trailing dots or spaces."""
    clean = " ".join(UNSAFE.sub(" ", ascii_fold(text)).split())
    if limit is not None:
        clean = clean[:limit]
    return clean.rstrip(". ")


def file_stem(name: str) -> str:
    """A person's name as it begins a file name, in plain letters joined by hyphens."""
    return "-".join(STEM_UNSAFE.sub("", ascii_fold(name)).split()) or FALLBACK_STEM


def file_name(kind: str, *, name: str | None = None, template: str | None = None) -> str:
    """The file name employers see for a `kind` ("cv" or "letter") document, from its template setting.

    `name` defaults to the `you.name` setting and `template` to the kind's setting; both are passed in
    tests and by callers that already hold them. The result is a safe path part (see `safe_part`).
    """
    if kind not in TEMPLATE_KEYS:
        raise ValueError(f"unknown document kind {kind!r}; one of {', '.join(TEMPLATE_KEYS)}")
    if name is None or template is None:
        from . import settings

        name = settings.get("you.name") if name is None else name
        template = settings.get(TEMPLATE_KEYS[kind]) if template is None else template
    words = ascii_fold(name or "").strip() or FALLBACK_STEM
    try:
        text = template.format(name=words)
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"The file name {template!r} can only use {{name}}.") from e
    return safe_part(text)


def folder_name(number: int, company: str, title: str) -> str:
    """An application folder's name: `0042 Company - Title`, at most `MAX_FOLDER` characters."""
    return safe_part(f"{number:04d} {company} - {title}", MAX_FOLDER)
