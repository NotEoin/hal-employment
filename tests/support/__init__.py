"""Shared test helpers. Importing this package first puts the whole test run in a temporary home.

`python -m unittest discover tests` never runs `tests/__init__.py`, so every test module imports `support`
before `jobhunt` (an AST test in `test_paths.py` holds that). On import this sets `HAL_TEST_MODE=1` and, when
no `HAL_EMPLOYMENT_HOME` is set, a fresh temporary one that is removed when the run ends. A test run started
without the gate's variables therefore still never reaches real data.

Importing it also puts `support.offline` in place, so no test reaches the internet: a lookup or connection
that would leave this computer fails at once, as it would offline.
"""
import atexit
import os
import shutil
import tempfile

from . import offline

os.environ.setdefault("HAL_TEST_MODE", "1")
if not os.environ.get("HAL_EMPLOYMENT_HOME", "").strip():
    _home = tempfile.mkdtemp(prefix="hal-tests-")
    os.environ["HAL_EMPLOYMENT_HOME"] = _home
    atexit.register(shutil.rmtree, _home, ignore_errors=True)
offline.install()
