"""The `privacy.export` action kind: the zip of everything Hal keeps, without secrets or private answers.

Kept apart from `privacy_routes.py` so `runner.load_kinds()`, which imports only `actions` modules, finds it
(gate G9) without loading the routes. The zip itself is `privacy_routes.export`.
"""
from __future__ import annotations

from . import clock, runner

KIND = "privacy.export"


@runner.action(KIND, area="settings", label="Making a zip of everything", priority=0,
               why="You pressed Export everything", exclusive="kind", example=lambda world: {})
def export_everything(ctx: runner.Ctx) -> dict:
    """Writes the zip into the exports folder and registers it for download."""
    from . import helpfile
    from .privacy_routes import export

    folder = helpfile.exports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = clock.iso().replace(":", "").replace("-", "")
    target = folder / f"Hal export {stamp}.zip"
    export(target, ctx.step)
    ctx.step("Saving the zip")
    file_id = helpfile.register_file(ctx.con, target, "export", "Everything Hal keeps")
    ctx.changed("Made a zip of everything Hal keeps.")
    return {"file_id": file_id}
