# Testing Hal Employment

How the tests run, what the checks prove, and how to keep tests away from real data.

## Run the tests

```sh
uv sync --frozen --extra browser --extra documents   # once per clone; Windows leaves out documents
uv run python -m playwright install chromium         # once; for the browser tests and printing
scripts/dev/checkpoint.sh --quick                    # before each commit
scripts/dev/checkpoint.sh                            # every check, with the browser tests on
```

`checkpoint.sh` makes a temporary `HAL_EMPLOYMENT_HOME`, sets `HAL_TEST_MODE=1` and picks a random
`HAL_EMPLOYMENT_PORT` between 20000 and 29999, so a check never reads your own Hal's data or meets your own
Hal's server. To run the suite by hand, set the same three variables:

```sh
export HAL_EMPLOYMENT_HOME="$PWD/.tmp-home" HAL_TEST_MODE=1 HAL_EMPLOYMENT_PORT=24321
.venv/bin/python -m unittest discover tests                      # browser tests skip
HAL_TEST_BROWSER=1 .venv/bin/python -m unittest discover tests   # browser tests on
```

On Windows (PowerShell), with git on `PATH` for the importer's tests:

```powershell
uv sync --frozen --extra browser                     # no documents extra on Windows
.venv\Scripts\python.exe -m playwright install chromium
$env:HAL_EMPLOYMENT_HOME = "$PWD\.tmp-home"; $env:HAL_TEST_MODE = "1"; $env:HAL_EMPLOYMENT_PORT = "24321"
$env:HAL_TEST_BROWSER = "1"
.venv\Scripts\python.exe -m unittest discover tests
```

Run it from a normal sign-in (a desktop session or a scheduled task with a password). An SSH session signed
in with a key cannot use Windows' data protection (DPAPI), so the secrets tests fail there with "Access is
denied".

CI (`.github/workflows/ci.yml`) runs every test, browser tests on, on Linux with Python 3.11 and 3.13 and on
Windows with Python 3.13. Each platform runs the suite in five shares, one job each, and
`scripts/dev/run_tests.py` spreads a share over the runner's CPUs in processes of a few tests. To run the
whole suite that way here, one process per CPU:

```sh
HAL_TEST_MODE=1 HAL_TEST_BROWSER=1 uv run python scripts/dev/run_tests.py   # --share 2/5 runs one share
```

## What the checks prove

`checkpoint.sh --quick` checks the staged lines (ASCII, ruff, personal strings) and runs the tests without
the browser. The full run checks the lines added since the base, turns the browser tests on, and then runs
these groups again on their own, so a failure names the promise it broke:

| Check | Tests | What it proves |
| --- | --- | --- |
| UI smoke | `tests/ui/test_shell.py` | Every page of the app draws at both window sizes with no errors and passes the axe accessibility check |
| Command-line goldens | `scripts/dev/golden_cli.py --check` | The command line prints what it printed before the app was added |
| Privacy | `tests/test_ai_guard.py`, `tests/test_settings_private.py`, `tests/test_autofill.py` (`PrivatePaths`, `WhyThisAnswer`), `tests/test_answers_routes.py` | Private answers never reach the AI and show only as saved; the guard blocks a canary value end to end |
| Never Submit | `tests/test_never_submit.py`, `tests/test_guard.py` | Practice forms count posts on the server, in Hal's window and through the helper, and the count stays 0; the only email Hal sends is the person's own summary |
| Settings | `tests/test_settings_registry.py` | Every setting read is registered, and Hal never sets the goal, scoring or CV choices by itself |
| Action kinds | `tests/test_action_kinds.py`, `tests/test_runner.py` | Every action kind, run from its example, ends done with a step and an Activity row with a why; actions survive a restart |
| Migrations | `tests/test_migrate.py`, `tests/test_backup.py` | Old databases migrate with counts kept and a backup made |

`check_ascii.py` and `check_ruff.py` look only at lines added since the base (the staged diff with
`--quick`). A line identical to one the same diff removes counts as moved, so a mechanical move does not make
old lines new. `check_ascii.py --tree` checks every file except a short list of older files.

`check_personal.py` reads its patterns from `~/.config/hal-employment/personal-strings.txt`, which is never
in the repository, and prints file and line only, never the match. It is skipped when the list is absent, so
any clone can run the checks. A string matches as a whole word, so a public handle that contains a listed name is not a hit. `--tree` scans every tracked file, `--staged` the staged lines and `--base REV`
the lines added since a commit.

## Rules for tests

- Names are behaviour sentences: `test_a_closed_job_leaves_the_applications_in_progress`.
- People, employers and places are made up. Never use a real person's details.
- No test touches the network, a real model or real data. Fakes stand in for each. `tests/support/offline.py`
  enforces the first: a lookup of any name but `localhost` and this computer's own raises `NoLookup`, and a
  stream connection to a public address raises `NoRoute`, so code under test sees what it sees on a computer
  with no network, at once instead of after a timeout. Loopback, private addresses and Unix sockets still work.
  A browser or other child process is not covered; serve its pages from `127.0.0.1`.
