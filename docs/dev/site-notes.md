# Notes about job sites

How Hal keeps what works on each job site as notes, how the engines read them, and how a person's report
becomes a note. Build spec sections 10.3 to 10.5. The code is `jobhunt/sites/`.

## Files

| File | What it does |
| --- | --- |
| `sites/seed.toml` | The built-in notes, with stable ids: the page tricks the engines had written into them |
| `sites/notes.py` | `Note`, `NoteSet`, `for_page`, `used`, `site_key`, `propose`, `add`, switch, edit, delete, share |
| `sites/behaviour.py` | One function per engine question, worked out from a page's notes (see "Hook points") |
| `sites/cards.py` | The cards of the "Notes about job sites" tab (`GET /sites`) |
| `sites/report.py` | "Something wrong here?": keep the report, draft a note, save it, the three ways on |
| `sites/actions.py` | The action `sites.propose_note` and the data task `sites.import_state` |
| `sites/routes.py` | The routes of API v2 under What Hal remembers |
| `ai/prompts/site_note.txt` | The prompt that turns the person's words into one note |
| `web/static/app/memory/sites-tab.js`, `dialogs/report.js` | The tab and the dialog |

## A note

A note has a kind, a scope, conditions, an action and words:

- **Kind**: one of the note kinds (`open_url`, `wait_redirect`, `press_first`,
  `form_frame`, `ignore_frame`, `cookie_button`, `needs_account`, `buttons`, `field_transform`, `field_answer`,
  `field_skip`, `widget`, `upload_via`, `timing`, `note`). Each kind is read at one hook, so a note acts the
  same way every time. `notes.KINDS` lists what each kind's action may say; `notes.validate` refuses anything
  else. `widget` has no Enter option, because Enter can send a form.
- **Scope**: `global`, `ats` (an application system such as `workday`), `site` (the registrable domain,
  `reed.co.uk`), `tenant` (one employer on a shared system: `acme.wd3.myworkdayjobs.com`,
  `boards.greenhouse.io/acme`) or `job` (an address prefix or a job id). `site_key(url)` gives the key Hal
  keeps a site's notes and sign-in under; Workday and Greenhouse keep the employer.
- **Conditions**: address conditions (`url_contains`, `url_matches`, `host_contains`, `host_contains_any`,
  `not_host_contains`, `job_id_prefix`, `step`) are checked once in `for_page`. Field conditions (`label`, a
  pattern; `field_kind`; `source`, where Hal found the job) must be named by the caller of `NoteSet.first`,
  `all` or `for_field`, so a note for one question never lands on another.
- **Origin**: `builtin`, `you`, `learned` or `hal`. Every note on screen says where it came from.
- **Status**: `active`, `proposed` (Hal learned it; used only after "Use this") or `off`.

### Which note wins

`NoteSet.first(kind, **cond)` gives the most specific active note: job, then tenant, then site, then
application system, then global; among equals the one with more conditions, then the person's own over
learned over built in, then the newest. Two notes for the same thing that disagree are a conflict
(`NoteSet.conflicts()`), shown on the site's card, unless the loser is a global built-in default, which a
site note exists to override. Kinds whose notes add up (`buttons`, `note`, frames, cookie buttons) never
conflict; `timing` merges key by key over the defaults.

### Built-in notes and updates

Built-in notes are in `seed.toml`, each with a stable id (`seed:<id>` in the API). When the person switches
one off, rewords it or Hal counts a use, a `site_notes` row with `seed_id` holds that; its `text` is empty
until the person rewords it, and its action always comes from the seed. So an update can change a built-in
note's action or words without undoing a switch-off or the person's wording
(`test_a_switched_off_built_in_note_stays_off_after_an_update`). Built-in notes cannot be deleted.

With only the built-in notes active, every function in `behaviour.py` answers exactly what the old engine
constants did (`test_with_only_built_in_notes_hal_behaves_as_today`). The one deliberate difference is that
frames on `docs.google.com/forms` are read.

