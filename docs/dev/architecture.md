# Architecture

How Hal Employment's server, its shared contracts and each area fit together, for anyone adding an area or
changing the core. How to run the tests is in [testing.md](testing.md); how Hal installs and updates itself
is in [release-and-update.md](release-and-update.md).

## The shape in one paragraph

`python -m jobhunt` is the one entry point: the command line (`jobhunt/cli/`) and the server (`serve`). The
server is the standard library's HTTP server on `127.0.0.1`, serving the app (`web/static/app/`, Preact and
htm with no build step) and the API (`/api/v2/*`, routes found by name in each area). Slow work runs as
persisted actions with an Activity row each, and the schedule starts them in-process. Data lives in the
folders of `core/paths.py`, never in the code folder. Each area is a subpackage (`find/`, `apply/`,
`cvstudio/`, `docs/`, `setup/`, `remote/`, `ai/` and so on) that plugs in through module names (Discovery,
below), so adding an area edits no core file.

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
platform folder when it holds `.hal-data`; an older checkout that still keeps `data/jobs.db` inside it; else
the platform folder. A folder named `*.imported-*` is refused.

Older module constants (`db.DB_PATH`, `browser.PROFILE`, `record.VAULT` and others) still work: a module
`__getattr__` (PEP 562) resolves them on each read, and module code reads them through `_live(name)`, which
honours a value a test assigned.

**Test guard.** With `HAL_TEST_MODE=1`, or under the unittest runner, and no override, every data path
raises `RealDataInTests`. Test modules import `support` before `jobhunt`
(`test_every_test_module_imports_support_before_jobhunt` checks it).

**Worktree guard.** In a linked git worktree (its `.git` is a file), every data path with no override
raises `RealDataFromWorktree` when it would land in the account's real home, in test mode or not. Set
`HAL_EMPLOYMENT_HOME` to a folder inside the worktree before running any Hal command there.

## Time (`core/clock.py`)

`now_utc()`, `iso()` (`2026-10-22T09:00:00Z`), `local()`, `from_local()` and `local_day()`.
`HAL_EMPLOYMENT_FAKE_NOW` fixes the time in tests and `HAL_EMPLOYMENT_TZ` the local zone (`time.tzset()` is
Unix only, so tests never set `TZ`). Code that shows or plans in local time calls `clock.local()`, never
`astimezone()` with no zone. `uk_zone()` is UK time on any computer: the zone database's Europe/London, or
on Windows, which has none without `tzdata`, the UK's own summer-time rule. `db.now()` returns `iso()`;
older rows end in `+00:00` and compare the same on 19 characters.

## Database (`jobhunt/db.py`)

`db.connect(path=None)` opens `paths.db_path()` with WAL, foreign keys and a 30 s busy timeout, then runs
`core.db.migrate.migrate` when `PRAGMA user_version` is below `LATEST`, and raises `DataTooNew` when above.
Only migrations make the schema: 0001 is the baseline, so `db.py` holds no `CREATE` statement. A migration
backs up `jobs.db` first. `LATEST` is 23. Migration 0018 (`0018_old_answers`) is the one destructive
migration: it drops the old `answers` table, because the saved answers live in `answers2` alone; every other
migration only adds. Migration 0021 adds `writing_edits`, the person's edits of Hal's drafts (How Hal learns,
below).

## Settings (`core/settings/`)

`settings.get(key)` answers the stored value or the registered default; an unregistered key is a
`KeyError`. `set`, `set_many` (all or nothing) and `reset` parse, check and store. One change is one
`BEGIN IMMEDIATE` transaction in `store.py`: the value row, a `settings_history` row, the bump of
`meta.settings_rev`, an Activity row with Undo (`{"type": "setting", "key", "value"}`) and, for `goal=True`
keys, a `goal_versions` row holding every `goal.*` and `scoring.*` value. A `setting` event follows. Only
values that differ from the default are stored. Each process caches the table per database file and re-reads
it when the revision moves (checked at most every 2 s). `@settings.cached(prefix)` memoises until a setting
changes; `on_change(prefix, fn)` subscribes and returns an unsubscribe function. `set(..., by="hal")` on
`goal.*`, `scoring.*` or `cvdocs.*` raises `PermissionError`: Hal never changes the person's goal, scoring
or CV choices by itself. The registry and catalogue are `core/settings/registry.py` and `catalog/*.py`.

- **Secrets** (`secrets.py`): the keyring ("Hal Employment") first, every call with a timeout; without one,
  `config/secrets.json` with mode 0600 (DPAPI on Windows). A background read raises `SecretsLocked` instead
  of prompting. With a home override or in tests the system keyring is never used.
- **Private answers** (`private.py`): the `[applicant]` table of `private.toml`, rewritten atomically with
  mode 0600. Every reply says only saved or not saved. `value_for_form` (only in `autofill.py`) and
  `values_for_guard` (only in `ai/guard.py`) return values; an AST test holds them there.
- **Config shim** (`jobhunt/config.py`): `CONFIG` is a read-only mapping over live settings for the older
  modules. `tests/test_settings_registry.py` checks that every key read is registered and that `CONFIG` is
  never read outside the shim.

## Discovery (`core/plugins.py`)

`plugins.modules(kind)` imports every module under `jobhunt/` named `<kind>.py` or `*_<kind>.py`, in dotted
order, for the kinds `routes`, `settings`, `actions`, `health`, `home`, `search`, `schedule`, `undo` and
`tasks`. A module that fails to import is logged and kept in `plugins.FAILED` for the health page.
`@plugins.on_start` registers work the server runs once at start. Data tasks are also found in `actions`
modules; the to-do list module `jobhunt.tasks` is never loaded as one. A data task whose module name does not
fit the `actions` pattern is listed in `TASK_MODULES` in `core/db/tasks.py`; `cv.import_v1`
(`cvstudio/import_v1.py`) is registered that way. Migrations (`core/db/migrations/`) and
command modules (`cli/`) are never loaded this way: the migration runner and `cli.register_all` find them.

## Server (`web/app.py`, `web/router.py`)

One `ThreadingHTTPServer` on `127.0.0.1` (never a setting). `AppHandler`, a subclass of the standard
library's `BaseHTTPRequestHandler`, answers in this order:

1. the Host check, which refuses a request that reaches the port under another name;
2. `/healthz`, `/open` and `/pair`;
3. the CSRF check for writes;
4. the session or the local key;
5. `/api/v2/*` through the router;
6. `/files` and `/doc/<job>/<cv|letter>`, an application's documents;
7. the app's own files from `static/app/`, under a strict Content-Security-Policy.

There is one server and one app. The older Classic screens, `/classic/`, the unversioned `/api/*` routes and
`web/server.py` are gone, so a new area adds routes under `/api/v2/` and a page under `static/app/`.

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

