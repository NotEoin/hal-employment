"""jobhunt - discover, rank and track job applications.

    python -m jobhunt discover            fetch every source, score, alert on strong new matches
    python -m jobhunt list [-s STATUS] [-m MIN] [-n N] [--new-today]
    python -m jobhunt show ID
    python -m jobhunt set ID STATUS [NOTE]
    python -m jobhunt note ID TEXT
    python -m jobhunt stats
    python -m jobhunt rescore
    python -m jobhunt probe NAME [NAME ...] [--save]   find a company's ATS board
    python -m jobhunt mail [--days N]     sync the job inbox, update statuses, alert on what needs you
    python -m jobhunt inbox [-k KIND] [-n N]   classified recruiting email
    python -m jobhunt digest [--dry-run|--spoken]   email the daily summary, or print it for speech
    python -m jobhunt vault               rewrite the pipeline note in Hal's Employment vault
    python -m jobhunt record [sync|status|search Q|ask Q]   the employment record vault
    python -m jobhunt pack ID|--shortlisted [--no-web]   draft application packs
    python -m jobhunt serve [--port N] [--open]   run the Hal Employment web interface
    python -m jobhunt queue               the jobs waiting in the queue, best first
    python -m jobhunt todo [add TEXT|done ID]   the to-do list
    python -m jobhunt contacts ID         find who to message about an application

IDs can be given as any unique prefix or substring.
"""
# The venv trampoline, standard library only and before any other jobhunt import: the Hal tools
# run `/usr/bin/python3 -m jobhunt <cmd>` from the checkout, and this re-runs that inside its `.venv`, with
# UTF-8 mode on so Windows consoles print the same text.
import os
import subprocess
import sys
from pathlib import Path


def _venv_python() -> Path | None:
    root = Path(__file__).resolve().parent.parent
    p = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    return p if p.exists() else None


if os.environ.get("HAL_EMPLOYMENT_NO_TRAMPOLINE") != "1" and sys.prefix == sys.base_prefix:
    _py = _venv_python()
    if _py is not None:
        os.environ["HAL_EMPLOYMENT_NO_TRAMPOLINE"] = "1"
        _args = [str(_py), "-X", "utf8", "-m", "jobhunt", *sys.argv[1:]]
        if os.name == "nt":
            # Windows has no exec that keeps the console; the child's exit code is passed on.
            sys.exit(subprocess.call(_args))
        os.execv(str(_py), _args)

import argparse  # noqa: E402 - after the venv trampoline

from . import db  # noqa: E402 - after the venv trampoline
from .cli import register_all  # noqa: E402 - after the venv trampoline
from .cli.docs import cmd_check, cmd_contacts, cmd_letter, cmd_pack  # noqa: E402 - after the venv trampoline
from .cli.find import (  # noqa: E402 - after the venv trampoline
    cmd_add,
    cmd_discover,
    cmd_list,
    cmd_note,
    cmd_probe,
    cmd_queue,
    cmd_rescore,
    cmd_screen,
    cmd_set,
    cmd_show,
    cmd_stats,
)
from .cli.mail import cmd_digest, cmd_inbox, cmd_mail, cmd_todo  # noqa: E402 - after the venv trampoline
from .cli.record import cmd_record  # noqa: E402 - after the venv trampoline
from .cli.system import cmd_serve, cmd_vault  # noqa: E402 - after the venv trampoline
from .core import tls  # noqa: E402 - after the venv trampoline


def main():
    tls.use_system_trust()
    p = argparse.ArgumentParser(prog="jobhunt", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("discover"); s.add_argument("-q", "--quiet", action="store_true"); s.set_defaults(fn=cmd_discover)
    sub.add_parser("rescore").set_defaults(fn=cmd_rescore)
    s = sub.add_parser("mail"); s.add_argument("--days", type=int); s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(fn=cmd_mail)
    s = sub.add_parser("digest"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--spoken", action="store_true"); s.set_defaults(fn=cmd_digest)
    sub.add_parser("vault").set_defaults(fn=cmd_vault)
    s = sub.add_parser("record", help="read, search or ask the employment record vault")
    s.add_argument("op", nargs="?", default="status", choices=["status", "sync", "search", "ask"])
    s.add_argument("text", nargs="*"); s.set_defaults(fn=cmd_record)
    sub.add_parser("queue").set_defaults(fn=cmd_queue)
    s = sub.add_parser("contacts"); s.add_argument("id"); s.set_defaults(fn=cmd_contacts)
    s = sub.add_parser("todo"); s.add_argument("op", nargs="?", choices=["add", "done"])
    s.add_argument("rest", nargs="*"); s.set_defaults(fn=cmd_todo)
    s = sub.add_parser("serve", help="run the Hal Employment web interface")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true")
    s.add_argument("--background", action="store_true",
                   help="no console: log only to logs/server.log in the data folder")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("add", help="track an advert found by hand, skipping the filters")
    s.add_argument("url"); s.add_argument("--company", required=True); s.add_argument("--title", required=True)
    s.add_argument("--location", default=""); s.add_argument("--salary"); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("screen", help="ask the local model which jobs a new graduate could get")
    s.add_argument("-m", "--min", type=float, default=50); s.add_argument("-n", type=int, default=60)
    s.set_defaults(fn=cmd_screen)
    s = sub.add_parser("letter", help="render a pack's cover letter as a PDF in the CV's style")
    s.add_argument("id"); s.add_argument("--to", help="recipient lines separated by |"); s.add_argument("--subject")
    s.set_defaults(fn=cmd_letter)
    s = sub.add_parser("check"); s.add_argument("id", nargs="?"); s.add_argument("--ready", action="store_true")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("pack"); s.add_argument("id", nargs="?"); s.add_argument("--shortlisted", action="store_true")
    s.add_argument("--no-web", action="store_true")
    s.add_argument("--cv-only", action="store_true", help="re-render tailored CVs for drafted packs")
    s.set_defaults(fn=cmd_pack)
    s = sub.add_parser("inbox"); s.add_argument("-k", "--kind"); s.add_argument("-n", type=int, default=60)
    s.set_defaults(fn=cmd_inbox)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)

    s = sub.add_parser("list")
    s.add_argument("-s", "--status", default="new", help=f"one of {', '.join(db.STATUSES)}, or 'all'")
    s.add_argument("-m", "--min", type=float)
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--new-today", action="store_true")
    s.add_argument("--internships", action="store_true", help="list graduate internships instead of jobs")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show"); s.add_argument("id"); s.add_argument("--full", action="store_true"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("set"); s.add_argument("id"); s.add_argument("status", choices=db.STATUSES)
    s.add_argument("note", nargs="*"); s.set_defaults(fn=cmd_set)
    s = sub.add_parser("note"); s.add_argument("id"); s.add_argument("text", nargs="+"); s.set_defaults(fn=cmd_note)

    s = sub.add_parser("probe"); s.add_argument("names", nargs="+")
    s.add_argument("--save", action="store_true"); s.add_argument("--keep-empty", action="store_true")
    s.set_defaults(fn=cmd_probe)

    register_all(sub)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
