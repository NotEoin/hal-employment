"""The desktop's side of using Hal from another computer: `open-url`, `helper-files` and `remote-launcher`.

The laptop launcher runs these over SSH, through the `~/.local/bin/hal-employment-cli` shim,
and reads their stdout: `open-url` must print exactly one line, the link, and anything else goes to
stderr; `helper-files --tar` writes nothing but the tar; `remote-launcher` nothing but the script. So
these commands print to stdout only at the very end, and every message is on stderr.
"""
from __future__ import annotations

import socket
import sys
import tarfile

SKIP_PARTS = ("__pycache__", "node_modules")


def cmd_open_url(args) -> None:
    """Makes sure Hal is running, then prints a one-time link for a window on this or another computer."""
    from ..remote import service
    from ..web import auth

    port = auth.server_port()
    try:
        service.ensure_running(port)
    except service.NotStarted as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    host = args.host_label or socket.gethostname()
    url = auth.mint_open_url(remote=args.remote, remote_host=host if args.remote else None,
                             port=args.local_port or port)
    sys.stdout.write(url + "\n")
    sys.stdout.flush()


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    # the laptop gets plain files it owns, whoever made them here
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def cmd_helper_files(args) -> None:
    """Writes the browser helper's files as a tar to stdout (`--tar`), or prints where they are."""
    from ..apply import helper_mirror

    src = helper_mirror.source()   # the folder the desktop refreshes its visible copy from
    if not (src / "manifest.json").is_file():
        print(f"The browser helper's files are missing from {src}.", file=sys.stderr)
        sys.exit(1)
    if not args.tar:
        print(src)
        return
    out = sys.stdout.buffer
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for f in sorted(src.rglob("*")):
            rel = f.relative_to(src)
            if not f.is_file() or any(p.startswith(".") or p in SKIP_PARTS for p in rel.parts):
                continue
            tar.add(f, arcname=rel.as_posix(), recursive=False, filter=_tar_filter)
    out.flush()


def cmd_remote_launcher(args) -> None:
    """Prints the launcher for the other computer, filled in with this computer's host, user and port."""
    from ..remote import launcher
    from ..web import auth

    try:
        data = launcher.render(args.os, host=args.host or launcher.this_host(), user=args.user or launcher.this_user(),
                               port=args.port or auth.server_port(), hal=launcher.hal_command())
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def register(sub) -> None:
    """Adds `open-url`, `helper-files` and `remote-launcher` to the command line's subcommands."""
    s = sub.add_parser("open-url", help="print a one-time link that opens Hal, starting Hal first")
    s.add_argument("--remote", action="store_true", help="the link is for a window on another computer")
    s.add_argument("--local-port", type=int, help="the port the other computer's tunnel listens on")
    s.add_argument("--host-label", help="this computer's name as the other computer shows it")
    s.set_defaults(fn=cmd_open_url)
    s = sub.add_parser("helper-files", help="the browser helper's files, for another computer")
    s.add_argument("--tar", action="store_true", help="write them to stdout as a tar")
    s.set_defaults(fn=cmd_helper_files)
    s = sub.add_parser("remote-launcher", help="print the launcher for another computer")
    s.add_argument("--os", required=True, choices=("linux", "mac", "windows"), help="the other computer's system")
    s.add_argument("--host", help="this computer's name on your network (default: its host name)")
    s.add_argument("--user", help="your user name on this computer (default: this account)")
    s.add_argument("--port", type=int, help="the port Hal listens on here (default: its setting)")
    s.set_defaults(fn=cmd_remote_launcher)
