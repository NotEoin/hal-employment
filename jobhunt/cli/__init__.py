"""The command bodies of `python -m jobhunt`, one module per area.

A module that adds new commands defines `register(sub)`, taking the parser's subparsers; `__main__` calls
each after building the legacy commands, so no package edits a shared list and the legacy help is unchanged.
"""

import importlib
import pkgutil


def register_all(sub) -> None:
    """Calls `register(sub)` on every module of this package that defines it, in name order."""
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(module, "register"):
            module.register(sub)
