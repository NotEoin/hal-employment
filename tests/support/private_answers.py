"""Made-up private answers saved in the real store's file for the length of a block.

`saved(answers)` writes `answers` as the `[applicant]` table of the private answers file in the current
temporary home, so `value_for_form` and `values_for_guard` are the real code reading a real file. The
file's earlier bytes, or its absence, are put back afterwards. It writes the file directly rather than
through `private.save`, because `save` also adds a history row and an Activity row that tests counting
Activity rows would see. Only made-up answers are ever passed here.
"""
from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

from jobhunt.core.settings import private


@contextlib.contextmanager
def saved(answers: dict[str, str] | None = None) -> Iterator[dict[str, str]]:
    """The private answers are `answers` (none by default) while the block runs."""
    answers = dict(answers or {})
    file = private.path()
    before = file.read_bytes() if file.is_file() else None
    file.parent.mkdir(parents=True, exist_ok=True)
    # a JSON string of ASCII text is also a valid TOML basic string
    lines = [f"[{private.TABLE}]"] + [f"{key} = {json.dumps(value)}" for key, value in answers.items()]
    file.write_text("\n".join(lines) + "\n", encoding="ascii")
    file.chmod(private.FILE_MODE)
    try:
        yield answers
    finally:
        if before is None:
            file.unlink(missing_ok=True)
        else:
            file.write_bytes(before)
