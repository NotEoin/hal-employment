# Notes about job sites

How Hal keeps what works on each job site as notes, how the form engines read them, and how a person's
report becomes a note. The code is `jobhunt/sites/`.

Job sites differ in small ways: one wants the city without the county, another hides its form behind an
Apply button, a third has a cookie banner over the form. Rather than a growing list of special cases in the
engines, each of these is a note with a source the person can see, switch off or reword.

## Files

| File | What it does |
| --- | --- |
| `sites/seed.toml` | The built-in notes, with stable ids |
| `sites/notes.py` | `Note`, `NoteSet`, `for_page`, `used`, `site_key`, `propose`, `add`, switch, edit, delete, share |
| `sites/behaviour.py` | One function per engine question, worked out from a page's notes (see "Hook points") |
| `sites/cards.py` | The cards of the "Notes about job sites" tab (`GET /sites`) |
| `sites/report.py` | "Something wrong here?": keep the report, draft a note, save it, the three ways on |
| `sites/actions.py` | The action `sites.propose_note` |
| `sites/routes.py` | The routes under What Hal remembers |
| `ai/prompts/site_note.txt` | The prompt that turns the person's words into one note |
| `web/static/app/memory/sites-tab.js`, `dialogs/report.js` | The tab and the dialog |

## A note

A note has a kind, a scope, conditions, an action and words:

- **Kind**: one of `open_url`, `wait_redirect`, `press_first`, `form_frame`, `ignore_frame`,
  `cookie_button`, `needs_account`, `buttons`, `field_transform`, `field_answer`, `field_skip`, `widget`,
  `upload_via`, `timing` and `note`. Each kind is read at one hook, so a note acts the same way every time.
  `notes.KINDS` lists what each kind's action may say; `notes.validate` refuses anything else. `widget` has
  no Enter option, because Enter can send a form.
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

### Uses and "may be out of date"

`notes.used(note_id, job_id, ok=None, detail="", url=, step=)` counts a use (a win or a failure when `ok`
says so) and writes a `site_events` row (`note_used` or `note_failed`). A note used three times that then
fails three times in a row is marked "may be out of date"; it stays on, and the next win clears the mark.
`notes.because(note)` gives the words for a field's or step's trace: "because of your note from 20 Oct
(civic-example.org.uk)". `notes.seen(url, account=)` records a visit and the site's sign-in state per tenant.

## Hook points

Both engines (Hal's own window and the browser helper) and the form filler ask these questions. Each takes
the page's `NoteSet` from `notes.for_page(url, ats=, job_id=, step=)`.

| Engine question | Function |
| --- | --- |
| Which address holds the form? | `behaviour.application_url(job, notes)` |
| Which words mark Apply, thanks, applied, closed, checks, blocks, code walls? | `behaviour.page_words(notes)`; Next and Submit stay `guard.NEXT_WORDS` and `guard.FINAL_WORDS`, which a note can only add to |
| Has the advert closed? | `behaviour.closed(survey, status, notes)` |
| Is this a holding page to wait out? | `behaviour.wait_redirect(url, notes)` |
| Could this frame hold the form? Which hosts embed forms? | `behaviour.frame_ok(url, notes)`, `form_hosts(notes)` |
| Which cookie buttons, in which order? | `behaviour.cookie_patterns(notes)` |
| May Escape close a list? | `behaviour.no_escape(notes)` |
| How long to wait, how many Apply presses? | `behaviour.timing(notes)` |
| What to type in a date box, or a searched list? | `behaviour.date_value`, `search_terms` |
| How to reshape one answer? | `behaviour.transform(value, how)` with `NoteSet.for_field(label)` |
| How did you hear about this job? | `behaviour.how_heard(source, notes)` |
| Is this a job board? | `behaviour.job_board(url, notes)` |

With only the built-in notes active, these answer what the engines' own constants would
(`test_with_only_built_in_notes_hal_behaves_as_today`). `NoteSet.wire()` is the list sent to the browser
helper with every command.

## "Something wrong here?"

1. `POST /sites/report {job_id, site, what, words}` keeps a `site_reports` row and starts
   `sites.propose_note`.
2. The action sends the words, the page survey and the note kinds to the `site_note` prompt. The survey holds
   question labels and button names only, never answers, and leaves out every question
   `autofill.private_question` matches. A reply that passes `notes.validate` becomes a draft with the scopes
   "Just this site", "All <system> sites" and "Everywhere"; nothing is used until `POST
   /sites/reports/{rid}/save`. When the AI is busy, missing or answers something unusable, the words are saved
   at once as a `note` for the site.
3. `POST /sites/reports/{rid}/next {choice}`: `retry` starts a fresh `apply.fill`; `myself` marks the
   application `by_hand` and answers the Documents route; `skip` stops Hal and closes its tab, keeping the
   application.

`GET /sites/notes/{id}/share` gives "Hal note for <site>: <text>", to paste into an issue so a useful note
can become a built-in one. A note that holds a fixed answer is never shared.
