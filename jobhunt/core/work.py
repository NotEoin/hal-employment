"""Work classes: which kind of work an AI call serves, so the AI's queue can put the person's first.

The AI serves calls in class order, first come first served within a class (`ai._Turns`):

- `ON_SCREEN` (0): something the person asked for and waits on, on the page (a recruiter's read of their CV,
  a change to a letter, a form being filled) and every web request.
- `RECORD` (1): updates of the person's record (reads of what they added, the bulk ingest, a sort they
  accepted, the folder sync). Document writes read the record, so they go after it.
- `DOCUMENTS` (2): application documents (`docs.prepare`, packs made ahead of time).
- `OTHER` (3): everything else (job discovery, rescoring, email, upkeep).

Design decisions, each with its reason:

- A class is separate from an action's priority (`runner`) and the AI allowance's priority (`ai.budget`).
  The priority picks a thread pool and the Claude allowance's rules; the class only orders the AI's queue.
  Before classes, every AI call made in an action queued at priority 2, first come first served, so a
  record update waited behind every document write queued before it (2026-11-18).
- The class lives in a contextvar: the runner enters the action's class while it runs it, and code that
  hands work to other threads copies its context (`contextvars.copy_context().run`), as the record readers do.
- A call made outside any class takes it from its priority (`for_priority`), so an explicit `priority=` on
  `ai.chat` still decides the order there: 0 is on screen, 1 a document, 2 other work.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

ON_SCREEN, RECORD, DOCUMENTS, OTHER = 0, 1, 2, 3
CLASSES = (ON_SCREEN, RECORD, DOCUMENTS, OTHER)
# what an action priority (and an AI allowance priority) means when no class says otherwise
BY_PRIORITY = {0: ON_SCREEN, 1: DOCUMENTS, 2: OTHER}
# action kinds whose first word names their class when their priority does not settle it
BY_AREA = {"record": RECORD, "docs": DOCUMENTS, "cv": ON_SCREEN, "apply": ON_SCREEN, "form": ON_SCREEN}

_class: contextvars.ContextVar[int | None] = contextvars.ContextVar("hal_work_class", default=None)


def _checked(cls: int) -> int:
    if cls not in CLASSES:
        raise ValueError(f"unknown work class {cls!r}; one of {CLASSES}")
    return cls


def for_priority(priority: int) -> int:
    """The class of a call or action that has only a priority (0, 1 or 2)."""
    try:
        return BY_PRIORITY[priority]
    except KeyError:
        raise ValueError(f"unknown priority {priority!r}; one of {tuple(BY_PRIORITY)}") from None


def for_kind(kind: str, priority: int) -> int:
    """The default class of an action kind: on screen at priority 0, else by its first word, else by priority.

    A record kind is a record update even in the background (the folder sync); a background kind of another
    area is other work, whatever its first word (`cv.import_v1`, a data task).
    """
    if priority == 0:
        return ON_SCREEN
    area = kind.split(".", 1)[0]
    if area == "record":
        return RECORD
    if priority >= 2:
        return OTHER
    return BY_AREA.get(area, for_priority(priority))


def current() -> int | None:
    """The class the running code was given, or None outside any."""
    return _class.get()


def enter(cls: int) -> contextvars.Token:
    """Makes `cls` the class of the code that runs next in this context; `leave` with the token undoes it."""
    return _class.set(_checked(cls))


def leave(token: contextvars.Token) -> None:
    """Puts back the class from before the `enter` that gave `token`."""
    _class.reset(token)


@contextlib.contextmanager
def entered(cls: int) -> Iterator[None]:
    """Runs the block as work of class `cls`; AI calls inside it queue in that class."""
    token = enter(cls)
    try:
        yield
    finally:
        leave(token)


def of_call(priority: int) -> int:
    """The class an AI call queues in: the class entered around it, else the call's priority's."""
    cls = _class.get()
    return for_priority(priority) if cls is None else cls
