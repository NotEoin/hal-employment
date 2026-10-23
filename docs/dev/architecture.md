# Architecture

How Hal Employment's server, its shared contracts and each area fit together, for anyone adding an area or
changing the core. How to run the tests is in
[testing.md](testing.md); how Hal reaches people and updates itself is in
[release-and-update.md](release-and-update.md).

## The shape in one paragraph

`python -m jobhunt` is the one entry point: the command line (`jobhunt/cli/`, every old command unchanged)
and the server (`serve`). The server is the standard library's HTTP server on `127.0.0.1`, serving the app
(`web/static/app/`, Preact without a build step), the API (`/api/v2/*`, routes found by name in each area)
and Classic (`/classic/`, the old screens). Slow work runs as persisted actions with an Activity row each;
the schedule starts them in-process. Data lives in the folders of `core/paths.py`, never in the code
folder. Each area is a subpackage (`find/`, `apply/`, `cvstudio/`, `docs/`, `setup/`, `remote/`, `ai/` and
so on) that plugs in through module names (Discovery, below), so adding an area edits no core file.

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

`now_utc()`, `iso()` (`2026-10-22T09:00:00Z`), `local()`, `from_local()` and `local_day()`.
`HAL_EMPLOYMENT_FAKE_NOW` fixes the time in tests and `HAL_EMPLOYMENT_TZ` the local zone (`time.tzset()` is
Unix only, so tests never set `TZ`). Code that shows or plans in local time calls `clock.local()`, never
`astimezone()` with no zone. `uk_zone()` is UK time on any computer: the zone database's Europe/London, or
on Windows, which has none without `tzdata`, the UK's own summer-time rule. `db.now()` returns `iso()`; older rows end in `+00:00` and compare the same on 19
characters.

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
- **Gate G7** (`tests/test_settings_registry.py`): every key read is registered, and `CONFIG` is never read
  outside the shim: `LEGACY_ALLOWED` is empty, so a new read fails.

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

A browser that closes the page mid-reply is normal: any `ConnectionError` while replying (Linux
`BrokenPipeError`, Windows `ConnectionAbortedError`, WinError 10053) is dropped without a log line or an
Activity row. `AppServer.stop()` stops serving, ends the event streams and waits for the requests in flight,
because each holds a database connection and Windows cannot remove an open file. The listen backlog is
64, not socketserver's 5: Windows refuses connections past the backlog, which broke a page's first burst.
`app.answering(port)` gives the connection 0.5 s, as Windows takes about 2 s to refuse a closed loopback port.

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
- `runner.idle(timeout)` waits until no action of this process is queued or running, each has closed its
  connection and lock file, and every row has landed (tests call it before removing their home).
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

## Notes about job sites (`jobhunt/sites/`)

- Every page trick the engines know is a note: a kind (one hook each), a scope (`global`, `ats`, `site`,
  `tenant`, `job`), a condition, an action and words for the person. `notes.for_page(url, ats=, job_id=,
  step=)` gives the page's `NoteSet`; the most specific active note of a kind wins, and two that disagree are
  a conflict named on the site's card. The 25 built-in notes ship in `sites/seed.toml` with stable ids; a
  person's switch-off or rewording is a `site_notes` row naming the `seed_id`, so an update changes a built-in
  note's action without undoing their choice.
- `notes.site_key(url)` is the one rule for "which site is this": the registrable domain, except that Workday
  and Greenhouse keep the employer (`acme.wd3.myworkdayjobs.com`, `boards.greenhouse.io/acme`).
  `browser.site_key` and `apply/queries.py` use it, and sign-in state is kept per employer in `sites.account`.
- "Something wrong here?" (`sites/report.py`, `sites/actions.py`) sends the person's words and a survey of the
  page (labels and button names only, never answers, never a private question) to the `site_note` prompt.
  A valid answer is shown as a draft with its scope choices and is used only after Save; with no AI, the
  words are saved at once as a `note` for the site.
- How notes are chosen, the hook table and the report flow: [Notes about job sites](site-notes.md).

## Engines and note hooks (`browser.py`, `jobhunt/extension.py`, `apply/driver.py`)

- Both engines read the page's `NoteSet` at the hook points below. Built today: `open_url`,
  `press_first`, `cookie_button` and `field_transform` (`city_only`, `digits_only`, `uk_phone`,
  `date:FORMAT`, `max_len:N`). The helper's hooks run on the server side, so `content.js` needs no notes.
  With only the built-in notes on, each hook gives what the old constants gave
  (`test_with_only_built_in_notes_hal_behaves_as_today`). Each use is recorded with `notes.used` and its trace
  line (`{note, field, what}`) is kept on the field and in `form["notes_used"]`, which the Form step lists.
