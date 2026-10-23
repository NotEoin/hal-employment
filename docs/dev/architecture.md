# Architecture of the core runtime

How the new server and its shared contracts fit together, for anyone adding an area or changing the core.

## Folders (`jobhunt/core/paths.py`)

Every folder is a function, resolved when called. Nothing in `jobhunt/` builds a data path from the code
folder; `tests/test_paths.py` fails on `ROOT / "data"` and similar outside `core/paths.py` and the importers.

| Function | Holds |
| --- | --- |
| `code_dir()` | The checkout or install: `.venv`, `extension/` |
| `config_dir()` | `install.json`, `local-api-key`, optional `paths.toml` |
| `data_dir()` | `jobs.db`, `private.toml`, `browser-profile/`, `apply/`, `cv/`, `backups/`, `logs/`, `state/` |
| `cache_dir()` | Anything Hal can make again |
| `documents_dir()`, `applications_dir()`, `helper_dir()`, `record_default()` | Folders the person opens |

`data_dir()` resolves in this order: `HAL_EMPLOYMENT_HOME` or `set_override()`; `config/paths.toml`; the
platform folder when it holds `.hal-data`; the legacy layout (`<checkout>/data` in a developer checkout that
still has `data/jobs.db`); else the platform folder. A folder named `*.imported-*` is refused.

Old module constants (`db.DB_PATH`, `browser.PROFILE`, `cv.OVERRIDES`, `pack.PACKS`, `record.VAULT`,
`server.SERVABLE` and others) still work: a module `__getattr__` (PEP 562) resolves them on each read, and
module code reads them through `_live(name)`, which honours a value a test assigned.

**Test guard.** With `HAL_TEST_MODE=1`, or under the unittest runner, and no override, every data path
raises `RealDataInTests`. Test modules import `support` before `jobhunt` (checked by
`test_every_test_module_imports_support_before_jobhunt`).

**Worktree guard.** In a linked git worktree (a second working copy, whose `.git` is a file), every data path with no
override raises `RealDataFromWorktree` when it would land in the account's real home, in test mode or not.
Set `HAL_EMPLOYMENT_HOME` to a folder inside the worktree before running any Hal command there.

## Time (`core/clock.py`)

`now_utc()`, `iso()` (`2026-10-22T09:00:00Z`) and `local_day()`. `HAL_EMPLOYMENT_FAKE_NOW` fixes the time in
tests. `db.now()` returns `iso()`; older rows end in `+00:00` and compare the same on 19 characters.

## Database (`jobhunt/db.py`)

`db.connect(path=None)` opens `paths.db_path()` with WAL, foreign keys and a 30 s busy timeout, then runs
`core.db.migrate.migrate` when `PRAGMA user_version` is below `LATEST`, and raises `DataTooNew` when above.
Only migrations make the schema: 0001 is the old baseline, so `db.py` holds no `CREATE` statement.

## Settings (`core/settings/`)

`settings.get(key)` answers the stored value or the registered default; an unregistered key is a
`KeyError`. `set`, `set_many` (all or nothing) and `reset` parse, check and store. One change is one
`BEGIN IMMEDIATE` transaction in `store.py`: the value row, a `settings_history` row, the bump of
`meta.settings_rev`, an Activity row with Undo (`{"type": "setting", "key", "value"}`) and, for `goal=True`
keys, a `goal_versions` row holding every `goal.*` and `scoring.*` value. A `setting` event follows. Only
values that differ from the default are stored. Each process caches the table per database file and re-reads
it when the revision moves (checked at most every 2 s). `@settings.cached(prefix)` memoises until a setting
changes; `on_change(prefix, fn)` subscribes and returns an unsubscribe function. `set(..., by="hal")` on
`goal.*`, `scoring.*` or `cvdocs.*` raises `PermissionError`. Undo from Activity goes through
`core/settings/settings_undo.py`, which calls `settings.undo(payload)`. The registry and catalogue are
`core/settings/registry.py` and `catalog/*.py`.

- **Secrets** (`secrets.py`): the keyring ("Hal Employment") first, every call with a timeout; without
  one, `config/secrets.json` with mode 0600 (DPAPI on Windows). A background read raises `SecretsLocked`
  instead of prompting. With a home override or in tests the system keyring is never used.
- **Private answers** (`private.py`): the `[applicant]` table of `private.toml`, rewritten atomically
  with mode 0600. Every reply says only saved or not saved. `value_for_form` (only in `autofill.py`) and
  `values_for_guard` (only in `ai/guard.py`) return values; an AST test holds them there.
