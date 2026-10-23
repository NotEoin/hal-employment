"""Updates Hal Employment, or goes back to the version before the last update.

Run outside `.venv`, by uv's managed Python, so `uv sync` can replace `.venv` while this runs (Windows locks
loaded `.pyd` files):

    <tools>/uv run --no-project --python 3.13 scripts/updater.py [--check | --rollback] [--background]

The steps live in `jobhunt/core/updates.py`, which uses the standard library only; this file puts the clone
on the import path and hands over. Everything it needs is imported before git changes any file.
"""
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    sys.path.insert(0, str(CODE))
    from jobhunt.core import updates

    sys.exit(updates.main())