A browser that closes the page mid-reply is normal: any `ConnectionError` while replying (Linux
`BrokenPipeError`, Windows `ConnectionAbortedError`) is dropped without a log line or an Activity row.
`AppServer.stop()` stops serving, ends the event streams and waits for the requests in flight, because each
holds a database connection and Windows cannot remove an open file. The listen backlog is 64, not
socketserver's 5, because Windows refuses connections past the backlog and a page's first burst of requests
is larger than 5. `app.answering(port)` gives the connection 0.5 s, as Windows takes about 2 s to refuse a
closed loopback port.

## Sign-in (`web/auth.py`)

- `mint_open_url(remote=, remote_host=, port=, next=)` writes a one-time token (SHA-256 only, 120 s) to
  `open_tokens` and returns `http://127.0.0.1:<port>/open?t=...`. It works whether or not the server runs.
- `/open` swaps it for a `sessions` row and the cookies `hal_s_<id8>` and `hal_b_<id8>` (HttpOnly,
  SameSite=Strict), named for this install so servers on other ports never overwrite each other.
- Writes need `X-Hal: 1` and, when an Origin is sent, `http://` plus the request's Host. Any port is
  accepted, so an SSH tunnel from a laptop works.
- `Authorization: Bearer <config/local-api-key>` lets the command line, the updater and the remote launcher
  in without a window.
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
  `undo_with`, `waiting_for_you`, `checkpoint`, `resumed_from` and `progress(done, total, text)`.
- `progress` counts items (files read, pages fetched) in `actions.done_n` and `done_total`, beside the
  steps and without adding one, so a long action shows a bar. The Action shape carries `done_n`,
  `done_total` and `eta_s`: the time per item since the action started, times the items left.
- Two pools: user (4 threads, priorities 0 and 1) and background (2, priority 2). One running action per
  subject unless `replace=True`.
- Bookkeeping writes go through one writer thread with its own connection, so a caller holding a write
  transaction never waits on itself; reads in the same process call `runner.flush()` first.
- `runner.idle(timeout)` waits until no action of this process is queued or running, each has closed its
  connection and lock file, and every row has landed (tests call it before removing their home).
- Each live action holds `state/actions/<id>.lock`. At server start `recover()` marks only rows whose lock
  is free as interrupted ("Hal closed before this finished"), so a command-line run beside the server is
  left alone. A stop from another process is read at the next checkpoint.
- `activity.record()` refuses an empty why; every text field passes `ai.guard.contains_private` and is
  replaced by "(left out: it held one of your private answers)" on a match. `@activity.undo_handler(type)`
  lives in `undo.py` or `*_undo.py` modules.
- `GET /api/v2/status` shows the most urgent action: one waiting for the person, then the lowest priority
  number, then the newest. `/activity`, `/actions` and their stop, undo and carry-on routes are in
  `core/activity_routes.py`.
- Data tasks (`core/db/tasks.py`): each is an action kind of the same name at priority 2, run once in
  migration order at server start; a failed task is tried again at the next start.
- `tests/test_action_kinds.py` runs every kind from its `example` in the made-up world: it must end done,
  take a step and leave an Activity row with a why.

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
  presses the button.
- `tests/test_never_submit.py` and `tests/test_guard.py` fill practice forms that count posts on the
  server, in Hal's window and with the helper, and the count stays 0.

## Applications: one approval flow (`apply/actions.py`, `apply/routes.py`, `apply/queries.py`)

- `apply/actions.py` holds the rules as plain functions and the action kinds that do the slow work.
  "Apply with Hal" starts `docs.prepare`. `approve_documents` keeps the person's edits, stamps
  `docs["approved"]`, saves through `documents.save` (which rewrites the numbered application folder,
  `docs/folders.publish`), fixes the mode, session and helper the form is filled with, and starts
  `apply.fill`. Editing documents after a fill marks the form out of date. `discard` keeps each reason
  apart; `restore`, `sent_myself`, `change_status` and `add_note` are the rest of the board's buttons.
- The kinds `apply.fill`, `apply.apply_edits`, `apply.next_page`, `apply.draft_rest`, `apply.adopt`
  and `apply.match_tab` wrap the driver, so each run has steps, Stop and an Activity row.
- The form driver's API in `core/runner.py` is keyed by job (`run_action`, `stop_action`, `ACTIONS`), so
  one application has at most one run of the driver. Each run is persisted as an action of kind
  `form.<name>`, and the driver's `_say` adds a step to it.
- `apply/routes.py` serves the Applications routes and only calls those rules. `apply/queries.application`
  builds the application page: the steps, previous and next, the documents, the form's fields with where
  each answer came from, and the Sent timeline. A private answer leaves the page API only as "Filled in from
  your private answers".
- Both modes show the same words for the form (`driver.form_words`), and the last page hands over as in the
  section above. `apply/home.py` and `apply/search.py` add applications to Home and to Ctrl K.

## Notes about job sites (`jobhunt/sites/`)

- Every page trick the engines know is a note: a kind (one hook each), a scope (`global`, `ats`, `site`,
  `tenant`, `job`), a condition, an action and words for the person. `notes.for_page(url, ats=, job_id=,
  step=)` gives the page's `NoteSet`; the most specific active note of a kind wins, and two that disagree are
  a conflict named on the site's card. The built-in notes ship in `sites/seed.toml` with stable ids; a
  person's switch-off or rewording is a `site_notes` row naming the `seed_id`, so an update changes a
  built-in note's action without undoing their choice.
- `notes.site_key(url)` is the one rule for "which site is this": the registrable domain, except that
  Workday and Greenhouse keep the employer (`acme.wd3.myworkdayjobs.com`, `boards.greenhouse.io/acme`).
  Sign-in state is kept per employer in `sites.account`.
- "Something wrong here?" (`sites/report.py`, `sites/actions.py`) sends the person's words and a survey of
  the page (labels and button names only, never answers, never a private question) to the `site_note`
  prompt. A valid answer is shown as a draft with its scope choices and is used only after Save; with no AI,
  the words are saved at once as a `note` for the site.
- How notes are chosen, the hook table and the report flow: [Notes about job sites](site-notes.md).

## Engines and note hooks (`browser.py`, `jobhunt/extension.py`, `apply/driver.py`)

- Hal fills forms two ways: in the person's browser through the helper (`extension.py` on the server,
  `extension/` in the browser), the default, or in its own Playwright window (`browser.py`), a developer
  choice on Linux. Both read the page's `NoteSet` at
  the same hook points: `open_url`, `press_first`, `cookie_button` and `field_transform` (`city_only`,
  `digits_only`, `uk_phone`, `date:FORMAT`, `max_len:N`). The helper's hooks run on the server side, so
  `content.js` needs no notes. Each use is recorded with `notes.used`, and its trace line
  (`{note, field, what}`) is kept on the field and in `form["notes_used"]`, which the Form step lists.
