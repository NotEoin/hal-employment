"""A small YAML subset for note frontmatter: plain and quoted scalars and lists, written and read back.

Obsidian reads `tags` and other list fields only as YAML lists, so notes Hal writes use block lists
(`tags:` then `  - one` lines). Reading also takes flow lists (`tags: [a, b]`) as people type them. Scalars
are read back as strings, never as numbers or booleans, so a value round-trips as it was written and the
record's own readers can treat every scalar as text. Nothing else of YAML (maps, anchors, multi-line
strings) is supported; a line that is none of these is ignored, as the record's reader always did.

This module imports nothing from Hal, so `record` can use it without an import cycle.
"""
from __future__ import annotations

import re

FENCE = "---"
KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_-]*):(?:\s+(.*)|\s*)$")
ITEM = re.compile(r"^\s+-\s+(.*)$|^-\s+(.*)$")
# A plain scalar that YAML would read as something other than this text, or that would not parse at all.
NEEDS_QUOTES = re.compile(r"""^[\s\[\]{}>|*&!%@#`'",?:-]|:\s|\s#|\s$|^(true|false|yes|no|null|~|[-+.\d][\d.e+-]*)$""",
                          re.I)


def _scalar(value) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = " ".join(str(value).split())
    if not text or NEEDS_QUOTES.search(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def dump(fields: dict) -> str:
    """`fields` as a frontmatter block with its fences; a list or tuple becomes a block list."""
    lines = [FENCE]
    for key, value in fields.items():
        if not KEY.match(f"{key}:"):
            raise ValueError(f"{key!r} cannot be a frontmatter key")
        if isinstance(value, list | tuple):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines += [f"  - {_scalar(v)}" for v in value]
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append(FENCE)
    return "\n".join(lines) + "\n"


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return re.sub(r"\\(.)", r"\1", text[1:-1])
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _flow(text: str) -> list[str]:
    """The items of `[a, "b, c", d]`."""
    items = re.findall(r'\s*("(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\'|[^,]+)', text.strip()[1:-1])
    return [v for v in (_unquote(i) for i in items) if v]


def parse(text: str) -> tuple[dict, str]:
    """The frontmatter of a note as {key: str or list of str}, and the body after it.

    A note with no frontmatter, or with no closing fence, is all body.
    """
    if not text.startswith(FENCE):
        return {}, text
    end = text.find("\n" + FENCE, len(FENCE))
    if end == -1:
        return {}, text
    fields: dict = {}
    key = None
    for line in text[len(FENCE):end].splitlines():
        item = ITEM.match(line)
        if item and key is not None:
            if not isinstance(fields[key], list):
                fields[key] = []
            fields[key].append(_unquote(item.group(1) or item.group(2) or ""))
            continue
        m = KEY.match(line)
        if not m:
            continue
        key, raw = m.group(1), (m.group(2) or "").strip()
        if raw.startswith("[") and raw.endswith("]"):
            fields[key] = _flow(raw)
        else:
            fields[key] = _unquote(raw)
    body = text[end + 1 + len(FENCE):]
    return fields, body[1:] if body.startswith("\n") else body
