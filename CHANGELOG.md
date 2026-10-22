# Changelog

Each version has two parts. "What's new" is in plain words and is shown on the Updates page.
"Developer notes" is for people changing the code.

## 2.0.0 (in progress)

### What's new

- A new app window: a menu down the left, a line at the top that says what Hal is doing (with Why? and
  Stop), search or jump with Ctrl K, and the list of shortcuts with `?`.
- Light and dark looks, a simple menu (five items and More), and a screen that says to open Hal from its
  shortcut when the window is not signed in.
- Pages that are still being built say so, and are left out of the menu.
- Hal Employment opens through its shortcut with a one-time link, and each window is signed in. A web page
  in any browser cannot read or change anything in Hal.
- The classic screens are still there, at `/classic/`.
- Hal's records now carry a version number. Before Hal changes them, it makes a checked copy in the
  `backups` folder.
- Hal never presses Submit, Send or a final Apply, in any mode. On the last page Hal says "Check the marked
  answers, then press Submit on the employer's site yourself.", brings the form forward and watches for the
  site's thanks; the job moves to Applied when the site confirms it. In Classic the last page's button is
  "Approve and fill in".
- On a job board you are signed in to, Hal waits for you to press Apply, then fills the form that follows.
  When a site emails a code, Hal types it into the box (with "Read sign-in codes" on); you press the button.
- Private questions on application forms (disability, adjustments, guaranteed interview, caring) are never
  sent to the AI, never guessed and never remembered. With a saved private answer Hal types it in; without
  one it leaves the question to you with "Answer this yourself". Private answers show only "Saved" or "Not
  saved", and Hal stops any AI request that would include one.
- Every test invitation now brings the same to-do: "Check the test invitation for anything you want to ask
  before you start".
- Every change to a setting is kept in its history, with Undo in Activity. Changing what you are looking for
  keeps a goal version as well. `python -m jobhunt settings get`, `set` and `list`.
- Passwords and keys are kept in your desktop keyring or Windows Credential Manager, else in a file only you
  can read. Hal never asks for your keyring password from the background.
- Everything Hal does is kept in Activity with why it did it, and survives a restart. Work cut short by a
  restart is marked "Hal closed before this finished" and can carry on. The line at the top shows the most
  urgent thing Hal is doing, with its step, Why? and Stop.
- Hal can use the AI on this computer (Ollama), another local server that speaks the OpenAI format, or your
  own Claude subscription through Claude Code. Which AI Hal uses never changes by itself. "Never use online
  AI" refuses Claude and any server outside your home network. Claude use follows your daily limit and the
  hours you keep it free.
- Your edits to Hal's instructions are kept, can be reset, and say when Hal's original has changed.
- Hal scores jobs against your own goal: your places, roles, the kinds of role you chose, your working
  pattern and your lowest pay. Nothing about any one person is built in any more. Every job keeps its
  reasons.
- Internships count as real jobs: a paid internship you chose no longer loses 10 points. Unpaid roles stay
  hidden unless you switch on "Show unpaid roles". Schemes for current students only are hidden once you
  have finished your course, and Hal says why. Pay by the hour, day, week or month is compared per year. A
  part-time or fixed-term job you did not choose costs a few points instead of being hidden.
- `python -m jobhunt import` moves your data from the old checkout into Hal Employment's own folder, with a
  dry run first and `--undo` to go back; `migrate` and `backup` update and save the database by hand.

### Developer notes

Settings, actions, AI, job finding, applying and the import (2026-10-22). How the pieces work is in
[docs/dev/architecture.md](docs/dev/architecture.md).

- Gates: `checkpoint.sh` now also names G5 (privacy: the guard, the store and the form-fill exclusions),
  G6 (never Submit, browser), G7 (settings) and G9 (action kinds). All of G1 to G10 run; G8 without the
  personal scan.
