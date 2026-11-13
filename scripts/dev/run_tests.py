"""Runs one share of the test suite in parallel processes, so CI spreads the suite over jobs and their CPUs.

The suite run one test at a time took 27 minutes on Linux and 38 on Windows (CI run 43266048071). Tests are
listed in discovery order and cut into chunks of `CHUNK` tests; share I of N takes every Nth chunk, so each
slow browser class is spread over every share, and runs its chunks `--workers` at a time, each in a process of
its own. Each process makes its own temporary home (`tests/support`) and finds free ports, so they never meet.
A chunk's output is printed in one piece when it ends.

    python scripts/dev/run_tests.py [--share I/N] [--workers W] [--must-pass NAME ...]

`--must-pass` names tests (by method name) that must run and pass, not skip: each is checked in the share that
holds it, and a name no share holds fails every share. Exit status 0 when every chunk passed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
CHUNK = 4    # tests per process: a browser test takes up to 80 s on a CI runner, and four of them in one process
             # set the whole run's length; smaller chunks pay more for start-up and repeated class setup
CHUNK_TIMEOUT_S = 1200     # the slowest chunk takes about 2 minutes on a CI runner; past this it has hung
PASSED = "run_tests passed: "
CHILD = f"""
import json, sys, unittest
sys.path.insert(0, {str(TESTS)!r})
passed = []
class Result(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test)
        passed.append(test.id())
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
ok = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=Result).run(suite).wasSuccessful()
print({PASSED!r} + json.dumps(passed), flush=True)
sys.exit(0 if ok else 1)
"""


def test_ids() -> list[str]:
    """Every test id, in the order `unittest discover tests` runs them."""
    def walk(suite):
        for t in suite:
            yield from walk(t) if isinstance(t, unittest.TestSuite) else [t.id()]
    sys.path.insert(0, str(TESTS))
    return list(walk(unittest.defaultTestLoader.discover(str(TESTS), top_level_dir=str(TESTS))))


def run_chunk(ids: list[str]) -> tuple[bool, str, list[str]]:
    """Whether the chunk passed, its output, and the ids that passed."""
    # each process makes its own home
    env = {k: v for k, v in os.environ.items() if k != "HAL_EMPLOYMENT_HOME"} | {"PYTHONIOENCODING": "utf-8"}
    try:
        done = subprocess.run([sys.executable, "-c", CHILD, *ids], cwd=ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=CHUNK_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        return False, f"{(e.stdout or '')}{(e.stderr or '')}\nhung: still running after {CHUNK_TIMEOUT_S} s\n", []
    lines = done.stdout.splitlines(keepends=True)
    passed = [json.loads(line[len(PASSED):]) for line in lines if line.startswith(PASSED)]
    out = "".join(line for line in lines if not line.startswith(PASSED)) + done.stderr
    return done.returncode == 0, out, passed[-1] if passed else []


def main(argv: list[str] | None = None) -> int:
    """Runs this share and returns 1 when a chunk failed or a must-pass test did not pass."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--share", default="1/1", help="I/N: run the Ith of N shares (default all)")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--must-pass", nargs="*", default=[], metavar="NAME")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    i, n = (int(x) for x in args.share.split("/"))
    if not 1 <= i <= n:
        p.error(f"--share {args.share}: I must be between 1 and N")

    ids = test_ids()
    names = {t.rsplit(".", 1)[-1] for t in ids}
    missing = [m for m in args.must_pass if m not in names]
    chunks = [ids[k:k + CHUNK] for k in range(0, len(ids), CHUNK)][i - 1::n]
    mine = [t for c in chunks for t in c if t.rsplit(".", 1)[-1] in args.must_pass]
    print(f"share {i}/{n}: {sum(map(len, chunks))} of {len(ids)} tests in {len(chunks)} chunks, "
          f"{args.workers} at a time", flush=True)

    started, failed, passed = time.monotonic(), 0, set()
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for ok, out, ok_ids in pool.map(run_chunk, chunks):
            print(out, flush=True)
            failed += not ok
            passed.update(ok_ids)
    print(f"share {i}/{n}: {len(chunks) - failed} of {len(chunks)} chunks passed in "
          f"{time.monotonic() - started:.0f} s")
    for m in missing:
        print(f"::error::{m} is not in the suite: was it renamed?")
    for t in mine:
        if t not in passed:
            print(f"::error::{t} was skipped or failed")
    return int(bool(failed or missing or set(mine) - passed))


if __name__ == "__main__":
    sys.exit(main())
