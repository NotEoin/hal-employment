"""Private answers: disability, adjustments, guaranteed interview schemes and caring.

They live in the data folder's `private.toml`, table `[applicant]`, the shape the old code used, so the
importer's byte-for-byte copy works unchanged. Hal only ever shows whether each one is saved; the values
reach exactly two places: `autofill`'s fixed rules that type them into a form (`value_for_form`) and the
AI guard that refuses any prompt containing one (`values_for_guard`). An AST test keeps it that way.

Design notes:
- A missing answer means "Prefer not to say": the question is left for the person to answer on the form.
- The file is rewritten whole, created with mode 0600 and swapped in atomically, so a crash never leaves
  half a file. Keys this module does not know are kept as they were.
- Values are never logged, never in Activity, events, exports or help files. A change records only that
  answers were saved, with no words of the answer.
"""
from __future__ import annotations

import math
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .. import paths

YES_NO = ("Yes", "No")
YES_NO_PNTS = ("Yes", "No", "Prefer not to say")
TABLE = "applicant"
SETTING = "you.private"     # the Settings row these answers belong to
FILE_MODE = 0o600
MAX_ANSWER_CHARS = 2000     # far longer than any form's box for these questions
GUARD_MIN_CHARS = 8         # shorter free text is too common to search prompts for
HELP = ("Used only to answer these questions on forms. Never sent to the AI, never in logs or exports. "
        "Hal shows only whether they are saved.")

QUESTIONS: tuple[tuple[str, str, tuple[str, ...] | None], ...] = (
    ("disability", "Do you consider yourself to have a disability or long-term condition?", YES_NO_PNTS),
    ("disability_type", "If you would like to say, which?", None),
    ("guaranteed_interview", "Apply under a guaranteed interview scheme when one is offered?", YES_NO),
    ("adjustments", "Adjustments you need for interviews or tests", None),
    ("carer", "Do you look after someone?", YES_NO_PNTS),
)
KEYS = tuple(q[0] for q in QUESTIONS)
OPTIONS = {key: options for key, _, options in QUESTIONS}


class PrivateAnswerError(ValueError):
    """An answer that cannot be saved; str() is the words for the person, with no part of the answer."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(message)
        self.key = key


def path() -> Path:
    """The private answers file in the data folder."""
    return paths.private_path()


def _read_all() -> dict[str, Any]:
    file = path()
    if not file.is_file():
        return {}
    try:
        return tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        # the line number only: the message of a TOML error can quote the answer
        raise RuntimeError(f"{file} cannot be read near line {getattr(e, 'lineno', '?')}; "
                           "fix it or save your answers again") from None


def _answers() -> dict[str, str]:
    table = _read_all().get(TABLE, {})
    return {k: v for k, v in table.items() if isinstance(v, str) and v.strip()}


def read_table() -> dict[str, Any]:
    """The whole `[applicant]` table, for `config.private()`'s old callers."""
    return dict(_read_all().get(TABLE, {}))


def status() -> dict[str, bool]:
    """Saved or not, per question: the only thing any API returns."""
    saved = _answers()
    return {key: key in saved for key in KEYS}


def questions() -> list[dict]:
    """The questions with their options and whether each is saved, never the answers."""
    saved = status()
    return [{"key": key, "label": label, "help": HELP, "options": list(options) if options else None,
             "saved": saved[key]} for key, label, options in QUESTIONS]


def _clean(key: str, value: Any) -> str:
    if key not in OPTIONS:
        raise PrivateAnswerError(key, "Hal does not keep that answer.")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PrivateAnswerError(key, "Type the answer as words.")
    value = value.strip()
    if len(value) > MAX_ANSWER_CHARS:
        raise PrivateAnswerError(key, "That answer is too long for a form.")
    options = OPTIONS[key]
    if value and options:
        match = next((o for o in options if o.lower() == value.lower()), None)
        if match is None:
            raise PrivateAnswerError(key, "Choose one of the options.")
        value = match
    return value


def _toml_string(value: str) -> str:
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or code == 0x7F or code > 0x7E:
            # escaped, so the file stays ASCII whatever was typed
            out.append(f"\\u{code:04X}" if code <= 0xFFFF else f"\\U{code:08X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("private.toml cannot hold an infinite number")
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise ValueError(f"private.toml cannot hold a {type(value).__name__}")


def _key(name: str) -> str:
    plain = name and all(c.isascii() and (c.isalnum() or c in "-_") for c in name)
    return name if plain else _toml_string(name)


def _dump(data: dict[str, Any], prefix: str = "") -> str:
    """TOML for `data`: plain values first, then each table under its own header."""
    lines = [f"{_key(k)} = {_toml_value(v)}" for k, v in data.items() if not isinstance(v, dict)]
    for name, table in data.items():
        if isinstance(table, dict):
            header = f"{prefix}{_key(name)}"
            lines.append("")
            lines.append(f"[{header}]")
            lines.append(_dump(table, header + ".").strip("\n"))
    return "\n".join(lines).strip("\n") + "\n"


def _write_all(data: dict[str, Any]) -> None:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    text = _dump(data)
    fd, tmp = tempfile.mkstemp(prefix=".private-", dir=file.parent)
    try:
        os.chmod(tmp, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, file)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    os.chmod(file, FILE_MODE)


def save(answers: dict[str, Any]) -> dict[str, bool]:
    """Merges answers into the file (an empty answer clears it); returns `status()`. All or nothing."""
    cleaned = {key: _clean(key, value) for key, value in answers.items()}
    data = _read_all()
    table = dict(data.get(TABLE, {}))
    for key, value in cleaned.items():
        if value:
            table[key] = value
        else:
            table.pop(key, None)
    data[TABLE] = table
    _write_all(data)
    _record("Saved your private answers")
    return status()


def clear(key: str) -> None:
    """Forgets one answer."""
    if key not in OPTIONS:
        raise PrivateAnswerError(key, "Hal does not keep that answer.")
    data = _read_all()
    table = dict(data.get(TABLE, {}))
    if key in table:
        del table[key]
        data[TABLE] = table
        _write_all(data)
    _record("Cleared one of your private answers")


def _record(text: str) -> None:
    """History and an Activity row with no words of any answer."""
    from . import store

    store.record_saved(SETTING, text=text, why="You changed your private answers in Settings.")


def value_for_form(key: str) -> str | None:
    """The saved answer for a form's private question; only autofill's `_health` and `_carer` call this."""
    if key not in OPTIONS:
        raise KeyError(f"no private question is called {key!r}")
    return _answers().get(key)


def values_for_guard() -> list[str]:
    """The free-text answers long enough to search prompts for; only `ai/guard.py` calls this."""
    saved = _answers()
    out = []
    for key, _, options in QUESTIONS:
        value = saved.get(key)
        if options is None and value and len(value) >= GUARD_MIN_CHARS:
            out.append(value)
    return out
