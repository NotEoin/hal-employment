# Changelog

Each version has two parts. "What's new" is in plain words and is shown on the Updates page.
"Developer notes" is for people changing the code.

## 2.0.0 (in progress)

### What's new

- A new app window: a menu down the left, a line at the top that says what Hal is doing (with Why? and
  Stop), search or jump with Ctrl K, and the list of shortcuts with `?`.
- Light and dark looks, a simple menu (five items and More), and a screen that says to open Hal from its
  shortcut when the window is not signed in.
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
- The daily screens are in the new app: Home, Jobs, Applications, Documents, Messages, Your record, Activity
  and Settings. `jobhunt open` opens the new app; Classic stays at `/classic/` for My CV.
- Home lists what needs you today, most urgent first (applications waiting for you, interview and test
  emails, documents to make again, work Hal could not finish, your to-dos), then new matches, this week's
  numbers, your goal and what Hal did today, each line with Why?
- Jobs: Matches and Saved with Sort, place, working pattern and kind filters, and J, K, A, S and N on the
  keyboard. Hidden lists every reason Hal hid jobs, with a way to change each in one press and "Re-check
  jobs". The job page lists every point and the total; "This does not matter to me" takes one requirement
  out of that job's score. "Not for you?" keeps your reason, with Undo. Saving a job with a closing date
  adds a to-do two days before it closes. "Add one advert" reads an advert from its address. Your goal keeps
  its versions, and an older one can be restored.
- Applications: a board (Getting ready, Form filled, Sent, Interview, Offer) and a page for each application
  with four steps. Documents come first; you check and edit them and press "Approve and fill in the form".
  The Form step marks every answer "Fine" or "Check this" and says where it came from. Hal says the same
  words in its own window and through the browser helper, and on the last page you press Submit yourself.
  "I sent it myself" and the site's thanks move it to Sent with a follow-up date. Discarding keeps your
  reason.
- Every application has its own numbered folder, `Documents/Hal Employment/Applications/0412 Company -
  Title`, holding exactly what you approved. Documents made before this version move into these folders
  once, by the job id in each old `pack.md`; a job that shared an old folder is marked "Documents need
  making again" on Home.
- Documents: each application's files with Open, Download and Copy, Download all, Open the folder (only on
  the computer Hal runs on) and Copy-paste details for forms Hal cannot fill. New how-to: "Send an
  application yourself".
- Messages: the emails Hal sorted and Hal's own alerts in one list; opening one marks it read.
- Activity: everything Hal did, by day, with "Why and what", Undo where a change can be undone, an area
  filter and Show earlier.
- Your record: what is in the folder, Ask your record (with the files each fact came from), drop files in to
  add them, and a download for every file. The record is searched by words with no AI needed, or by meaning
  when the AI on this computer answers. Files in your record's Private folder are never sent to the AI.
- Settings: every setting, with search, "Changed" and Reset, "Restart Hal" and "Re-check N jobs" when a
  change needs them. Is Hal working? with a Fix button per check and a help file with your details taken
  out. Privacy and data: where your data is, what leaves this computer, a zip of everything (no passwords,
  keys or private answers) and Delete everything, which keeps Hal's program files and your record. Hal's
  instructions to the AI can be read and changed under Advanced.
- Hal runs its own schedule: searches, email checks, the daily summary, reading your record, a nightly backup
  and a tidy. Every run shows in Activity with Stop. A missed search runs once when Hal starts. Pause stops
  searches and the summary; email checks carry on. Your old `jobhunt-*` timers keep running their jobs while
  they are on, and Hal's schedule skips those, so nothing runs twice.
- "Start Hal when the computer starts" works on Windows and Linux. Pop-ups and phone alerts follow your
  settings and quiet hours.
- Hal's instructions to the AI name no one: your name, studies and the roles you want come from your
  settings. Forms are filled from your settings. A salary, start date or notice answer is kept for that job
  only, and a "why us" answer or an essay is never reused. A box for a code a site emails you is never filled
  from earlier answers or by the AI.
- The daily summary goes only to one address of your own. Job sites see "HalEmployment/<version>" and never
  your email address. Job sites are searched in the places of your goal.
- Hal draws your CV and letter itself and reads PDFs itself, so poppler is no longer needed; accented names
  stay as you write them, and a CV builds without module marks or a website folder. A cover letter's header
  matches the CV sent with it.
- The owner's Hal add-on (only with "Use my Hal setup"): the Pipeline note, spoken urgent alerts, Hal's
  models as AI choices, a copy of each drafted pack in the Applications folder you choose, and the `/jobs`
  command written from a template that names no one.