- Settings: one transaction per change with history, Undo and goal versions; `core/settings/secrets.py`
  (keyring, else `config/secrets.json` 0600; never the system keyring with a home override or in tests);
  `core/settings/private.py` (saved or not saved only; `value_for_form` and `values_for_guard` allowed only
  in `autofill.py` and `ai/guard.py`); `config.CONFIG` is a read-only view over live settings and reads the
  checkout's `config.toml` only in the legacy layout; `/api/v2/settings`, `/secrets`, `/private` routes;
  `cli/settings.py`. Undo from Activity goes through `core/settings/settings_undo.py`.
- Actions: `core/runner.py` (`@action`, `start`, `run`, `stop`, `recover`, `carry_on`; one writer
  thread; a lock file per live action), `core/activity.py`, `core/status.py`, the Activity, actions and
  status routes, and the data-task runner (`tasks.run_due`, `start_due`, `run_now`). Classic's runner API
  is kept; classic actions are persisted as `classic.<name>`.
- AI: `jobhunt.ai.chat` and `chat_json` over `ai/ollama.py`, `ai/openai_compat.py` and
  `ai/claude_cli.py`, with `ai/budget.py`, `ai/gpu.py`, `ai/guard.py` and `ai/provider.py`; `llm.py` and
  `gpu.py` are shims. `autofill.private_question(field)` is the one private-question classifier. The
  prompts store and `/api/v2/prompts` routes. `tests/support/fake_claude/` is the fake command line;
  `tests/support/private_answers.py` saves made-up answers in the real store.
- Find: `jobhunt/find/` (`goal`, `kinds`, `pay`, `rules`, `closed_check`, `actions`, `presets/`,
  `starters/`); `score.py`, `replenish.py` and `screen.py` read the goal live. The data task
  `find.rescore_all` runs once after migration 6. The CLI goldens were re-recorded with the default goal;
  the output format is unchanged, and `tests/test_score.py` proves the technical preset reproduces the
  ef53088 scores with an owner-like goal, apart from the internship change.
- Applying: `apply/guard.py`, the guarded presses and the page-world submit block in both engines,
  `_hand_over` in the driver, the legacy `submit` route answering 410, and the practice site that counts
  posts (`tests/support/practice_site.py`, `tests/site/practice/`).
- Import: `core/importer.py`, `core/answers_rescope.py` (data task `answers.rescope`), `cli/data.py`
  (`import`, `migrate`, `backup`), `scripts/dev/export_owner_prefs.py` and `tests/support/old_repo.py`. The
  import saves secrets just after the seal, so the store's history rows go into the new database and the
  old one is never opened; an old private answer that is not one of the store's choices is left for the
  person instead of stopping the import.
- Fixes joining the parts: the find action kinds take a step and run on the real runner (the runner stub is
  gone); the data-task tests set the real tasks aside; the made-up world answers the screen prompt; the
  `CONFIG` allow-list lost the entries the AI, find and applying code converted; the never-Submit check allows the
  shim's `signed_in_apply` line; the AI layer's fallbacks to `config.private()` are gone.

The foundations (2026-10-22): the uv project and dev checks, the core server, the app shell, and the
database schema and settings registry.

Project and tests:

- The project is a uv project: `uv sync --frozen --extra browser --extra documents`, then
  `scripts/dev/checkpoint.sh`. See [docs/dev/testing.md](docs/dev/testing.md). Python 3.13 by
  `.python-version`; 3.11 or newer is supported. Playwright is locked at 1.63.0 to match Chromium build 1243.
- `/usr/bin/python3 -m jobhunt <cmd>` from the checkout re-runs itself inside `.venv`, so the Hal tools keep
  working with the new dependencies.
- `checkpoint.sh` runs gates G1, G2, G3, G4, G8 and G10 (`--quick` before each commit). `check_ascii.py` and
  `check_ruff.py` check added lines only; a line the same diff removes elsewhere counts as moved.
- Command-line goldens, recorded at ef53088 on a made-up database, hold the output (`tests/goldens/cli/`,
  `scripts/dev/golden_cli.py`). Help follows G4's rule: only `open` and `serve --background` are new.
  `tests/goldens/score.json` holds today's scores of the made-up jobs.
