"""Commands for moving and keeping data: `import`, `migrate` and `backup`.

`import` moves the owner's old in-checkout data into Hal's own folders (`core.importer`), or hands a
USB stick install to `core.importer_usb` once that exists. `migrate` shows and runs pending
database migrations without opening the database through `db.connect()`, which would migrate it silently.
`backup` writes one snapshot of the database. Each prints plain lines and exits 1 with the reason when it
stops.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

from ..core import paths

DB_TIMEOUT_S = 30     # the same wait as db.connect(): the server and the CLI share the file


def cmd_import(args) -> None:
    """Runs the one-time import, its dry run, or its undo."""
    from ..core import importer

    try:
        if args.from_usb_install:
            try:
                usb = importlib.import_module("jobhunt.core.importer_usb")
            except ImportError:
                sys.exit("Copying from an old Hal on a USB install is not ready in this version of Hal.")
            usb.run(Path(args.from_usb_install), dry_run=args.dry_run)
            return
        if args.undo:
            importer.undo(force=args.force, system=importer.System(say=print))
            return
        opts = importer.Options(repo=args.from_repo, dry_run=args.dry_run, retire_timers=args.retire_timers,
                                force=args.force)
        importer.run(opts, importer.System(say=print))
    except importer.ImportStopped as e:
        sys.exit(f"Stopped: {e}")


def cmd_migrate(args) -> None:
    """Prints the pending migrations and, unless --dry-run, runs them with a backup first."""
    from ..core.db import migrate

    path = paths.db_path()
    if not path.is_file():
        sys.exit(f"There is no database at {path} yet.")
    con = sqlite3.connect(path, timeout=DB_TIMEOUT_S)
    try:
        todo = migrate.pending(con)
        if not todo:
            print(f"Nothing to do: your records are at version {migrate.user_version(con)}.")
            return
        for mig in todo:
            print(f"{mig.number:04d} {mig.title}")
        if args.dry_run:
            print(f"{len(todo)} to run. Nothing was changed.")
            return
        done = migrate.migrate(con, db_path=path, backup_dir=paths.backups_dir())
        print(f"Updated your records to version {done[-1] if done else migrate.user_version(con)}.")
    except (migrate.MigrationError, migrate.DataTooNew) as e:
        sys.exit(str(e))
    finally:
        con.close()


def cmd_backup(args) -> None:
    """Writes one snapshot of the database to the backups folder and prints its path."""
    from .. import db
    from ..core import backup

    con = db.connect()
    try:
        print(backup.snapshot(con, args.reason, folder=paths.backups_dir()))
    except (backup.BackupError, sqlite3.Error, OSError) as e:
        sys.exit(f"The backup failed: {e}")
    finally:
        con.close()


def register(sub) -> None:
    """Adds `import`, `migrate` and `backup` to the command line's subcommands."""
    s = sub.add_parser("import", help="move your old Hal's data into Hal Employment's own folders")
    src = s.add_mutually_exclusive_group()
    src.add_argument("--from-repo", type=Path, metavar="PATH", help="the old checkout (default: this one)")
    src.add_argument("--from-usb-install", metavar="PATH", help="an old Hal installed from the USB stick")
    s.add_argument("--dry-run", action="store_true", help="show what would happen and change nothing")
    s.add_argument("--retire-timers", action="store_true", help="hand the old jobhunt-* timers to Hal's schedule")
    s.add_argument("--undo", action="store_true", help="put the old data back where it was")
    s.add_argument("--force", action="store_true", help="go ahead although Hal answers, or undo after changes")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("migrate", help="update the database to this version of Hal, with a backup first")
    s.add_argument("--dry-run", action="store_true", help="list the pending updates and change nothing")
    s.set_defaults(fn=cmd_migrate)
    s = sub.add_parser("backup", help="save a copy of the database in the backups folder")
    s.add_argument("--reason", default="by hand", help="a few words kept in the file name")
    s.set_defaults(fn=cmd_backup)