- My CV replaces Classic's CV screens. The line on top always says what employers get ("Hal's copy of your
  CV, version 4", or "your original file, unchanged"), with a preview. Your CV shows the CV as Hal prints it,
  your file, what employers get and a glance at the checks and suggestions. "How your CV is made" offers
  "Use my CV as it is", "Help me improve it" and "Build me a fresh CV".
- Every change to your CV is a new version that never changes. Versions lists them all: tick two to compare
  them, use any one again, or move one to the bin, where it stays for 30 days. Saves from the classic CV
  screen become versions too.
- Hal keeps its own copy of your CV. Your website CV and the changes you made in Hal are imported once, as
  versions; Hal never reads or writes your website again. An edit made after renaming an entry is now kept.
  Anything the import could not place is listed on Home and My CV until it is back.
- Add your CV as a PDF or Word file, or paste its text. Hal keeps your file exactly as it is and reads it into
  sections. "Check what Hal read" shows your file beside Hal's reading; anything Hal was not sure of, or could
  not find word for word in your file, says "Check this", and a correction is a new version. Hal says at once
  when it cannot read a file (a picture of a CV, over 10 MB or 6 pages, an old .doc file) and what to do.
- Eleven CV types cover any career, from humanities, technical, science, business and any-subject graduates
  to students looking for paid internships and placement years, career changers, experienced people, school
  leavers, academics and creative work. Hal suggests one with a reason, shows tips for each, compares your CV
  under two types with no AI, and lets you copy a type and change it as text (a mistake is shown with its
  line).
- The Checks tab: a score for how job-site scanners read your CV (fixed published weights, no AI, the same
  CV always gets the same score) with "Fix it" for symbols and headings, the checks of your CV type, a
  recruiter's read (Hal's opinion, part of no score) and your CV against one saved job.
- Four looks (Classic, Modern and Compact are "Safe for job sites"; Two columns "May confuse job sites"),
  each shown as a real preview. The preview is the page Hal prints. PDFs are printed by a browser Hal
  controls, never your own (Microsoft Edge on Windows, Playwright's Chromium on Linux, never a snap or flatpak
  browser), and every PDF is checked. When no browser can print, Hal makes the PDF itself and says why.
  Names with accents print in every look. Download Hal's copy as PDF, Word, plain text, JSON Resume or the
  website's cv.json. A cover letter can be printed in the CV's look.
- Suggestions: Hal reads your CV one section at a time against your CV type and suggests one change at a
  time, each with why, what it is based on and its evidence. Spelling, symbols, dates, words to avoid,
  too many entries and section order are found with no AI. Accept, Edit, Undo, Accept all (for consistency,
  symbol and date fixes), "Leave it as it is" with your reason (and "Remember this"), and "Try another way".
  Hal never adds a number, year or qualification it cannot find: it leaves a gap such as `[how many?]`.
  Locked lines are never touched.
- Adjust for each job: No, A little or More, each change undoable before you approve; off, with the reason,
  while you send your original file. "Build me a fresh CV" asks one question at a time and writes a CV of
  your type; module marks are optional.
- Letters, messages, answers, interview notes and the search for who to message follow your CV type, your
  tone, length, sign-off and "Never use" list. Every letter says what it used and what was checked, on the
  application page too, and a letter Hal writes while filling a form is checked the same way. Ask for
  changes on an application's documents now works. The pack's checklist no longer tells you to run a command.
  `python -m jobhunt letter` and `check` find the pack in the numbered application folder again.
- The setup guide: eleven steps from Welcome to All set, saved as you go. "Save and finish later" closes it
  and the next opening carries on at the same step; a new install opens it by itself, once, and skipped
  steps stay on Home's "Finish setting up" card until they are done.
