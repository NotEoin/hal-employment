"""`python -m jobhunt settings get KEY | set KEY VALUE | list [PREFIX]`: settings from the command line.

Values print as JSON (text plain), so scripts can read them. Keys and passwords print only "saved", "not saved"
or "locked", and private answers only "saved" or "not saved". Neither can be set here, because a value typed on
the command line stays in the shell's history; the Settings page takes them.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _words(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _elsewhere(item) -> str | None:
    """The status of a secret or private row, or None for a stored value."""
    from ..core.settings import private, secrets

    if item.kind == "secret":
        return secrets.status(item.secret_name)
    if item.kind == "private":
        saved = private.status()
        return f"{sum(saved.values())} of {len(saved)} saved"
    return None


def _line(key: str) -> str:
    from ..core import settings

    item = settings.setting(key)
    status = _elsewhere(item)
    if status is not None:
        return f"{key} = ({status})"
    value = settings.get(key)
    mark = " *" if key in settings.changed() else ""
    words = settings.display(key, value)
    shown = _words(value)
    return f"{key} = {shown}{mark}" + (f"  ({words})" if words and words != shown else "")


def cmd_settings(args) -> None:
    """Runs one `settings` subcommand."""
    from ..core import settings
    from ..core.settings import registry

    try:
        if args.op == "list":
            reg = settings._registry()
            keys = [k for k, s in sorted(reg.REGISTRY.items())
                    if k.startswith(args.key or "") and (s.stored or s.kind in registry.ELSEWHERE_KINDS)]
            if not keys:
                sys.exit(f"settings: no setting starts with {args.key!r}")
            for key in keys:
                print(_line(key))
            return
        if not args.key:
            sys.exit(f"settings {args.op}: give a key, such as goal.min_pay")
        item = settings.setting(args.key)
        if args.op == "get":
            status = _elsewhere(item)
            print(f"({status})" if status is not None else _words(settings.get(args.key)))
            return
        if item.kind in registry.ELSEWHERE_KINDS:
            sys.exit("settings set: save keys, passwords and private answers on the Settings page")
        if args.value is None:
            sys.exit("settings set: give the new value")
        result = settings.set(args.key, args.value)
        shown = _words(result.value)
        print(f"{args.key} = {shown}" + (f"  ({result.display})" if result.display and result.display != shown else ""))
        if result.restart:
            print("Restart Hal for this to take effect.")
    except registry.SettingError as e:
        sys.exit(f"settings: {e}")
    except KeyError as e:
        sys.exit(f"settings: {e.args[0] if e.args else e}")


def register(sub) -> None:
    """Adds `settings get KEY | set KEY VALUE | list [PREFIX]` to the command line's subcommands."""
    s = sub.add_parser("settings", help="read or change Hal's settings")
    s.add_argument("op", choices=["get", "set", "list"])
    s.add_argument("key", nargs="?", help="a setting such as goal.min_pay, or for list a prefix such as goal.")
    s.add_argument("value", nargs="?", help="for set: the new value, written as you would type it on the page")
    s.set_defaults(fn=cmd_settings)