- An upload is checked by finding the file's name in the input or on the page; a dropped file is
  `UPLOAD_FAILED`, never done. The cookie clicker never presses inside a form. Multi-select lists get every
  value. Google Forms frames are read. With "Open forms in: This window", the helper brings the tab forward
  before each read, fill and press; watches (`peek`, `look`) never switch tabs.
- `Browser._run` services Playwright while its queue is empty, so a tab the page opens is followed at once and
  a person's own Submit in Hal's window is not held until Hal's next call. The page-world submit block counts
  its holders, so two commands in one tab never lift each other's block.
- Not built yet: forms in frames for the helper (`allFrames`), drop zones, review-page detection, and the
  note kinds `wait_redirect`, `form_frame`, `ignore_frame`, `needs_account`, `buttons`, `widget`,
  `upload_via` and `timing` (each is one more `notes.first(kind)` at its hook point).

## Saved answers and "Why this answer?" (`autofill.py`, `jobhunt/answers/`)

- `autofill.propose(..., notes=)` answers in this order: private and carer answers, site notes
  (`field_answer`, `field_skip`), saved answers (by scope), the profile, the CV and record, then the AI. A note
  never answers a question `autofill.private_question` matches. The driver passes the form's `NoteSet`, so the
  employer's own notes apply, not only the advert's.
- Every field carries `why` (the words under "Why this answer?") and `notes_used`. A private answer's why is
  only "From your private answers. These are never shown to the AI or saved in logs.", in the API and again in
  `app/form-step.js`. `autofill.by_you()` resets the why when the person's value replaces Hal's.