- About you is filled in from your CV ("From your CV"), reads pay leniently ("Hal reads this as 22,000
  pounds a year") and never resets a value you changed in Settings, including right to work, when you go
  through the guide again.
- Your record step makes the record folder with its subfolders and "Start Here.md", or finds the one already
  there and leaves its files alone; "Add files" copies files in, so File Explorer is never needed.
- Email step: Hal saves the pasted Gmail app password only when Gmail accepts it. Phone alerts step: a private
  ntfy code shown as a QR code, kept when you go through the step again, and "Send a test". All set starts
  the first search once; Home shows "Checked 40 of 300 jobs" while it runs.
- "What are you looking for?" (setup step 4): career stage, studying, course end, kinds of role, a career-area
  starter, kinds of work, places, office or home and lowest pay, ending in "Here is what I understood"; "Yes,
  save this" saves it as one goal change.
- Where Hal looks (Jobs) lists every site Hal searches with when it was last searched, jobs seen and kept,
  and the error when a site failed. Hal's own sites follow your career and can be switched off; sites you
  added can be removed. Hal works without job-board keys and says what is missed ("Reed has no key, so Hal
  skips it."), with a two-step "Get a free key" guide.
- Searches are built from your goal: each role with each kind of role you chose, at most 12 per site, in your
  places. Internship, placement, insight and apprenticeship searches are added only when you chose them.
- "Add a job site or careers page": paste an address and Hal says what it is, lists its jobs scored against
  your goal, and Save adds the site and keeps the ticked jobs, with Undo in Activity. Hal knows Greenhouse,
  Lever, Ashby, Workable, SmartRecruiters, Recruitee and Personio, linked RSS or Atom feeds and schema.org job
  data; other pages say "Hal cannot read this page by itself yet. Add one advert instead." "Watch a company"
  looks for the company's board on every system Hal knows.
- Job alert emails from UK student, graduate, charity, public sector and apprenticeship boards become
  matches. Your old board list and watched companies move into Where Hal looks once.
- "How should Hal think?": Hal recommends your Claude subscription or "On this computer" from this computer's
  memory and graphics card. Claude in three steps: Install (no admin prompt, no console window), Sign in
  (Claude's own page; Hal never sees your password) and Test. A free Claude plan is explained before any call:
  "Your Claude plan cannot run Claude Code. It needs Claude Pro or Max."
- Settings, Hal's brain: "Signed in as <email> (Claude Pro)" with Sign out, and the allowance in counts ("Hal
  used Claude 23 times today. 12 were background checks"). When the sign-in runs out, Home shows "Sign in to
  Claude again".
- Browser helper 1.0: paired once from setup's "Browser helper" step, with steps and pictures for Opera,
  Google Chrome, Microsoft Edge, Brave and Vivaldi. The one-time code works once, for two minutes, and the
  helper gets its own key; requests without it are refused. Each application keeps the helper that carries
  it, so a desktop browser and a laptop browser never take each other's commands.
- Hal copies the helper into a visible folder every time it starts (`Documents/Hal Employment/Browser helper`
  on Linux, `%USERPROFILE%\Hal Employment\Browser helper` on Windows). An older helper is asked once to reload
  itself. The old 0.4 or 0.5 helper is refused unless Advanced, "Accept the old browser helper" is on.
- Hal installs from one file: `install-hal-windows.cmd` (double-click; no admin prompt, no typing) or
  `install-hal-linux.sh`. It gets its own tools, signs in to GitHub in the browser, makes the shortcuts,
  copies an older Hal it finds (a USB stick install is copied in, never moved), starts Hal and opens
  the setup guide. Running it again repairs the install.
- Settings, Updates: the version, "Check now", a daily check, What's new, **Update Hal** and **Go back to the
  previous version**. An update stops Hal, backs up your jobs, gets the new version, sets it up and starts Hal
  again; if any step fails, Hal goes back, says "Nothing was lost" and does not offer that version again. Hal
  never updates while it is filling in a form. Only the owner's signed commits install, once the owner has
  added a signing key; until then Settings, Updates says updates are not yet checked.
- Use Hal from another computer: Hal and its AI keep running on the desktop, and a laptop on the same home
  network opens the full app through SSH with the launcher "Hal Employment (desktop)". Hal still listens only
  on the desktop itself. In a window from another computer, "Show me the form" asks that computer's browser
  helper, Hal's alerts show in that window, and Settings lists those windows with Sign out.
- Windows: pages no longer lose files on first load, a page closed mid-reply is not logged as an error, "Is
  Hal running?" answers in half a second, and Claude's reset times and the daily allowance are read in UK time.
- Packs: an advert that lists no requirements is matched on its main duties, so the Fit table and the
  letter's Evidence line are never empty. The letter greets the person the advert names, and the message
  uses the name the advert gives, never a guessed Ms or Mr. Hal answers only the questions an advert writes
  out, never adds the AI's notes about its own drafts, and leaves an empty employment record out of what it
  asks the AI.
- The company check shows a Wikipedia article or a Companies House entry only when it names the whole
  employer, not a namesake. Suggestions on My CV and a recruiter's read speak to you; AI wording never says
  "I" or "my", and a job's CV never gains a result your CV does not have.

- What Hal remembers is built. "Waiting for you" lists Hal's suggestions from your choices, each with why,
  the evidence and what would change; nothing changes until "Yes, do this". "Not now" asks again after a week
  and "Never suggest this" keeps it from coming back. "Learned" lists what you said yes to, where it came from
  and when, with a switch and Delete that put back what it changed (unless you changed it yourself since). Each
  tab shows how many items it holds.
- Hal suggests a smaller travel distance when you hide four or more jobs as "Too far" in a week, all beyond it
  (the jobs are the evidence); adding a word to the roles you want when five or more jobs you saved or applied
  to share it; and locking a CV line you kept twice with "Keep my wording". Hiding the fourth such job says
  "Hal has a suggestion for you". Hal also looks for patterns each night. Home shows "Hal has N suggestions for
  you". Settings, Advanced, "Suggestions from your choices" switches this off for jobs, your CV or both.
- Notes about job sites (What Hal remembers): one card per site with its application system, whether it needs
  an account, the last visit and its notes. Every note says where it came from ("Built in", "You told Hal",
  "Hal learned") and how often it worked, and can be switched off, reworded or shared. A note Hal learned
  waits for "Use this". Notes that disagree are named on the card, and a note that worked and then keeps
  failing is marked "May be out of date".
- "Something wrong here?" (on the Form step and on each site's card): say what went wrong in your own words.
  Hal shows the note it will remember and lets you use it on just this site, every site of the same
  application system, or everywhere; nothing is used until you save it. When Hal's AI is busy or missing, your
  words are kept as a note for the site at once. Then: try again with the note, finish it yourself with the
  documents beside the tab, or skip the job for now. "Share this note" copies "Hal note for <site>: <text>";
  a note that holds one of your answers is never shared, and no note ever answers a private question.
- Notes drive both ways of filling forms: opening the application form directly, pressing "Apply" first, the
  cookie banner's button, and typing an answer the way a site wants it (only the city, only digits, a UK
  phone number, a date format, a length). Each note used is listed beside the answer it changed.
- Every answer on the Form step says where it came from: "Why this answer?" names the profile setting, the
  saved answer and where it may be used, the site note and whose it is, the researched salary, the CV line or
  record passage an AI answer draws on, or why Hal left it for you. A private answer says only "From your
  private answers. These are never shown to the AI or saved in logs." The Form step lists "Notes Hal used on
  this site", once each. A site note can answer a question on that site only; Hal answers in this order:
  private answers, notes about the site, your saved answers, your profile, your CV and record, then the AI.
- What Hal remembers, Saved answers: every saved answer with "Where may Hal use this?" (Everywhere, Only on a
  site, Only for one job, Never reused), how often it was used, and Delete. Answers marked "Choose where to
  use it" come first and are never used until you pick. Private answers are never listed.
- Applying: an upload is checked, and a file the site dropped is shown as "the site did not take the file",
  never as done. The cookie clicker never presses a button inside the application form. Lists that take
  several answers get every answer. Google Forms are read. Signing in to one employer's Workday or Greenhouse
  site no longer marks every other employer on that system as signed in.
- "Open forms in: This window" is ready: the form's tab is brought forward before Hal reads, fills or presses
  in it, and Hal's checks every few seconds while it watches a form no longer switch tabs.
- Browser helper: code walls, bot checks and closed adverts are seen the way Hal's window sees them; a form
  with many slow lists gets longer to fill; reading a form opens only the lists still empty.
- A new README says what Hal Employment is, how to install it, run it and test it, and where your data
  lives. Two guides for people using Hal: "Getting started" (setup step by step, a normal day, where things
  are, What Hal remembers, what to do when something goes wrong) and "Your privacy". SECURITY.md,
  THIRD_PARTY_NOTICES.md and LICENSE ("private software, licensed to named collaborators for personal use;
  not for redistribution") are added.
- Windows: Hal's searches, phone alerts and email reach their websites on a new laptop. Hal now asks Windows to
  check each website's certificate, as Edge does. The phone alerts QR code shows again.
- Setting up the browser helper: the first button copies the extensions page's address and opens your
  browser, ready for you to paste it, and the folder step says how to paste the folder.
- Windows: the shortcuts show Hal's eye, and the install window is called "Installing Hal Employment".
- Home's "new matches" counts only the jobs Matches shows.
- Installing Claude no longer shows Claude's own advice about PATH, which Hal follows by itself, and the
  Install button no longer comes back for a moment once Claude is in.
- Update Hal works on a new install: Hal stops, installs the new version and starts again, and Settings then
  says Hal is up to date. A failed update stops the new version before it goes back.
- Windows: the remote launcher works from an ordinary Hal folder (`C:\Users\...\hal-employment`) when Hal's
  command is not installed; it was refused as "a space or symbol in its name".
- The setup guide's CV step keeps the way to start you chose, even when you chose before the page had
  finished loading.
- Words: the line at the top says "Next job search at 13:30"; one of a thing reads in the singular ("1 answer
  needs you", "1 change", "Where your application is"); a window from another computer asks "Is <computer>
  asleep?"; Copy says "Copied" only when the browser took the words. Help has "How applying with Hal works",
  the first-application guide again. Each thing has one name ("Open the setup guide", "Roles you want",
  "Settings, You").
- My CV: Your file has "Replace", and the upload when you have not added a file yet.
- Your record: when the AI cannot answer, the passage is shown as "From your record", with the reason.
- Settings: App password says where it is kept; Where Hal looks counts your sites; Browser helper folder shows
  the folder with Copy; lists of choices and the goal history use plain words; Earlier versions of your goal
  shows once; a button found by search opens its group; in a window from another computer, rows name the
  computer Hal runs on. No setting sticks out of its card in a laptop-sized window.
- The CV preview shows the focus ring when you Tab to it, and Is Hal working? wraps long folder paths on a
  narrow screen.

### Developer notes

Final pass (2026-10-23): a first install on a fresh Windows 11 computer, the Windows test fixes, and the
words, accessibility and polish of every page.

- Windows install: `jobhunt/core/tls.py`, with `truststore` for Windows in `pyproject.toml` and `uv.lock`; the install
  files carry their pinned SHA-256 checksums; `platform.open_browser_window()`, and
  `POST /helpers/open-extensions-page` returns `address`; tests have the `laptop` page size (1033 x 690, Hal's
  Edge window on a 1280 x 800 screen); the server makes `config/local-api-key` at start, `GET /api/v2/version`
  takes the local key (`session_or_local`), and on Windows the updater ends a stuck server with `taskkill /F`
  without `/T`.
- Windows tests: an action still going 10 s after a `HalServer` test is stopped fails the test by name (enter the
  fakes a request may use before `HalServer`, so they come out after the server stops); browser pages report
  console errors with their address, and a page that never draws names the requests that got no answer; the
  Linux install scripts are parsed with Git for Windows' bash on Windows.
- Gates: G2 runs `ui.test_shell.NoStubs` (no `/* STUB:`, `ui/stub.js` or "This part is being built" in
  the app); `ui/stub.js` and `router.stubPages()` are gone. The words test is strict (no `HAL_WORDS_STRICT`).
  `LEGACY_ALLOWED` is empty, so any `CONFIG` read outside `jobhunt/config.py` fails G7.
- Code: `ui/clipboard.js` `copyText()` for every Copy; `Upload` takes `fresh` (start at the drop zone);
  `core/status.py` `NEXT_WORDS`; `parse.SHOWN_PREFIX`, and `describe()` words a `multi` value by its labels;
  `/settings/schema` rows carry `remote`, and `advanced.port` is `remote="label"`; the classic
  `server.Handler` drops a `ConnectionError`; `tests/support/ui.py` has the one `real_app()`.

Notes about job sites, engine fixes, form answers with their why, learning, and the documentation
(2026-10-23). How these parts work is in
[docs/dev/architecture.md](docs/dev/architecture.md) and [docs/dev/site-notes.md](docs/dev/site-notes.md).

- Notes about job sites: `jobhunt/sites/` (`notes.py`, the notes contract with `switch`, `edit`,
  `accept`, `delete`, `share_text`, `seen` and `because`; `seed.toml`, 25 built-in notes with stable ids;
  `behaviour.py`, one function per engine hook; `cards.py` for `GET /sites`; `report.py`; `actions.py` with
  `sites.propose_note` and the data task `sites.import_state`; `routes.py`); prompt `site_note` (priority 0,
  role checker); `memory/sites-tab.js` and `dialogs/report.js`. A person's choice about a built-in note is a
  `site_notes` row with `seed_id`, so an update changes the note's action without undoing it.
- Engines: the hooks `open_url`, `press_first`, `cookie_button` and `field_transform` in `browser.py` and
  `extension.py`, each use recorded through `notes.used` with a trace line on the field and in
  `form["notes_used"]`; `browser.site_key` is `notes.site_key`; the upload check (`UPLOAD_FAILED`); practice
  pages `upload-check`, `upload-kept`, `consent-in-form`, `multiselect`, `gform`, `background-tab`,
  `popup-later`, `city-only` and `apply-first`. `Browser._run` services Playwright while idle; the page-world
  submit block counts its holders; `extension.reset()` forgets a helper whose poll was still held open;
  `tests/test_extension.py` runs on `HelperBrowser`.
- Answers: `autofill.propose(..., notes=)` and `ask_model(..., notes=)` take the page's `NoteSet`; every
  field has `why` and `notes_used`; `autofill.by_you()`; `jobhunt/answers/` (`saved.py`, `routes.py`: `GET`,
  `PUT`, `DELETE /answers`); `app/form-step.js` and `memory/answers-tab.js`.
- Learning: `jobhunt/learn/` (`patterns`, `items`, `places`, `actions`, `routes`, `home`, `settings`,
  `undo`, `schedule`); action kind `learn.patterns` (priority 2, no AI); `GET/POST /memory/waiting`,
  `GET/PUT/DELETE /memory/learned/{id}`; Activity undo type `learned_item`; `pages/memory.js`,
  `memory/waiting-tab.js`, `memory/learned-tab.js`, `css/memory.css`.
- Documents and CI: README.md, `docs/README.md` (the index), `docs/user/`, SECURITY.md,
  THIRD_PARTY_NOTICES.md, LICENSE, the final `docs/dev/architecture.md`, and `tests/test_docs.py` (the index,
  links, how-to shape, README commands read back from `--help`, notices, pinned CI actions). CI's Linux rows
  install poppler-utils, the test output goes to `tests.log`, and the Windows job fails unless the PowerShell
  parse check, the real Edge print and the platform layer's Edge and browser-finding tests each said `ok`.
- Joining the parts: the two `sites.notes` stand-ins are gone and every note test runs on the real store;
  the driver passes the form's `NoteSet` to autofill, counts each note answer once and keeps the person's
  edits as "You typed this."; a list answer stays a list for a multi-select; "This window" of
  `applying.window` is enabled; the memory tabs export `useCount()`; `learn/schedule.py` runs
  `learn.patterns` at 03:30 and `learn.enabled` is a watched setting; `POST /jobs/{id}/not-for-me` returns the
  waiting suggestion's id for a "Too far" reason; the Form step's report carries its job and application
  system, so "Saved. What now?" offers its three ways out; G5 also runs `test_autofill.WhyThisAnswer` and
  `test_answers_routes`; the shell smoke's fake server answers `/sites` and `/answers` and names each 404;
  `HelperBrowser.start_helper` waits for its own helper; `tests/test_docs.py` has no files still to come.

Remote use, the browser helper, the setup guide, job sources, Claude setup, and install and update
(2026-10-23), with the fixes from two Windows test passes and from a run of the documents on a real model.

- Remote use: `cli/remote.py` (`open-url [--remote] [--local-port N] [--host-label NAME]`, which prints
  exactly one line; `helper-files [--tar]`; `remote-launcher --os linux|mac|windows`), `jobhunt/remote/`
  (`service.ensure_running(port)`: a server that answers, else the platform's unit read back with `systemctl
  --user is-active`, else the transient unit `hal-employment-remote`), the routes `/remote/instructions`,
  `/remote/launcher`, `/remote/sessions`; `notify.notify` publishes a `notify` event and holds back the desktop
  pop-up while a remote window's stream is open (`state/remote-window.json`, 30 s expiry);
  `core/remote.js` (`onlyThere`, `popUpState`, `askForPopUps`); launchers `scripts/remote/hal-remote.sh` and
  `hal-remote.cmd`.
- Browser helper 1.0: `apply/helpers.py`, `helper_routes.py`, `helper_mirror.py`, `helper_health.py`,
  `apply/browsers.toml`, `extension/pair.js`; helper keys through `auth.register_verifier`; per-helper command
  queues; a fixed manifest key, so the id is `ljbgefmpodgbnjbnhokpcgcchiicmaja` everywhere.
- Setup guide: `jobhunt/setup/` (`state`, `routes`, `home`, `record_folder`, `email_test`, `phone`) and
  the routes `GET /setup`, `PUT /setup/{step_id}`, `POST /setup/later`, `GET /setup/about`, `PUT
  /setup/about/fields` (`{values, seen}`: a field equal to what the page showed is never written), `POST
  /setup/record`, `POST /email/test`, `POST /notify/phone/topic`, `POST /notify/phone/test`. `setup.state.done()`
  is also true for a developer checkout that held jobs before the guide existed, so the owner's install never
  opens it by itself.
- Sources: `find/sources_registry.py`, `find/seed/sources.toml`, `find/detect.py`, `find/source_actions.py`
  (`find.detect_site`, data task `find.import_boards`), `find/source_routes.py`, `find/source_undo.py`,
  `sources/feeds.py`, the generic alert parser in `gmail.py` with `find/seed/alert_senders.toml`; the goal routes
  `GET /goal/starters`, `POST /goal/quiz` and `POST /goal/understood`. See
  [docs/dev/architecture.md](docs/dev/architecture.md).
- Claude setup: `ai/claude_setup.py` (`executable`, `auth_status`, `install`, `sign_in`, `sign_out`,
  `recommend`), the kinds `ai.claude_install`, `ai.claude_sign_in` and `ai.test`, the `/ai/*` routes, `ai/health.py`
  and `ai/ai_home.py`.
- Install and update: `scripts/updater.py` runs `core/updates.py` under uv's Python outside `.venv` (a
  transient unit `hal-employment-update` on Linux, a detached breakaway process on Windows);
  `scripts/install/pins.toml` and `pins.py` (`--check`, `--fill`, `--make DIR`); `scripts/install/allowed_signers`
  (comments only until the owner adds a key); `core/importer_usb.py`; `cli/install.py` (`--windows`, `--linux`,
  `--dev`, `--remove`); `core/update_actions.py` and `core/update_home.py`. See
  [docs/dev/release-and-update.md](docs/dev/release-and-update.md).
- Windows: the listen backlog is 64; `ConnectionError` ends a reply quietly; local times go through
  `clock.local()` and `HAL_EMPLOYMENT_TZ` fixes the zone for tests; `runner.idle()`, `AppServer.stop()` and
  `driver.stop_watching()` wait for files to close. The test helper browser
  (`support.practice_site.HelperBrowser`) runs on a thread of its own (`on_helper`, `in_tab`); the made-up CV
  files are kept byte for byte (`.gitattributes`); `letter` and `check` close their database.
- Real-model fixes: `pack.written_out`, `pack._requirements` (summary fallback), `pack.contact_names`,
  `pack.salutation`, `pack.untitled`, `pack.drop_notes`; `reputation.names` and a whole-name `reputation._key`;
  `prep.request` and the pack's fit, letter and verify requests leave the record out when it is empty.
- Joining the parts: the remote group is drawn by its own panel; the AI panel's info rows show once; the
  model rows offer the models of the AI in use; Home says "The rest are optional." only when they are;
  `install` and the remote commands are in `NEW_COMMANDS` with their help goldens; the shell smoke's fake
  server answers Where Hal looks and the goal step; the helper step draws only the found browser's pictures;
  `helper-files` tars `helper_mirror.source()`.

The CV studio (2026-10-23): the CV store, CV reading, CV types and checks, looks and printing, CV
suggestions, and documents shaped by the CV type. They are described in
[docs/dev/architecture.md](docs/dev/architecture.md) (CV store, CV reading, CV types and checks, looks and
printing, CV suggestions, documents).

- CV store: `cvstudio/model.py` (schema 2: stable ids, `validate`, `apply`, `diff`, `to_v1`/`from_v1`,
  `not_found`), `cvstudio/store.py` (the 8.12 contract, versions, the 30-day bin, files by SHA-256),
  `cvstudio/import_v1.py` (data task `cv.import_v1`, registered from `cvstudio/legacy_actions.py`),
  `jobhunt/cv.py` over the store (`cv.SITE`, `OVERRIDES`, `BUILT_OVERRIDES` and `ORIGIN` are gone),
  `cvstudio/routes.py`, `home.py` and `search.py`. Classic `POST /api/cv/source` runs the kind `cv.source`.
- CV reading: `cvstudio/parse.py`, `parse_actions.py` (`cv.parse`), `parse_routes.py`, the `cv_parse`
  prompt; made-up CVs from `tests/support/make_cv_fixtures.py` (`--check` compares).
- CV types and checks: `cvstudio/types.py` and `cvstudio/types/*.toml`, `cvstudio/checks.py`
  (`scanner`, `type_checks`, `for_type`, `fix`, `for_version`, `recruiter_read`), `type_routes.py`,
  `checks_routes.py`, `checks_actions.py` (`cv.recruiter`). `cvscore.keywords` takes a type's keywords.
- Looks and printing: `cvstudio/looks/`, `render.py`, `printer.py`, `export.py`, `look_routes.py`,
  `cvpdf.draw_view`, `letter.render(..., look=)`; fonts in `static/app/fonts/cv/` (Source Sans 3 and Source
  Serif 4 from fontsource 5.3.0, fetched by `scripts/dev/vendor_web.py`); `tests/goldens/scanner/`
  (`HAL_RECORD_SCANNER=1` re-records) and the non-blocking `.github/workflows/print-compare.yml`.
- Suggestions: `cvstudio/suggest.py`, `tailor.py` (`for_job`), `fresh.py`, `suggest_actions.py`
  (`cv.suggest`, `cv.another`, `cv.fresh`), `suggest_routes.py` (with `GET /cv/fresh/questions` and
  `GET /cv/adjust`), prompts `cv_suggest` and `cv_another`.
- Documents: `cvstudio/context.py` (`build`), one letter path `pack.write_letter`,
  `pack.read_letter_notes`, `contacts.queries`, `lead_pattern` and `hiring_titles`, `docs/write_routes.py`
  and `docs/write_actions.py` (`docs.change`, `docs.rewrite_letter`, `docs.prep`, `docs.contacts`).
- Joining the parts: `router.HtmlResponse` takes a `policy`, so the CV preview sends `render.POLICY` as its
  header; `PUT /cv/look` and `PUT /cv/type` save through the store, and `PUT /cv/look` answers the `/cv`
  header; GET `/cv`'s glance card holds the scanner score and type checks; an upload makes the file the
  baseline; CV reading knows every built-in type's section titles; the whole-CV suggestion target maps to
  the model's `""`; `suggest.recheck` changes only rows still pending; the runner no longer reports an
  action that has just ended as busy in another window; the application page shows the letter's notes and
  `docs.prepare` passes its action to `pack.build`. Every test stand-in for the CV modules is deleted, and
  `tests/support/people.cv_type(key)` sets a person's CV type through the real store.

The schedule and platform layer, identity from settings, the record and PDFs, documents, Home, Messages,
Activity, applications, jobs and the Settings page (2026-10-23).

- Schedule and platform: `core/platform/` (`current()`, browsers, app window, start at login, hidden
  and detached children, `retire_legacy_timers(dry_run)` and `restore_legacy_timers()`), `core/scheduler.py`
  and `core/builtin_schedule.py` (jobs as persisted actions; a job whose old timer is enabled is skipped),
  `core/schedule_routes.py` (pause, restart, schedule, retire timers), `core/health.py` (`Check`,
  `CheckResult`, `Fix`, `run_all`, `problem_count`), `notify.py` and `vault.py` on settings, and the Hal
  add-on in `jobhunt/addons/hal/`. `hal-commands/` is gone. `import --retire-timers` now runs.
- Identity: 14 prompt files under `ai/prompts/` filled by `pack.person()` from `you.*` and the goal;
  `autofill.profile()` from `you.*`; saved answers only in `answers2` with a scope; `tasks.after_sent`;
  `digest.recipient`; the inbox address and password from settings and secrets; `tests/support/people.py`
  (three made-up people with LEAKS lists) and `tests/test_prompts_neutral.py`.
- Record and PDFs: `pdfdraw.py` (Hal's PDF writer), `cvstudio/pdfread.py` on pypdfium2,
  `scripts/dev/compare_pdf_readers.py`; `record.py` BM25 search with optional search by meaning and private
  folders kept from the AI; `jobhunt/evidence/` (routes, `record.sync` and `record.ask` kinds, health check);
  `cv.site()` reads the imported website CV. `coursework.py` is gone.
- Documents, Home, Messages, Activity: `docs/folders.py`, `docs/files.py`, `docs/relayout.py` (data
  task `docs.relayout_packs`), `core/home_routes.py` (`GET /home`, merged from every `home.py`; lower
  `urgency` first), `jobhunt/mail/` (`mail.check`, `notify.digest`), `stats.week`, and the four pages.
- Applications: `apply/actions.py` (the approval rules and the `docs.prepare` and `apply.*` kinds),
  `apply/routes.py`, `apply/queries.py` (board, application page), `apply/home.py`, `apply/search.py`;
  Classic's slow routes start actions. See [docs/dev/architecture.md](docs/dev/architecture.md).
- Jobs: `find/routes.py`, `find/feedback.py`, `find/job_actions.py`, `find/goal_routes.py`,
  `find/home.py`, `find/search.py`, and the Jobs and job pages.
- Settings: `core/health_routes.py`, `core/helpfile.py`, `core/privacy_routes.py` and
  `core/privacy_actions.py`, the Settings page and its panels.
- Fixes joining the parts: the importer and Classic record tests run on the real stores and kinds (both stubs
  are gone); one `GET /home`, with the core's paused and move-data items on Home's urgency scale; the record
  check is a `health.Check`; a health Fix may name a POST route (`{"post": path}`), and Pause in Settings
  posts `/status/pause`; `privacy.export` is found by `runner.load_kinds()`; Delete everything works for a
  home inside a worktree; the base CV folder is read live and `/pages` no longer falls back to poppler; a CV
  with no title can be tailored; each letter is drawn with the CV sent with it; old pack folders are found
  through `pack.PACKS`; drafted packs are copied into the add-on's Applications folder; `after_submit` is
  gone; course notes come only from the record; `jobhunt open` no longer sends Classic; the board list is read
  from the import's copy; job sites search the goal's places; the fake server answers every built page
  (`MADE_UP_PAGES`), and the shell tests poll the rail and title from Python. The `pack` golden prints the
  numbered application folder.
- Repo clean-up: `config.toml`, `boards.toml`, `companies.txt` and
  `excluded-companies.txt` are deleted (the import copied them); the test fixtures name made-up universities,
  cities and degrees. `check_personal.py` gains `--tree` and `--base REV`, and `checkpoint.sh` runs it in both
  modes; it prints nothing on this tree.

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
