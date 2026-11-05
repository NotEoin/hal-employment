# Changelog

What changed in each version, in plain words.

## Unreleased

### What's new

- **Read your web links into your record.** Hal can read your own site or blog (up to 30 pages under the
  link you give), your GitHub profile and repositories with their READMEs, and Google Docs and Slides
  shared with anyone. Sites such as Instagram and LinkedIn need you signed in, so Hal asks you to save the
  page and drop the file in instead.
- **Read a whole folder or zip into your record.** Add a zip with **Add files** on Your record, or put files
  in the **+Inbox** drop folder, and Hal reads every document into a note of its own, CVs, transcripts and
  letters first. Each note has a summary, tags and links to the organisations, places, skills and courses
  it mentions. Code projects and a course's lecture notes each become one note. Files OneDrive or Dropbox
  has not downloaded, files with no text and copies are listed rather than read, and a fact whose words
  are not in its document is marked **Check this**. Hal carries on from the next file after a restart.
- **Your profile.** Hal keeps the facts it finds in your record as a profile: jobs, education with its
  modules and marks, skills with the words that show them, projects, certificates, achievements and links.
  Each fact names the files it came from. The same job in two CVs is one entry, written the same way in
  both or not ("Sept 2023 - present", "09/2023"), and "MS Excel" and "Excel" are one skill.
- **Your corrections stay.** Confirm, edit or hide each fact in your profile. When Hal reads your files
  again it never changes a fact you checked or edited, and a hidden fact stays hidden.