- **Config shim** (`jobhunt/config.py`): `CONFIG` is a read-only mapping over live settings. The
  checkout's `config.toml` is read only in the legacy layout. Renamed values read back in the old words
  (`helper` reads as `extension`); `signed_in_apply` is always `[]`; `ntfy_topic` comes from the secrets.
- **Gate G7** (`tests/test_settings_registry.py`): every key read is registered, and `CONFIG` is read only
  where `LEGACY_ALLOWED` lists it. An owner who converts a module removes its entries; a stale entry fails.

## Discovery (`core/plugins.py`)

`plugins.modules(kind)` imports every module under `jobhunt/` named `<kind>.py` or `*_<kind>.py`, in dotted
order, for the kinds `routes`, `settings`, `actions`, `health`, `home`, `search`, `schedule`, `undo` and
`tasks`. A module that fails to import is logged and kept in `plugins.FAILED` for the health page.
`@plugins.on_start` registers work the server runs once at start. Data tasks are also found in `actions`
modules and in `jobhunt.core.answers_rescope`; the old to-do list `jobhunt.tasks` is never loaded as one.
Migrations (`core/db/migrations/`) and
command modules (`cli/`) are never loaded this way: the migration runner and `cli.register_all` find them.

## Server (`web/app.py`, `web/router.py`)

One `ThreadingHTTPServer` on `127.0.0.1` (never a setting). `AppHandler` subclasses the legacy handler and
answers, in order: Host check, `/healthz`, `/open`, `/pair`, the write check, the session or local key,
`/api/v2/*` through the router, `/classic/*`, the legacy `/api/*`, `/files`, `/pages`, `/doc/*`, then the app's
files from `static/app/` with the app's Content-Security-Policy.

An area adds routes in its own `routes.py` or `*_routes.py`:

```python
from jobhunt.web.router import NotFound, Request, route

@route("GET", "/api/v2/jobs/{job_id}")          # auth="session" by default
def job(req: Request) -> dict:
    row = req.con.execute("SELECT * FROM jobs WHERE id = ?", (req.params["job_id"],)).fetchone()
    if row is None:
        raise NotFound("That job is not in Hal's list.")
    return dict(row)
```

A dict or list is sent as JSON and `None` as `{"ok": true}`. Errors become `{"error": {"code", "message",
"field", "fix"}}`: `SettingError` 400 with `field`, `LookupError` 404, `PermissionError` 403, `RuntimeError`
and `ValueError` 409, anything else 500 with plain words, the traceback in `logs/server.log` and an Activity
row in area system. `local_only=True` answers 409 `remote` in a session from another computer.

## Sign-in (`web/auth.py`)

- `mint_open_url(remote=, remote_host=, port=, next=)` writes a one-time token (SHA-256 only, 120 s) to
  `open_tokens` and returns `http://127.0.0.1:<port>/open?t=...`. It works whether or not the server runs.
- `/open` swaps it for a `sessions` row and the cookies `hal_s_<id8>` and `hal_b_<id8>` (HttpOnly,
  SameSite=Strict), named for this install so servers on other ports never overwrite each other.
- Writes need `X-Hal: 1` and, when an Origin is sent, `http://` plus the request's Host. Any port is
  accepted, so a laptop tunnel works.
- `Authorization: Bearer <config/local-api-key>` lets the CLI, updater and launcher in without a window.
- `auth.register_verifier("helper", fn)` and `("pairing", fn)` are how the browser helper's keys plug in.

## Events (`core/events.py`, `web/sse.py`)

`events.publish(type, **ids)` numbers the event, keeps it in a ring of 1,000 and offers it to each
subscriber's bounded queue (a reader that falls behind gets `resync`). `/api/v2/events` streams them with a
`: ping` every 15 s, replays from `Last-Event-ID`, and allows four streams per session (a fifth sends the
oldest `bye`). `sse.DbPoller` publishes `activity`, `action_steps` and `alerts` rows written by other
processes, once a second. Payloads carry ids, never private data.

## Actions and Activity (`core/runner.py`, `core/activity.py`)

- An area registers a kind with `@runner.action(kind, area=, label=, why=, priority=, resumable=,
  exclusive=, example=)`. `start()` runs it in the background and returns its id; `run()` runs it in the
  calling thread (the command line, scheduled children, data tasks). `Ctx` gives `step`, `used`, `changed`,
  `undo_with`, `waiting_for_you`, `checkpoint` and `resumed_from`.
- Two pools: user (4 threads, priorities 0 and 1) and background (2, priority 2). One running action per
  subject unless `replace=True`.
