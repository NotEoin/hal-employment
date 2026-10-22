"""The allowance: who may use the AI now, in which order, and the record of every call.

Priorities: 0 "things you just asked for", 1 "documents for jobs you chose", 2 "background checks". The
rules, in the order `admit` applies them:

- `ai.local_only` refuses any backend that sends text off this computer, at every priority.
- After Claude says a limit is reached, background work waits until the reset time; priorities 0 and 1
  still try, because the person is waiting and the limit may already have lifted.
- Background work is refused during the `ai.keep_free` hours and once today's successful background
  calls reach `ai.daily_cap`.

The cap and the keep-free hours apply to the Claude subscription only (`CAPPED`): a local model costs the
person nothing but electricity. Hal cannot read the subscription's meter, so it counts its own calls in
`ai_usage` (sizes only, never prompt text). Times there are UTC; "today" and the hours are UK time.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta, tzinfo

from ..core import clock
from .base import ChatResult, Limited, LLMUnavailable

log = logging.getLogger(__name__)

YOU_ASKED, YOU_CHOSE, BACKGROUND = 0, 1, 2
PRIORITIES = (YOU_ASKED, YOU_CHOSE, BACKGROUND)
CAPPED = ("claude_cli",)            # backends whose calls spend the person's subscription
UK_ZONE = "Europe/London"           # UK only for now
LIMIT_RETRY_MINUTES = 60   # a limit message that names no reset time pauses background work an hour

_priority: contextvars.ContextVar[int | None] = contextvars.ContextVar("hal_ai_priority", default=None)


def _london() -> tzinfo:
    """UK time; the computer's own zone when tzdata is missing (it is not a Windows dependency)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(UK_ZONE)
    except Exception:
        return datetime.now().astimezone().tzinfo or UTC


def local_now() -> datetime:
    """Now in UK time (the fixed test time when one is set)."""
    return clock.now_utc().astimezone(_london())


def _day_start_utc() -> str:
    start = datetime.combine(local_now().date(), time(0), tzinfo=_london())
    return clock.iso(start)


@contextlib.contextmanager
def at_priority(priority: int) -> Iterator[None]:
    """Runs the block with `priority` as the default for every AI call made inside it."""
    if priority not in PRIORITIES:
        raise ValueError(f"unknown AI priority {priority!r}; one of {PRIORITIES}")
    token = _priority.set(priority)
    try:
        yield
    finally:
        _priority.reset(token)


def current_priority() -> int:
    """The priority of the action running now, else background."""
    value = _priority.get()
    return BACKGROUND if value is None else value


def _con() -> sqlite3.Connection:
    from .. import db

    return db.connect()


def _setting(key: str):
    from ..core import settings

    return settings.get(key)


def limited_until(provider: str = "claude_cli", con: sqlite3.Connection | None = None) -> datetime | None:
    """When the last limit `provider` reported lifts, or None when no limit holds now."""
    own = con is None
    con = con or _con()
    try:
        row = con.execute("""SELECT id, limited_until FROM ai_usage WHERE provider = ? AND limited_until IS NOT NULL
                             ORDER BY id DESC LIMIT 1""", (provider,)).fetchone()
        if row is None:
            return None
        lifted = con.execute("SELECT 1 FROM ai_usage WHERE provider = ? AND ok = 1 AND id > ? LIMIT 1",
                             (provider, row[0])).fetchone()
    finally:
        if own:
            con.close()
    until = clock.parse(row[1])
    if lifted or until <= clock.now_utc():
        return None
    return until


def _clock_words(at: datetime) -> str:
    return at.astimezone(_london()).strftime("%H:%M")


def limit_words(until: datetime | None) -> str:
    """What the person reads while Claude's limit holds."""
    when = _clock_words(until) if until else "when the limit resets"
    at = f"at {when}" if until else when
    return f"Claude limit reached. Hal is still finding jobs; writing starts again {at}."


def _in_hours(raw, now: datetime) -> bool:
    from ..core.settings import parse

    hours = parse.hours(raw) if raw else None    # the stored value, or words such as "22:00 to 07:30"
    if not hours:
        return False
    start, end = (time.fromisoformat(h) for h in hours)
    t = now.time()
    return start <= t < end if start <= end else t >= start or t < end


