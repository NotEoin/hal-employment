"""Puts the CV stand-ins in place of the CV store, model and types this checkout does not have yet.

`install()` is called by the suggestion test modules at import. A module that exists for real is always used,
so once the CV modules exist nothing here takes effect and the `cv_early_*` stubs can go.
"""
from __future__ import annotations

import importlib
import sys

from support.stubs import cv_early_checks, cv_early_model, cv_early_store, cv_early_types

STANDS_IN = {"model": cv_early_model, "store": cv_early_store, "types": cv_early_types, "checks": cv_early_checks}


def install() -> list[str]:
    """Installs a stand-in for each missing contract module; returns the names it stood in for."""
    import jobhunt.cvstudio as package

    used = []
    for name, stub in STANDS_IN.items():
        full = f"jobhunt.cvstudio.{name}"
        if sys.modules.get(full) is stub:
            used.append(name)
            continue
        try:
            importlib.import_module(full)
        except ModuleNotFoundError as e:
            if e.name != full:
                raise
            sys.modules[full] = stub
            setattr(package, name, stub)
            used.append(name)
    return used
