"""What one action's AI calls cost, read back from `ai_usage`: calls, tokens and seconds for each prompt.

`jobhunt docs-time` prints it for an application's last document write, so the five-minute target for one
application's documents (2.3.2) can be checked on real data rather than guessed.

Design decisions, each with its reason:

- Seconds are the calls' own time, from asking to the answer, so they include any wait at the model behind
  other work. Model time is also estimated from the tokens at `READ_TOKENS_S` and `WRITE_TOKENS_S`, which is the
  measure that stays the same when the card is shared (the speeds of Hal's local model on the owner's card,
  measured from the gateway's request log, 2026-11-19; another card gives other numbers, so it is a guide).
- Calls with no tokens (the Claude CLI reports none) count in calls and seconds, and add nothing to the
  estimate; `tokens` says so with `None`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

READ_TOKENS_S = 500      # prompt tokens a second read
WRITE_TOKENS_S = 55      # tokens a second written, thinking and answer alike
MS_PER_S = 1000
DOCUMENTS_KIND = "docs.prepare"


@dataclass(frozen=True)
class PromptCost:
    """One prompt's calls within an action."""

    purpose: str
    effort: str | None
    calls: int
    failed: int
    seconds: float
    tokens_in: int | None
    tokens_out: int | None
    thinking: int | None

    @property
    def model_seconds(self) -> float:
        """The model time the tokens take at the measured speeds (module notes); 0 with no tokens."""
        return (self.tokens_in or 0) / READ_TOKENS_S + (self.tokens_out or 0) / WRITE_TOKENS_S


def _sum(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def by_prompt(con: sqlite3.Connection, action_id: str) -> list[PromptCost]:
    """The action's AI calls grouped by prompt, in the order each prompt was first asked."""
    rows = con.execute("""SELECT purpose, effort, ok, ms, tokens_in, tokens_out, tokens_thinking FROM ai_usage
                          WHERE action_id = ? ORDER BY id""", (action_id,)).fetchall()
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)
    return [PromptCost(purpose=purpose, effort=next((r[1] for r in found if r[1]), None), calls=len(found),
                       failed=sum(1 for r in found if not r[2]), seconds=sum(r[3] or 0 for r in found) / MS_PER_S,
                       tokens_in=_sum([r[4] for r in found]), tokens_out=_sum([r[5] for r in found]),
                       thinking=_sum([r[6] for r in found]))
            for purpose, found in grouped.items()]


def last_documents(con: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    """The job's last finished document write (`docs.prepare`, done or not), or None."""
    return con.execute("""SELECT id, state, started, ended FROM actions WHERE job_id = ? AND kind = ?
                          AND ended IS NOT NULL ORDER BY started DESC LIMIT 1""", (job_id, DOCUMENTS_KIND)).fetchone()