- An upload is checked by finding the file's name in the input or on the page; a dropped file is
  `UPLOAD_FAILED`, never done. The cookie clicker never presses inside a form. Multi-select lists get every
  value. Google Forms frames are read. With "Open forms in: This window" (Preferences), the helper opens the
  tab beside Hal's, in its tab group, and brings it forward before each read, fill and press; watches
  (`peek`, `look`) never switch tabs.
- `Browser._run` services Playwright while its queue is empty, so a tab the page opens is followed at once
  and a person's own Submit in Hal's window is not held until Hal's next call. The page-world submit block
  counts its holders, so two commands in one tab never lift each other's block.
- Not built yet: forms in frames for the helper (`allFrames`), drop zones, review-page detection, and the
  note kinds `wait_redirect`, `form_frame`, `ignore_frame`, `needs_account`, `buttons`, `widget`,
  `upload_via` and `timing` (each is one more `notes.first(kind)` at its hook point).

## Saved answers and "Why this answer?" (`autofill.py`, `jobhunt/answers/`)

- `autofill.propose(..., notes=)` answers in this order: private and carer answers, site notes
  (`field_answer`, `field_skip`), saved answers (by scope), the profile, the CV and record, then the AI. A
  note never answers a question `autofill.private_question` matches. The driver passes the form's
  `NoteSet`, so the employer's own notes apply, not only the advert's.
- Every field carries `why` (the words under "Why this answer?") and `notes_used`. A private answer's why is
  only "From your private answers. These are never shown to the AI or saved in logs.", in the API and again
  in `app/form-step.js`. `autofill.by_you()` resets the why when the person's value replaces Hal's.
- `answers/saved.py` and `answers/routes.py` serve the Saved answers tab (`GET`, `PUT`, `DELETE /answers`):
  each answer with "Where may Hal use this?"; rows waiting for a choice are never used until the person
  picks; a private question is never listed, changed or deleted (404).

## AI (`ai/`)

`ai/base.py` holds the contract: `ChatRequest`, `ChatResult`, `Status`, `Backend`, `LLMUnavailable` (with
`kind`), `Limited` and `PrivateLeak`. `ai/fake.py`'s `FakeBackend` answers by purpose or `re:` pattern and
logs every request. `prompts.text(id, **values)` reads `ai/prompts/<id>.txt` (header lines `# name:`,
`# used:`, `# needs:`), prefers the person's edit in `prompt_overrides`, and fills only `{word}`
placeholders, so JSON examples keep their braces. No prompt names a person: everything about the applicant
comes from their settings.

`jobhunt.ai.chat(...)` runs, in order:

1. The private-answer guard (`ai/guard.py`): a prompt holding any saved free-text private answer of 8 or
   more characters raises `PrivateLeak`. Option words are never searched for, as they are common words.
2. The allowance (`ai/budget.py`): the "Never use online AI" switch, the pause after a Claude limit, and for
   Claude only the hours kept free and the daily cap on background checks.
3. The GPU gate (`ai/gpu.py`, local models only): wait while another program holds the graphics card.
4. The backend, under `ai.parallel` turns served lowest priority first.
5. One `ai_usage` row of sizes only.

Backends: `ai/ollama.py` (a local model through Ollama), `ai/openai_compat.py` (any local server that speaks
the OpenAI format), `ai/hal_gateway.py` (the Hal suite's model gateway: the same format plus the owner token
read from `ai.hal_token_file` at each call) and `ai/claude_cli.py` (Claude Code on the person's own subscription: prompt on stdin,
system prompt in a file deleted after the call, tools off). The default is Hal's gateway when it answers
with the stored token, else the local model on Linux when Ollama answers, else Claude. With Hal chosen a
down gateway is an error in words, never a call to Claude. Local calls also hold a gpu-light claim
(`ai/gpu.py`, `claim`: `interactive` for a call the person waits for, `batch` for background work) when the
`gpu_light` client is installed; the gateway makes its own claims, so `hal` never claims twice. `ai.provider` is stored once, at the first server start or first AI call, and
never changes by itself, because which AI sees the person's data is their choice. In a test run the
backend is a made-up AI that is never ready. One classifier, `autofill.private_question(field)`, keeps
private questions out of every AI request.

### Claude Code (`ai/claude_setup.py`)

Install runs Anthropic's own per-user script with no window and no admin rights (PATH in
`HKCU\Environment` on Windows). Sign-in is `claude auth login` in a small window; Hal reads only `claude
auth status` (JSON, kept 60 s) and never sees a password or token. In a test run the real installer never
runs: tests use `tests/support/fake_claude/`.

## CV store (`cvstudio/model.py`, `cvstudio/store.py`)

- `cvstudio/model.py` gives every section, entry and line a stable id. `apply` changes a document by
  target (`""` is the whole document), `diff` compares by id (a swap counts as one move), and
  `to_v1`/`from_v1` keep the printable `cv.json`.
- `jobhunt/cv.py` is the reader of the store in the `cv.json` shape: `load()` returns the version in use as
  the PDF builder, the packs and the matching code expect it, so none of them needs the store itself.
- Versions never change after they are written. `store.new_version` adds one under `BEGIN IMMEDIATE`, so two
  saves at once get two numbers, and publishes the event `cv`. A version made with `job_id=` belongs to that
  job: it is kept out of the main list and never becomes Hal's main copy.
- The bin keeps a version for 30 days. It is emptied lazily (`purge_bin`, whenever versions are listed),
  and never removes a version an application or job still points at.
- Files are kept read-only as `cv/files/<sha256>.<ext>`; `file_bytes` checks the SHA-256 on every read and
  raises `CVError` when it no longer matches. `set_baseline_file` makes a kept file "your original file".

## CV reading (`cvstudio/parse.py`, `parse_actions.py`, `parse_routes.py`)

- `POST /cv/upload` does the cheap checks (10 MB, 6 pages, kind, a PDF with no text layer), keeps the file
  with `store.add_file(..., role="baseline")` and starts `cv.parse`. The action makes the file the
  baseline, then a version "Your file: <name> (never changed)" from Hal's own reading, then asks the AI
  (`cv_parse`) and makes "Hal read your uploaded CV into sections" only when the AI's reading differs.
- A field says "Check this" when its value is not in the file's normalised text, the AI's confidence is
  under 0.6, the year has two digits, or a field a CV normally has is missing. The AI's reading is put in
  the rules' shape first, so the same file read by rules or AI gives the same ids. With no AI, a
  `PrivateLeak` or an empty reply, Hal's own reading is kept and a step says why.
