# Changelog

What changed in each version, in plain words.

## 2.0.0 (in progress)

### What's new

Hal Employment is now an app with its own window, for any career, on Windows and Linux.

- **Two AI modes.** On its own, Hal uses Claude Code or a model on this computer. With Hal installed and
  its gateway answering, Hal uses Hal's local model and never falls back to Claude by itself, nor to an
  online model the gateway fronts. The gateway address must be on this computer, so the token stays here. Local calls
  hold a gpu-light claim when it is installed. Settings shows the mode. New setting keys: `ai.provider`
  accepts `hal`, plus `ai.hal_url` and `ai.hal_token_file`; saved settings keep working unchanged.
  Hal's mode warns when the gateway token file can be read by group or others.
- **Runs in WSL2.** The Linux install works inside WSL2 on Windows 11 ([steps](docs/howto/run-on-windows-with-wsl2.md)). Start at login says
  to turn systemd on instead of claiming it is enabled, the Claude sign-in names the terminal to install,
  and the local-model wait ignores programs `nvidia-smi` cannot name. Not yet run on a Windows PC.
- **A new app.** A menu down the left, a line at the top that says what Hal is doing (with Why? and Stop),
  search or jump with Ctrl K, light and dark looks, and a simple menu of five items. Hal opens from
  its shortcut with a one-time link, so a web page in any browser cannot read or change anything in it.
- **Home** lists what needs you today, most urgent first, then new matches, this week's numbers, your goal
  and what Hal did today, each line with Why?
- **Your goal, not a built-in one.** Hal scores jobs against your places, roles, kinds of role, working
  pattern and lowest pay, and every job keeps its reasons. Nothing about any one person is built in.
- **Internships count.** Graduate jobs and schemes, paid internships, placement years, insight and spring
  weeks, apprenticeships, part-time and fixed-term work are all kinds you can choose. Unpaid roles stay
  hidden unless you ask for them, and schemes for current students are hidden once you have finished.
- **Jobs**: Matches, Saved and Hidden, with filters and keyboard keys. Hidden says why each job was hidden
  and how to change it. "This does not matter to me" takes one requirement out of a job's score.
- **Where Hal looks**: every site Hal searches, when it last searched and what went wrong. Add an employer's
  careers page (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, job feeds and
  pages with search-engine job data), watch a company by name, or add one advert. Job alert emails from UK
  student, graduate, charity, public sector and apprenticeship sites become matches.
- **Applications**: a board from Getting ready to Offer, and a page for each application with four steps.
  You check and edit the documents, press "Approve and fill in the form", and Hal fills it in. Every answer
  says where it came from ("Why this answer?").
- **Hal never presses Submit**, Send or a final Apply, in any mode. On the last page it says "Check the
  marked answers, then press Submit on the employer's site yourself." and watches for the site's thanks.
- **Documents**: each application's files in a numbered folder, with Download, Copy and copy-paste details
  for forms Hal cannot fill. "I sent it myself" moves an application to Sent.
- **My CV**: upload a PDF or Word file and "Check what Hal read", or build a fresh CV one question at a time.
  Every change is a version you can compare and go back to. Eleven CV types cover any career, from
  humanities and technical graduates to career changers, school leavers and academics.
- **Checks and suggestions**: a scanner score with fixed published weights and no AI, the checks of your CV
  type, and suggestions one section at a time, each with its reason. Hal never adds a number, year or
  qualification it cannot find; it leaves a gap such as `[how many?]` for you.
- **Four looks** for your CV and letter, each previewed exactly as it prints. PDFs are printed by a browser
  Hal controls and checked; when none can print, Hal draws the PDF itself. Download as PDF, Word, plain
  text or JSON Resume.
- **Letters and answers** follow your CV type, your tone, length and "Never use" list. Every letter says
  what it used and what was checked.
- **Your choice of AI.** A local model through Ollama, another local server that speaks the OpenAI format,
  or your own Claude Pro or Max subscription through Claude Code. Which AI Hal uses never changes by itself,
  and "Never use online AI" keeps everything on your computer. Claude use follows your daily limit and the
  hours you keep free.
- **Private answers** to equality and adjustment questions are never sent to the AI, never guessed, never
  logged and never shown again; Hal stops any AI request that would include one.
- **The setup guide**: eleven steps from Welcome to All set, saved as you go, with "Save and finish later".
  It recommends a local model or Claude from this computer's memory and graphics card, and installs Claude
  Code with no admin prompt.
- **The browser helper** fills forms in Opera, Google Chrome, Microsoft Edge, Brave or Vivaldi, where you
  are already signed in. It is paired once with a one-time code and acts only on tabs Hal opened or you
  handed to it. A helper from before pairing is turned away, with words that say to load the new one.
- **What Hal remembers**: suggestions from your choices (nothing changes until you say yes), what you said
  yes to, your saved answers and where each may be used, and notes about job sites. "Something wrong here?"
  turns your own words about a site into a note Hal uses next time.
- **Activity** keeps everything Hal did and why, with Undo where a change can be undone. Work cut short by a
  restart can carry on.
- **Settings**: every setting with search and Reset, "Is Hal working?" with a Fix button per check, and
  Privacy and data: where your data is, what leaves the computer, a zip of everything and Delete everything.
- **Install from one file**: double-click `install-hal-windows.cmd` (no admin rights, no typing) or run
  `install-hal-linux.sh`. Running it again repairs the install.
- **Updates** install only when you press Update Hal, never while a form is being filled. Your jobs are
  backed up first, a failed update goes back by itself, and "Go back to the previous version" is one press.
- **Use Hal from another computer**: a laptop on the same network opens the full app over SSH, while Hal and
  its AI keep running on the desktop.
- **Your data lives outside the program folder**, in your own user folders, so an update never touches it.
  `python -m jobhunt import` moves data from an older copy that kept it inside the program folder.
- **Part of the Hal ecosystem**: with "Use my Hal setup" on, Hal Employment writes its pipeline note into
  the Hal vault, speaks urgent alerts, offers Hal's local models and can pause with `hal off`.
- The command line keeps its commands and gains `open`, `settings`, `import`, `migrate`, `backup` and
  `install`.
