"""How hard the AI thinks before each answer: one effort level for every prompt, and the thinking budget sent.

Every request to a model that thinks carries `reasoning_effort` (`none`, `low`, `medium`, `high`) and
`thinking_budget_tokens`, the most thinking tokens it may spend before it answers. The level comes from what
the prompt does (`LEVELS`, one table with a reason for each), the budget from the level (`BUDGETS`), and the
budget is never more than the person's hard cap, `ai.thinking_cap_tokens` (`budget`). Only Hal's gateway is
sent either field (`hal_gateway.GatewayBackend`); other backends ignore them.

Design decisions, each with its reason:

- The budgets come from the time one application's documents may take (owner, 2026-11-19: five minutes at
  most, then about two once each document is written in one call), on the speeds measured that day (about 55
  tokens a second written, 500 read): calls x (prompt tokens / 500 + (thinking + answer tokens) / 55).
  Before, about 90% of each call was thinking (a median 1,400 to 1,600 tokens for a 130-token answer). The
  seven calls of `docs.prepare` (analyse, brief, fit, letter, message, answers, verify) think at most 4,608
  tokens in all; the budget, with a redraft of each document, is in docs/dev/architecture.md ("Thinking
  effort") and `tests/test_ai_effort.py`. The letter thinks at `medium`, as it now chooses and weaves the
  evidence itself; the message, the answers and the check at `low`.
- Reads of the record keep thinking on at the level they always had (`high`, today's 8,192-token bound): with
  thinking off a read kept 55 facts against 77 (owner's test, 2026-11-12), so they are never lowered.
- The check that guards against invented facts (`verify`) gets `low`, not `none`: it weighs
  each claim against the facts, and a few hundred tokens of thinking is cheap beside a wrong letter.
- `none` is only for jobs with nothing to reason about (a short note, a name, a report turned into a note):
  on Hal's Qwen 3.8 model, thinking off answered a small reasoning question wrongly twice where `low` answered
  it in 22 to 37 tokens (gateway owner's test, 2026-11-19), so a pick or a sort that must judge gets `low`.
- Hal's gateway takes the smaller of the budget sent and its own for the level (low 1,024, medium 4,096, high
  16,384 in its 2026-11-19 build), so the budgets here are what bind.
- A prompt missing from the table is sent no level and the cap as its budget, so a new prompt is never
  slowed or starved by accident; a test names every prompt file that has no row.
"""
from __future__ import annotations

from dataclasses import dataclass

NONE, LOW, MEDIUM, HIGH = "none", "low", "medium", "high"
EFFORTS = (NONE, LOW, MEDIUM, HIGH)
# Thinking tokens each level may spend. `high` is the bound every request had until 2.3.2, so a read thinks as
# it did; the others are sized for the time an application's documents may take (module notes).
BUDGETS = {NONE: 0, LOW: 512, MEDIUM: 1280, HIGH: 8192}
CAP_KEY = "ai.thinking_cap_tokens"
CAP_DEFAULT = BUDGETS[HIGH]     # no prompt is cut below its own level until the person lowers the cap
CAP_MAX = 32768                 # past this a model's context, not thinking, is what runs out
READ_WHY = "a record read: kept at the level it had, as thinking off lost facts (2026-11-12)"


@dataclass(frozen=True)
class Effort:
    """One prompt's level and the reason for it."""

    level: str
    why: str

    def __post_init__(self) -> None:
        if self.level not in EFFORTS:
            raise ValueError(f"unknown effort {self.level!r}; one of {EFFORTS}")


# Every prompt in `ai/prompts/`, plus the settings test, by the id each call passes as its `purpose`.
LEVELS: dict[str, Effort] = {
    # an application's documents (`docs.prepare`), in the order they are asked
    "analyse": Effort(LOW, "lifts lists out of the advert; little to weigh"),
    "brief": Effort(NONE, "a short note on the employer from the advert and web pages"),
    "fit": Effort(MEDIUM, "weighs each requirement against the evidence"),
    "letter": Effort(MEDIUM, "writes the whole letter in one go: picks and weaves the evidence into an argument"),
    "message": Effort(LOW, "writes a short note to the person hiring"),
    "answers": Effort(LOW, "writes short answers to an advert's questions"),
    "verify": Effort(LOW, "a guard: checks every draft's claims against the facts, once"),
    "prep": Effort(LOW, "writes notes for an interview or a test"),
    # the person's CVs
    "cv_parse": Effort(HIGH, "reads an uploaded CV into sections; a read, kept as it was"),
    "cv_extract": Effort(HIGH, READ_WHY),
    "cv_compose": Effort(MEDIUM, "plans a whole CV from the person's facts"),
    "cv_needs": Effort(LOW, "lists what an advert asks for"),
    "cv_evidence": Effort(MEDIUM, "weighs where the CV shows each requirement"),
    "cv_suggest": Effort(LOW, "words one change to one section"),
    "cv_another": Effort(LOW, "words that change another way"),
    "cv_judge": Effort(MEDIUM, "a recruiter's read weighs the whole CV"),
    "letter_judge": Effort(MEDIUM, "a recruiter's read weighs the whole letter"),
    # reading the record
    "ingest_doc": Effort(HIGH, READ_WHY),
    "record_cv": Effort(HIGH, READ_WHY),
    "project_read": Effort(HIGH, READ_WHY),
    "coursework_read": Effort(HIGH, READ_WHY),
    "lecture_topics": Effort(HIGH, READ_WHY),
    "lecture_note": Effort(HIGH, READ_WHY),
    "profile_same": Effort(HIGH, "merges cards after a read; part of the read, kept as it was"),
    "profile_tier": Effort(HIGH, "sorts cards after a read; part of the read, kept as it was"),
    "sort_pick": Effort(HIGH, "places a file in the record; part of the read, kept as it was"),
    "record_ask": Effort(MEDIUM, "answers the person's question from their record"),
    # finding jobs, forms and email
    "screen": Effort(LOW, "a yes or no on one advert, with a reason"),
    "form_fill": Effort(LOW, "writes one form answer from the person's settings"),
    "form_review": Effort(LOW, "a guard: clears form answers that miss the question"),
    "choose_control": Effort(LOW, "picks the one button that opens the form; must reason"),
    "adopt_name": Effort(NONE, "names a job from its page"),
    "contacts_pick": Effort(LOW, "judges which people fit the job; must reason"),
    "email_refine": Effort(LOW, "sorts an email its rules could not; must reason"),
    "site_note": Effort(NONE, "turns the person's report into a note"),
    "ai_test": Effort(NONE, "the settings page's check that the AI answers"),
}


def of(purpose: str) -> Effort | None:
    """The effort for a prompt id, or None for one the table does not name."""
    return LEVELS.get(purpose)


def budget(purpose: str, cap: int) -> tuple[str | None, int]:
    """(level, thinking budget) for a request: the prompt's level and its budget, never above `cap`; a prompt
    the table does not name gets no level and the cap. A budget of 0 is always level `none`, so a model that
    switches thinking off by level does so; a negative cap counts as 0."""
    cap = max(0, int(cap))
    found = of(purpose)
    if found is None:
        return (NONE if cap == 0 else None), cap
    spent = min(BUDGETS[found.level], cap)
    return (NONE if spent == 0 else found.level), spent
