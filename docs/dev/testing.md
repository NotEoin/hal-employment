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

On Windows (PowerShell), with git on `PATH` for the importer's tests:

```powershell
uv sync --frozen --extra browser                     # no documents extra on Windows
.venv\Scripts\python.exe -m playwright install chromium
$env:HAL_EMPLOYMENT_HOME = "$PWD\.tmp-home"; $env:HAL_TEST_MODE = "1"; $env:HAL_EMPLOYMENT_PORT = "24321"
$env:HAL_TEST_BROWSER = "1"
.venv\Scripts\python.exe -m unittest discover tests
```

Run it from a normal sign-in (a desktop session or a scheduled task with a password). An SSH session signed in
with a key cannot use Windows' data protection (DPAPI), so the secrets tests fail there with "Access is denied".

## Gates

| Gate | Command | What it proves |
| --- | --- | --- |
| G1 | `python -m unittest discover tests` | Every unit and integration test passes |
| G2 | `tests/ui/test_shell.py` (browser) | Every route of the new app draws at both sizes with no errors, axe clean |
| G3 | `tests/test_classic.py` | Classic works through the new server |
| G4 | `python scripts/dev/golden_cli.py --check` | The command line prints what it printed at ef53088 |
| G5 | `tests/test_ai_guard.py`, `tests/test_settings_private.py`, `tests/test_autofill.py` `PrivatePaths` and `WhyThisAnswer`, `tests/test_answers_routes.py` | Private answers never reach the AI and show only as saved; the guard blocks a canary; a private answer's why says only that it is private, and the Saved answers tab never lists one; end to end |
| G6 | `tests/test_never_submit.py`, `tests/test_guard.py` (browser) | Hal never presses Submit: practice forms count posts on the server, in both modes; static checks; the only email is your own summary |
| G7 | `tests/test_settings_registry.py` | Every setting read is registered; `CONFIG` is read only where `LEGACY_ALLOWED` lists it; Hal never sets goal, scoring or CV keys |
| G8 | `check_ascii.py`, `check_ruff.py`, `check_personal.py` | Added lines are ASCII and ruff-clean; no personal string |
| G9 | `tests/test_action_kinds.py`, `tests/test_runner.py` | Every action kind, run from its example, ends done with a step and an Activity row with a why; actions survive a restart |
| G10 | `tests/test_migrate.py`, `tests/test_backup.py` | Old databases migrate with counts kept and a backup made |


`check_ascii.py` and `check_ruff.py` look only at lines added since the base (the staged diff with
`--quick`). A line identical to one the same diff removes counts as moved, so a mechanical move does not
make old lines new. The code at ef53088 still has about 230 ruff findings and some non-ASCII lines; each is
fixed when its owner next edits the line. `check_ascii.py --tree` checks every file except that legacy list.

`check_personal.py` reads its patterns from `~/.config/hal-employment/personal-strings.txt` (never in the
repo) and prints file and line only. It is skipped when the list is absent. The full gates scan the
whole tree (`--tree`) and `--quick` the staged lines (`--staged`); `--base REV` scans the lines added since a
commit.

## Rules for tests

- Names are behaviour sentences: `test_a_closed_job_leaves_the_applications_in_progress`.
- People, employers and places are made up. Never use the owner's details, or anyone else's.
- No test touches the network, a real model or the owner's data. Fakes stand in for each.
- Shared helpers live in `tests/support/`; import them as `from support import ...`, because
  `unittest discover tests` puts `tests/` on the import path.
- Every test passes on Linux and Windows (the CI runs both). Windows cannot remove a file that is still open,
  so a test closes its connections before its home goes: `with HalServer(), closing(db.connect()) as con:`,
  never an `addCleanup(con.close)` that runs after the home is removed. `fresh_home()`, `HalServer` and the
  legacy `Practice` wait for actions (`runner.idle()`), requests (`AppServer.stop()`) and tab watches
  (`driver.stop_watching()`) before removing theirs.
- Wait on a condition with a timeout (`runner.idle()`, `runner.wait()`, a deadline loop), never on a sleep.
- Build expected paths with `pathlib` (`str(Path("/usr/bin/flatpak"))`), and write a path into TOML as a
  literal string (`'C:\Users\...'`), because a basic string reads `\U` as an escape.
- A test that needs a time zone sets `HAL_EMPLOYMENT_TZ` (`UTC` needs no zone database), never `TZ` with
  `time.tzset()`, which exists only on Unix. File modes (`0o600`) are checked on Linux only.
- Skip a test on Windows only when the behaviour is Linux-only (systemd units, `/usr/bin/python3`), with the
  reason in the skip.
- A browser the test drives with Playwright while it waits on Hal has a thread of its own that keeps
  Playwright serviced between calls, as `HelperBrowser` in `tests/support/practice_site.py` does; call into it
  with `on_helper` or `in_tab`. Playwright's sync API reads its driver only during a call, and on Windows a
  page's own form post waited for the test's next call (CI, 2026-10-23).

## Docs tests

`tests/test_docs.py` checks the docs as code: a new doc goes in `docs/README.md` (every row and every relative
link must point at a file), each how-to has a task title, numbered steps, a trailing comment on every
command and ends with a `| Symptom | Fix |` table, and the README's script options and `jobhunt` commands
are read back from each command's `--help`. Add a doc and its index row in the same commit.

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

Made-up CV files for the CV reading tests (a humanities graduate, a technical graduate and a career
changer, each as PDF, Word and text, plus a picture-only PDF) are made by `tests/support/make_cv_fixtures.py`
into `tests/fixtures/cv/`. The files are the same byte for byte on every run; `--check` compares them with
the files on disk.

```bash
.venv/bin/python tests/support/make_cv_fixtures.py --check   # 0 when tests/fixtures/cv/ is up to date
```

## Command-line goldens

`tests/goldens/cli/*.txt` hold what each command of gate G4 printed at ef53088, run in-process in a fresh
world (`tests/support/cli.py` lists the cases). `tests/goldens/score.json` holds today's `score()` and score
notes for every made-up job, for the scoring rewrite's compatibility test. Timestamps, the world's folder and
the greeting's part of the day are normalised.

Help follows gate G4's rule. An old command's help stays byte for byte, apart from options added by
name (`NEW_OPTIONS`, today `serve --background`). The top-level help keeps every old line and may only gain
the names of new commands (`NEW_COMMANDS`, today `open`), whose own help goldens are recorded when they are
added. `cli.comparable()` takes out exactly those additions before comparing.

```sh
.venv/bin/python scripts/dev/golden_cli.py --check            # what changed, as diffs
.venv/bin/python scripts/dev/golden_cli.py --record list-all  # only in a commit meant to change that output
```