- `tests/support/__init__.py` sets `HAL_TEST_MODE=1` and a temporary home, and every test module imports
  `support` before `jobhunt`. `tests/support/world.py` is the made-up world; `tests/support/api.py` runs a
  real server in a temporary home; `tests/support/fake_api.py` and `ui.py` serve and drive the app shell;
  `tests/support/old_db.py` builds made-up databases in the old shapes.

Code layout:

- Command bodies live in `jobhunt/cli/<area>.py`; a module that defines `register(sub)` adds commands.
  `jobhunt open [--port N] [--next PATH] [--print]` opens a signed-in window; `serve --background` logs only
  to `logs/server.log`.
- `jobhunt/web/server.py` is split into `core/runner.py`, `core/worker.py`, `apply/driver.py`,
  `apply/queries.py`, `cvstudio/legacy_actions.py` and `docs/legacy_actions.py`; `server.py` keeps the legacy
  Handler and re-exports every moved name. Classic's files moved to `static/classic/`.
- `jobhunt/core/paths.py`: every folder is a function resolved when called. `HAL_EMPLOYMENT_HOME`
  or `paths.set_override()` puts everything in one folder; the owner's checkout keeps its `data/` (the legacy
  layout) until his data is moved. In a test run with no override, data paths raise `RealDataInTests`.
- Old path constants (`db.DB_PATH`, `browser.PROFILE`, `SHOTS`, `SIGNED_IN`, `cv.DATA`, `OVERRIDES`, `BUILT`,
  `pack.PACKS`, `cvscore.WORDS`, `cvbuild.SOURCES`, `PREVIEW`, `record.VAULT`, `OLLAMA`, `MODEL`,
  `config.PRIVATE_PATH`, `server.SERVABLE`) are read live through a module `__getattr__`.
- `core/plugins.py` finds area modules by name (`modules(kind)`, `on_start`); it never loads migrations or
  CLI modules. Also `core/clock.py`, `core/events.py` and `core/naming.py`.
- New server `jobhunt/web/app.py`: router (`@route`), auth (open links, cookies `hal_s_<id8>` and
  `hal_b_<id8>`, local API key, `register_verifier`), event stream with replay and heartbeat, and
  `/api/v2/session`, `/session/end`, `/events` and `/search`. See
  [docs/dev/architecture.md](docs/dev/architecture.md).
- The app shell in `jobhunt/web/static/app/`: Preact 10.24.3, hooks and htm 3.1.1 vendored as ES modules with
  an import map (no Node or bundler), local fonts, `core/` (store, API client, shared event stream, routes,
  keys, formats), `ui/` (controls, dialog host, toasts) and `shell/`. Files owned by later packages start
  with `/* STUB: owned by Pxx */` and export `STUB`. `scripts/dev/vendor_web.py` fetches the vendored files
  pinned by SHA-256.
- AI contract: `jobhunt/ai/base.py`, `fake.py` (`FakeBackend`) and `prompts.py`.

Database and settings:

- `db.connect(path=None)` resolves its file when called and runs the migration runner; `db.now()` ends in
  `Z`. Only migrations make the schema.
- `jobhunt/core/db/migrate.py` runs the 17 migrations, each in its own `BEGIN IMMEDIATE`
  transaction, under the lock `<data>/state/migrate.lock`, after a checked backup
  `backups/jobs-v<from>-to-v<to>-<UTC>Z.db`. `PRAGMA user_version` is the truth; a newer database raises
  `DataTooNew`.
- `jobhunt/core/db/tasks.py`: `@data_task`, `due()` and `mark()`; the runner is in `core/runner.py`.
- `jobhunt/core/backup.py`: `snapshot(con, reason)` with retention.
- `jobhunt/core/settings/`: the registry (`Setting`, `register`, `load_all`, `visible`), lenient parsing
  (`parse.check`, `parse.describe`), the 3.7 legacy key table, the catalogue of 204 settings in 16 groups,
  and a minimal SQLite store (`get`, `set`, `set_many`, `reset`, `cached`, `schema`, `on_change`) with
  history and Activity rows.