def background_today(provider: str, con: sqlite3.Connection | None = None) -> int:
    """Successful background calls to `provider` since UK midnight."""
    own = con is None
    con = con or _con()
    try:
        row = con.execute("SELECT COUNT(*) FROM ai_usage WHERE provider = ? AND priority = ? AND ok = 1 AND at >= ?",
                          (provider, BACKGROUND, _day_start_utc())).fetchone()
    finally:
        if own:
            con.close()
    return int(row[0])


def admit(priority: int, purpose: str, backend=None) -> None:
    """Raises `LLMUnavailable` (with its `kind` and plain words) when a call may not run now."""
    if backend is None:
        from . import backend as current

        backend = current()
    if backend.online and _setting("ai.local_only"):
        raise LLMUnavailable("Hal is set to never use online AI.", kind="local_only")
    if backend.name not in CAPPED:
        return
    until = limited_until(backend.name)
    if until is not None and priority == BACKGROUND:
        raise Limited(limit_words(until), until=until)
    if priority != BACKGROUND:
        return
    if _in_hours(_setting("ai.keep_free"), local_now()):
        raise LLMUnavailable("Hal keeps Claude free for you at this time of day, so background checks wait.",
                             kind="keep_free")
    cap = int(_setting("ai.daily_cap"))
    if background_today(backend.name) >= cap:
        raise LLMUnavailable(f"Hal has used today's {cap} background checks. It carries on tomorrow.", kind="cap")


def record(result_or_error: ChatResult | BaseException, *, priority: int, purpose: str, chars_in: int,
           chars_out: int, provider: str = "", model: str = "", role: str = "", ms: int | None = None,
           job_id: str | None = None, action_id: str | None = None) -> None:
    """Adds one `ai_usage` row: sizes, timing and the outcome, never the prompt or the reply."""
    ok = isinstance(result_or_error, ChatResult)
    if ok:
        provider, model, ms = result_or_error.provider, result_or_error.model, result_or_error.ms
    kind = None if ok else getattr(result_or_error, "kind", "unavailable")
    until = getattr(result_or_error, "until", None) if not ok else None
    if kind == "limit" and until is None:
        until = clock.now_utc() + timedelta(minutes=LIMIT_RETRY_MINUTES)
    con = _con()
    try:
        with con:
            con.execute("""INSERT INTO ai_usage (at, provider, model, role, priority, purpose, ok, error_kind,
                                                 limited_until, ms, chars_in, chars_out, action_id, job_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (clock.iso(), provider or "unknown", model, role, priority, purpose or "unnamed", int(ok), kind,
                         clock.iso(until) if until else None, ms, chars_in, chars_out, action_id, job_id))
    finally:
        con.close()



def today(provider: str = "claude_cli") -> dict:
    """Today's counts for the Settings allowance row: {calls, background, cap, limited_until, keep_free_now}."""
    con = _con()
    try:
        calls = con.execute("SELECT COUNT(*) FROM ai_usage WHERE provider = ? AND ok = 1 AND at >= ?",
                            (provider, _day_start_utc())).fetchone()[0]
        background = background_today(provider, con)
        until = limited_until(provider, con)
    finally:
        con.close()
    return {"calls": int(calls), "background": background, "cap": int(_setting("ai.daily_cap")),
            "limited_until": clock.iso(until) if until else None,
            "keep_free_now": _in_hours(_setting("ai.keep_free"), local_now())}


def words(provider: str = "claude_cli") -> str:
    """The allowance row in plain words."""
    t = today(provider)
    times = "time" if t["calls"] == 1 else "times"
    were = "was a background check" if t["background"] == 1 else "were background checks"
    out = f"Hal used Claude {t['calls']} {times} today. {t['background']} {were} (your limit is {t['cap']})."
    if t["limited_until"]:
        out += (f" Background checks are paused until {_clock_words(clock.parse(t['limited_until']))} because "
                "Claude said you reached a limit.")
    return out
