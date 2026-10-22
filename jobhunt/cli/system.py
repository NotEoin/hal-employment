"""Commands for running Hal itself: serve, vault and open.

`serve` runs the new server (`web.app`), which also serves the classic screens at /classic/. `open` is what
shortcuts and launchers run: it makes sure the server answers, mints a one-time link and opens it, because
the app only opens through such a link. The parser for the legacy commands stays in
`jobhunt/__main__.py`, whose help text is held by goldens; `add_open_command` is wired in when the help
goldens learn the new name.
"""
import os
import subprocess
import sys
import time
import webbrowser

from .. import db

START_WAIT_S = 30        # a cold start migrates the database first; it never takes this long
START_POLL_S = 0.25


def cmd_serve(args):
    from ..web import app

    code = app.serve(args.port, args.open, getattr(args, "background", False))
    if code:
        sys.exit(code)


def cmd_vault(args):
    from .. import vault
    print(vault.write(db.connect()) or f"vault not found: {vault.VAULT}")


def _start_server(port: int) -> None:
    """Starts `jobhunt serve --port N` in the background, detached from this console."""
    argv = [sys.executable, "-m", "jobhunt", "serve", "--port", str(port)]
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        flags["start_new_session"] = True
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)


def cmd_open(args):
    """Opens Hal Employment in a signed-in window, starting the server first when it is not running."""
    from ..web import app, auth

    port = args.port or auth.server_port()
    if not app.answering(port):
        _start_server(port)
        deadline = time.monotonic() + START_WAIT_S
        while not app.answering(port):
            if time.monotonic() > deadline:
                sys.exit("Hal Employment did not start. See logs/server.log in Hal's data folder.")
            time.sleep(START_POLL_S)
    url = auth.mint_open_url(port=port, next=args.next)
    if args.print:
        print(url)
    else:
        webbrowser.open(url)


def add_open_command(sub) -> None:
    """Adds `open [--port N] [--next PATH] [--print]` to the command line's subcommands."""
    s = sub.add_parser("open", help="open Hal Employment in a signed-in window")
    s.add_argument("--port", type=int, help="the port Hal listens on (default: its setting)")
    s.add_argument("--next", help="the page to show, a path on Hal's server such as /classic/")
    s.add_argument("--print", action="store_true", help="print the one-time link instead of opening it")
    s.set_defaults(fn=cmd_open)
