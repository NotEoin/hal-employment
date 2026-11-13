"""Runs one share of the test suite in parallel processes, so CI spreads the suite over jobs and their CPUs.

The suite run one test at a time took 27 minutes on Linux and 38 on Windows (CI run 43266048071). Tests are
listed in discovery order and cut into chunks; share I of N takes every Nth chunk and runs them `--workers` at
a time, each in a process of its own. Each process makes its own temporary home (`tests/support`) and finds
free ports, so they never meet. A chunk's output is printed in one piece when it ends.

A class with its own `setUpClass` starts a browser or a server once for its tests, and such classes hold the
slowest tests (24 of the 25 slowest chunks, measured 2026-11-13). Their chunks are small and start first, so
no 80-second browser test begins after the quick ones and sets the end of the run: the whole suite went from
157 to about 100 seconds on 20 cores.

    python scripts/dev/run_tests.py [--share I/N] [--workers W] [--timings FILE] [--must-pass NAME ...]

`--timings` keeps each chunk's time in FILE (JSON) and starts the slowest first on the next run; without it,
the classes that set themselves up start first.

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
CHUNK = 8         # quick tests per process: a process starts in about 0.2 s
SLOW_CHUNK = 2    # tests per process for a class that sets itself up: one browser test takes up to 80 s
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


def test_ids() -> list[tuple[str, bool]]:
    """Every test id in the order `unittest discover tests` runs them, with whether its class sets itself up."""
    base = unittest.TestCase.setUpClass.__func__

    def walk(suite):
        for t in suite:
            yield from walk(t) if isinstance(t, unittest.TestSuite) else [
                (t.id(), getattr(type(t).setUpClass, "__func__", base) is not base)]
    sys.path.insert(0, str(TESTS))
    return list(walk(unittest.defaultTestLoader.discover(str(TESTS), top_level_dir=str(TESTS))))


def chunks_of(tests: list[tuple[str, bool]]) -> tuple[list[list[str]], list[list[str]]]:
    """The slow chunks and the quick ones: runs of tests alike, cut to `SLOW_CHUNK` or `CHUNK` tests."""
    slow: list[list[str]] = []
    quick: list[list[str]] = []
    current: list[str] = []
    kind = None
    for test_id, sets_up in tests:
        if not current or sets_up != kind or len(current) == (SLOW_CHUNK if sets_up else CHUNK):
            current, kind = [], sets_up
            (slow if sets_up else quick).append(current)
        current.append(test_id)
    return slow, quick


def run_chunk(ids: list[str]) -> tuple[bool, str, list[str], float]:
    """Whether the chunk passed, its output, the ids that passed, and how long it took."""
    started = time.monotonic()
    # each process makes its own home
    env = {k: v for k, v in os.environ.items() if k != "HAL_EMPLOYMENT_HOME"} | {"PYTHONIOENCODING": "utf-8"}
    try:
        done = subprocess.run([sys.executable, "-c", CHILD, *ids], cwd=ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=CHUNK_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        return (False, f"{(e.stdout or '')}{(e.stderr or '')}\nhung: still running after {CHUNK_TIMEOUT_S} s\n", [],
                float(CHUNK_TIMEOUT_S))
    lines = done.stdout.splitlines(keepends=True)
    passed = [json.loads(line[len(PASSED):]) for line in lines if line.startswith(PASSED)]
    out = "".join(line for line in lines if not line.startswith(PASSED)) + done.stderr
    return done.returncode == 0, out, passed[-1] if passed else [], time.monotonic() - started


def main(argv: list[str] | None = None) -> int:
    """Runs this share and returns 1 when a chunk failed or a must-pass test did not pass."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--share", default="1/1", help="I/N: run the Ith of N shares (default all)")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--timings", type=Path, metavar="FILE")
    p.add_argument("--must-pass", nargs="*", default=[], metavar="NAME")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    i, n = (int(x) for x in args.share.split("/"))
    if not 1 <= i <= n:
        p.error(f"--share {args.share}: I must be between 1 and N")

    tests = test_ids()
    names = {t.rsplit(".", 1)[-1] for t, _ in tests}
    missing = [m for m in args.must_pass if m not in names]
    slow, quick = chunks_of(tests)
    chunks = slow[i - 1::n] + quick[i - 1::n]
    timings = json.loads(args.timings.read_text()) if args.timings and args.timings.is_file() else {}
    chunks.sort(key=lambda c: -timings.get(c[0], 0))     # stable: with no timings the order stays as built
    mine = [t for c in chunks for t in c if t.rsplit(".", 1)[-1] in args.must_pass]
    print(f"share {i}/{n}: {sum(map(len, chunks))} of {len(tests)} tests in {len(chunks)} chunks, "
          f"{args.workers} at a time", flush=True)

    started, failed, passed = time.monotonic(), 0, set()
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for chunk, (ok, out, ok_ids, took) in zip(chunks, pool.map(run_chunk, chunks), strict=True):
            print(out, flush=True)
            failed += not ok
            passed.update(ok_ids)
            timings[chunk[0]] = round(took, 1)
    if args.timings:
        args.timings.write_text(json.dumps(timings, indent=0, sort_keys=True))
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