- A test passes in any company: CI runs a few tests per process, so a test never counts on an earlier one
  having imported a module or loaded an action kind.
- Shared helpers live in `tests/support/`; import them as `from support import ...`, because
  `unittest discover tests` puts `tests/` on the import path.
- Every test passes on Linux and Windows. Windows cannot remove a file that is still open, so a test closes
  its connections before its home goes: `with HalServer(), closing(db.connect()) as con:`, never an
  `addCleanup(con.close)` that runs after the home is removed. `fresh_home()` and `HalServer` wait for
  actions (`runner.idle()`), requests (`AppServer.stop()`) and tab watches (`driver.stop_watching()`) before
  removing theirs. An action still going 10 s after a `HalServer` test is stopped and fails the test by
  name, rather than as a `PermissionError` when its home goes.
- Enter the fakes a request may use before `HalServer`, so they come out after the server stops: a page's
  last request can land after the test ends.
- A test that starts an action waits for it with `support.actions.wait_for_actions()`, or for one action
  with `finished(action_id)`, which fails by naming the step an action is stuck at. `World` calls
  `wait_for_actions` first when it closes, because the actions still need its database.
- A browser test that must act before the app's effects have run calls `support.ui.late_effects(page)`
  before the page loads. It holds back the page's animation frames and 100 ms timers, as a busy computer
  does, which is when state that follows a prop or route shows up one render late. To wait for something to
  appear, use `AppPage.shows(locator, "what it is")`: its failure says what the page was still waiting for,
  with the errors and failed requests the page reported.
- Wait on a condition with a timeout (`runner.idle()`, `runner.wait()`, a deadline loop), never on a sleep.
- Build expected paths with `pathlib` (`str(Path("/usr/bin/flatpak"))`), and write a path into TOML as a
  literal string (`'C:\Users\...'`), because a basic string reads `\U` as an escape.
- A test that needs a time zone sets `HAL_EMPLOYMENT_TZ` (`UTC` needs no zone database), never `TZ` with
  `time.tzset()`, which exists only on Unix. File modes (`0o600`) are checked on Linux only.
- Skip a test on Windows only when the behaviour is Linux-only (systemd units, `/usr/bin/python3`), with the
  reason in the skip.
- A browser the test drives with Playwright while it waits on Hal has a thread of its own that keeps
  Playwright serviced between calls, as `HelperBrowser` in `tests/support/practice_site.py` does; call into it
  with `on_helper` or `in_tab`. `HelperBrowser` starts Hal's real server on a free port, loads the helper
  unpacked into Chromium and pairs it the way a person does: a signed-in window asks for a code, and the
  helper's browser opens Hal's `/pair` page. Playwright's sync API reads its driver only during a call, and on Windows a
  page's own form post otherwise waits for the test's next call.

## Docs tests

`tests/test_docs.py` checks the docs as code: every doc has a row in `docs/README.md`, every row and every
relative link points at a file, each how-to has a task title, numbered steps and a trailing comment on every
command and ends with a `| Symptom | Fix |` table, and the README's script options and `jobhunt` commands are
read back from each command's `--help`. Add a doc and its index row in the same commit.

## The made-up world

`tests/support/world.py` builds a temporary home with an invented applicant, 17 jobs across every status,
history, emails, to-dos and one application. While a `World` is open, every module points inside it: the
database, the settings, the inbox (a fake IMAP server), the record folder, the private answers (always
empty) and the local models (canned answers for the document prompts, "unavailable" for the rest).
`HAL_EMPLOYMENT_HOME` points at its folder, so the path functions resolve there too.

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

Two made-up graduates' sectioned records, a software graduate (Avery Quillon) and a history graduate
(Marnie Quillfeather), are in `tests/support/graduates.py`. `graduates.sectioned(con, who, folder)` builds
one through the sections as a person would on Your record: two CVs, a letter, a course with a starred
dissertation, other coursework, lecture material and a starred project (a code project made by
`tests/support/repos.py`, or an exhibition added by hand). Run it with a fake AI in place.
`tests/test_pack_graduates.py` writes a whole pack for each with the fake writer of `test_pack.py`, and checks
that only starred projects and jobs are told, that the dissertation is told under its title, that no
studied-only skill is said to be used, that the letter carries the person's voice, that the fit table cites
its sources, and that the pack makes at most 30 model calls (23 and 19 when measured on 2026-11-08).

## Command-line goldens

`tests/goldens/cli/*.txt` hold what each command printed before the app was added, run in-process in a fresh
world (`tests/support/cli.py` lists the cases), so the command line keeps working for scripts that use it.
`tests/goldens/score.json` holds `score()` and its notes for every made-up job. Timestamps, the world's
folder and the greeting's part of the day are normalised.

A command's help stays byte for byte, apart from options added since by name (`NEW_OPTIONS`) and the names of
new commands in the top-level help (`NEW_COMMANDS`). `cli.comparable()` takes out exactly those additions
before comparing.

```sh
.venv/bin/python scripts/dev/golden_cli.py --check            # what changed, as diffs
.venv/bin/python scripts/dev/golden_cli.py --record list-all  # only in a commit meant to change that output
```
