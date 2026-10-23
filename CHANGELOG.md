# Changelog

What changed in each version, in plain words.

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
