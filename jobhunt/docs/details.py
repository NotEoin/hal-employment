"""Copy-paste details (Documents): the profile facts a person types into forms Hal cannot fill.

Every row comes from a `you.*` setting. Private answers (`you.private`, the equality and adjustment questions)
are never read here: this module does not import the private store, and a test holds it to that.
"""
from __future__ import annotations

from ..core import settings

# (label, setting key) in the order forms usually ask for them; empty values are left out.
ROWS = (
    ("Full name", "you.name"), ("Email", "you.email"), ("Phone", "you.phone"), ("Town", "you.city"),
    ("LinkedIn", "you.links.linkedin"), ("Website", "you.links.website"), ("GitHub", "you.links.github"),
)
LATER_ROWS = (("Dissertation", "you.education.dissertation"), ("Right to work", "you.right_to_work"),
              ("Notice", "you.notice"))


def _value(key: str) -> str:
    value = settings.get(key)
    if value in (None, "", [], False):
        return ""
    if key == "you.right_to_work":
        return settings.display(key, value)
    return str(value).strip()


def degree_line() -> str:
    """The degree as one line: "BA (Hons) History, 2:1, Pennine University, 2023"."""
    parts = [_value("you.education.degree"), _value("you.education.subject")]
    head = " ".join(p for p in parts if p)
    rest = [_value("you.education.grade"), _value("you.education.university"),
            _value("you.education.start_year") and "from " + _value("you.education.start_year")]
    return ", ".join(p for p in [head, *rest] if p)


def rows() -> list[dict]:
    """The rows for "Copy-paste details": label and value, only the ones that are filled in."""
    out = [{"label": label, "value": _value(key)} for label, key in ROWS]
    out.append({"label": "Degree", "value": degree_line()})
    out += [{"label": label, "value": _value(key)} for label, key in LATER_ROWS]
    return [r for r in out if r["value"]]