- Report rows name one field each (`contact/<key>`, `s:<section>/e:<entry>/<field>`, `.../b:<bullet>`). A
  correction makes "You corrected N things Hal misread".

## CV types and checks (`cvstudio/types.py`, `cvstudio/checks.py`)

- Built-in types are `cvstudio/types/*.toml`; copies are rows in `cv_types`, edited as TOML, and a mistake is
  reported with its line. `suggest(goal, doc)` picks a type by career stage, subject and roles, with one
  sentence why. `arrange` and `outline` show a CV under a type, so comparing two types needs no AI.
- `checks.scanner` gives the scanner score with fixed weights (30/20/15/15/10/10) and no AI, from the printed
  PDF when given one, else the document's own text, so the same CV always gets the same score.
  `type_checks` runs the type's checks by id (weight 0 switches one off). `recruiter_read` is the AI's
  opinion and part of no score.

## Looks and printing (`cvstudio/render.py`, `printer.py`, `export.py`)

- A look is `cvstudio/looks/<id>/{look.toml, cv.css, letter.css}` over `looks/base.css`.
  `render.render_cv_html` draws the page; the type sets order, titles and limits, and the person's Sections
  layout wins. The page holds no script and carries its own Content-Security-Policy; the preview route sends
  the exact bytes the printer prints.
- `printer.to_pdf` prints with Microsoft Edge on Windows, Playwright's Chromium on Linux, and an ordinary
  Chromium, Chrome or Edge only when Playwright is missing; never a snap or flatpak browser, which cannot
  read Hal's temporary files. 60 s timeout, and the whole process tree is ended when it runs out. Every PDF
  is checked (opens, page count, name, first section title). When no browser can print, `cvpdf.draw_view`
  draws a simple PDF itself and Activity says why.
- `export.py` gives PDF, Word (WordprocessingML written with the standard library), plain text, JSON Resume
  1.0.0 and `cv.json`. PDFs are cached in `cache/cv-print/` by the page's hash.

## CV suggestions (`cvstudio/suggest.py`, `tailor.py`, `fresh.py`)

- `suggest.py` reviews one section at a time in the type's order, then the whole CV. Spelling, symbols,
  numeric dates, banned and never-use phrases, limits, empty sections and order need no AI; the `cv_suggest`
  prompt may add at most one suggestion per section after them.