### Uses and "may be out of date"

`notes.used(note_id, job_id, ok=None, detail="", url=, step=)` counts a use (a win or a failure when `ok`
says so) and writes a `site_events` row (`note_used` or `note_failed`). A note used three times that then
fails three times in a row is marked "may be out of date"; it stays on, and the next win clears the mark.
`notes.because(note)` gives the words for a field's or step's trace: "because of your note from 20 Oct
(civic-example.org.uk)". `notes.seen(url, account=)` records a visit and the site's sign-in state per tenant.

## Hook points

The engines and the form filler call these at the hooks below. Each takes the page's
`NoteSet` from `notes.for_page(url, ats=, job_id=, step=)`.

| Engine question | Function | Old code it replaces |
| --- | --- | --- |
| Which address holds the form? | `behaviour.application_url(job, notes)` | `browser.application_url` |
| Which words mark Apply, thanks, applied, closed, checks, blocks, code walls? | `behaviour.page_words(notes)` | `APPLY_BUTTON`, `SUCCESS`, `APPLIED`, `CLOSED`, `BOT_CHECK`, `BLOCKED`, `CODE_WALL`; Next and Submit stay `guard.NEXT_WORDS` and `guard.FINAL_WORDS`, which a note can only add to |
| Has the advert closed? | `behaviour.closed(survey, status, notes)` | `browser._closed` |
| Is this a holding page to wait out? | `behaviour.wait_redirect(url, notes)` | the Adzuna wait in `_settle` and `background.js` |
| Could this frame hold the form? Which hosts embed forms? | `behaviour.frame_ok(url, notes)`, `form_hosts(notes)` | `NOT_FORM_FRAME`, `ATS_FRAME` |
| Which cookie buttons, in which order? | `behaviour.cookie_patterns(notes)` | `COOKIE_BUTTONS` |
| May Escape close a list? | `behaviour.no_escape(notes)` | `_close_list` |
| How long to wait, how many Apply presses? | `behaviour.timing(notes)` | `NAV_TIMEOUT`, `MAX_PRESSES`, `extension.FILL_WAIT` |
| What to type in a date box, or a searched list? | `behaviour.date_value`, `search_terms` | `browser._date`, the comma split in `_pick` |
| How to reshape one answer? | `behaviour.transform(value, how)` with `NoteSet.for_field(label)` | nothing (new) |
| How did you hear about this job? | `behaviour.how_heard(source, notes)` | `autofill.HOW_HEARD` |
| Is this a job board? | `behaviour.job_board(url, notes)` | `applying.aggregators` |

`NoteSet.wire()` is the list sent to the browser helper with every command.

## "Something wrong here?"

1. `POST /sites/report {job_id, site, what, words}` keeps a `site_reports` row and starts
   `sites.propose_note` (priority 0, role checker).
2. The action sends the words, the page survey and the note kinds to the `site_note` prompt. The survey holds
   question labels and button names only, never answers, and leaves out every question
   `autofill.private_question` matches. A reply that passes `notes.validate` becomes a draft with the scopes
   "Just this site", "All <system> sites" and "Everywhere"; nothing is used until `POST
   /sites/reports/{rid}/save`. When the AI is busy, missing or answers something unusable, the words are saved
   at once as a `note` for the site.
3. `POST /sites/reports/{rid}/next {choice}`: `retry` starts a fresh `apply.fill`; `myself` marks the
   application `by_hand` and answers the Documents route; `skip` stops Hal and closes its tab, keeping the
   application.

`GET /sites/notes/{id}/share` gives "Hal note for <site>: <text>", for whoever looks after Hal to build the
note in. A note that holds a fixed answer is never shared.

## Data task

`sites.import_state` (after migration 14) reads the old `signed-in.json` and marks each site signed in,
per employer where the old file named a whole system, and copies the "too much effort" events to
`sites.effort`. It only reads: the old file stays for the engines until their sign-in check moves to
`sites.account`.
