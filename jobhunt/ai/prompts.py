"""Hal's instructions to the AI, one file per prompt, with the person's edits on top.

`jobhunt/ai/prompts/<id>.txt` starts with header lines `# name:`, `# used:` and `# needs: a, b`, then the
text. The package that owns a prompt's module creates its file, so no shared list is edited. `text(id,
**vars)` returns the person's edit from `prompt_overrides` when there is one, else the file, with
`{placeholders}` filled. Only `{word}` placeholders are replaced, so JSON examples in a prompt keep their
braces. A missing required placeholder raises; an edit that drops one is refused in plain words.
Private answers are never a placeholder.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / "prompts"
HEADER = re.compile(r"#\s*(name|used|needs)\s*:\s*(.*)")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


class PromptError(ValueError):
    """A prompt is missing, malformed, or was given too little to fill it in."""


@dataclass(frozen=True)
class Prompt:
    """One prompt file."""

    id: str
    name: str
    used: str
    needs: tuple[str, ...]
    text: str

    @property
    def sha(self) -> str:
        """The SHA-256 of the default text, to see when an update changed a prompt someone edited."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def parse(prompt_id: str, raw: str) -> Prompt:
    """A prompt from its file's text."""
    head: dict[str, str] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines) and (m := HEADER.fullmatch(lines[i].strip())):
        head[m.group(1)] = m.group(2).strip()
        i += 1
    if "name" not in head:
        raise PromptError(f"prompt {prompt_id} has no '# name:' line")
    needs = tuple(n.strip() for n in head.get("needs", "").split(",") if n.strip())
    return Prompt(prompt_id, head["name"], head.get("used", ""), needs, "\n".join(lines[i:]).strip("\n") + "\n")


def all_prompts(folder: Path | None = None) -> dict[str, Prompt]:
    """Every prompt file, by id."""
    folder = folder or FOLDER
    if not folder.is_dir():
        return {}
    return {f.stem: parse(f.stem, f.read_text(encoding="utf-8")) for f in sorted(folder.glob("*.txt"))}


def get(prompt_id: str, folder: Path | None = None) -> Prompt:
    """One prompt by id; PromptError when there is no such file."""
    file = (folder or FOLDER) / f"{prompt_id}.txt"
    if not file.is_file():
        raise PromptError(f"there is no prompt {prompt_id!r}")
    return parse(prompt_id, file.read_text(encoding="utf-8"))


def override(prompt_id: str, con: sqlite3.Connection | None = None) -> str | None:
    """The person's edit of a prompt, or None."""
    from .. import db

    own = con is None
    con = con or db.connect()
    try:
        row = con.execute("SELECT text FROM prompt_overrides WHERE id = ?", (prompt_id,)).fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return None   # before migration 0003 nobody has edited a prompt
        raise
    finally:
        if own:
            con.close()
    return row[0] if row else None


def check_edit(prompt: Prompt, text: str) -> None:
    """Refuses an edit that drops a placeholder the prompt needs."""
    present = set(PLACEHOLDER.findall(text))
    for name in prompt.needs:
        if name not in present:
            raise PromptError(f"This instruction needs {{{name}}}; Hal fills it in from your settings.")


def fill(template: str, needs: tuple[str, ...], values: dict[str, object]) -> str:
    """`template` with each `{name}` in `values` replaced; a needed name without a value raises."""
    missing = [n for n in needs if n not in values]
    if missing:
        raise PromptError(f"no value for {', '.join('{' + n + '}' for n in missing)}")
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def text(prompt_id: str, *, con: sqlite3.Connection | None = None, folder: Path | None = None, **values: object) -> str:
    """The prompt's text, the person's edit first, with its placeholders filled in."""
    prompt = get(prompt_id, folder)
    return fill(override(prompt_id, con) or prompt.text, prompt.needs, values)