- `answers/saved.py` and `answers/routes.py` serve the Saved answers tab (`GET`, `PUT`, `DELETE /answers`):
  each answer with "Where may Hal use this?"; rows waiting for a choice are never used until the person picks;
  a private question is never listed, changed or deleted (404).

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
  under 0.6, the year has two digits, or a field a CV normally has is missing. The AI's reading is put in
  the rules' shape first (a grade written after the qualification goes in `grade`, a lone date in `start`, a
  heading the rules know keeps its kind), so the same file read by rules or AI gives the same ids. With no AI, a `PrivateLeak` or
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
  `recruiter_read` is the AI's opinion and part of no score: a short summary written to "you" (the main
  strength, then the one change that helps most), as a recruiter for the type's kind of candidate.
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
  numbers, years and qualifications Hal cannot find become `[how many?]`, `[when?]` or `[which?]` (any
  bracketed question counts as a gap, so Accept refuses it and a job's CV drops it), no I or we in the AI's
  wording when the type writes without them (a fresh CV's AI profile too), no new
  banned phrase, the fingerprint (sha1 of target, trigger id and first after) is not one the person left, and
  the type's limits hold.
- Accept makes a new version (`origin="improved"`, `by="you"`); Undo and Accept all are versions too. Leaving
  a suggestion keeps the reason; "Remember this" is a `cv_preferences` row.
- `tailor.for_job` (No, A little, More) keeps each change as a model change in `job_cvs`, and rebuilds the
  job's CV from the base version plus the changes still on. An AI change for a job only rewords: one with a
  gap, or of a kind that adds what came of the work (Results, Add), is dropped. Sending the original file forces No.
- The whole CV is the target `cv` in a suggestion (`suggest.WHOLE_CV`) and `""` in the model
  (`suggest.model_target`). `recheck` touches only rows still pending, so a decision made while the tab
  reloads is kept.
- `fresh.py` builds a CV of the chosen type from the interview answers and turns failing checks into
  suggestions.

## Documents (`cvstudio/context.py`, `pack.py`, `docs/write_*.py`)

- `cvstudio.context.build(job=None)` is the one source of every document prompt's placeholders: the person
  (`you.*`, `goal.*`), the CV type's `[documents]` and `[writing]`, `cvdocs.*` and up to four voice examples
  from `answers2` rows of scope `never` (never anything `autofill.private_question` marks). Job facts are never
  placeholders, so a prompt reads the same for every job of one person.
- One letter path, `pack.write_letter` (advert, record, fit map, letter, checks), serves the pack, the letter
  drafted at form time (`documents.draft_letter`) and the writing actions. The pack's `## Letter notes`
  section says Tone, Length, Evidence and Checked; the application page reads them back
  (`pack.read_letter_notes`), and `docs.prepare` passes its action so the record passages used show under
  Activity "Used".
- The advert reading (`pack._analyse`, prompt `analyse`) keeps only the questions the advert writes out
  (`pack.written_out`: most of a question's words are in the advert), so a model's note about questions is
  never answered. When the advert states no requirement, its main duties are the requirements, and the
  role summary stands in when the reading still has none (`pack._requirements`), so the fit map is never
  empty. The letter greets the first person the advert names (`pack.salutation`), else "Hiring Team"; the
  message greets them by that name, never with a title (Ms, Mr) the advert does not give (`pack.untitled`).
  `pack.drop_notes` takes a model's closing notes about a draft ("(About 75 words ...)") and Markdown
  bold out of every letter and message before its checks run.
- `docs/write_routes.py` serves `POST /applications/{id}/documents/change`, `documents/letter/rewrite`,
  `/prep` and `/contacts`; each starts its kind in `docs/write_actions.py` (`docs.change`,
  `docs.rewrite_letter`, `docs.prep`, `docs.contacts`) and returns `{action_id}`.

## Sources (`find/sources_registry.py`, `find/detect.py`, `find/source_routes.py`)

- Every place Hal searches is a `sources` row. Built-ins come from `find/seed/sources.toml` and follow the
  scoring preset until the person switches one (`config.chosen`). `fetch()` touches only the network and
  raises `Skipped` with words for a site left out on purpose (no key, read from email); `record()` writes
  `source_runs` and the source's health. `BUILTIN_FETCHERS` is the same dict as `cli.find.STANDALONE`.
- Queries come from `queries(source, goal)`: each role with each chosen kind's phrases, at most 12, the
  source's own `config.queries` first.
- Detection (`find/detect.py`) is pure over the address and the page's text; a look is held in the server
  process for 30 minutes under `detected_id`. Saving a site is undone through `find/source_undo.py`.
- Job alert emails from the senders in `find/seed/alert_senders.toml` (or `email.alert_senders`) go through
  one generic parser in `gmail.py`: each linked job becomes a lead.

## How Hal learns (`jobhunt/learn/`)

- `learn/patterns.py` finds three patterns: "Too far" for 4 or more jobs in 7 days, all beyond X miles (a
  smaller distance); 5 or more saved or applied-to jobs sharing a title word missing from `goal.roles` (add the
  word); two "Keep my wording" answers on one CV line (lock it). A pattern only calls `items.offer()`; it never
  changes a setting, note or the CV (`test_nothing_changes_without_your_yes`). A waiting suggestion whose
  pattern no longer holds is withdrawn on the next run.
- `learn/items.py` applies a yes from the live value: a setting through `settings.set(..., by="learned")` with
  its own Activity row and Undo (type `learned_item`), or a CV lock as a `cv_preferences` row of kind
  `lock_line`. The replaced value is kept, and switch off, Delete and Undo put it back only while the value is
  still the one Hal set. "Not now" asks again after 7 days; "Never suggest this" keeps the fingerprint
  (`finding:max_distance:<current>:lower`, `finding:roles:add:<word>`, `cv:lock_line:<target>`).
- Distances: scoring has none yet, so `learn/places.py` holds 160 UK town centres and measures straight-line
  miles from "Where you live". A job whose location names no town in the table is never evidence.
- When it runs: the action `learn.patterns` (priority 2, no AI) starts 20 s after the last job event, CV
  suggestions event or finished search (a listener started with the server), nightly at 03:30
  (`learn/schedule.py`), and for the "Too far" pattern alone when a job is hidden, so the reply's
  `suggestion_id` says one is waiting. Learning's own events never start a run. Settings, Advanced,
  "Suggestions from your choices" (`learn.enabled`) switches it off for jobs, the CV or both.

## Schedule (`core/scheduler.py`)

- Jobs come from `schedule.py` or `*_schedule.py` modules that define `JOBS`. A thread wakes every
  `TICK_S`; `tick()` starts each due job as a persisted action of its kind, so every run has steps, Stop and
  an Activity row. A command-line job runs as a hidden child (`python -X utf8 -m jobhunt <args>`, one-hour
  limit), each printed line a step.
- The next run of each job is stored in `schedule_state`, so a slot missed while the computer was off runs
  once at start (`schedule.catch_up`). Each tick compares the settings its plan was made from, so new times
  apply without a restart, including a change made by another process.
- A job whose old `jobhunt-*` systemd timer is still enabled never runs in-process (no double running). Pause
  is the `meta` row `schedule:paused`, read the same by every process.

## Platform (`core/platform/`)

`platform.current()` is everything Hal asks of the computer: opening files and windows, pop-ups, start at
login, finding browsers, hidden or detached children, retiring the old timers. No other module tests
`os.name`. `linux.py` and `windows.py` take their outside world (command runner, registry reader, file
checks, home) as constructor arguments, so tests drive the real code with fakes. The app window and PDF
printing never depend on the person's browser: Microsoft Edge on Windows; on Linux the first Chromium-family
browser (never a snap or flatpak one for printing), else a tab.

## Health, Home and search providers

An area adds lines to "Is Hal working?" with `CHECKS` in a `health.py` or `*_health.py` module, needs to
Home in `home.py` or `*_home.py`, and results to Ctrl K in `search.py`. `core/health.run_all` turns a
check that raises into a "bad" row naming it, so one broken area never hides the others.

## Setup guide (`jobhunt/setup/`, `static/app/setup/`)

`setup/state.py` keeps one `setup_steps` row per step reached (`todo`, `done`, `skipped`) and the guide's
place in the row `guide`. It never reads or writes a setting, so going through a step again cannot reset a
value changed in Settings; the steps save their own fields through the settings store. `state.done()` is
what `/session` and `/open` ask before opening the guide by itself. The frame loads each step from
`static/app/setup/steps/<id>.js` (default export `Step`), so an area adds or changes its step without
touching the frame. Only Email, Phone alerts and Updates can be skipped.

## Claude Code (`ai/claude_setup.py`)

Install runs Anthropic's own per-user script with no window and no admin rights (PATH in
`HKCU\Environment` on Windows). Sign-in is `claude auth login` in a small window; Hal reads only `claude
auth status` (JSON, kept 60 s) and never sees a password or token. In a test run the real installer never
runs: tests use `tests/support/fake_claude/`.

## Install and updates (`cli/install.py`, `core/updates.py`, `scripts/updater.py`)

The install files clone Hal's files, run `uv sync`, then `jobhunt install --windows|--linux`, which makes
the shortcuts, the start-at-login entry and the install record; `install --dev` is a developer's checkout.
Updates are `git pull --ff-only` with commit-signature checks, `uv sync`, migrations (with a backup) and a
restart, run by `scripts/updater.py` under uv's own Python so `.venv` can be replaced while it runs.
`core/updates.py` uses the standard library and `core.paths` only, and reaches git, uv and the server through
one `World` object, so tests run the real steps against local bare repositories. The details, signing
included, are in [release-and-update.md](release-and-update.md).

## Using Hal from another computer (`jobhunt/remote/`)

The laptop runs a launcher (`scripts/remote/hal-remote.sh` or `.cmd`, filled in by `remote/launcher.py`)
that opens an SSH tunnel and asks the desktop, over SSH, for a one-time link (`jobhunt open-url --remote`).
The server still listens only on the desktop's `127.0.0.1`; the session's `remote` flag is the only sign of
a remote window, because tunnel traffic looks local. `local_only=True` routes answer 409 `remote` there,
and `core/remote.js` turns desktop-only buttons into downloads. `remote/service.ensure_running` uses a
server that answers, else the platform's unit (read back with `is-active`), else a transient unit of its
own. `remote/sessions.py` writes `state/remote-window.json` with an expiry while a remote window is open,
so every process holds back desktop pop-ups.

## Browser helper (`apply/helpers.py`, `jobhunt/extension.py`, `extension/`)

- A helper is paired once: a signed-in window asks for a one-time code (two minutes, single use), the helper
  swaps it for its own key, and only SHA-256 hashes are stored. Every helper request carries `X-Hal-Key`
  and, when the browser sends one, an `Origin` of an allowed extension id; the checks plug into `web.auth`
  through `auth.register_verifier`.
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

## Importers (`core/importer.py`, `core/importer_usb.py`)

`jobhunt import` moves the owner's old in-checkout data into the data folder: it builds a staging home
beside the target through `paths.set_override`, migrates it with the normal code, and appears only at the
seal, by one rename. `--from-usb-install` copies a USB stick install the same way and only renames
the old folder at the end. Both copy, never move, and write counts and hashes, never content, to the report.

## Hal add-on (`jobhunt/addons/hal/`)

What Hal Employment does only for someone running the Hal tools, all behind `addon.enabled`: the Pipeline
note, application note copies, spoken alerts, the Hal tools' model choices, the `hal off` pause and the
`/jobs` command. Nothing in the Hal tools is edited from here.

## Names (`core/naming.py`)

`file_name("cv" | "letter")` fills the `cvdocs.*file_name` template with `you.name`; `folder_name(n, company,
title)` makes `0042 Company - Title`. Both fold to ASCII and drop characters Windows refuses.

## App shell (`web/static/app/`) [6]

Conventions for page and dialog owners:

- A page module exports its component under the name in `core/router.js` ROUTES (`Home`, `Jobs`,
  `JobPage`, `Apps`, `AppPage`, `Messages`, `CvStudio`, `Docs`, `Record`, `Activity`, `Memory`,
  `Settings`); it gets the route's arg as `arg` (already decoded). Its first heading is `Head` (or an
  `h1`), which gets focus on each route change.
- No stub may be left: G2's `NoStubs` fails on `/* STUB:`, `ui/stub.js` or "This part is being built" in
  any file of the app. Copy goes through `ui/clipboard.js` `copyText()`.
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
