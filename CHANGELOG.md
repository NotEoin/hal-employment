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

### Developer notes

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
