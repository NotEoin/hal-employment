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
private questions out of every AI request. A request's `temperature` (`ChatRequest.temperature`, through
`ai.chat` and `llm.chat`) replaces the backend's own: 0.1 for JSON and 0.7 for prose on a local server,
the model's own prose setting on Ollama; the Claude CLI takes none.

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
- Writing reads only the person's own documents from the record: `record.context` and `record.writing_hits`
  (the pack, form filling, interview prep, the CV score and CV suggestions) keep a passage only when
  `record.for_writing` allows it: its note's `role` is `own`, or the note has no `hal` key and no role (the
  person wrote it), its `tier` is not `archive`, and its `doc_type` is not a CV or letter
  (`NOT_FOR_WRITING_TYPES`: every writer is given the current CV whole). Course material, someone else's documents and notes
  Hal is unsure about are never used. Each passage in the index carries its note's `role`, `hal`, `tier` and
  `doc_type`; an index of another `record.INDEX_VERSION` is read again in full on the next sync, and until
  then its passages are not used. `record.ask` still searches every passage. Requests label the passages
  `record.OWN_DOCUMENTS` ("from the applicant's own documents"), never "true".
- The evidence plan (`writing/plan.py`, prompt `evidence_plan`): before the letter is written,
  `pack.evidence_plan` asks once for up to `plan.MAX_ITEMS` (8) quotes from numbered sources
  (`plan.sources`: each CV line, each Profile block line and each record passage of `kept`). A list-shaped
  line is one source per entry, its heading kept on each (`writing/atoms.py`, `atoms.split`: "Second year
  modules (overall 73.3): Ferry Studies 86" from a CV line of every module and its mark; a skills list; a
  list of achievements), and a pick from an entry is the whole entry. A line is a list when a short heading
  with no comma is followed by entries of at most `atoms.ENTRY_WORDS` words, at least `atoms.LIST_MIN` of
  them, or two under a heading naming modules or skills or when each ends in a mark, so "Project, Ferry
  planner (Lua, SQL): ..." stays whole. A whole module line as one source let a sentence about one module
  cite another's "Team Project" (2026-11-08). A pick is kept
  only when its source exists and its quote is in that source word for word, spaces and case aside, and the
  text kept is the source's own (`plan.quoted`). Each item (`plan.Item`, E1, E2, ...) carries the card its
  source is about (a Project or Job line of the Profile block, a CV line naming one card, a passage of one of
  the card's `files`, or a passage of a listing that names one card; a listing's passage naming none is no
  source), the card's context (`profile.contexts.current`, never the reply's) and the person's
  part (`profile.writing.card_role`: the card's Role field and its team words, `stage.team_words`). A pick
  from a skills list (`plan.skills_line`: a Profile "Skill:" line, a CV line labelled "Skills:",
  "Languages:", "Tools:" and the like, or a label and a list of three or more) is tied to no card and marked
  (`Item.skills`), as it says what the person knows, never which project used it. A pick whose quote is a
  plan (`writing.planned.planned`, from its words or its passage's file) is marked (`Item.planned`) and
  says so in the request; a story lists such quotes apart (`Story.planned`), and a paragraph naming a tool
  only a plan gives fails unless it says the tool was planned (`paragraphs.PLANNED_SAID`). A
  draft or fit row may name a card's planned tool only in words of a plan (`drafts.misplaced_tools`,
  `drafts.evidence_checked`). The
  letter's Evidence line names the plan's quotes, else the record passages as before. The pack picks one
  plan for the letter, the message and the answers (with `COMMON_QUESTIONS`); `docs.change` picks one for
  the message and answers. Each pick also says what in the job it can help with (`Item.use`, the prompt's
  `use`), in the model's own words.
- Stories (`plan.stories`, `plan.Story`): the plan's items grouped into the pieces of work a draft tells, one
  per card (its items' quotes first, then the card's own `quotes` from `profile.writing.quotes`: the
  person's own sources only, never an older CV's, a plan's or one naming a tool `for_writing` drops, with
  the sentences of the card's own repository or project notes; `writing/quotes.py` keeps only sentences
  about the work, `quotes.about_work`: no table row, bare link, credit, rating, heading or line under
  `quotes.MIN_WORDS` (6) words with no verb, no command line (`quotes.COMMAND`: a command word and a flag,
  a `--` flag or a ` # ` comment), no dependency pinned to a version (`quotes.PINNED`), no file statistics
  (`quotes.STATS`), no line about the repository or its docs (`quotes.META`) and no sentence's tail
  starting in lower case (`quotes.FRAGMENT`); `quotes.sentences` skips code blocks, joins hard-wrapped
  lines into their paragraph or list item and drops a last sentence cut off with no full stop; and
  `quotes.ranked` puts those with a result or a number first, then those saying how (`quotes.HOW`);
  `plan.STORY_FACTS` (12) in all), one per passage's file and one per other item; a skills list is never a
  story. `Story.block` is what a request gives: the name and tag, where it was done, the person's part,
  what it is, its tools, its facts as plain lines, what is planned and what it can help with, never an
  evidence id. `plan.choose(stories, n, used)` takes the `n` the pack has told least, best first. Each
  story has a `kind` (`plan.PROJECT`, `JOB`, `MODULE`, `OTHER`): a pick of the degree itself (`plan.DEGREE`) is
  never a story (nor a card named as the degree, "First-Class Honours"), as the opening gives it, and a
  module line (`plan.module_of`) is a module story of its own,
  named after its module. A passage of a project's own file (it has a "(Kind: ...)" line) tied to no card is
  a project story named after its file (`profile.owners.file_name`), and `paragraphs.problems` requires
  every named story, card or not, to be named in its first sentence (`paragraphs._names`).
  Which work is told comes from one AI ranking per pack (`writing/rank.py`, prompt `story_rank`,
  `pack.story_ranking`): `rank.make` offers every project and job card that is not archived (with its
  `profile.writing.cards` `tier`, "core" marked, and its summary, else its first quote, else the person's
  part, `rank._line`) and the plan's named project stories with no card (`rank.candidates`), with the job's
  title, summary, requirements and the company brief (`rank.Job`). The prompt says core is the work the
  person counts as their best and asks for every piece of work ranked by fit. `rank.checked` keeps the
  reply's names that are among them (matched by their words, a trailing tag or a card's aliases aside),
  each once, with a one-line reason (`rank.Ranked`), and `rank.with_core` adds every core card the reply
  left out, last, so core work is always ranked (one core project was missing from 3 graded rankings,
  2026-11-08). `rank.order` puts the ranked stories in the order `rank.led` gives: the core ones in the
  ranking's top half, then the others in its top `rank.TOP_PLACES` (3), then the other core ones, then the
  rest, each in ranking order (a core AI assistant came 13th behind first-year coursework, 2026-11-08). It
  gives the letter the first `plan.LETTER_STORIES` project or job stories with facts in that order (modules
  and other lines only below `plan.MIN_PROJECTS`), and the message and answers every story in that order,
  the plan's unranked stories last (`rank.Told`). The pack
  log has the ranking and its reasons. `write_letter` makes the call unless given a `ranking`, and the pack
  passes the letter's to `write_others`, so a pack ranks once. Word overlap led with weak projects in 15
  graded packs (2026-11-08). When the call gives no usable name (or the AI is unavailable for it), the word
  count chooses as before: `plan.letter_stories`, the plan's project and job stories, then cards no story
  tells whose words share at least `plan.RELEVANT_MIN` (2) words with the job's requirements
  (`plan.relevance`, `plan.fitting`, core work first at an equal count), up to `plan.LETTER_STORIES`;
  modules and other lines only below `plan.MIN_PROJECTS` (2) of those. A CV or Profile
  line's sentences saying what the person is looking for (`plain.GOAL`) are taken out before it is a
  source (`plan.without_goal`), and `plain.problems` finds one in a draft (`plain.GOAL_SAID`). The modules
  the job's requirements name and no paragraph shows, best mark first (`plan.relevant_modules`, at most
  `plan.MODULE_CLAUSE` (2), from `profile.writing.marked_modules`), go to the opening's request
  (`frame.Facts.modules`) for one clause.