- **What does not add up.** Hal lists two files that give different dates for one job, a grade on your
  transcript that is not the one on your CV (Hal uses the transcript's until you choose), an end date
  before a start date, two full-time jobs at once, and gaps of more than six months.
- **Letters use your checked facts first.** Cover letters, answers and messages read the facts you checked
  or edited before the ones Hal found, and never a fact you hid.
- **Profile notes in your record folder.** A Profile folder holds Work history, Education, Skills, Projects
  and Inconsistencies notes, written from your profile, for reading in Obsidian.
- **Letters and answers written for the kind of job.** Hal reads which field a job is in from the advert
  and your goal, and follows that field's conventions: STAR examples against the behaviours for the Civil
  Service, the person specification for the NHS, publications and grants for academic posts, the portfolio
  link for creative work, a key skills section for technical roles. A job Hal cannot place gets general
  advice, never another field's.
- **Plainer, truer drafts.** Every letter, answer and CV suggestion is checked against Hal's writing rules:
  stock and machine-sounding words, "not just X but Y", lists of three qualities, long dashes, hedging, duty
  openers on CV lines, a letter that repeats the CV, a greeting and sign-off that do not match ("Dear Sir or
  Madam" ends "Yours faithfully"), and numbers your facts do not hold. A draft that breaks a serious rule is
  rewritten once; anything left is listed under **Check before sending**.
- **Critique as a recruiter.** On an application's Documents step, Hal reads your CV and cover letter
  against the advert as a hiring manager would: the three changes that would help most, the first six
  seconds, each requirement with the line that shows it, a grade per part with why, and comments on the
  lines it quotes. Your name and contact details are taken out first, and nothing is changed.
- **Hal learns your writing.** With **Match my own writing** on, the writer is told how you write
  (sentence length, contractions, spelling, your usual words), measured from your own letters, statements,
  essays and CVs, your rewritten answers and your edits, with two or three short passages of yours as
  examples. A word you take out of three of Hal's drafts becomes a **Never use** suggestion, and letters you
  cut short suggest a shorter length; nothing changes until you say yes.
- **Choose several career areas.** The goal step's career areas are now toggles: press every area you are
  interested in, such as Heritage and museums and Creative and media, and their example roles are joined.
- **Queue jobs, then approve them together.** **Add to queue** (or Q) beside **Apply with Hal** puts a job
  under Getting ready while you stay to choose more; Hal writes their documents side by side, and
  **Approve all** on the Applications board approves them together. **Apply with Hal** still opens the
  application at once.
- **See the documents themselves.** The Documents step shows the CV and cover letter PDFs Hal will upload
  beside the CV changes and the letter text.

- **Change the documents, or start the form from your tab.** **Change the documents** on the Form step takes
  an approved application back to its Documents step, to edit and approve them again. When Hal is stuck
  before the form, open it yourself in Hal's browser window and press **Fill in from this page**.

- **Preferences.** A new Settings section gathers how Hal behaves while you work: where forms open, whether
  their window comes to the front, closing job-alert pop-ups, keeping a site's own answers, pressing Next,
  how long to wait for you, checking adverts before writing, and reloading after an update.

### Fixed

- **Hal mode works without turning on the gateway's TCP address.** Hal Employment now reaches Hal's model
  gateway through its Unix socket when the address setting is left at its default, so `tcp = true` is only
  needed for a gateway reached through a tunnel.
- **No ten-minute wait without the browser part.** On an install without Hal's own browser window (such as
  WSL2 without the browser option), a step that needs that window now says so at once instead of waiting.
- **A site's comment box is never filled.** Signed in to Hacker News, a job post there shows a reply box, and
  Hal typed an application answer into it, one press away from posting it. Boxes known only as "text",
  "comment" or "reply" are now the site's own.
- **A search box known only as "q" is the site's own.** Hacker News's search box has no label, and Hal took it
  for a one-question form.
- **Forms filling side by side keep to their own tabs.** With several forms filling at once, one job's list of
  tabs came to hold another job's, and Hal read and filled the wrong page: Encord's form picked up Lane Clark &
  Peacock's questions, and every answer on both was lost. A tab now belongs to one job only.
- **A site's menu is not a sign-in page.** Every Hacker News page starts with its menu, ending "login", and Hal
  took each job post there for a sign-in page. A page's opening words now count only when they say so ("Log in
  to", "Forgot your password", "Welcome back"), and a "Search:" box is the site's own.
- **"First and last name" gets your full name.** "Legal First and Last Name" was given only your surname.
- **A job board's account page is left to you.** Adzuna's register page (email, password and the password again)
  was filled with your email address as the application's last page. Through the browser helper, as in Hal's own
  window, a short page with a password box is now a sign-in for you.
- **A job board you are signed in to waits for you to press Apply.** Totaljobs shows "My jobs" and "My account"
  rather than "Sign out", so Hal did not see that you were signed in and pressed its Apply, which can send your
  saved CV at once. An account's own links now count as signed in, as "Sign out" does.
- **Hacker News posts no longer lead to Y Combinator's sign-in.** The footer rule missed Hacker News's own row
  of links on a short post. A row of a site's own links (FAQ, Legal, Contact and the like) now counts as its
  footer wherever it sits.
- **A page that changes while Hal reads it is read again.** "Frame with ID 0 was removed." reached the Form step
  as it was. The helper now waits for the new page and reads it once more, and says in plain words if that fails
  too.
- **A job board's own search boxes are left alone.** Totaljobs' "Job title, skill or company" and "Town, city or
  postcode" were taken for an application's questions.
- **"This listing went offline" closes the job.** An expired Totaljobs advert saying "This listing went offline"
  was reported as a form of two questions, which were the site's own search boxes. Hal now reads it as closed.
- **A high school or second subject is not given your degree.** "What year did you graduate high school?" and
  "High School" were answered with your university, and "Secondary Major" with your degree subject.
- **Hal leaves your current pay and your past with an employer to you.** The expected salary went into "What is
  your current salary?", and Hal's model answered "Yes" to "Have you interviewed with Jane Street before?". Both
  kinds of question are now left for you, and never sent to the model.
- **A list answer the form refused gets a second try.** Greenhouse's location list found no option for "London"
  while another question's list was still open, and took it at once on its own. Hal now closes each list it
  opens, and picks each refused list answer once more by itself.
- **A site's own links are not the job's Apply.** On a Hacker News job post, Hal pressed the forum's "Apply to
  YC" link at the foot of the page and reached Y Combinator's sign-in. Hal no longer presses an Apply link in a
  page's footer, including a bar of links at the end of the page, and prefers one in the advert to one in a menu.
- **An advert with no form is not called the form's last page.** A jobs.ac.uk advert that sends you to the
  employer's own site held only two email-preference tick boxes, and Hal said it had filled the form's last
  page. A page whose only questions are optional sign-ups now counts as no application form.
- **A right-to-work question asked with sponsorship is never answered from sponsorship.** "Are you legally
  entitled to work in the United Kingdom? Will you require sponsorship?" was answered "No", from your answer to
  sponsorship. A question that asks about the right to work first is now answered as that, and one that asks
  both with a single answer is left for you.
- **The CV goes in once.** Lever relabels its upload box after each file ("Resume/CV ... Success!"), and Hal took
  the relabelled box for a new upload question, so a Spotify form got the CV four times. Once a form has its
  CV, an upload box that appears later is left alone.
- **Sign-in pages are left to you.** A sign-in page that asks for the email address first, and says so only in
  its words or its address (Y Combinator's account.ycombinator.com), was filled in with your email address and
  Continue was pressed. Hal now stops there for you to sign in yourself.
- **A double-clicked approval fills the form once.** Two presses of **Approve and fill in the form** sent two
  approvals: one failed with an error, and the other stopped the fill the first had started, which could leave
  the application with nothing running. A second press now joins the fill already under way.
- **A closed application says so.** An application whose advert had closed still showed its step, with
  **Approve and fill in the form** or the Form step's buttons. Its page now says why it closed and offers
  **Back to Matches**.
- **Queueing many jobs keeps the page clear.** Each **Add to queue** showed its own message, and twenty of
  them covered the job list. They now join into one ("Added 20 jobs to Getting ready"), and at most three
  messages show at once.
- **Queued documents carry on after a restart.** Restarting Hal while it wrote documents for queued jobs
  left them all stopped under Getting ready; Hal now starts them again by itself.
- **Getting ready says when documents were not made.** A job whose documents stopped part way said "Check
  and approve documents" with nothing to check. It now says the documents were not made, and **Make the
  missing documents** on the board starts Hal on all of them again.
- **An open page picks up an update.** A page left open while Hal was updated or restarted kept its old
  screens. It now reloads itself once Hal is back, or, while you are typing, offers **Reload**.
- **Buttons work at once while Hal is busy.** With jobs queued, **Fill it in again**, **Fill in from this
  page**, approving one application and the Form step's other buttons waited until every queued job's
  documents were written. What you press now starts straight away.
- **The Form step says what Hal is doing.** While Hal worked on a form, the step showed an empty banner, the
  hint for when Hal is stuck, and "Press Submit yourself" with nothing filled. It now says what Hal is
  doing, such as "answering 7 questions", and keeps the hint for when Hal has stopped.
- **A job board's "no longer available" banner closes the job.** Hal looked for that only in a page's title
  and headings, so an advert taken down with a banner (as Adzuna shows it) was reported as "no form found".
- **Closed adverts are found before documents are written.** Hal now checks a queued advert is still open
  before writing for it, and closes the job if it is not. Some boards refuse that check; their closed
  adverts are found when Hal opens the form.
- **Fewer questions left for you.** Hal answers a phone's dialling code ("Country code") from your country,
  and keeps what a site has already filled in when Hal has no answer of its own, where before it counted
  a code the site had set to +44 as a required question for you.
- **"This window" opens forms beside Hal.** With "Open forms in: This window", a form opened wherever the
  browser last had focus. It now opens next to the Hal tab you used last, in that tab's tab group.
- **"Wait for you to sign in" is used.** The setting was shown but never read, and Hal always waited
  15 minutes.
- **A code split into boxes is a code.** A page asking for an emailed code one digit a box (six boxes, as
  Oracle shows it) was taken for the form's last page. Hal now waits for the code and types it in.
- **Hal opens in your default browser.** It opened in a separate Chromium or Edge app window, apart from the
  browser holding your sign-ins and the browser helper.
- **Application forms open in a window of Hal's own, in front.** The first form opens a new browser window,
  and every form after it goes into that window until you close it. The window used to open behind
  yours, so it looked as if no form had opened. "Open forms in: This window" in Settings still opens
  them as tabs beside Hal's.
- **Approving stays on the application.** **Approve and fill in the form** went back to the Applications
  board; the application now moves on to its Form step, where Hal's answers appear as it fills the form.
- **Hal gets past job boards' search boxes and job-alert pop-ups.** On an Adzuna advert Hal filled in the
  board's own job search and its "similar jobs by email" box as if they were the application, and a job-alert
  pop-up lay over the page. Hal now leaves a site's search and job-alert boxes alone, closes pop-ups that
  ask for job alerts, newsletters or an app, however the site builds them, and presses Apply to reach the
  employer's form.
- **A job-alert box opened by Apply is passed.** On Adzuna, Apply opened a box under it asking for an email
  address for similar jobs, and Hal said "No Apply button recognised". Hal now presses its "No thanks, take me
  to the job" link, which leads on to the employer.
- **An open dropdown no longer hides the form from Hal.** When a searched list found nothing, Hal left it open on
  "No results", and some forms (Ashby's) hide every other question while a list is open. Hal then saw three
  questions, took the page for a new one and filled the whole form again. Hal now closes the list, and reads
  the whole form even while a list is open.
- **Searched lists are filled through the browser helper.** A list that offers its options only once you type,
  such as Ashby's "Where are you currently located?", was left empty with "no option matching", since the
  helper was never told it could type there. It now types the answer until the list offers it.
- **Follow-up questions are left alone when they do not apply.** An optional "If you answered yes to the above,
  ..." after a "No", or "If you selected other, please specify" after another choice, was answered with
  filler such as "A" or "N/A". Hal now leaves it empty.
- **A board's own search and job alerts are left alone in a page-wide form.** On a board whose whole page
  is one form (as ProductionBase builds it), Hal filled the board's job search ("Job Types", "Industry") and
  its job-alert box as the application. Hal now tells them apart by the part of the page they sit in.
- **"Applications closed" closes the job.** A banner saying applications had closed was not read as a
  closed advert.
- **Answers survive a form that rebuilds itself.** A form that read the uploaded CV and built itself again
  (as Ashby's does) lost Hal's answers, which came back as "no longer on the page". Hal now uploads first,
  waits for the form to settle, then fills in the rest, and puts the CV in only once.
- **The CV goes in the right upload.** A form that named its cover letter and certificate uploads but not the
  CV's got no CV, since Hal took "cover" to mean the CV's upload was named too. The CV now goes in the
  first upload named for no other document, and Hal knows the German words for CV and cover letter.
- **A stopped form says so.** When Hal could not finish a form, because the helper was not connected or a
  wait ran out, the Form step still said Hal was filling it in. It now says why Hal stopped and what to press.
- **The helper's button says what it is for.** Pressed on Hal's own page or on a browser page, it showed a
  bare "!"; it now says to press it on an employer's application page.
- **Fewer wrong jobs for humanities graduates.** Specialist technical titles (software, machine learning,
  data engineering and similar) are hidden as "Outside your field" unless one of your roles names them.
- **The right scoring for a new graduate.** A new graduate who types non-technical roles is no longer scored
  with the programmers' preset, and "Here is what I understood" names the starting point.
- **Lists keep up.** Jobs and Applications refresh when Hal finishes a re-check, a search or documents,
  "Show more jobs" and opened Hidden groups refresh too, and a new application appears on the board
  without reloading the page. A job you apply for leaves Matches at once, even while Hal is busy with others.
- **An advert listed again shows once.** Agencies repost the same advert each day under a new id, and each
  copy was a new match. Hal now shows one copy of a job with the same employer, title and place: the one
  you applied for or saved, else the newest. The rest are under Hidden, "Listed more than once".
  Discarding an application now skips only its copies in the same place, so the same role in another city
  stays in Matches.
- **Cover letters are made.** Apply with Hal now makes the cover letter PDF with the CV; before, it was
  made only when you edited or approved the documents.
- **CV changes show their new wording, and Undo works.** Each change said only "(changed)", and Undo was
  recorded but the CV sent still held Hal's change.
- **The laptop launcher stays open.** "Hal Employment (desktop)" closed its window as soon as the tunnel
  started, which ended the tunnel too. Fetch the launcher again (step 5 of the how-to). A launcher that
  cannot connect now keeps its window open and says why.

## 2.0.0 (2026-11-03)

### What's new

Hal Employment is now an app with its own window, for any career, on Windows and Linux.

- **No false token warning on Windows.** Windows has no Unix file modes, so Hal no longer warns there that
  the gateway token file is readable by other users.
- **Shorter README.** It now lists requirements, install, run and test, and points to the docs for the design.
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
