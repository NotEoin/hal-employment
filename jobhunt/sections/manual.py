"""Projects added by hand (`manual`): what the person typed, and the files and pictures they attached.

Exhibitions, research, events, campaigns, writing, hardware: anything with no repository or web page. The
person types a name, what it was, when, their part, the tools or methods used and links (`clean_form`, kept in
the source's `meta.form`), and can attach files (`pipeline.attach`). `ManualReader` turns the form into
project facts and reads the attachments' text with the document reader's rules (`readers.text_of`).

Design decisions, each with its reason:

- The facts carry the person's own authority: the kind of document's type is `by_you`
  (`registry.DOC_KINDS["manual"]`), which `profile.merge.RANKS` puts above every document, as the person's
  word on their own project is what they typed (sections plan, 2026-11-09).
- Each fact quotes the line of the note that holds what the person typed, so every fact is traceable like
  any other, and a field left empty gives no fact.
- Attachments only add words to the note (for search and the writer's quotes); facts never come from them,
  as the person already said what the project was.
"""
from __future__ import annotations

import re

from jobhunt.sections import readers

NAME_MAX = 200             # a project's name is a line
TEXT_MAX = 4000            # "what it was" is a paragraph or two
LINKS_MAX = 10
TOOLS_MAX = 40
FIELDS = ("name", "what", "when", "part", "tools", "links")
LABELS = {"what": "What it was", "when": "When", "part": "Your part", "tools": "Tools or methods",
          "links": "Links"}


def _line(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _items(value, limit: int) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,;\n]", str(value or ""))
    return list(dict.fromkeys(" ".join(str(v).split()) for v in raw if str(v).strip()))[:limit]


def clean_form(form) -> dict:
    """The hand-made project's form checked and tidied: ValueError for one with no name, an unknown field, or a
    link that is not a web address."""
    if not isinstance(form, dict):
        raise ValueError("Fill in the project's form.")
    unknown = sorted(set(form) - set(FIELDS))
    if unknown:
        raise ValueError(f"A project added by hand has no {', '.join(unknown)}.")
    out = {"name": _line(form.get("name"), NAME_MAX), "what": " ".join(str(form.get("what") or "").split())[:TEXT_MAX],
           "when": _line(form.get("when"), NAME_MAX), "part": _line(form.get("part"), NAME_MAX),
           "tools": _items(form.get("tools"), TOOLS_MAX),
           "links": _items(str(form.get("links") or "").replace(",", "\n") if not isinstance(form.get("links"), list)
                           else form.get("links"), LINKS_MAX)}
    if not out["name"]:
        raise ValueError("Give the project a name.")
    bad = [u for u in out["links"] if not u.lower().startswith(("http://", "https://"))]
    if bad:
        raise ValueError(f"{bad[0]} is not a web address. Links start with https:// or http://.")
    return {k: v for k, v in out.items() if v}


class ManualReader:
    """The `manual` reader: a project the person typed in, with what they attached."""

    name = "manual"

    def read(self, source: readers.Source) -> readers.Reading:
        """The form as project facts quoted from the note, and the attachments' text below it."""
        form = (source.meta or {}).get("form") or {"name": source.locator}
        name = form.get("name") or source.locator or "Untitled project"
        lines = [f"# {name}", ""]
        for key in ("what", "when", "part"):
            if form.get(key):
                lines.append(f"- {LABELS[key]}: {form[key]}")
        for key in ("tools", "links"):
            if form.get(key):
                lines.append(f"- {LABELS[key]}: {', '.join(form[key])}")
        attached = readers.text_of(source.files) if source.files else ""
        if source.files:
            lines += ["", "## Attached", ""] + [f"- {f.rel}" for f in source.files]
        if attached:
            lines += ["", attached]
        text = "\n".join(lines).strip() + "\n"
        place = "typed by you"

        def fact(fields: dict, field_name: str, quote: str) -> readers.Fact:
            return readers.Fact("project", {"name": name, **fields}, readers.Quote(field_name, quote, place))

        facts = [fact({}, "name", f"# {name}")]
        mapping = (("what", "summary"), ("when", "period"), ("part", "role"))
        facts += [fact({field_name: form[key]}, field_name, f"- {LABELS[key]}: {form[key]}")
                  for key, field_name in mapping if form.get(key)]
        if form.get("tools"):
            quote = f"- {LABELS['tools']}: {', '.join(form['tools'])}"
            facts.append(fact({"tools": form["tools"]}, "tools", quote))
            facts += [readers.Fact("skill", {"name": t}, readers.Quote("name", quote, place)) for t in form["tools"]]
        if form.get("links"):
            facts.append(fact({"link": form["links"][0]}, "link", f"- {LABELS['links']}: {', '.join(form['links'])}"))
        return readers.Reading(title=name, date=readers.date_in(form.get("when", "")), text=text,
                               summary=form.get("what", ""), facts=facts)


readers.register(ManualReader())
