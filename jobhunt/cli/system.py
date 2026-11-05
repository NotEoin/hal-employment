"""Commands for running Hal itself: serve, vault and open.

`serve` runs the server (`web.app`). `open` is what shortcuts and launchers run: it makes sure the server
answers, mints a one-time link and opens it in one app window through the platform layer (Edge on Windows,
the first Chromium-family browser on Linux), because the app only opens through such a link. The parser
for the legacy commands stays in `jobhunt/__main__.py`, whose help text is held by goldens; `register`
adds `open`, a name gate G4 allows the top-level help to gain.
"""
import sys
import time

from .. import db

START_WAIT_S = 30        # a cold start migrates the database first; it never takes this long
START_POLL_S = 0.25
SERVE_UNIT = "hal-employment-serve"   # the transient unit a server started by `open` runs in (Linux)


def cmd_serve(args):
    """Handles `jobhunt serve`: runs the web server."""
    from ..web import app

    code = app.serve(args.port, args.open, getattr(args, "background", False))
    if code:
        sys.exit(code)


def cmd_vault(args):
    """Handles `jobhunt vault`: writes the pipeline note into the Hal vault."""
    from .. import vault
    print(vault.write(db.connect()) or f"vault not found: {vault.VAULT}")


def _start_server(port: int) -> None:
    """Starts `jobhunt serve --port N` detached, so it outlives this command (and, on Linux, its terminal)."""
    from ..core import platform

    argv = [sys.executable, "-m", "jobhunt", "serve", "--background", "--port", str(port)]
    platform.current().spawn_detached(argv, unit=SERVE_UNIT)


def cmd_open(args):
    """Opens Hal Employment in one signed-in window, starting the server first when it is not running."""
    from ..core import platform
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
        platform.current().open_in_default_browser(url)


def register(sub) -> None:
    """Adds `open [--port N] [--next PATH] [--print]` to the command line's subcommands."""
    s = sub.add_parser("open", help="open Hal Employment in a signed-in window")
    s.add_argument("--port", type=int, help="the port Hal listens on (default: its setting)")
    s.add_argument("--next", help="the page to show, a path on Hal's server such as /#settings")
    s.add_argument("--print", action="store_true", help="print the one-time link instead of opening it")
    s.set_defaults(fn=cmd_open)
