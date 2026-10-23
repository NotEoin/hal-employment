"""Action kinds for updates and the stick import: `updates.check`, `updates.sign_in`, `setup.import_usb`.

The update itself never runs as an action inside the server: it stops the server, so it is spawned as its
own process (`update_routes.start_updater`). The check and the sign-in only fetch, so they run here with
steps and an Activity row like any other action.

Design decisions, each with its reason:

- A scheduled check never prompts for a GitHub sign-in; only a person's own press in a
  local session passes `interactive=True`, which lets Git Credential Manager open its window.
- When a check finds a new version, one Messages item is added per new commit, so the daily check does not
  repeat the same message every morning.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import db
from . import clock, runner, updates

log = logging.getLogger(__name__)

AREA = "settings"
READY_TITLE = "A new version of Hal is ready"
SIGNED_IN = "Signed in to GitHub."


def _alert_ready(result: dict) -> None:
    """One Messages item for this new version, with What's new and "Update Hal"."""
    con = db.connect()
    try:
        title = READY_TITLE
        body = "\n".join(["What's new:", *[f"- {line}" for line in result.get("whats_new", [])]])
        marker = f"update:{result.get('newest', '')[:updates.SHORT]}"
        seen = con.execute("SELECT 1 FROM alerts WHERE kind = 'update' AND body LIKE ?", (f"%{marker}%",)).fetchone()
        if seen:
            return
        action = json.dumps({"label": "Update Hal", "route": "settings-updates"})
        con.execute("INSERT INTO alerts (at, level, title, body, kind, action) VALUES (?, 'info', ?, ?, 'update', ?)",
                    (clock.iso(), title, f"{body}\n({marker})", action))
        con.commit()
    finally:
        con.close()


def check(ctx: runner.Ctx, interactive: bool = False) -> dict:
    """Steps 1 to 3 of an update: fetch, compare, check signatures; keeps the result for Settings and Home."""
    ctx.step("Looking for a new version of Hal")
    inst = updates.read_install()
    if inst.developer:
        ctx.step(updates.DEVELOPER)
        return {"developer": True}
    world = updates.World(inst, interactive=interactive)
    try:
        result = updates.check_and_keep(world)
    except updates.UpdateError as e:
        ctx.step(e.words)
        raise RuntimeError(e.words) from e
    ctx.step(result["words"])
    if result.get("available"):
        _alert_ready(result)
    return {k: result[k] for k in ("available", "behind", "words") if k in result}


def sign_in(ctx: runner.Ctx) -> dict:
    """"Sign in to GitHub again": a fetch that may open Git Credential Manager's sign-in window."""
    ctx.step("Opening GitHub's sign-in")
    inst = updates.read_install()
    if inst.developer:
        ctx.step(updates.DEVELOPER)
        return {"developer": True}
    world = updates.World(inst, interactive=True)
    try:
        updates.check_and_keep(world)
    except updates.UpdateError as e:
        ctx.step(e.words)
        raise RuntimeError(e.words) from e
    ctx.step(SIGNED_IN)
    return {"signed_in": True}


def import_usb(ctx: runner.Ctx, path: str, dry_run: bool = False) -> dict:
    """Copies the old stick install into this Hal and retires it."""
    from . import importer, importer_usb

    ctx.step("Looking at the older Hal on this computer")
    try:
        report = importer_usb.run(Path(path), dry_run=dry_run, say=ctx.step)
    except importer.ImportStopped as e:
        raise RuntimeError(str(e)) from e
    return {"steps": len(report.steps), "dry_run": dry_run}


def _fake_stick(world) -> dict:
    """G9's example: a made-up stick install inside the made-up world, imported as a dry run."""
    from . import importer_usb

    folder = importer_usb.make_example(Path(world.root) / "stick-example")
    return {"path": str(folder), "dry_run": True}


runner.action("updates.check", area=AREA, label="Check for updates", why="Daily update check", priority=2,
              exclusive="kind", example=lambda w: {})(check)
runner.action("updates.sign_in", area=AREA, label="Sign in to GitHub", why="You pressed Sign in again",
              priority=0, exclusive="kind", example=lambda w: {})(sign_in)
runner.action("setup.import_usb", area=AREA, label="Copy your old Hal", why="Hal found an older Hal on this computer",
              priority=0, exclusive="kind", example=_fake_stick)(import_usb)