- Bookkeeping writes go through one writer thread with its own connection, so a caller holding a write
  transaction never waits on itself; reads in the same process call `runner.flush()` first.
- Each live action holds `state/actions/<id>.lock`. At server start `recover()` marks only rows whose lock
  is free as interrupted ("Hal closed before this finished"), so a command-line run beside the server is
  left alone. A stop from another process is read at the next checkpoint.
- Classic's API stays (`run_action`, `stop_action`, `_checkpoint`, `_say`, `ACTIONS`, `POOL`); classic
  actions are kept as kind `classic.<name>`.
- `activity.record()` refuses an empty why; every text field passes `ai.guard.contains_private` and is
  replaced by "(left out: it held one of your private answers)" on a match. `@activity.undo_handler(type)`
  lives in `undo.py` or `*_undo.py` modules.
- `GET /api/v2/status` shows the most urgent action: one waiting for the person, then the lowest priority
  number, then the newest. `/activity`, `/actions` and their stop, undo and carry-on routes are in
  `core/activity_routes.py`.
- Data tasks (`core/db/tasks.py`): each is an action kind of the same name at priority 2, run once in
  migration order at server start; a failed task is tried again at the next start. `run_now(name)` is for
  the importers.
- Gate G9 (`tests/test_action_kinds.py`) runs every kind from its `example` in the made-up world: it must
  end done, take a step and leave an Activity row with a why.

## Applying: Hal never presses Submit (`apply/guard.py`)

- `guard.never_press(control, page, kind)` refuses Submit, Send, Finish and Confirm-and-submit words, Apply
  on a form or signed-in page, and any submit-type control (a `<button>` with no type in a form included)
  unless it carries Next words on a page that is not final. When in doubt the page counts as final. A
  refusal raises `WouldSubmit` and writes a `site_events` row `would_submit`. `content.js` `neverPress`
  mirrors it for the helper.
- Both engines guard every press, press only Next on a form, never press Enter, and type a line break bound
  for a one-line box as a space. For each command that acts on a page, a block in the page's own world
  cancels submits and the form's own send methods, letting through only the vetted Next control
  (`data-hal-allow`).
- On the last page the driver hands over instead of sending (`_hand_over`): state ready with "Check the
  marked answers, then press Submit on the employer's site yourself.", `handed_over_at` set, the form
  brought forward, and a watch for the site's thanks. Emailed codes are typed (`type_code`); the person
  presses the button. The legacy `submit` route answers 410.
- Gate G6 (`tests/test_never_submit.py`, `tests/test_guard.py`): practice forms count posts on the server,
  in Hal's window and with the helper, and the count stays 0.

## Applications: one approval flow (`apply/actions.py`, `apply/routes.py`, `apply/queries.py`)

- `apply/actions.py` holds the rules as plain functions and the action kinds that do the slow work.
  "Apply with Hal" starts `docs.prepare`. `approve_documents` keeps the person's edits, stamps
  `docs["approved"]`, saves through `documents.save` (which rewrites the numbered application folder,
  `docs/folders.publish`), fixes the mode, session and helper the form is filled with, and starts
  `apply.fill`. Editing documents after a fill marks the form out of date. `discard` keeps each reason
  apart; `restore`, `sent_myself`, `change_status` and `add_note` are the rest of the board's buttons.
- The kinds `apply.fill`, `apply.apply_edits`, `apply.next_page`, `apply.draft_rest`, `apply.adopt`,
  `apply.match_tab` and `apply.look` wrap the driver, so each run has steps, Stop and an Activity row.
- `apply/routes.py` serves the Applications routes of API v2 and only calls those rules.
  `apply/queries.application` builds the application page: the steps, previous and next, the documents,
  the form's fields with where each answer came from, and the Sent timeline. A private answer leaves the
  page API only as "Filled in from your private answers".
- Both modes show the same words for the form (`driver.form_words`), and the last page hands over as in
  the section above. `apply/home.py` and `apply/search.py` add applications to Home and to Ctrl K.

## AI contract (`ai/base.py`, `ai/fake.py`, `ai/prompts.py`)

`ChatRequest`, `ChatResult`, `Status`, `Backend`, `LLMUnavailable` (with `kind`), `Limited` and
`PrivateLeak`; old role names map through `role_for`. `FakeBackend` answers by purpose or `re:` pattern and
logs every request. `prompts.text(id, **values)` reads `ai/prompts/<id>.txt` (header lines `# name:`,
`# used:`, `# needs:`), prefers the person's edit in `prompt_overrides`, and fills only `{word}`
placeholders, so JSON examples keep their braces.

