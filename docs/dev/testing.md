# Testing Hal Employment

How the tests run, what each gate proves, and how to keep tests away from real data.

## Run the tests

```sh
uv sync --frozen --extra browser --extra documents   # once per checkout; Windows leaves out documents
scripts/dev/checkpoint.sh --quick                    # before each commit
scripts/dev/checkpoint.sh                            # before a pull request: every gate below
```

`checkpoint.sh` makes a temporary `HAL_EMPLOYMENT_HOME`, sets `HAL_TEST_MODE=1` and picks a random
`HAL_EMPLOYMENT_PORT` between 20000 and 29999, so a gate never reads the owner's data or meets his server
on port 8737. To run the suite by hand, set the same three variables:

```sh
export HAL_EMPLOYMENT_HOME="$PWD/.tmp-home" HAL_TEST_MODE=1 HAL_EMPLOYMENT_PORT=24321
.venv/bin/python -m unittest discover tests        # without the browser tests that check HAL_TEST_BROWSER
HAL_TEST_BROWSER=1 .venv/bin/python -m unittest discover tests
```

The legacy tests from `tests/test_apply.py` drive Playwright's Chromium whatever `HAL_TEST_BROWSER` says;
new browser tests skip unless it is `1`.

## Gates

| Gate | Command | What it proves |
| --- | --- | --- |
| G1 | `python -m unittest discover tests` | Every unit and integration test passes |
| G4 | `python scripts/dev/golden_cli.py --check` | The command line prints what it printed at ef53088 |
| G8 | `check_ascii.py`, `check_ruff.py`, `check_personal.py` | Added lines are ASCII and ruff-clean; no personal string |


`check_ascii.py` and `check_ruff.py` look only at lines added since the base (the staged diff with
`--quick`). A line identical to one the same diff removes counts as moved, so a mechanical move does not
make old lines new. The code at ef53088 still has about 230 ruff findings and some non-ASCII lines; each is
fixed when its owner next edits the line. `check_ascii.py --tree` checks every file except that legacy list.

`check_personal.py` reads its patterns from `~/.config/hal-employment/personal-strings.txt` (never in the
repo) and prints file and line only. It is skipped when the list is absent and runs with `--personal`.

## Rules for tests

- Names are behaviour sentences: `test_a_closed_job_leaves_the_applications_in_progress`.
- People, employers and places are made up. Never use the owner's details, or anyone else's.
- No test touches the network, a real model or the owner's data. Fakes stand in for each.
- Shared helpers live in `tests/support/`; import them as `from support import ...`, because
  `unittest discover tests` puts `tests/` on the import path.

## The made-up world

`tests/support/world.py` builds a temporary home with an invented applicant, 17 jobs across every status,
history, emails, to-dos and one application. While a `World` is open, every legacy module points inside it:
the database, `config.CONFIG`, the inbox (a fake IMAP server), the vaults, the packs folder, the private
answers (always empty) and the local models (canned answers for the pack prompts, "unavailable" for the
rest). `HAL_EMPLOYMENT_HOME` points at its folder, so the new path functions resolve there too.

```python
from support.world import World

with World() as w:      # w.root, w.con, w.now
    ...
```

## Command-line goldens

`tests/goldens/cli/*.txt` hold what each command of gate G4 printed at ef53088, run in-process in a fresh
world (`tests/support/cli.py` lists the cases). `tests/goldens/score.json` holds today's `score()` and score
notes for every made-up job, for the scoring rewrite's compatibility test. Timestamps, the world's folder and
the greeting's part of the day are normalised.

```sh
.venv/bin/python scripts/dev/golden_cli.py --check            # what changed, as diffs
.venv/bin/python scripts/dev/golden_cli.py --record list-all  # only in a commit meant to change that output
```
