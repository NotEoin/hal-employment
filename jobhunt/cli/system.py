"""Commands for running Hal itself: serve and vault.

The bodies moved unchanged from `jobhunt/__main__.py`; the parser, its arguments and help stay there,
because the voice bridge and the /jobs command parse what these commands print.
"""
from .. import db


def cmd_serve(args):
    from ..web import server
    server.serve(args.port, args.open)


def cmd_vault(args):
    from .. import vault
    print(vault.write(db.connect()) or f"vault not found: {vault.VAULT}")