`jobhunt.ai.chat(...)` runs, in order: the private-answer guard (`ai/guard.py`, free-text
answers of 8 or more characters, option words never searched for), the allowance (`ai/budget.py`: the
never-online switch, the pause after a Claude limit, and for Claude only the keep-free hours and daily cap),
the GPU gate (`ai/gpu.py`, local models only), the backend under `ai.parallel` turns served lowest priority
first, then one `ai_usage` row of sizes only. Backends: `ai/ollama.py`, `ai/openai_compat.py` and
`ai/claude_cli.py` (prompt on stdin, system prompt in a file deleted after the call, tools off).
`ai.provider` is stored once, at the first server start or first AI call, and never changes because Ollama
stopped. In a test run the backend is a made-up AI that is never ready. `llm.py` and `gpu.py` are thin
shims; old roles `main`, `writer`, `fast` map to `checker`, `writer`, `quick`. One classifier,
`autofill.private_question(field)`, keeps private questions out of every AI request.

## CV store (`cvstudio/model.py`, `cvstudio/store.py`, `jobhunt/cv.py`)

- `cvstudio/model.py` is schema 2: every section, entry and line has a stable id. `apply` changes a document
  by target (`""` is the whole document), `diff` compares by id (a swap counts as one move), and
  `to_v1`/`from_v1` keep the printable `cv.json`. Fields schema 2 has no place for travel in `extras`.
- Versions never change after they are written. `store.new_version` adds one under `BEGIN IMMEDIATE`, so two
  saves at once get two numbers, and publishes the event `cv`. A version made with `job_id=` belongs to that
  job: it is kept out of the main list and never becomes Hal's main copy.
- The bin keeps a version for 30 days. It is emptied lazily (`purge_bin`, whenever versions are listed),
  and never removes a version an application or job still points at.
- Files are kept read-only as `cv/files/<sha256>.<ext>`; `file_bytes` checks the SHA-256 on every read and
  raises `CVError` when it no longer matches. `set_baseline_file` makes a kept file "your original file".
- `jobhunt/cv.py` is a compatibility layer over the store for Classic and the old modules (`load`,
  `for_editing`, `save_base`); a classic save becomes a version. Nothing reads the website folder any more.
- The data task `cv.import_v1` (after migration 11) imports today's `cv.json`, each overrides snapshot and
  the overrides as versions. The task loader does not look for a module named `import_v1`, so the task is
  registered by importing it from `cvstudio/legacy_actions.py`. Text it could not
  place goes into `extras.not_found`, which shows on Home and My CV until a later version holds it again.

## CV reading (`cvstudio/parse.py`, `parse_actions.py`, `parse_routes.py`)

- `POST /cv/upload` does the cheap checks (10 MB, 6 pages, kind, a PDF with no text layer), keeps the file
  with `store.add_file(..., role="baseline")` and starts `cv.parse` (subject `cv.parse`, replacing a reading
  still running). The action makes the file the baseline, then version "Your file: <name> (never changed)"
  from Hal's own reading, then asks the AI (`cv_parse`, role checker, priority 0) and makes "Hal read your
  uploaded CV into sections" only when the AI's reading differs.
- A field says "Check this" when its value is not in the file's normalised text, the AI's confidence is
  under 0.6, the year has two digits, or a field a CV normally has is missing. With no AI, a `PrivateLeak` or
  an empty reply, Hal's own reading is kept and a step says why.
- Report rows name one field each (`contact/<key>`, `s:<section>/e:<entry>/<field>`, `.../b:<bullet>`). A
  correction makes "You corrected N things Hal misread". `GET /cv/parse-report` answers an empty report before
  any CV is added, so no page meets a 404.

## CV types and checks (`cvstudio/types.py`, `cvstudio/checks.py`)

- Built-in types are `cvstudio/types/*.toml`; copies are rows in `cv_types`, edited as TOML, and a mistake is
  reported with its line. Editing a built-in makes "<type> (yours)" first. `suggest(goal, doc)` picks a type by
  career stage, subject and roles, with one sentence why. `arrange` and `outline` show a CV under a type, so
  comparing two types needs no AI.
- `checks.scanner` gives the scanner score with fixed weights (30/20/15/15/10/10) and no AI, from the printed
  PDF when given one, else the document's own text. `type_checks` runs the type's checks by id (weight 0
  switches one off). `for_version` caches results in `cv_checks` and writes `cv_versions.scanner_score`.
  `recruiter_read` is the AI's opinion and part of no score.
- `cvscore.keywords` takes the type's keywords and aliases; Classic keeps the old list.

## Looks and printing (`cvstudio/render.py`, `printer.py`, `export.py`)