- Guards run on every suggestion: the target exists and is unlocked, `before` matches the text there now,
  numbers, years and qualifications Hal cannot find become `[how many?]`, `[when?]` or `[which?]` (Accept
  refuses a gap, and a job's CV drops it), no I or we in the AI's wording when the type writes without them,
  no new banned phrase, the fingerprint is not one the person left, and the type's limits hold.
- Accept makes a new version; Undo and Accept all are versions too. Leaving a suggestion keeps the reason,
  and "Remember this" is a `cv_preferences` row.
- `tailor.for_job` (No, A little, More) keeps each change for a job in `job_cvs` and rebuilds the job's CV
  from the base version plus the changes still on. An AI change for a job only rewords: one that adds a
  result the CV does not have is dropped.
- `fresh.py` builds a CV of the chosen type from interview answers and turns failing checks into
  suggestions.

## Documents (`cvstudio/context.py`, `pack.py`, `docs/write_*.py`)

- `cvstudio.context.build(job=None)` is the one source of every document prompt's placeholders: the person
  (`you.*`, `goal.*`), the CV type's `[documents]` and `[writing]`, `cvdocs.*`, the profile block
  (`profile`, see Profile below) and the person's voice. Job facts are never placeholders, so a prompt
  reads the same for every job of one person.
- The voice (`writing/voice.py`, while `cvdocs.voice` is on) is read with no AI from the person's own
  writing: the finals of their edits (`writing_edits`), the answers they rewrote (`answers2` rows never
  reused on forms) and the CVs, letters, statements and essays in the record, found by the ingest's
  `doc_type` frontmatter or else by file name, outside private folders and never converted on the way. The
  voice card states measured values (median sentence length, contractions, British or American spelling,
  usual openings and words, words they take out of drafts); 2 or 3 exemplars of the closest kind are cut to
  80 words and 1,500 characters in all. Checker-list words the person uses themselves (`own_words`) are
  never flagged for them.
- One letter path, `pack.write_letter` (advert, record, fit map, letter, checks), serves the pack, the letter
  drafted at form time and the writing actions. The pack's `## Letter notes` section says Tone, Length,
  Evidence and Checked; the application page reads them back.
- The advert reading keeps only the questions the advert writes out, so a model's own note about questions
  is never answered. When the advert states no requirement, its main duties stand in, so the fit map is
  never empty. The letter greets the first person the advert names, else "Hiring Team", and never adds a
  title the advert does not give.
- `docs/write_routes.py` serves the document change, letter rewrite, interview prep and contacts routes;
  each starts its kind in `docs/write_actions.py` and returns `{action_id}`.
- Critique as a recruiter: `POST /applications/{id}/documents/critique` starts `docs.critique` (priority 0,
  subject `<job id>:critique`, so it never stops Hal writing or filling for the job). `writing/critique.py`
  reads the CV sent with the application and its letter against the advert: the writing checker's findings
  as line comments, then `cv_judge` (a six-second screen of the top `TOP_CHARS` in its own block, the
  advert's requirements mapped to quoted evidence before any grade, anchored 1 to 5 grades on nine criteria
  including Fit to advert and Voice) and `letter_judge` (seven criteria, among them Why them and Adds to the
  CV). The person's name, email, phone and town are replaced before the AI reads anything; a quote not in
  the document is dropped. The action's result is `{field, screen, requirements, documents: [{document,
  title, summary, grades, lines}], top_fixes, ai, note}`; nothing is changed. My CV's recruiter's read uses
  the same `cv_judge` prompt and parser.

## Writing (`jobhunt/writing/`)

- `rules.py` holds the rule set as data: each `Rule` has an id (T truth, L language, B bullets, C letters),
  the rule in plain words, the document kinds it applies to (letter, cv, answer) and a severity. `high`
  makes the writer revise its draft once (`pack.revise_once`, one call, never a loop); `high` and `medium`
  are listed under "Check before sending"; `low` is advice shown only in a critique. `FIELDS` are the field
  add-ons, each with the words that identify it and conventions per document kind.
- `check.py` is the deterministic checker for letters, CV lines and form answers: one-match words (L1),
  fashionable words two to a paragraph (L2), evaluative words by density (L3), filler, `-ing` tails,
  puffery, staged contrasts, stacked qualities, the other spelling, dashes, bold, chatbot words and
  placeholders, stock openings and closings, hedges, weak bullet openers, first person in CV lines, bullets
  over 35 words, a letter sentence that repeats a CV line, greeting and sign-off pairing ("Dear" and a name
  with "Yours sincerely", a team or "Sir or Madam" with "Yours faithfully"), numbers no source holds and
  length limits. It replaced `pack.CLICHES`; every word that list flagged is still flagged.
- `fields.py` resolves the field from the advert alone (title words count three times); an advert that shows
  none is the general field, never the person's, so a computing graduate's letter for a complaints role is
  not written as a tech one. The person's own field (CV type, roles, subject) is background only, and decides
  only when there is no job at all (a CV looked over on its own). The analyse step stores it on the
  analysis, and the letter, answers, form-filling and CV suggestion requests carry its conventions, plus
  the person's own field's background advice when the two differ. Conventions are in the request, never
  the prompt, since they depend on the job.
- Sources. The rules and conventions are Hal's own wording, drawn from UK careers guidance read on
  2026-11-05: Prospects (CVs, cover letters, example, academic and creative CVs), the National Careers
  Service's CV sections, Newcastle University Careers' CV booklet, GOV.UK's Success Profiles candidate
  overview, the NHS Business Services Authority's guide to supporting information in NHS Jobs, jobs.ac.uk
  career advice and Harvard FAS Career Services; and from what open CV tools check (Resume Matcher, Reactive
  Resume, OpenResume). The catalogues of machine-sounding patterns that informed the word lists are credited
  in THIRD_PARTY_NOTICES.md. Thresholds from these sources (35-word bullets, two A4 pages, about 250 words
  per behaviour example) are guidance; the tests check that Hal applies them, not that they are right.

### Web links for the record (`ingest/web.py`)

`web.fetch(urls, page_cap=None, progress=None)` returns a list of `Page` (url, title, Markdown text, kind
`page`, `repo`, `profile` or `doc`, meta, error). It never raises for a link: a link it cannot read gives
one `Page` with `error` in plain words. Every request has a 20 s timeout, a size cap and Hal's user agent.

- **Own sites and blogs:** read breadth first from the given page, on the same host and under its folder
  only, up to `record.web_pages` pages (default 30), where robots.txt allows. Assets are skipped, and a
  page is read once (fragment stripped, canonical link honoured). Markdown comes from MarkItDown when the
  documents extra is installed, else from a stdlib reader that keeps headings and lists. Both keep only
  `<main>` or the `<article>`s when the page has them, and drop the nav, header, footer and aside.
- **GitHub:** a profile link reads the profile and the person's own repositories (forks skipped) through
  the REST API; a repository link reads that one. Each repository gives its description, topics,
  languages, stars, homepage, last push and README. `record.github_token` (a secret) is sent when set. A
  used-up rate limit stops that link and the batch's later GitHub links with the reset time on the Page.
- **Google Docs and Slides:** read through the plain-text export address, which works only for a link
  shared with anyone; a private one asks the person to download it and drop the file in.
- **Instagram, LinkedIn, Facebook, X, TikTok and Threads** are never fetched: their Page says the site
  needs the person signed in.

`tests/test_ingest_web.py` serves a made-up site, GitHub API and Google export from 127.0.0.1.
## Reading files into the record (`jobhunt/ingest/`)

One `record.ingest` action (area documents, priority 2, `exclusive="kind"`, resumable) reads folders, zips,
the drop folder and web links into the record folder as Obsidian notes. Each item is a row of
`ingest_items` (migration 0019) with the stage it reached, committed as it ends, so after a restart the
runner starts the action again with the same batch and only pending items are read.

1. **Sources** (`sources.py`). Pasted links (`POST /api/v2/record/links`, at most 20, read by
   `ingest/web.py`), a folder on this computer (`POST /api/v2/record/ingest`, `local_only`), a zip
   uploaded through `POST /api/v2/record/files` (saved under `<data>/ingest/uploads/`; that route alone takes
   bodies up to `UPLOAD_MAX_BODY`, 256 MB), and the drop folder (`record.drop_folder`, default
   `<record>/+Inbox`), which the scheduled `record.sync` checks (`actions.queue_drop`). Zips unpack under
   `<data>/ingest/unpacked/<batch>/`: names that are absolute or climb out with `..` are refused, links
   skipped, members counted as they are copied against a per-file and a per-zip cap (stated sizes can lie),
   encrypted members named, and zips inside zips unpacked to depth 3. Each refusal is an item in state
   `error`.
2. **Discover** (`discover.py`). Size, SHA-256 and the kind by the first bytes. Junk folders (`.git`,
   virtual environments, `node_modules`, caches, and build folders inside a repository) are never entered;
   weights, programs, databases and fonts are left out by name. So are hidden folders and files, an AI
   tool's instruction files (`CLAUDE.md`, `AGENTS.md`), lockfiles and minified or bundled code
   (`discover.is_tool_file`). A file of zero bytes only is a cloud
   placeholder: `not_downloaded`, listed, never converted. A file with the same SHA-256 as one already read,
   in any batch, is a `duplicate` named once under "Also at" in the first copy's note.
3. **Group** (`group.py`). A repository (a `.git` folder or a manifest at its top) is one `repo` item,
   summarised from its README, docs headings, manifest, language mix and file tree, never file by file. A
   folder whose own files are mostly notes or slides (three or more) is a `course` unit: light notes per
   file and one summary note linking them.
   Copies of one project (`versions.py`, run by `engine.run` before reading) are one project when they
   share a git remote from `.git/config` (https, ssh and `.git` forms alike), or, when either has none,
   when their names match once copy marks such as "(2)", "-v3", "-final" or "Copy of" are removed (one
   name's words may hold the other's) and at least half the smaller copy's files, and two or more, share
   their paths, README and licence files not counted. The newest is the latest commit in
   `.git/logs/HEAD`, else the newest file, then more files; its time is kept in the item's `mtime`. Only
   the newest is read. Each other copy becomes `older_version` with `dup_of` the newest and no note, and
   `versions.record_earlier` lists them under "Earlier versions" in the newest copy's note. A newer copy
   of a project already noted (compared by its folder, or by the file tree in its note when a zip's copy
   is gone) is read over that note at the same path, so links still lead to it, and the copy read before
   becomes `older_version`. The same folder read again unchanged is a `duplicate`.
4. **Rank** (`rank.py`). CV, transcript, letters and statements, certificates, references, job papers,
   then reports and coursework, projects, writing, notes, slides, email, pictures and the rest, by words in
   the name, then the folders, then the start of a plain text file.
5. **Convert** (`convert.py`). `record.text_of`, the record's own conversion. An empty result is `no_text`
   and is never sent to a model, because a model given no text invents a document and its author.
6. **Understand** (`understand.py`). Rich documents get the `ingest_doc` prompt (role checker), cleaned in
   code: fixed categories and doc types, 12 to 30 tags, and every fact's quote checked against the text; a
   quote not found marks the fact "Check this" and the note `hal: needs-review`. An organisation or place
   that neither the text nor the file's path names is dropped, with its tag, so a guess gets no entity note.
   Light documents, and every document while the AI is unavailable, get tags from the path, headings,
   frequent words and years. The engine then judges the document's role on its own thread (`roles.py`):
   `own`, `taught`, `others` or `unsure`. The AI's "role" is taken for taught and someone else's; its
   "own" holds only when the text or path names the person (`roles.identity`: `you.name`'s parts,
   `you.other_names`, and usernames and emails from links and basics their CVs and letters gave). A
   repository's text lists its committers and remotes from `.git/logs/HEAD` and `.git/config`. Without the
   AI, slides and a course unit's files are `taught`, the rest `unsure`. `roles.allowed` keeps the facts
   each role may give: everything for `own` (contact details and links only from a CV or a letter), skills
   for `taught`, nothing otherwise. An `others` document gets state `not_mine`: no note, no copy, no facts,
   and `items.summary` names the first few. Facts go to
   `jobhunt.profile.store.add_found(con, kind, fields, evidence)` with evidence
   `{source_rel, quote, field, doc_type, verified, role}`; the ingest keeps no profile of its own. Reading a
   note again replaces its old facts (`store.replace_source`). A fact
   the store refuses (`ValueError`) is left out and logged, not the whole document. When the profile holds
   anything, the run ends by writing the Profile notes and publishing one `profile` event.
7. **Write and link** (`notes.py`). `Sources/<Category>/<Title>.md` with YAML-list frontmatter
   (`frontmatter.py`, which the record's reader also uses), entity notes in `Organisations/`, `Projects/`,
   `Courses/` and `Skills/` (`type: entity`, skipped by search) for what `notes.noted` keeps: the
   organisations, skills and projects of the facts a document gave, and the courses of own and taught
   documents, one note per module code (`entity_key`); no people or places. The text of code and data files
   is fenced (`FENCED_SUFFIXES`), so their brackets make no links. An `Index.md` per
   category, a wikilink on the first mention of each known title, and the original copied to
   `Attachments/Sources/` when `record.copy_originals` is on and the file is at most 100 MiB. The record's
   own conversion leaves the drop folder and `Attachments/Sources/` alone, so no passage is indexed twice.

Reading is split so the AI can work on several items at once: worker threads (`ai.parallel` of them, which
also caps the AI layer's own turns) only convert and describe (`engine._prepare`), and the action's thread
writes notes, entities, facts and rows in the items' own order, so the database and record have one
writer. A file whose SHA-256 matches one already with a worker waits for it and becomes its duplicate.
`engine.by_model` decides what the AI reads: rich rules always, every document but code and pictures when
`record.read_all` is on, pictures when `record.ingest_vision` is.

`POST /api/v2/record/ingest/again` (or `jobhunt ingest --again`) queues a `record.ingest` with `again`:
`engine.mark_again` moves the newest row of each document note written by rules (`model = 0`, not code or
pictures, file still there) into the new batch as pending. A pending row that already has a note is read by
the AI and its note rewritten at the same path (`notes.write_source(at=)`), carrying over the "Part of",
"Also at" and "Earlier versions" sections, so links still lead to it; when the AI does not answer, the note is left as it was and
the row stays `model = 0` for next time. `items.by_rules` counts those notes for the page's button.

Private files (`privacy.py`). In `engine._prepare`, after conversion and before any AI call,
`privacy.held_back` runs `privacy.reason(rel, text)` unless `record.privacy_check` is off or the row is
`allowed`; after the AI's answer it runs again with the reply's `sensitive` field as `said`. A reason sends
the item to `_finish` with no text, which sets state `private` and the reason in `ingest_items.private`
(migration 0023) and writes no note, no copy and no facts. The rules want a hit that checks out (NI number
or SSN beside its label, a passport machine-readable zone, sort code with account number, an IBAN by mod 97,
a card number by Luhn and card words or grouping, an NHS number by mod 11 beside "NHS", a private key body,
a token or password value that is not a placeholder) or a cluster of payslip or medical words; the
clusters and the password rule do not count in text with two or more teaching or coursework markers. A copy
of a private file in the same batch is its `duplicate` (`items.first_copy`). `items.private` lists every
batch's private rows not let in, newest first, as `{id, name, reason}` (`private` in the summary).
`POST /api/v2/record/ingest/private/allow` `{id}` queues a `record.ingest` with `allow`, whose action moves
the row into its batch as pending with `allowed = 1` (`privacy.requeue`); when the file is gone (a zip
member or upload, removed once read) it only sets `allowed` and answers `add_again`. `items.add` marks a new
row allowed when an allowed row has the same SHA-256 and the same path or `rel`, so a re-added zip's member
is read.

`GET /api/v2/record/ingest` returns the latest ingest: `state`, `done`, `total`, `eta_s` (this run's pace
only, so a resumed ingest is not counted as fast), counts by stage, state and category, the item in hand,
`not_downloaded` with the first paths, recent errors, `by_rules` and whether it was a read `again`. Web links are read by `ingest/web.py` when it is
present (`fetch(urls, page_cap=)`), imported lazily; without it the ingest says web links are not
available yet.
## Profile (`jobhunt/profile/`)

- The profile is the facts about the person that Hal finds in their record: jobs, education (with modules
  and marks), skills, projects, achievements, certificates, basics (name, email, phone, location) and links.
  Migration 0020 keeps them in `profile_items` (fields as JSON, a state and an origin), `profile_evidence`
  (the file inside the record, the quote, the field, the document type, what that file said and whether
  the quote was found in it) and
  `profile_conflicts`.
- The ingest calls `store.add_found(con, kind, fields, evidence)` for each fact. `merge.py` decides, in code,
  when it is an item already there: employers and institutions compared without case, "the", Ltd or word
  order; the same employer with overlapping or touching dates, or the same title, is one job; skills
  compared without case and through other names ("MS Excel" is "Excel"). A qualification is matched by its
  level (`merge.level`: bachelor, master, GCSE, Access and so on) and subject, not its wording, at
  institutions whose names hold one another ("Wexley University" is "The University of Wexley"); a BA and
  then an MA stay two. Certificate and achievement names that differ by a letter or two merge, never when
  their numbers differ ("Level 2", "Level 3"). An education fact naming a project, module or dissertation
  with no qualification level is refused, as is one whose subject is only a module code ("CSC1034") and a
  training course with no place of study ("CITI Program Training", found as a certificate instead). A job
  whose employer is only a kind of place ("bar", "shop") and that has nothing but a title is refused. Dates are stored as `YYYY-MM` or `YYYY` from UK ways of writing
  them ("May 2024", "05/2024", "2023-24", "Sept 2023 - present", "Summer 2023"). An unknown kind or field
  raises `ValueError` naming it. `store.rebuild_found(con)` merges every unchecked found item again from its
  sources after these rules change, leaving what the person confirmed, edited, hid or added.
- A found item (state `hal`) takes each field from its best-ranked source: transcript, then certificate,
  CV, letter and notes. An education item's modules come from its transcripts and certificates when any
  lists modules, else from its CVs, never from notes, slides or coursework; they are one per module code
  (`merge.module_code`: "CSC1032", or a bare "1032" when that code is already listed) with the fullest name
  and the best source's mark, and modules without a code merge by close name. An end date before this month
  (`merge.ended`) makes a job or course not current, even where another source said "present"; "present" with
  no end date stays current. Confirmed and edited items keep the person's values, and no later ingest changes
  them; a hidden item stays hidden when its file is read again. A note rewritten in place
  (`store.replace_source`) has its evidence replaced: each item it spoke for is worked out again, an unchecked
  found item nothing speaks for any more is deleted, and a confirmed item whose facts from that note changed
  goes back to `hal`, so the review shows it again; an edited item keeps its values. Only items added by hand
  can be deleted.
- `conflicts.py` lists what does not add up: two sources giving one field different values (each value
  with its files), only for fields that are facts (`CHECKED_FIELDS`: dates, grades, employers, job titles,
  qualification levels, issuers) and compared by `merge.value_key`, so case, punctuation, plurals, number
  words and common abbreviations are no conflict; an end before a start, two jobs not marked part-time
  that share more than a month, and a gap of more than `GAP_MONTHS` (6) months. Each conflict has a
  fingerprint, so finding it again keeps its row and a settled one stays settled.
- `profile/routes.py` serves `GET /api/v2/profile` (counts, basics, links, open and settled conflicts, each
  naming its items) and
  `GET /api/v2/profile/{kind}`, the person's edit, confirm, hide, add and delete, and
  `POST /api/v2/profile/conflicts/{id}/resolve` with the right value, the item to keep, or "keep them all".
  Its docstring gives every reply's shape; `tests/support/fake_api.py` has made-up replies in the same
  shapes, which `tests/test_profile_routes.py` compares with the real ones.
- `profile.context_block(con, limit)` is the "Profile" block for writing prompts: facts the person checked
  or edited first, then the ones Hal found, never hidden ones, no email or phone, and no found fact whose every
  quote was missing from its document (it waits for the person to confirm it). Each item is one line of at most
  `LINE_MAX` characters, cut at a whole list item, and one location is named. `pack._kept` puts it
  above the record's passages (`profile.writing.with_record`), so the fit map, letter, message and answers
  read it, and `cvstudio.context.build` gives it to every document prompt as the `profile` value.
- `profile.write_notes(folder)` writes the `Profile/` notes in the record folder (`Profile.md`,
  `Work history.md`, `Education.md`, `Skills.md`, `Projects.md`, `Inconsistencies.md`), each
  `type: dashboard` so search leaves them out. A note whose text has not changed is not written again.

## Sources (`find/sources_registry.py`, `find/detect.py`, `find/source_routes.py`)

- Every place Hal searches is a `sources` row. Built-ins come from `find/seed/sources.toml` and follow the
  scoring preset until the person switches one. `fetch()` touches only the network and raises `Skipped` with
  words for a site left out on purpose (no key, read from email); `record()` writes `source_runs` and the
  source's health.
- Queries come from `queries(source, goal)`: each role with each chosen kind's phrases, at most 12 per site.
- Detection (`find/detect.py`) is pure over the address and the page's text; a look is held in the server
  process for 30 minutes under `detected_id`. Saving a site is undone through `find/source_undo.py`.
- Job alert emails from the senders in `find/seed/alert_senders.toml` (or `email.alert_senders`) go through
  one generic parser in `gmail.py`: each linked job becomes a lead.

## How Hal learns (`jobhunt/learn/`)

- `learn/patterns.py` finds four patterns: "Too far" for 4 or more jobs in 7 days, all beyond X miles (a
  smaller distance); 5 or more saved or applied-to jobs sharing a title word missing from `goal.roles` (add
  the word); two "Keep my wording" answers on one CV line (lock it); and the person's edits (below). A pattern only calls `items.offer()`; it
  never changes a setting, note or the CV (`test_nothing_changes_without_your_yes`). A waiting suggestion
  whose pattern no longer holds is withdrawn on the next run.
- `learn/items.py` applies a yes from the live value: a setting through `settings.set(..., by="learned")`
  with its own Activity row and Undo, or a CV lock as a `cv_preferences` row. The replaced value is kept, and
  switch off, Delete and Undo put it back only while the value is still the one Hal set. "Not now" asks again
  after 7 days; "Never suggest this" keeps the suggestion's fingerprint.
- Edits (`writing/edits.py`): saving the documents through the route (`save_documents(by_you=True)`), an
  approval with edits, a form answer the person changed (`autofill.by_you` keeps Hal's as `hal_value`, then
  `remember`), an edited CV suggestion and a CV line reworded on My CV each keep a draft and final pair in
  `writing_edits` (migration 0021). Hal's own rewrites teach nothing, and a private question's answer is never
  kept. The pattern `writing_edits` (area `writing`) reads the pairs word by word with no AI: a word taken out
  of three drafts in a cut of at most two words, and never written in a final, offers "Never use <word>?"
  (`cvdocs.never_use`); two letters of 100 words or more cut by a tenth offer the nearest shorter
  `cvdocs.letter_words`. A kept pair publishes `writing`, which starts a run like a job event.
- Distances: `learn/places.py` holds 160 UK town centres and measures straight-line miles from "Where you
  live". A job whose location names no town in the table is never evidence.
- The action `learn.patterns` (priority 2, no AI) runs 20 s after the last job event, CV suggestions event
  or finished search, nightly at 03:30, and for the "Too far" pattern alone when a job is hidden.

## Schedule (`core/scheduler.py`)

- Jobs come from `schedule.py` or `*_schedule.py` modules that define `JOBS`. A thread wakes every
  `TICK_S`; `tick()` starts each due job as a persisted action of its kind, so every run has steps, Stop and
  an Activity row. A command-line job runs as a hidden child (`python -X utf8 -m jobhunt <args>`, one-hour
  limit), each printed line a step.
- The next run of each job is stored in `schedule_state`, so a slot missed while the computer was off runs
  once at start. Each tick compares the settings its plan was made from, so new times apply without a
  restart, including a change made by another process.
- A job whose older `jobhunt-*` systemd timer is still enabled never runs in-process, so nothing runs twice.
  Pause is the `meta` row `schedule:paused`, read the same by every process.

## Platform (`core/platform/`)

`platform.current()` is everything Hal asks of the computer: opening files and windows, pop-ups, start at
login, finding browsers, hidden or detached children. No other module tests `os.name`. `linux.py` and
`windows.py` take their outside world (command runner, registry reader, file checks, home) as constructor
arguments, so tests drive the real code with fakes. Hal opens in the person's default browser
(`open_in_default_browser`), where their browser helper is. PDF printing never depends on that browser:
Microsoft Edge on Windows; on Linux Playwright's Chromium.

## Health, Home and search providers

An area adds lines to "Is Hal working?" with `CHECKS` in a `health.py` or `*_health.py` module, needs to
Home in `home.py` or `*_home.py`, and results to Ctrl K in `search.py`. `core/health.run_all` turns a
check that raises into a "bad" row naming it, so one broken area never hides the others.

## Setup guide (`jobhunt/setup/`, `static/app/setup/`)

`setup/state.py` keeps one `setup_steps` row per step reached (`todo`, `done`, `skipped`) and the guide's
place in the row `guide`. It never reads or writes a setting, so going through a step again cannot reset a
value changed in Settings; the steps save their own fields through the settings store. The frame loads each
step from `static/app/setup/steps/<id>.js` (default export `Step`), so an area adds or changes its step
without touching the frame. Only Email, Phone alerts and Updates can be skipped.

## Install and updates (`cli/install.py`, `core/updates.py`, `scripts/updater.py`)

The install files clone Hal, run `uv sync`, then `jobhunt install --windows|--linux`, which makes the
shortcuts, the start-at-login entry and the install record; `install --dev` is a developer's clone. Updates
are `git merge --ff-only` with commit-signature checks, `uv sync`, migrations (with a backup) and a restart,
run by `scripts/updater.py` under uv's own Python so `.venv` can be replaced while it runs. The details are
in [release-and-update.md](release-and-update.md).

## Using Hal from another computer (`jobhunt/remote/`)

The laptop runs a launcher (`scripts/remote/hal-remote.sh` or `.cmd`, filled in by `remote/launcher.py`)
that opens an SSH tunnel and asks the desktop, over SSH, for a one-time link (`jobhunt open-url --remote`).
The server still listens only on the desktop's `127.0.0.1`; the session's `remote` flag is the only sign of
a remote window, because tunnel traffic looks local. `local_only=True` routes answer 409 `remote` there,
and `core/remote.js` turns desktop-only buttons into downloads. `remote/service.ensure_running` uses a
server that answers, else the platform's unit, else a transient unit of its own. `remote/sessions.py`
writes `state/remote-window.json` with an expiry while a remote window is open, so every process holds back
desktop pop-ups.

## Browser helper (`apply/helpers.py`, `jobhunt/extension.py`, `extension/`)

- A helper is paired once: a signed-in window asks for a one-time code (two minutes, single use), the helper
  swaps it for its own key, and only SHA-256 hashes are stored. Every helper request carries `X-Hal-Key`
  and, when the browser sends one, an `Origin` of an allowed extension id.
- Each application goes to one helper: the one holding its tab, else the one paired in the browser of the
  window that started it, else the most recently seen helper of the same kind. Queues are per helper.
- Browsers load the helper unpacked from a visible copy (`paths.helper_dir()`, refreshed at every start by
  `apply/helper_mirror.py`), because snap and flatpak browsers cannot read hidden folders and Windows hides
  AppData in its folder picker.

## Privacy and help files (`core/privacy_routes.py`, `core/helpfile.py`)

"Privacy and data" builds its rows from `GET /privacy`. Export everything (`privacy.export`) leaves out
`private.toml`, the config folder, the browser profile, form pictures and logs by folder, and drops any
file holding a saved secret or, through the AI guard, a private answer. Delete everything never touches
the code folder or the record folder. The help file passes every line through `helpfile.scrub()` (names,
contacts and saved answers replaced, lines holding a private answer dropped) and is shown before it is
saved.

## Importing older data (`core/importer.py`)

`jobhunt import` moves data kept inside an older checkout into the data folder: it builds a staging home
beside the target through `paths.set_override`, migrates it with the normal code, and appears only at the
end, by one rename. It copies, never moves, and writes counts and hashes, never content, to its report.

## Hal ecosystem add-on (`jobhunt/addons/hal/`)

What Hal Employment does only for someone running the Hal ecosystem, all behind "Use my Hal setup"
(`addon.enabled`): the Pipeline note in the Hal vault, a copy of each application note, spoken urgent
alerts, the Hal tools' models offered as AI choices, the `hal off` pause and the `/jobs` command for Claude
Code. The Settings group shows only when `~/.config/hal/config.toml` exists. Nothing in the Hal tools is
edited from here.

## Names (`core/naming.py`)

`file_name("cv" | "letter")` fills the `cvdocs.*file_name` template with `you.name`; `folder_name(n, company,
title)` makes `0042 Company - Title`. Both fold to ASCII and drop characters Windows refuses.

## App shell (`web/static/app/`)

Conventions for pages and dialogs:

- A page module exports its component under the name in `core/router.js` ROUTES (`Home`, `Jobs`,
  `JobPage`, `Apps`, `AppPage`, `Messages`, `CvStudio`, `Docs`, `Record`, `Activity`, `Memory`,
  `Settings`); it gets the route's arg as `arg` (already decoded). Its first heading is `Head` (or an
  `h1`), which gets focus on each route change.
- Copy goes through `ui/clipboard.js` `copyText()`. `tests/ui/test_shell.py` fails on a placeholder page
  left in the app.
- A dialog is `dialogs/<kind>.js` with a default export; set `.wide`, `.drawer` or `.label` on the
  component as needed and start it with `DlgHead`. Open it with `openDialog(kind, props)`; props arrive as
  `d`. `ask({title, text, yes, no, danger})` returns a promise of true or false.
- Page keys (J, K, A, S, N, [ and ]) are claimed with `useKeys({ j: fn, ... })`; nothing fires while
  typing or while a dialog is open.
- Server data: `useApi(path, { on: [ev => ...] })`; `core/events.js` `stalePaths` maps each event to the
  paths it makes stale.
- Words: text for Advanced and Details panels goes in a literal preceded by `/* details */`, which the
  words test skips.
- State that follows a prop or the route is synced with `useLayoutEffect`, not `useEffect`: Preact runs
  effects after the next frame, so a plain effect shows the old value for one paint and a quick click or key
  press can act on it, as `pages/job.js` does.
- Setup steps default-export `Step`; `setup/setup.js` exports `Setup` (drawn over any page).
- `core/store.js` has `setTheme("light"|"dark"|"system")` and `setSimpleNav(bool)`.

In browser tests, wait on selectors, never on Playwright's `wait_for_function` with a string: its polling
evaluates the string, which the app's Content-Security-Policy refuses. `tests/support/ui.py` has the route
list, the sizes, the axe check and the sideways check.