- Paragraphs (`writing/paragraphs.py`): a letter whose plan tells any story is `pack._story_letter`: up to
  `plan.LETTER_STORIES` (3) stories, each drafted in its own call from its own story alone (prompt
  `story_paragraph`, shape `paragraphs.letter(cvdocs.letter_words, n)`: 3 to 4 sentences, a share of the
  letter's length after `paragraphs.FRAME_WORDS` (70) and never under 60 to 90 words, the work named in the
  first) and settled
  by `paragraphs.settle`. The checks with no AI (`paragraphs.problems`, `sentence_problems`) read the
  paragraph against its story only, through `paragraphs.Scope` (`pack.paragraph_scope`: every story and
  card, `pack.known_tools`, the employer's words and the stage line, the person's names and settings
  wording): a number or a known tool its facts lack (numbers in words read whole by `paragraphs.numbers`:
  "twenty-three" is 23 and "one hundred and thirteen" is 113, as "twenty-three" read as 20 dropped a true
  paragraph, 2026-11-08), named as another card's when that card holds it
  (`CROSSED_TOOL`) or as a plan's (`PLANNED_SAID`); another card's name (`CROSSED_NAME`, with
  `drafts.named_cards`, so "Quill Employment" never names "Quill"); `drafts.crossed`; degree or own-time
  words against its context; "I built" of a team's work without the person's part; `stage.ended_course`;
  `plain.problems` (with `plain.STOCK`: "aligns with", "resonates with", "directly supports", "satisfies the
  requirement", "the role requires", "matters for the role", "demonstrates my ability to", "matches my",
  "fits my", "suits my", "in line with my"); a sentence
  echoing the advert (`drafts.echoes`); a sentence the paragraph already said (`repeats.same`); the part as
  a label ("As the owner, ..."); `paragraphs.LIST_TOOLS` (4) tools in one sentence; a tool given a purpose
  the work's facts do not hold (`writing/purpose.py`, `purpose.unbacked`: "<tool> to/for <purpose>", with at most
  `purpose.GAP_WORDS` (2) words between, or "the <thing> in/with/using <tool>"; the purpose's content words,
  `purpose.GENERAL` words, tool names and the work's name aside, compared by their first `purpose.STEM` (6)
  letters, must be at least `purpose.PURPOSE_SHARE` (half) in the story's facts or in its name and summary,
  and a part of a system (`purpose.LAYER`: backend, deployment, database ...) must be in a fact naming the
  tool: Docker "to package the application" was an agent sandbox, 2026-11-08); `taught`'s module
  checks; the work unnamed in the first sentence; too few sentences; and the writing rules' high hits.
  When they find nothing and the paragraph says more than its facts' own words (`paragraphs.restates`),
  one AI call checks it against its story (`verify`, prompt `paragraph_verify`); quoted words its facts or
  the employer's words hold, word for word or by their roots, are dropped. A paragraph with a problem is
  drafted once more with the problems listed; one still failing loses, from each failing sentence, the one
  clause after the first (`paragraphs.CLAUSE`: parts after a comma, a semicolon, "and", "while", "using",
  "with" or "which") whose removal leaves the sentence passing and at least `paragraphs.CLAUSE_LEAVES` (4)
  words (`paragraphs._without_clause`; for `verify`'s findings, the clause holding the quoted words,
  `Problem.span`), else the whole sentence when the rest still reads (`flow.reads_without`, and the shape's
  least sentences and name kept); when the redraft cannot be cut this way its first draft is tried, as
  paragraphs on LoRA for chest X-rays and a BiomedCLIP part were dropped over one clause (2026-11-08). Else
  it is dropped and
  "Check before sending" says so (`Paragraph.listed`), unless it is a module's or the degree's story dropped
  for having nothing to say (`paragraphs.education`). A sentence fixed or cut is never listed. A paragraph
  that passes every check with no AI and fails only `verify` is never dropped (`paragraphs._doubted`): the
  clauses or sentences the AI flagged are cut when the rest still reads, else it is kept whole and listed
  (`Paragraph.checks`, `paragraphs.DOUBTED`), in a letter, the message and the answers alike
  (`pack._story_others` lists them too, as a message kept a crossed timing claim while the pack said
  "Nothing to check", 2026-11-08); a redraft that fails a check with no AI and cannot be cut falls
  back to a first draft that failed `verify` alone. A letter came out at 56 words when three paragraphs were
  dropped, two over claims the record supports (2026-11-08). `pack._letter_bodies` then tries the next
  project or job story with facts of `rank.Told.others`, up to `pack.LETTER_TRIES` (3), while fewer
  paragraphs stand than were chosen or they and `paragraphs.FRAME_WORDS` come to under `pack.SHORT_MIDDLE`
  (half) of `cvdocs.letter_words` (`pack._short_middle`). The
  opening and closing are one call (`writing/frame.py`, `frame.settle`, prompt `letter_frame`) from `frame.Facts`
  (`pack.letter_frame`: the name, `opening.described`, the role, the motivation pairing, the stories the
  middle tells and today), checked the same way with the role and the institution required. The opening's
  reason is one concrete thing about the employer: the request gives the pairing's employer fact alone, never
  its item, and with a pairing the opening needs one specific word of that fact; it never names a card of the
  person's or the work the middle tells (`frame.motive_problems`, `frame.NAMES_WORK`), as openings told a
  project a paragraph then told again (5 cases in 15 graded packs, 2026-11-08). Neither paragraph retells the
  middle (`frame.retold` against `frame.Facts.middle`, the paragraphs' names and texts: a sentence
  `repeats.same` as one of theirs, sharing `frame.RETOLD_RUN` (4) own words in a row with one, names, role and
  employer aside, or giving one of its numbers). The pair is drafted once more when either fails; an opening still failing is `opening.Opening`'s built sentence and a closing
  still failing is left out. Paragraphs and the frame are drafted at `paragraphs.DRAFT_TEMPERATURE`. The
  message and the answers are told the same way (`pack._story_others`, through `pack.write_others`): the
  message is `opening.Opening` and one story in shape `paragraphs.MESSAGE` (one or two sentences, then one
  question about the team's work); an answer is one story, or two when its question asks for more than one
  example (`plan.examples_wanted`), in shape `paragraphs.answer`, and a question asking why this employer
  (`drafts.why_question`) is one story in shape `paragraphs.why`, from the letter's motivation pairing and
  its item's story (`pack._story_of`) while the pack has told that story no more than any other, else the
  story told least, counted as told either way (`pack._why_story`): a project the letter told was told
  again in the "why here" answer while other work was untold (2026-11-08). Every one of these shapes but `why` names its work in its first
  sentence (`Shape.name`, `paragraphs.NOT_NAMED`), as a letter paragraph does, and is drafted again once when
  it does not: answers and messages opened "I wrote a 113-test suite ..." in 12 of 15 graded packs
  (2026-11-08). A `why` answer leads with the reason about the employer from the pairing's fact and may tie
  it to its story in one sentence at most: its first sentence must say "you" or "your", name the employer,
  or share a specific word (`motive.STEM` (5) letters) of the fact (`Shape.employer`, `motive.addresses`,
  `paragraphs.NOT_EMPLOYER`), and is drafted again once when it does not, as four graded answers said only
  "because I built <project>" (2026-11-08). The message, then the answers, take the stories the letter (`told`:
  its paragraphs' stories), the message and earlier answers told
  least (`plan.choose`), best first in the ranking's order (`rank.Told.others`), so they tell the next work
  the letter did not. When an answer's or the message's paragraphs are all dropped the next story is
  tried, up to `pack.STORY_TRIES` (3) stories (`pack._story_answer`, `pack._story_message`), then the
  whole-draft path writes what is still empty (`write_others`' `whole_answers`, asking `answers` for the
  blank questions alone, and `whole_message`); only a question still blank is listed (`pack.NO_ANSWER`), with
  its dropped paragraphs. A message body must ask a question (`Shape.question`, `paragraphs.NO_QUESTION`)
  and never say the built opening's degree again (`paragraphs.message(opening)`, `Shape.after`,
  `paragraphs.says_again`: a sentence `repeats.same` as the opening, or one matching `plan.DEGREE` with at
  least `paragraphs.DEGREE_SHARED` (2) of the opening's own words; `paragraphs.SAID_AGAIN`). A
  draft with no story is drafted whole as below.
- What replaced sentence checks (2026-11-08): a grader capped every graded pack at 2 of 5 for readability
  across four designs that drafted against evidence ids ("[E2]") and checked, rewrote, cut and smoothed each
  sentence, named projects with "For <card>, " and cut the advert's lead-ins with no AI. Those modules
  (`writing/cites.py`, `writing/grounded.py`), their prompts (`sentence_check`, `sentence_fix`,
  `paragraph_check`, `paragraph_smooth`, `paragraph_add`), the second letter draft and the cross-draft
  repeat rewrite are gone; one story a paragraph tells each piece of work once instead.
  `tests/fixtures/paragraph_flags.json` holds the sentences of each kind a local model wrote, each tied to
  the story its paragraph tells, and `tests/test_writing_paragraphs.py` requires each judged right with no
  AI, and the ones only the AI can see left to it. Calls per pack, counted with the fake AI of
  `tests/test_pack.py` (`test_a_whole_pack_keeps_each_projects_tools_to_itself_within_about_thirty_calls`:
  two look-alike projects, a letter, a message and two answers, no fix needed): 17, the ranking among them. A real pack made about
  55 with sentence checks. `pack.build` logs each pack's count ("pack for job <id>: <n> model calls",
  through `llm.counted`, a context variable that counts this thread's `llm.chat` and `llm.chat_json`
  calls; blocks nest), so the count on a real model can be measured. "Check before sending" lists the paragraphs left out, the questions no story
  answers, the eligibility lines, cut-offs and the checks with no AI on style; the verify call is not made
  for a draft told by stories. The section is in every pack, saying `pack.NOTHING_TO_CHECK` when it lists
  nothing (`pack.add_company_check` replaces that line with its flags), since packs without it gave no sign
  of a broken letter (2026-11-07). It also lists a letter under `flow.LENGTH_FLOOR` (0.8) of
  `cvdocs.letter_words` (`pack.SHORT_FLAG`), and a paragraph of the letter or the message that opens by
  pointing back ("This", "It", "That", "These" and a noun) at nothing the paragraph before says
  (`flow.dangling`, `pack.DANGLING_FLAG`).
- Plain first-person writing (`writing/plain.py`): `plain.problems` finds a sentence opening with the
  advert's requirement ("For the requirement to ...", "Regarding the need for ...", "As required ...", "The
  advert requires ...", "The role asks for ...", "Regarding <X>, ...", "In terms of <X>, ...", "Your
  requirement to ...", "Your need for ...", "Your work on ...": openings said the advert's words as the
  employer's, 2026-11-08), tying the
  work to the job with a stock phrase (`plain.STOCK`), pointing at the CV ("My CV lists ...", "as listed in
  my CV"), speaking of the person in the third person ("the applicant", "the candidate", their name with a
  verb) or pasting a setting's wording (`plain.settings_labels`: the right-to-work choices' labels, and
  `you.work_status` unless it is a sentence in the first person). It is one of each paragraph's problems
  with no AI (`paragraphs.sentence_problems`, through `paragraphs.Scope.person` and `labels`), so its
  paragraph is drafted again; what is left in any draft is listed by `pack.check_drafts`. A lead-in is never
  cut out with no AI, as cutting one left garbled text (2026-11-08). `plain.tidy` is applied to the letter,
  the message and the answers before they are returned: no stray evidence id (`plain.STRAY_ID`, as a
  "[E4,M]" reached a form answer), straight quotes and apostrophes, no space before a line's first word,
  and each distinctive project name (two words or more, or capitals, an acronym or a digit inside a word)
  spelt as its card spells it. The settings reach a request only as plain values: `cv.status_line` says the
  right to work in `cv.WORK_WORDS` (shared with form filling) and the nationality the person gave, never
  `you.work_status`, after a letter said "as a British or Irish citizen" for a UK and Irish citizen
  (2026-11-07).