- A look is `cvstudio/looks/<id>/{look.toml, cv.css, letter.css}` over `looks/base.css`. `render.render_cv_html`
  draws the page; the type sets order, titles and limits, and the person's Sections layout wins. The page
  holds no script and carries its own Content-Security-Policy (`render.POLICY`) in a meta tag; the preview route
  sends the exact bytes the printer prints, with the same policy as its header (`HtmlResponse.policy`).
- `printer.to_pdf` prints with Microsoft Edge on Windows, Playwright's Chromium on Linux, and an ordinary
  Chromium, Chrome or Edge only when Playwright is missing; never a snap or flatpak browser. 60 s timeout, and
  the whole process tree is ended when it runs out. Every PDF is checked (opens, page count, name, first
  section title). When no browser can print, `cvpdf.draw_view` makes the simple PDF and Activity says why.
- `export.py` gives PDF, Word (stdlib WordprocessingML), plain text, JSON Resume 1.0.0 and the website's
  `cv.json`. PDFs are cached in `cache/cv-print/` by the page's hash.
- Keeping an uploaded Word file's own design is not built yet. The later approach: anchors from
  the parse, run-preserving replacement, refusal words, PDF only through Word or LibreOffice.

## CV suggestions (`cvstudio/suggest.py`, `tailor.py`, `fresh.py`)

- `suggest.py` reviews one section at a time in the type's order, then the whole CV. Spelling, symbols,
  numeric dates, banned and never-use phrases, limits, empty sections and order need no AI; the `cv_suggest`
  prompt may add at most one suggestion per section after them.
- The guards run on every suggestion: the target exists and is unlocked, `before` matches the text there now,
  numbers, years and qualifications Hal cannot find become `[how many?]`, `[when?]` or `[which?]`, no new
  banned phrase, the fingerprint (sha1 of target, trigger id and first after) is not one the person left, and
  the type's limits hold.
- Accept makes a new version (`origin="improved"`, `by="you"`); Undo and Accept all are versions too. Leaving
  a suggestion keeps the reason; "Remember this" is a `cv_preferences` row.
- `tailor.for_job` (No, A little, More) keeps each change as a model change in `job_cvs`, and rebuilds the
  job's CV from the base version plus the changes still on. Sending the original file forces No.
- The whole CV is the target `cv` in a suggestion (`suggest.WHOLE_CV`) and `""` in the model
  (`suggest.model_target`). `recheck` touches only rows still pending, so a decision made while the tab
  reloads is kept.
- `fresh.py` builds a CV of the chosen type from the interview answers and turns failing checks into
  suggestions.

## Names (`core/naming.py`)

`file_name("cv" | "letter")` fills the `cvdocs.*file_name` template with `you.name`; `folder_name(n, company,
title)` makes `0042 Company - Title`. Both fold to ASCII and drop characters Windows refuses.

## App shell (`web/static/app/`) [6]

Conventions for page and dialog owners:

- A page module exports its component under the name in `core/router.js` ROUTES (`Home`, `Jobs`,
  `JobPage`, `Apps`, `AppPage`, `Messages`, `CvStudio`, `Docs`, `Record`, `Activity`, `Memory`,
  `Settings`); it gets the route's arg as `arg` (already decoded). Its first heading is `Head` (or an
  `h1`), which gets focus on each route change.
- When a stub is replaced, delete both the `/* STUB: ... */` line and `export const STUB`; the rail
  then shows the page in a person's own install.
- A dialog is `dialogs/<kind>.js` with a default export; set `.wide`, `.drawer` or `.label` on the
  component as needed and start it with `DlgHead`. Open it with `openDialog(kind, props)`; props arrive as
  `d`. `ask({title, text, yes, no, danger})` returns a promise of true or false.
- Page keys (J, K, A, S, N, [ and ]) are claimed with `useKeys({ j: fn, ... })`; nothing fires while
  typing or while a dialog is open.
- Server data: `useApi(path, { on: [ev => ...] })`; events in `core/events.js` `stalePaths` already
  reload the paths each event changes.
- Words: text for Advanced and Details panels goes in a literal preceded by `/* details */`, which the
  words test skips.
- Setup steps default-export `Step`; `setup/setup.js` exports `Setup` (drawn over any page).
- Settings, Advanced calls `setTheme("light"|"dark"|"system")` and `setSimpleNav(bool)` from
  `core/store.js`.

In browser tests, wait on selectors, never on Playwright's `wait_for_function` with a string: its polling
evaluates the string, which the app's Content-Security-Policy refuses. `tests/support/ui.py` has the route
list, the sizes, the axe check and the sideways check.
