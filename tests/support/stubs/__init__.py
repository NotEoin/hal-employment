"""Stand-ins for modules that are not built yet, installed only while the real ones are missing.

The server codes against the migration runner (`jobhunt.core.db.migrate`) and settings registry
(`jobhunt.core.settings.registry`). While those are missing, `install()` puts these stubs in their place
so the server's tests run; once the real modules exist, `install()` does nothing and this folder can go.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUBS = {"jobhunt.core.db.migrate": HERE / "migrate.py", "jobhunt.core.settings.registry": HERE / "registry.py"}


def install() -> list[str]:
    """Installs each stub whose real module is missing; returns the names installed."""
    done = []
    for name, file in STUBS.items():
        if name in sys.modules or importlib.util.find_spec(name) is not None:
            continue
        spec = importlib.util.spec_from_file_location(name, file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        parent, _, leaf = name.rpartition(".")
        setattr(importlib.import_module(parent), leaf, module)
        done.append(name)
    return done