- The claim check, `pack.unsupported_claims` (prompt `verify`), checks drafts with no story to tell, and form
  answers, against the CV, the checked
  profile and the passages of the person's own documents. The advert and the company brief go in a fenced
  `EMPLOYER` block as the employer's own words, never flagged for being restated; the fit map is not a
  fact, as the AI wrote it. A claim the AI quotes that one CV line or sentence states (`drafts.stated_in`:
  `CV_SHARE` of the claim's own words) is dropped. `tests/fixtures/check_flags.json` holds made-up
  sentences of each kind the checks flagged, rightly and wrongly, and `tests/test_writing.py` requires
  every one judged right. `pack.fix_claims` rewrites the letter, the message and the answers (all of them
  in one call) once each when the check finds a claim, then checks again; only claims still there are
  listed under "Check before sending", and the letter's Checked line starts with `pack.SOFTENED`. Form
  filling (`autofill.check_written`) runs the numbers check on each AI answer of `CHECKED_WORDS` or more
  and one claim check per form, with the advert as the employer's words; a flagged answer gets a "Hal is
  unsure; check this: ..." note and is not rewritten. Answers and letters drafted for the job earlier
  reach the form filler only as examples of style (`autofill.STYLE_LABEL`), never as facts.
- The fit map is checked with no AI after the fit call. `pack.complete_fit` finds each must-have no row or
  gap speaks for (`missing_rows`: a row holding `ROW_SHARE` of the requirement's words) and asks the fit
  prompt once more for those alone; one still missing becomes a gap ("... (no evidence found)").
  `pack.consistent_fit` then makes a row whose evidence says there is none a gap, never partial or strong,
  and makes a row or gap about where the job is that the person's settings settle a strong row whose
  evidence is the reason (`eligibility.where_you_live_reason`: "You live in Made-up Town", a move, or
  office days for a person happy in an office), also when the requirement names their own town and the
  advert's location names none.
- The fit map (`pack._fit`) also gets every project and job card the person checked
  (`profile.writing.cards`, each with its kind, tools, summary and other names), never ranked or cut like
  the Profile block. Each reply first loses the model's own reasoning (`pack.without_reasoning`: a sentence of
  a row's evidence or of a gap matching `pack.REASONING`, "I will ...", "let me", "if strict", "this table";
  a row left with no evidence is a gap), as "I will mark this as a gap if strict ..." reached a fit table
  (2026-11-08). A cell holding a mark of thinking aloud (`pack.THINKING`: "Wait,", "re-reading", "however,
  the", "I need to", "the instruction says") is taken out whole, and a cell still over `pack.CELL_WORDS` (40)
  words keeps its first sentence, as a reasoning dump of about 400 words survived the sentence filter. `pack.with_card_tools` then turns a gap, or a row whose evidence says there is none, that
  names a card's tool into a partial match citing the card (`project (personal project): Tide gauge (tools:
  AWS)`).
  Last, `pack.checked_fit_tools` reads each part of a row's evidence that names project cards
  (`drafts.evidence_checked`) as `drafts.misplaced_tools` reads a draft: a tool of `known_tools` that none of
  the named cards' own facts, nor a CV line naming the card as `drafts.named_cards` reads it, names
  (`drafts.backed`), or that only a card of a similar name holds (as `drafts.crossed`), is taken out of the
  row (`drafts.without_tools`), and a part left with no tool is dropped. A row that lost evidence is partial
  at best, and one left with none becomes a gap. Then `pack.covered_gaps` turns a gap that names a card's
  tool or a checked skill, or a skill `pack.EQUIVALENTS` knows (CI/CD: GitHub Actions, GitLab CI, Jenkins;
  graph algorithms: A*, Dijkstra, BFS), into a partial match citing where the record shows it or its
  equivalent (a card's tools or quotes, the checked skills or the CV): the fit table listed CI/CD and
  graph algorithms as gaps although the CV lists GitHub Actions and a project uses A* (2026-11-08). Each
  card of a similar name a row names is followed by its tag (`drafts.tagged`). The letter's Evidence line goes through the same check, and a plan item about such
  a card that does not name it says the card and its tag (`pack._item_note`). Fit rows and the Evidence line
  gave a local assistant its namesake's stack, as a CV line of "Quill Employment" named "Quill" too
  (2026-11-08).
- Checks with no AI on finished drafts (`writing/drafts.py`): a letter or message whose last line is a
  sentence with no closing punctuation is asked for once more with room to finish (`pack.CUT_NOTE`, a
  wider `ROOM_CTX`); if that one stops too it is kept to its last full sentence and listed (`CUT_FLAG`).
  The AI layer does not say why a reply stopped, so the text is the only sign. `misplaced_tools` turns a
  sentence that gives one project card a tool its own tools, name and summary never name (but another card or a checked skill does) into an unsupported
  claim, and the verify request lists every card with its tools so the AI checks claims per project.
  `wrong_context` does the same for a sentence that names one project card with degree words ("degree",
  "university", "coursework", "dissertation", "my studies", "my research", module work) or own-time words
  ("personal project", "own time", "side project", "hobby") that disagree with the card's context; a
  sentence that also says words agreeing with it is left alone. A sentence names a card
  (`drafts.names_card`) by its name, by one of its `aliases` (`profile.writing.aliases`: the names of hidden
  project cards resting on one of its project files, its repository's or folder's name, the end of its
  link; never through a file that lists projects, `profile.owners.Files.listings`, a file backing two or more
  visible project cards or naming several projects, which is in no card's `files`, gives no passage context
  and gives a card no quote naming another card and not it; nor through another card's own file,
  `owners.Files.owners`: a repository or project file whose name is a visible card's name, link end or merged
  card's name, case and separators aside and without what an import adds (`owners.bare`: a copy marker such
  as "(2)", a version such as "v2.0.0", and trailing words such as "repository", "release notes" or "-main"),
  a whole name and never a part of one, which gives every other card no tool, quote, file, alias or
  passage context, `profile.writing.elsewhere_only`; the record keeps the evidence as stored), or by an acronym or inner-capitals word of its name that no other card holds, used as a name
  ("the LIDAR Autonavigation project" for "LIDAR Autonomous Navigation"). In evidence words (a fit row, the
  letter's Evidence line) a kind of project ("personal projects", `drafts.CONTEXT_PHRASE`) that does not fit a
  named card's context (`drafts.PHRASE_FITS`) is taken out and each card's own context words follow its name
  (`drafts.own_contexts`). A card named only inside a longer
  named card's name ("Quill" in "Quill Employment") is not named (`drafts.named_cards`). Both go into the one
  rewrite per draft that `pack.fix_claims` asks for, and what is left is listed.
- Similar names (`profile.writing.similar`): project cards with the same first word of at least
  `STEM_MIN` letters (not a word in `NOT_STEMS`), or one name inside the other, each carry a `tag`, the first
  `TAG_WORDS` words of their own summary up to a comma or a word such as "for", "with" or "that" ("Quill
  (offline assistant over personal notes)", "Quill Employment (job-search helper)"), on their Profile line,
  in `cards` (`tag`, `similar`) and in each plan item about them (`plan.Item.tag`). `drafts.crossed` finds a
  sentence naming one of them with a tool, or a word of the tag (not `NOT_PURPOSE`), that only the other's
  card holds; it is one of each sentence's checks with no AI and part of `pack.unsupported_claims`. Drafts
  gave a local assistant its namesake's purpose and stack in every graded run (2026-11-08).
- `writing/taught.py`: `unsupported(draft, modules, sources)` turns a sentence that says a named module
  (one of `profile.writing.module_names`, the modules of the education facts the person checked, or any
  module code) covered, taught, included or introduced something into an unsupported claim, unless a CV
  line, profile line or passage naming that module also holds at least `COVERED_SHARE` of its content words
  (`content`: vague words such as "core" and "methods" left out, words compared by their first `STEM`
  letters). In a source naming several modules only the words from this module's name to the next count. A
  letter said a data science module "covered core statistical methods" when its notes covered databases
  and SQL (2026-11-07). It goes into the same one rewrite, and the verify prompt says the same.
  `joined(draft, modules, sources, cards)` finds a sentence tying a named module and a mark to a project
  card or an activity (`ACTIVITY`: "through a team project", "where I contributed to a team project", "by
  building Ferry planner") that the module's own entry (`atoms.split` of the sources' lines) never names;
  the modules of a list in the sources (`modules_listed`) count as named. `tooled(draft, modules, sources,
  known)` finds a sentence naming a module with a known tool or language its own entry and name never name
  ("Algorithm Design & Analysis gave me the specific C++ knowledge", 2026-11-08); a listed entry that is a
  tool's name is never a module. All three are among each paragraph's checks with no AI
  (`paragraphs.sentence_problems`), so they run on letter paragraphs, answers and messages alike, and
  `unsupported` and `tooled` on whole drafts (`pack.unsupported_claims`).
- `drafts.echoes(sentence, advert, brief, names)` finds a sentence that restates the employer: at least
  `ECHO_SHARE` (half) of its own words sit in runs of `ECHO_RUN` (three) words found in the advert or the
  brief, with little words and the words of the job title and employer's name (`names`) never counted, and
  at least `ECHO_MIN_WORDS` such words. It reads the letter's first paragraph (`first_paragraph`) and the
  answers whose question asks why this job or employer (`why_question`). `pack.fix_echoes` rewrites the
  letter, or all the answers in one call, once with the instruction to tie one specific thing of the
  applicant's to one specific thing about the employer, before the claim check; a sentence still echoing
  is listed as `pack.ECHO_FLAG`. The letter and answers prompts ask for this up front. Every paragraph a
  story tells (letter, answer, message) is also checked with `drafts.lifted(sentence, advert, brief, names,
  own)`: a run of `ECHO_RUN` (three) or more words found in the advert or the brief holding at least
  `drafts.LIFT_CONTENT` (2) own words, not all of them (by their first `drafts.LIFT_STEM` (5) letters) in the
  story's facts, fails a sentence about the person's work (`paragraphs.LIFTED_SAID`, drafted again once).
  A sentence about the employer or the role (`drafts.about_employer`: "you", "your", "this role", the
  employer's name or the job title) may use them. "low-latency systems" and "AI agents for customer
  support" became the person's claims in sentences mostly their own (2026-11-08).
- The letter's reason for the employer (`writing/motive.py`, prompt `motive_pair`): with a plan,
  `pack.write_letter` asks once (`motive.make`) for one fact of the advert or the company brief, one plan
  item and a one-line reason. `motive.checked` keeps the pairing only when the fact is in the advert or the
  brief word for word (`plan.quoted`), at least `MIN_SPECIFIC` (2) of its own words are not words any
  employer uses of itself (`GENERIC`, the little words, the job title and the employer's name), the item
  is the plan's, there is a reason, and the fact is not a duty or requirement of the job (`motive.duty`,
  `motive.DUTY`: "write and improve ...", "experience of ...", "you will ...", as openings led in with "Your
  requirement to write and improve SQL queries ...", 2026-11-08). The letter's opening (`writing.frame`) is
  drafted from the pairing's fact alone and a "why here" answer (`paragraphs.why`) from the fact and its item,
  or each is told there is none and to give no reason beyond the role; a letter with no story gets the
  pairing in its request (`motive.block`, or `motive.NONE_SAID`). The pairing is offered every plan item
  tied to a card (`motive.offered`), as the opening names no project.
- `writing/repeats.py`: `repeated(answers)` finds each sentence of a later form answer whose own words
  (at least `REPEAT_MIN_WORDS`) are `REPEAT_SHARE` in a sentence of an earlier answer. `pack.fix_repeats`
  rewrites the answers once, in one call, asking for other evidence in the later ones, after the echo
  rewrite and before the claim check; one still repeating is listed as `pack.REPEAT_FLAG`. `repeats.same`
  also finds a sentence a paragraph says twice (`paragraphs.REPEAT_SAID`). Told by stories, a pack tells each
  piece of work once by how it is drafted (`plan.choose`), so no sentence is compared across drafts. The fit
  map's gaps are kept once each (`unique_gaps`: the same words, case, order and "(no evidence found)"
  aside).
- `writing/eligibility.py`: `blockers(must_haves, person())` finds, with no AI, a degree level above the
  person's highest (checked education and `you.education.degree`), more years than
  `goal.experience_years`, and no sponsorship when `you.right_to_work` needs it. Each is an
  "ELIGIBILITY: ..." line at the top of "Check before sending", which no rewrite changes. A requirement
  that allows "or equivalent", is desirable or names a lower degree too is not plain, and an unknown level
  says nothing. The pack passes the advert's text, and a degree level or a number of years then counts
  only when one of the advert's `conditions` names it: a sentence in requirement phrasing (`REQUIRED`:
  must, required, essential, you will need, minimum) or a bullet under a heading that is, never one with
  softening words (`SOFT`: "also consider", "welcome", "nice to have", "We'd love to hear from you if").
  The fit map's eligibility note is dropped when it names a degree level no condition states
  (`unfounded_degree`). `where_you_live_settles` meets a fit row or gap, and drops a fit eligibility note, about
  the job's place when the person lives in the advert's town (settings or CV), will relocate, or is happy
  in an office.
- `writing/relocation.py`: `without_own_move(text, person)` takes out of the letter, the message and each
  answer, with no AI, an offer to relocate or move to a town of the person's settings (`own_places`, the
  parts of `eligibility.Person.places`; the CV's location line comes from the same settings): the clause
  when "and", "but" or "while" joins it to another, else its sentence. "I moved to ..." is a fact and stays.
- The pack's company check (`reputation.check(company, advert)`, also `jobhunt check`) compares trades as
  sectors of `SIC_DIVISIONS`: the advert's are its leading ones (`advert_sectors`, at least `SECTOR_SHARE`
  of the top sector's words, whole words with a plural "s" dropped). It keeps a Wikipedia article only when
  its title holds every word of the employer's name, it is not a disambiguation page (its `disambiguation`
  page property, the title, or "may refer to"), and it links to the employer's own site (the advert's link
  or an address in its text, never a job board's), or else its title and first sentence name one of the
  advert's sectors, or else, naming none, it names the advert's town. An article whose title adds words
  to the employer's name ("Brightvale Solar") needs the site link, or the advert's town and no other
  sector; a shared sector alone is not enough (`article_verdicts` gives each rule's verdict, and the lookup
  logs them). It keeps a Companies House
  registration only when its name starts with the employer's whole words, a word the name adds names no
  other sector, its company page says Active, and its SIC divisions share a sector with the advert; codes
  that name no trade (`NO_TRADE_SIC`) need the advert's town in its address instead. A registration
  incorporated within `NEW_COMPANY_DAYS` (365) of today is kept only when its name is the employer's
  exactly and one of the advert's own web addresses (`hosts`) is on its company page. A kept registration
  is stated as the employer only when the advert's own web address is on its company page, or its address
  names the advert's town and its SIC divisions one of the advert's sectors, at an address that is not a
  flat or a room (`SMALL_ADDRESS`) and with no micro-entity accounts (`SMALL_RECORD`) (`reputation.stated`);
  else one with the employer's exact name reads "Possible match: <name> (check it is the same company)"
  (`POSSIBLE`), and any other is left out. An IT consultancy at a flat was stated as a large trading firm
  (2026-11-07). Anything else reads "No reliable match found" with no sections to read. `tests/test_reputation.py` fakes every lookup.
- One letter path, `pack.write_letter` (advert, record, fit map, letter, checks), serves the pack, the letter
  drafted at form time and the writing actions. The letter and the message are drafted at
  `pack.DRAFT_TEMPERATURE` (`paragraphs.DRAFT_TEMPERATURE`, 0.4), cooler than other prose, and drafted once:
  only a failing paragraph or frame is drafted again, once. The pack's `## Letter notes` section says Tone,
  Length, Evidence and Checked; the application page reads them back.
- The advert reading keeps only the questions the advert writes out, so a model's own note about questions
  is never answered. When the advert states no requirement, its main duties stand in, so the fit map is
  never empty. The letter greets the first person the advert names, else "Hiring Team", and never adds a
  title the advert does not give.
- The message opens with a greeting and a first sentence built with no AI (`writing/opening.py`,
  `pack.message_opening`): "Hello <the advert's first hiring contact, else Hiring Manager>," and "I'm <name>,
  <`opening.described`: a first-class, 2:1 or 2:2 subject graduate from the institution (year), or a
  final-year subject student at it>, applying for the <title> role.", from `you.name` and the stage
  (`stage.Stage.subject`, `institution` and `grade`: the checked course's, else `you.education.subject`,
  `university` and `grade`). The message request carries `Opening.note` (the opening, and to write only what
  follows it); a greeting or an introduction the writer still adds is taken out (`opening.body_of`) before
  the claim check, and `write_others` returns `Opening.put(body)`, which starts with the opening
  (`Opening.starts`). All 15 graded messages lacked an introduction (2026-11-08). The writer's greeting is
  never kept, so no rewrite for a greeting is asked.
- The message's "To:" line (`pack.message_to`) and its greeting name only the advert's hiring
  contacts (`pack.hiring_contacts`): a named person whose role or sentence in the advert is not about the
  application process (`PROCESS_WORDS`), with a careers or recruitment mailbox address (`PROCESS_MAILBOX`)
  left off. With none, the line is the LinkedIn search hint.
- One path for the message and the answers, `pack.write_others(who, job, analysis, about, message_request,
  answers_request, facts, kept)`, returns {message, answers, flags}: cut-off, claims (with project
  contexts and misplaced tools), greeting, writing rules and restating the advert. The pack and
  `docs.change` both use it; `docs.change` reads the job once (`pack.read_job`), passes the reading to
  `pack.write_letter`, and gives the message and answers the same shared context
  (`pack.shared_context`) with the current draft and the person's words. For the story path it also takes
  the `questions`, the person's words (`request`), the letter's `pairing` (`paired` when the letter asked
  for one) and the stories the letter `told`. The action's result `flags` holds the letter's and the
  others'.
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
  over 35 words, a letter sentence copied nearly word for word from a whole CV line (`VERBATIM_SHARE`),
  greeting and sign-off pairing ("Dear" and a name with "Yours sincerely", a team or "Sir or Madam" with
  "Yours faithfully"), numbers no source holds and length limits. A code (`check.CODE`: capitals then
  digits, such as a module code "CSC2031/23") is no number to check (`check.without_codes`, also in
  `paragraphs.numbers`), as one person's module code and year were listed in every pack (2026-11-08).
  An `-ing` tail (L5) counts only when its
  verb is followed by a quality or significance word (`SIGNIFICANCE`) or by "how", "that" or "why", so a
  factual tail (", ensuring each record keeps its own ID") is not flagged. It replaced `pack.CLICHES`; every word that list flagged is still flagged. An L10 hit
  carries its fix (`Hit.fix`), and the flag reads `... : color (write "colour")`.
- `spelling.py` is the one source of UK and US spelling. `find(text, code)` returns each word spelt the
  other way with the form to write instead, and `respell` applies them all. It matches only words with
  one right form: -ize/-yze, -or/-our, -er/-re, -ense, catalog, doubled l, and a list of single words.
  The -ize rule needs no list, except for words ending in -size; the US -ise rule uses a list of stems.
  It never matches "program", "disk", "analog", "meter", the verb "license", or "dialog" before
  box or window. It also skips capitalised words inside a sentence, words in capitals or with inner
  capitals, code (a word joined to `_`, `.`, `/`, `(` or a digit), backticks, double quotes, web addresses and
  email addresses. `code_of` turns an unset, empty or unknown setting into `en-GB`, so the check is never
  off, and `chosen()` reads `cvdocs.spelling`. Each prompt that writes for the person (letter, message,
  answers, form_fill, prep, cv_compose, cv_suggest, cv_another) takes `{spelling}` from
  `spelling.WORDS`. Each AI draft is respelt before it is checked or shown: `pack.spelt` for the letter,
  message, answers and prep notes (the pack and `docs.change`), `autofill.ask_model` for form text,
  `fresh._ai_profile`, and `suggest.guard` for AI suggestions. A spelling has one right form, so it is
  fixed in place rather than by a revision. The person's own CV lines get a "Consistency" suggestion
  instead (`suggest._spelling`), and the CV check `one_spelling` and the voice card count with `find`.
- `fields.py` resolves the field from the advert alone (title words count three times); an advert that shows
  none is the general field, never the person's, so a computing graduate's letter for a complaints role is
  not written as a tech one. The person's own field (CV type, roles, subject) is background only, and decides
  only when there is no job at all (a CV looked over on its own). The analyse step stores it on the
  analysis, and the letter, answers, form-filling and CV suggestion requests carry its conventions, plus
  the person's own field's background advice when the two differ. Conventions are in the request, never
  the prompt, since they depend on the job.
- `stage.py` says where the person is today. `current(con, today)` reads the newest education fact the
  person checked and the settings `goal.study_end`, `goal.study_status`, `you.education.*`, `you.start`,
  `you.start_date` and `you.notice`; an end date with a month wins over a bare year, and the date decides
  whether the course has ended. `today_lines()` gives the "Today: 2026-11-07" and "Stage: Finished BSc ... in
  2026-06; available now." lines that the fit, letter, message, answers, verify, prep and form-filling
  requests carry (CV suggestions get `today` and `stage` in their JSON). `ended_course(text, stage)` finds,
  with no AI, the sentences of a draft that speak of finishing a course that has ended (`FINISHING`, and
  "following my graduation" and the like beside words of what is to come (`PENDING`) and none of what has
  been (`DONE`)), or that call the person a student now (`STUDENT`, "a Computer Science student", in a
  sentence with a word of the present, `NOW_WORDS`, or opening "As a ... student", and no "I built" or "I
  was", `I_DID`), or that say the course is still running: "currently completing", "on track to achieve",
  "expected to graduate", a predicted grade, or "in my final year" with no `I_DID` (`IN_FINAL_YEAR`).
  "I am currently completing a Bachelor of Science ... on track to achieve a First" passed after graduation
  (2026-11-08). `pack.unsupported_claims` adds them to the claims a draft is rewritten without, and the
  reason given with each (`ended_said`) holds the stage line for the rewrite to follow. `project_kind` names a
  project "personal project" when its words say own time ("own time", "side project", "personal project",
  "extends my dissertation"), else "degree work" (coursework or dissertation evidence, degree words such as
  "assignment", "practical 3" or "submitted for", a module code, also one joined to a word as in
  "IMAG2031blog", a `Sources/Coursework/` file), else "at work" (a placement, or the employer of a checked job
  named in it), else "personal project" for a repository or project file, plus "team project" ("team
  project", "group project", "team of 7", "7 developers", or a university named with a team word in one
  quote). `context_of` turns that into one of `CONTEXTS` (degree,
  team, work, personal): work first, then a team's (a university team project), then degree work. A card's
  context (`profile.contexts`, below) is said in the Profile block (`Project (degree work): ...`) and on the
  `(Kind: ...)` line of the record passages from its files, in `CONTEXT_WORDS`, and the letter, answers,
  message and form prompts say never to change it.
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
   `error`. Each item's `origin` (migration 0024) is where the person said its folder or zip came from:
   `mine`, `course` or `mixed` (`roles.ORIGINS`, default `mixed`). The folder route takes each of `folders`
   as a path or `{path, origin}`, the upload route a form field `origin` for its zips, the drop folder
   `record.drop_origin`, and `jobhunt ingest` `--mine DIR` and `--course DIR` beside its plain folders;
   the action keeps the non-mixed ones in its `origins` param. The row keeps it when it is read again or
   tidied.
2. **Discover** (`discover.py`). Size, SHA-256 and the kind by the first bytes. Junk folders (`.git`,
   virtual environments, `node_modules`, caches, and build folders inside a repository) are never entered;
   weights, programs, databases and fonts are left out by name. So are hidden folders and files, an AI
   tool's instruction files (`CLAUDE.md`, `AGENTS.md`), lockfiles and minified or bundled code
   (`discover.is_tool_file`). A file of zero bytes only is a cloud
   placeholder: `not_downloaded`, listed, never converted. A file with the same SHA-256 as one already read,
   in any batch, is a `duplicate` named once under "Also at" in the first copy's note.
3. **Group** (`group.py`). A repository (a `.git` folder or a manifest at its top) is one `repo` item,
   summarised from its README, docs headings, manifest, language mix and file tree, never file by file. A
   folder with a README at its top (`discover.has_readme`) in a `mine` or `mixed` origin, not a repository
   and not holding `group.COLLECTION_MIN` (2) or more other projects, is one `project` item with all its
   sub-folders (`group.projects`; the outermost one wins, and the folder given counts). Its files are read
   one by one, but `projects.facts_in` (called by `engine._judge`) turns each project fact read in one
   into a fact about the folder's project: named from the README's first heading or the folder
   (`projects.name_of`), keeping only tools and links (and the README's summary, role and dates). A CV,
   letter or transcript keeps its project facts. Any other fact keeps its name only when it is about
   another of the person's projects (`projects.Others`): a card the person checked or added, or that a
   repository, a CV, letter or transcript, or a file in no project folder speaks for (with the names of
   cards merged into it; a card only other project folders' notes speak for folds into theirs), a
   repository or another project folder, matched by `merge.same_item`,
   `merge._name_close` or a distinctive word only that one name shares (an acronym of at most
   `projects.ACRONYM_MAX` (5) capitals, so "SYSTEM" in a ticket id is the word "system", or a word of
   `projects.DISTINCT_MIN` (5) or more letters not in `projects.GENERIC_WORDS`). A document naming
   `merge.LISTING_NAMES` (3) or more projects inside the folder follows the same rule, so a dossier keeps
   the projects that have their own repositories while a research note naming three of the folder's tickets
   adds them to the folder's card. A file belongs to the innermost project folder holding it
   (`projects.folder_of`): a record can hold one inside another when an older read was given the inner one
   on its own, and that inner folder ties a fact only by a close name, as it shares the outer one's words.
   `fold_cards` logs each fact it leaves on its own card with the project it names. `projects.write_notes` writes the folder's note listing its files' notes, which
   link back under "Part of", and supporting files attach to it. A `course` origin never makes one. A
   folder outside any project folder whose own files are mostly notes or slides (three or more) is a
   `course` unit: a note per file
   and one summary note linking them, one per module code (`engine._courses`: a unit whose code
   already has a course note adds its files to it). A unit's files are light (`rank.in_unit`), except a
   document whose own name says it is work (`rank.work_named`: the report and project rules' words, essay
   or writing, and no lecture, slides or handout word), such as "Week 4 coursework.md": it is read as that
   rich kind, by the AI, and keeps its "Part of" link. A tidy sends such a note an older read filed by rules
   to the AI (`tidy._ask_again`). With `record.read_all` on, every unit file goes to the AI as before.
   Course material goes through `lectures.py`. Within a rank, typed notes are read before converted decks
   and handouts, and a deck or handout whose module code and title words match typed notes already written
   (`lectures.Covered`) gets state `covered`: no note, no AI call, its path under "Also at" in those notes.
   A converted document the AI calls `taught` is written again by the `lecture_note` prompt on the worker
   thread, and the note keeps that text instead of the raw words. Every `taught` note's text and summary
   lose emails and phone numbers (`lectures.scrub`), and its original is not copied into the record.
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
   and is never sent to a model, because a model given no text invents a document and its author. Text
   that is not empty starts with `Document author: NAME` and, when it differs, `Last saved by: NAME`
   (`convert.author_lines`): from `docProps/core.xml` (`dc:creator`, `cp:lastModifiedBy`) in Word,
   PowerPoint and Excel files, `meta.xml` (`meta:initial-creator`, `dc:creator`) in OpenDocument files, and
   `/Author` (`pdfread.metadata`) in a PDF. Placeholders (`PLACEHOLDER_AUTHORS`, a value with no letters, a
   computer's name) are left out. So `roles.names_person` finds the person there and the AI sees who made
   the file; a taught note's text drops these lines (`convert.without_author_lines`), and so does a light
   note's summary.
6. **Understand** (`understand.py`). Rich documents get the `ingest_doc` prompt (role checker), cleaned in
   code: fixed categories and doc types, 12 to 30 tags, and every fact's quote checked against the text; a
   quote not found marks the fact "Check this" and the note `hal: needs-review`. An organisation or place
   that neither the text nor the file's path names is dropped, with its tag, so a guess gets no entity note,
   and so is a fact's employer, institution or issuer (`understand.ORG_FIELDS`); a tidy takes it out of
   facts filed before.
   Light documents, and every document while the AI is unavailable, get tags from the path, headings,
   frequent words and years. The engine then judges the document's role on its own thread (`roles.py`):
   `own`, `taught`, `others` or `unsure`. The AI's "role" is taken for taught; its "others" holds only
   when its "author" is named in the text and is not the person, and the text and path do not name the
   person; its "own" holds only when the text or path names the person (`roles.identity`: `you.name`'s
   parts, `you.other_names`, and usernames and emails from links and basics their CVs and letters gave). A
   repository's text lists its committers and remotes from `.git/logs/HEAD` and `.git/config`. Without the
   AI, slides and a course unit's files are `taught`, the rest `unsure`. That is the `mixed` origin. For
   `mine`, the AI's own needs no name, its unsure is own unless the doc type is slides or a course page,
   and without the AI the work types (`roles.WORK_TYPES`: coursework, report, project, dissertation, essay,
   writing, repository) are own and the rest unsure. For `course`, everything is taught except what the AI
   calls own and the text names. Someone else's keeps its rule for every origin. `roles.allowed` keeps the facts
   each role may give: everything for `own` (contact details and links only from a CV or a letter), skills
   for `taught`, nothing otherwise. An `others` document gets state `not_mine` and the error column
   `made by <author>`: no note, no copy, no facts, and `items.summary` names the first few with their maker.
   A tidy reads `not_mine` items without a maker once more (`tidy._not_mine_again`). Facts go to
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

`tidy.py` holds a record filed by an older Hal to these rules (`record.ingest` with `tidy`, from
`POST /api/v2/record/ingest/tidy` or `jobhunt ingest --tidy`). `tidy.prepare` runs once per batch: a
database snapshot and a `.tar.gz` of the record folder in the backups folder, then each Hal note judged by
rules (role from its `role` or older `about_owner`, tool files, `privacy.reason`, decks `lectures.Covered`
already covers, copies of one project via `versions`), facts its role may not give deleted, course material
scrubbed, code text fenced, and the notes only the AI can settle (converted course material, unsure rich
documents) moved into the batch to be read again in place, unless the note's `read_by: ai` (and
`lecture_note`, for course material) says the AI already read it under these rules. Before that,
`projects.find_in_record` walks the folders older reads came from again, adds each project folder to the
batch for its note, takes its files out of their course units (`unit` cleared) and drops those units' notes.
`tidy.finish` runs when nothing is pending:
source notes no item points at are deleted with their facts and copied originals, entity notes outside
the profile's names and all People and Places notes go, links to deleted notes become plain words, and
`store.rebuild_found` merges the profile again (`_fold_again` keeps each moved fact's `moved_from`). Then
`projects.fold_cards` first takes apart what older rules did (`projects.take_apart`): each card merged
into a card nobody checked, unless the two share a project link (`duplicates.retake`), and each card
folded as "Part of" one, come back with `store.unfold(..., apart=False)`, which gives a refiled fact this
card's fields again; facts refiled away from a card that stayed return to it (`store.take_back`). Then it
files each project fact from a document inside a project folder again on the folder's card
(`store.refile`), by the rules `projects.facts_in` follows, and hides each unchecked card left with nothing
as "Part of <project>" (`store.fold_away`). The duplicate pass after the read merges again what the AI
agrees on. Notes without Hal's frontmatter are never
touched.

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

Supporting files (`attach.py`). Data (`.csv`, `.json`, `.xlsx` and the like, and anything but a document in
a results, output, data or figures folder), diffs and patches, logs, circuit, CAD and 3D files, camera photos
(a camera's file name such as `IMG_` or `DSC_`, or a camera maker in the Exif) and pictures Hal reads no
words from are never converted, sent to the AI or written as notes: `engine.run` leaves them out of the
reading, and a certificate or artwork picture whose OCR comes back empty stays pending. After `_courses`,
`attach.settle` finds each one's unit (`attach.Units`): the nearest folder below the one given that is a course
unit, or holds a repository or a document of a work type (`roles.WORK_TYPES`); a folder named as work
(`rank.work_named`) reaches work documents anywhere below it. The file becomes `attached` (`dup_of` the unit's
item, `note` its note), and its path inside the unit is listed under "## Files" in the unit's note (above
"## Original"; in a course note, its own list), which `engine._kept` carries over when the note is read again.
With no unit it is `skipped` with "a picture with no words" or "a data, log or design file with no project
around it". It gives no facts. `tidy.prepare` drops the notes an older read made of such files and places
them the same way (`attach.place`).

Notes from a person's vault (`vaultnotes.py`). In `engine._prepare`, before the privacy check, a text or
Markdown file with fewer than `EMPTY_WORDS` (2) words of its own, once headings and tag lines, lines of
links, embeds, code and query blocks and placeholder lines (`_(empty ...)_`, a short line saying a tool made a
stub) and template lines are taken out, is `skipped` with "an empty note": no note, no AI call; so is a note
whose frontmatter says `status: stub`. A template line (`vaultnotes.Templates`, counted once per read by
`engine._read_items` over every Markdown file of the batch, whatever its state, so a carry-on counts the
same) is one whose `template_key` (the note's own title as a mark, bold and spacing evened out, lower case)
is in at least `TEMPLATE_NOTES` (10) of the read's Markdown notes and `TEMPLATE_SHARE` (0.5%) of them. The
counts only decide emptiness; a note with words of its own keeps its template lines. `tidy.prepare` counts
over every file row's Markdown file and judges each filed note from its file while it is there
(`tidy._empty`). A file with no text at all is still `no_text`. A Markdown note whose frontmatter names a slide deck, PDF or Word file (`source_file`,
`source_path`, `source`, `original` ...; never in a Hal note, which has `hal`) and whose source is slides or
lecture notes by `rank.rule_for`, or whose `role` or `type` says lecture, exam, handout, solution, exercise or
tutorial, is described by rules with `said` taught, so it is taught for every origin, and gets `model` 2
(`items.BY_SOURCE`), which `mark_again` and `items.by_rules` leave out. Its source deck in the same read is
handled as any deck: covered when the note shares its module code and title words (`lectures.Covered`), else
read as course material. `tidy.prepare` skips empty notes an older read filed and makes notes from slides
taught. `items.summary` counts skipped files by reason (`skipped`), which the Overview shows in words.

Private files (`privacy.py`). In `engine._prepare`, after conversion and before any AI call,
`privacy.held_back` runs `privacy.reason(rel, text)` unless `record.privacy_check` is off or the row is
`allowed`; after the AI's answer it runs again with the reply's `sensitive` field as `said`, which counts only
when it names a kind the rules keep out (`SAID_KINDS`: never contact details, which every CV has, nor keys and passwords, whose values the rules check). A date of
birth beside a passport, licence or ID card number, or beside any ID number with fewer than `RESULTS_MIN`
words of results (a page of personal details), holds the file; any other date of birth is cut from the
text by `privacy.redact` before the AI call (and by tidy's rewrite), and the item's `_Read.redacted` keeps
`notes.keep_original` from copying the file. A reason sends
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
  compared without case or punctuation other than "+" and "#" and through other names ("MS Excel" is
  "Excel"), shown by the name their sources write in normal case (`merge.skill_name`: "Python scripting",
  not "python-scripting"). A qualification is matched by its
  level (`merge.level`: bachelor, master, GCSE, Access and so on) and subject, not its wording, at
  institutions whose names hold one another ("Wexley University" is "The University of Wexley"); a BA and
  then an MA stay two. Certificate and achievement names that differ by a letter or two merge, never when
  their numbers differ ("Level 2", "Level 3"). Two projects are one when they share a link that names one
  project (`merge.project_link`): never a profile page on a code host, a site's front page (nothing after
  the domain but "/", a query or a fragment) or the person's own website (`you.links.website`, read by
  `merge.own_site`), which many projects share; the same test decides `duplicates.certain` and
  `duplicates.retake`. An education fact naming a project, module or dissertation
  with no qualification level is refused, as is one whose subject is only a module code ("CSC1034") and a
  training course with no place of study ("CITI Program Training", found as a certificate instead). A job
  whose employer is only a kind of place ("bar", "shop") and that has nothing but a title is refused. Dates are stored as `YYYY-MM` or `YYYY` from UK ways of writing
  them ("May 2024", "05/2024", "2023-24", "Sept 2023 - present", "Summer 2023"). An unknown kind or field
  raises `ValueError` naming it. `store.rebuild_found(con)` merges every unchecked found item again from its
  sources after these rules change, leaving what the person confirmed, edited, hid or added.
- Matching scales (`index.py`): `index.match` finds the item a fact belongs to through `merge.match_keys`
  (a skill's compared name, a job's employer or, with none, its title, a basic's key, a link's address),
  each candidate checked with `merge.same_item`; education, projects, certificates and achievements, matched
  by closeness, are compared with every item of their kind from memory. The index is kept per connection
  (named by a token in a temporary table) and read afresh when `total_changes` or `PRAGMA data_version`
  moved since the store last wrote through it. The ingest (`engine._give_facts`) adds a document's facts
  with `refresh=False` and runs one `conflicts.refresh` per document. 2,000 synthetic facts merge in about
  0.3 s, against 4.4 to 5.5 s before (`tests/test_profile.py`, `Scale`, measured 2026-11-07).
- Folding (migration 0027): `store.refile(con, evidence_id, said)` files one piece of evidence again as
  another fact, on the card that fact matches (a new one when none), and `store.fold_away(con, item_id,
  into, why)` hides an unchecked found card with `merged_into` and `merged_why`; it raises `ValueError` for
  a card the person checked, edited, hid or added. Moved evidence keeps the card it was on in
  `profile_evidence.moved_from`. A new fact that matches a folded card goes to the card it was folded into
  (`add_found`), cards folded into a card that is folded later follow it, and `rebuild_found` folds each
  card again into the card rebuilt in its place, or shows it again when there is none. A folded card's
  tier reason is its `merged_why` rather than "You hid it". Shaped items carry `merged_into` and
  `merged_why`.
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
- The approval gate: `profile.context_block(con, limit)` is the "Profile" block for writing prompts, and it
  holds only facts the person confirmed or edited (`writing.USED_STATES`). A fact Hal found and nobody
  checked (state `hal`) and a hidden one never reach a CV, letter, answer or message prompt; with nothing
  checked the block is empty. Jobs, education, projects, certificates and achievements come core first,
  then supporting, then not sorted (`tiers.order`), most relevant to the job first within a tier (the
  word overlap of `RANKED_KINDS`), and an archived one never, even confirmed. No email or phone is included. The pages that write (My CV, an application's
  Documents step) show `ui/unchecked-note.js`, which reads `to_check` from `GET /api/v2/profile` and says
  how many facts wait. The person's newest CV speaks for their CVs (`writing.CvAuthority`: among
  evidence of `doc_type` cv and `role` own, the one whose note's `period` ends latest, then the newest
  `ingest_items.mtime`): a project's summary, role, period, link or tool, or a job's duty or achievement,
  that only older CVs give (each source's `said` fields, the person's own sources only) is left out of the
  block and of `cards` (`writing.for_writing`), and `GET /api/v2/profile/{kind}` gives each item's
  `older_cv` ({field: [value]}, `writing.older_cv_only`), shown as "only in an older CV". A card the person
  added, and a value no source gives (typed in an edit), are theirs. An older draft gave a team project
  tools the newest CV does not claim and its code never used (2026-11-08). Planned is not done
  (`writing/planned.py`): a project's tool, or a skill, whose every source giving it is a plan (plan words
  such as "will use", "we plan to", "the proposed", "to be deployed", or a plan document by its file name,
  `PLAN_FILE`: a design document, a specification, a proposal, unless its words say something was done) is
  left out of the block, of `cards`' `tools` and of `skill_names` (`writing.planned_only`); `cards` keeps
  such tools as `planned`, which `pack.known_tools` counts, so a draft or fit row giving one to the project
  is caught. `GET /api/v2/profile/{kind}` gives each item's `planned` ({field: {value: document}}), shown as
  "planned in <document>". A team's design document planned Docker and PostgreSQL, the app used SQLite, and
  every pack said the person used them (2026-11-08). Each item is one line of at most
  `LINE_MAX` characters, cut at a whole list item, and one location is named. A course's modules and a
  job's duties and achievements are one line each under it, naming the course or job (`entry_lines`:
  "Module (BA History): Oral History (68)", "Job duty (Guide at Fenwick Museum): Ran tours"), at most
  `LINE_MAX` characters of them per item. `pack._kept` puts it
  above the record's passages (`profile.writing.with_record`), so the fit map, letter, message and answers
  read it, and `cvstudio.context.build` gives it to every document prompt as the `profile` value.
- The check queue: `check.queue(con)` works out, from the items' states each time, what the person has
  still to check, so stopping and coming back needs nothing saved. Open conflicts come first, then one
  card per found `hal` item in `check.KIND_ORDER` (basics by `store.BASIC_KEYS`, then jobs, education,
  certificates, achievements, projects, links), then skills in groups by the file each rests on most
  (`check.best_source`: the person's own document, then `merge.rank`, then a quote found in the file).
  Skills are ranked by `check.strength`; the first `SKILLS_FIRST` (30) are queued and the rest, then the
  weak ones, go to `more` ("Show more"). `store.is_weak(item)` is the one rule for weak: a found skill that
  fewer than two files name and whose evidence has no `role` of "own" ("" is unknown and does not count).
  A skill whose every source has the role `taught` (`store.is_studied`) is in neither: the queue lists it
  under `studied`, which the page shows apart as "Studied in your course" with **I have used this**
  (confirm); unconfirmed, it never reaches a writing prompt. Only usable skills are queued
  (`store.is_usable`: on a CV, confirmed, edited or added, or a `merge.skill_key` match for a tool of a
  project whose tier is core or supporting and that is not hidden, `check.project_tools`); a skill that is
  not usable and not studied is listed under `mentioned`, which the page folds away as "Mentioned in your
  files" with the same **I have used this**. Neither reaches a writing prompt unconfirmed (`USED_STATES`),
  and a confirmed skill is usable. After each read the ingest action stores `check.skill_counts`
  ({usable, mentioned}, shown skills only) in its result as `skills`, which `items.summary` returns and the
  Overview shows.
  `GET /api/v2/profile/check` serves it with `done` and `total` for the progress bar, and
  `POST /api/v2/profile/confirm-many` and `/hide-many` {ids} change a group in one transaction with one
  conflicts refresh and one `profile` event. The page is `pages/check.js` (route `check`) with its cards
  in `record/check.js`. Above the queue, `check.best(con)` gives "Your best experiences" (`best` in the same
  reply): the tiered items not hidden and not archived, core then supporting then not judged
  (`tiers.order`), each tier by `check.strength` then `check.recency`, at most `check.BEST_MAX` (25), and
  the archived ones apart (`archived`). `record/best.js` shows them with Confirm, Edit and Move to archive
  (`PUT .../tier` archive), the archive folded away with Move up a tier (supporting), and **Sort them
  again** (`POST /api/v2/profile/tiers`).
- Tiers (`tiers.py`, migration 0025): each job, education, project, achievement and certificate has a
  `tier` (`core`: what the person would lead with for the jobs they want; `supporting`: true and useful when
  a job asks; `archive`: never used for writing; '' not judged), `tier_by` (`hal` or `person`), `tier_why`
  and `tier_basis`. `tiers.refresh(con)` applies the rules first (hidden is archive; a found card nobody
  checked with no evidence of role `own` is archive; a GCSE, O level, school-leaving or secondary school
  qualification is supporting), then sends the other cards to the `profile_tier` prompt (checker role,
  JSON, `tiers.BATCH` (20) cards a call) with the person's aim (`goal.roles` and `goal.interests`, else the
  CV's headline, else their course). The prompt says `leave_out` for archive, because its words reach
  every person's prompts. A tier the person set (`PUT /api/v2/profile/items/{id}/tier`) is never changed;
  Hal judges a card again only when the fingerprint of its evidence (`tier_basis`) changes. Without an AI
  only the rules run. Every shaped item carries `tier`, `tier_by`, `tier_why` and `tier_words`
  (`tiers.shown`), with the rules applied again on read, so a card the person confirms leaves the archive
  at once. The ingest action runs the pass after each read (`profile.actions.sort`), and
  `POST /api/v2/profile/tiers` starts it as the action `profile.tiers`.
- Duplicates (`duplicates.py`, migration 0027): after each read, before sorting, the ingest action runs
  `profile.actions.dedupe`, one pass of `duplicates.refresh(con)` over the projects, courses and achievements
  Hal found and nobody checked (`candidates`: not hidden, `merged_why` not `apart`). Only pairs with the
  same project link (`merge.project_link`) are one without asking (`certain`); the rest go to the
  `profile_same` prompt (checker role, JSON, `duplicates.BATCH` (60) cards a call, sorted by name so likely
  twins share a call), which answers `{"groups": [[id, ...]]}`. Each card (`duplicates.card`) carries its
  link's `host` (none for a profile host such as github.com) and the `repositories` it holds by name
  (`duplicates.repositories`), and the prompt asks the AI to group the cards of one website: a card named
  after the site, one named as its address, and the repository it is built from. A shared file is shown to the AI but is
  no proof; a CV, letter, transcript or listing (`duplicates.listing_files`: a file whose project facts
  name three or more projects, counted under the names of the cards a tidy moved them from) is not shown
  and never counts as shared. A pair whose dates are `YEARS_APART` (2) or more years apart with no shared
  file is never joined (`never`), nor two projects whose repositories or project folders differ (`apart`,
  from `duplicates.card_units` over `projects.note_units`: a repository's note counts for each card it
  speaks for, a note inside a project folder only for the folder's own card), nor a card holding such a unit
  and one holding none whose names, the unit's folder name and the site each card is (`duplicates.site`:
  its name when that is a web address, else the host of a front-page link, counted by the address's first
  part in lower case) share no distinctive word (`unrelated`,
  over `projects.distinctive_words`: an acronym, or a word of `projects.DISTINCT_MIN` (5) or more letters
  not in `projects.GENERIC_WORDS`), unless they share a project link. Each group goes into its
  `target` (the card a CV names, then `check.strength`, then the oldest) with `store.merge_into`, as
  "Merged into <name>", and holds only cards that share the target's link or that the AI put with the
  target itself, so pairs never chain; `store.merge_into` refuses a target that was merged away. `refold`
  puts a card's own sources before moved ones of the same rank, so the target keeps its name unless a
  better-ranked file, such as the CV, names it otherwise. Without an AI only `certain` merges.
  `duplicates.listing` gives each target with its merged cards to `GET /api/v2/profile/check` (`merged`),
  shown as "Hal merged these" (`record/check.js`, `MergedCards`), and
  `POST /api/v2/profile/items/{id}/unmerge` (`duplicates.undo`) takes them back out with `store.unfold`
  and marks every card of the group `apart`, so Hal never merges them again on its own. Folded cards are
  left out of the check queue's `done` and `total`, and a merged card's name is one of its target's
  `writing.aliases`.
- The person's merges ("Same as...", `record/same.js`, on the project and job cards of the check queue and
  of "Your best experiences"): `POST /api/v2/profile/items/{id}/same-as` {"into": kept id} runs
  `duplicates.same_as(con, item_id, into)`, which refuses a kind outside `tiers.TIERED_KINDS` and calls
  `store.same_as`. That merges a card of any state, unlike `store.merge_into`, which Hal's own pass uses and
  which still refuses a card the person checked (through `store.fold_away`). Its evidence moves with
  `moved_from`, each moved `said` takes the kept card's naming fields (`store.NAMED_BY`) so `refold` never
  renames the kept card, a hidden kept card is shown again as `hal`, and the merged card is hidden with
  `merged_why` from `store.SAME_AS` by the state it had ("You checked it, then said it is the same as
  <name>"). `store.unfold` gives that state back, so `edited` fields are never worked out again.
  `tiers.keep_judged` takes the kept card's evidence fingerprint afresh, so a tier Hal's AI gave stays; a
  context the person chose stays as it is. `duplicates.listing` reports Hal's merges and the person's apart
  (`by` "hal" or "person", `duplicates.merged_by`); the page shows the person's as "You merged these" with
  the same Undo (`duplicates.undo` takes both). `duplicates.hidden_twins` gives each shown card with the
  cards the person hid (not merged, not `apart`) whose evidence is all repository or project files
  (`owners.PROJECT_FILES`) the shown card also rests on, as `hidden_twins` of the check payload, shown as
  "Hidden cards from the same files" with a "Same as" button; nothing merges them without the person.
- Contexts (`contexts.py`, migration 0026): each card of the tiered kinds has a `context` (`degree`,
  `team`, `work`, `personal`, or '' when nothing shows it) and `context_by` (`hal` or `person`). A job is
  work and a course degree work by their kind; a project, achievement or certificate is read from its fields
  and evidence with `writing.stage.project_kind`. The sort pass stores Hal's reading (`contexts.refresh`),
  and writing reads it afresh (`contexts.current`) unless the person chose one with
  `PUT /api/v2/profile/items/{id}/context`, which Hal never changes. Shaped items carry `context`,
  `context_by` and `context_words`, the page's words ("Part of my degree", "University team project",
  "At work", "In my own time"), and `record/best.js` shows them as one choice on each best card.
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
