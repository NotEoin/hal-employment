# Changelog

What changed in each version, in plain words.

## Unreleased

### What's new

- **Letters tell your showcase from its own sources.** A project's story now comes from the project's own
  code, pages, files or form and your edits; a detail only your CV gives is marked as such for the writer.
  Showcase coursework is told like a showcase project, your dissertation by its title, and a job you put in
  your showcase leads the letter. A project only your CV names is told only when nothing is in your
  showcase.

- **Tested for every kind of graduate.** Whole application packs are now tested on two made-up records
  built through Your record, one for a software graduate and one for a history graduate, each within 30
  AI calls.

- **The fit table says where each row comes from.** A new Source column says "from the code project",
  "from your coursework", "from your current CV" and so on. A tool only an older CV gives a project is no
  longer said to be used in it, in drafts or in the table.

- **Letters sound like you.** With **Match my own writing** on, the cover letters and personal statements
  you added on Your record give each new letter a short sample of your own words, so Hal matches your tone
  and sentence length. Nothing in them becomes a fact about you.

- **Skills you studied are told as studied.** Letters and answers tell a skill from your own work or your
  coursework as something you did, list a skill from your CV, and say a skill you only met in lectures was
  studied, never used. A sentence that says you used one is drafted again. On **CV**, a skills line that
  lists a skill you only studied gets a suggestion to leave it out or mark it "(studied)".

- **Hal reads your coursework.** Essays, reports, lab reports, portfolios, code and anything else you add
  under **Coursework** on a course's card get a short summary, their mark when the work states one, and the
  skills and methods they show, each with the words it came from. Hal suggests which module each piece was
  for, and you confirm it or choose another.

- **Showcase your best coursework.** Press **Showcase** on a piece of coursework and it becomes a project of
  your degree on **Projects**, built only from that piece, which Hal tells employers about. Press it again
  and the project is hidden, while the skills it showed stay. Mark one piece per course with **This is my
  dissertation**, so Hal names it by its title and describes it as your dissertation.

- **Hal reads your lecture material.** Slides and notes added under **Lecture material** on a course's card
  become short study notes without the lecturers' contact details, and the topics they teach count as
  skills you studied, never as work you did. A very long deck is summarised rather than kept whole.

- **Each skill says where you showed it.** On **Skills**, a skill's sources now say "Done in your
  coursework: <the piece>" or "Studied in <the module>", so you can tell what you did from what you were
  taught.

- **Official papers set your marks.** A transcript added under **Official papers** on a course's card now
  gives that course its modules, each with its mark, and its grade. A degree certificate gives the grade.
  Hal reads these with the same fixed rules as a transcript on **Documents**, not the AI, so a mark is never
  moved to the wrong module.

- **Each part of Your record takes its own files.** Add your CVs on Overview, a job's reference or
  contract on its card, coursework, lecture material and official papers on each course's card, and
  certificates and other papers on **Documents** (the tab that was **Files**). Where you put a file tells
  Hal what it is and whose it is, so Hal no longer guesses. Each place lists what you added with **Read
  again** and **Remove**; removing a file takes out everything Hal took from it. **Showcase** marks the
  coursework and projects Hal should tell employers about. Hal says when a file looks like it belongs
  elsewhere ("This looks like a CV") but never moves it by itself. CVs, letters, documents, official
  papers, coursework and lecture material are read now; the ways to add projects arrive in a later update,
  and files added to them wait until then. See
  [Add your CVs, education, projects and documents](docs/howto/add-to-your-record.md).
- **Hal reads the CVs you add.** Each CV you add under **Your CVs** gives your jobs, volunteering,
  education with its modules and marks, skills, projects, awards, contact details and links, each with the
  line of the CV it came from. Hal dates each CV by the date at its top, else its latest entry, and shows
  that date beside it.
- **Your newest CV leads; older ones fill gaps.** The CV with the newest date is your **Current CV**, unless
  you press **Make this my current CV** on another. Your other CVs show **Older CV, fills gaps**: Hal takes
  from them only what your current CV and your documents leave out, such as an old job you dropped, and
  marks those facts "from an older CV". Your letters use the same current CV. A project that only your CVs
  mention shows "from your CV only".
- **Cover letters and personal statements are kept as samples of how you write.** Hal reads them and lists
  them under **Your CVs**, but takes no facts from them.
- **Transcripts, certificates and references on Documents.** A transcript sets your course's modules, marks
  and grade, ahead of what any CV says. A certificate gets its own card with who issued it and when. A
  reference is kept on the job it is about, with the referee's words marked as theirs, not yours. Under
  **Is it about one of these?** you can say which course, job or certificate a document is for. When a file
  looks like another kind ("This looks like a transcript"), Hal suggests it, and you decide.
- **A job's documents fill in the job.** A reference, contract or job description added to a job's card
  gives its title, dates and duties. Your current CV still comes first; these come before an older CV.
- **Code projects are read from the project itself.** On **Projects**, add a code project's folder or
  upload it as a folder or zip. Hal works out its languages, the tools its own files name, its tests and,
  from its history, its dates and whether you made it alone or with others (and what share of the changes
  were yours). The AI writes only a short summary from the README, and every fact says which file and line
  it came from. A project with none of your changes makes no card until you say it is yours.
- **Add your GitHub account or one project's link.** Hal lists your own projects, each on its own so you
  can remove or showcase one, and leaves out copies of other people's projects you never changed. Hal keeps
  a copy of each, so it still reads them when GitHub is down. Private projects need a GitHub token.
- **Add a folder of projects.** Give Hal the folder on this computer where you keep your projects, and each
  project folder inside becomes its own project, code or not.
- **Add a portfolio, a web page or an online document.** A portfolio site (Behance, Dribbble, ArtStation
  or your own) lists its pieces, and each one you tick becomes a project. A single page, article or shared
  Google Doc becomes a project with its title, date, the tools and materials it names, and your role when it
  states it.
- **Add a project by hand.** For an exhibition, event, campaign, piece of research or anything else, type
  its name, what it was, when, your part, the tools or methods and links, and attach files or pictures. What
  you type counts above your CV.
- **Letters tell your showcase projects.** Once you press **Showcase** on a project, letters, answers and
  messages tell only your showcase projects; the rest still count for your skills. What a project's own
  code, page or files say counts above your CV.
- **Projects stay up to date.** Every 6 hours (change it in Settings, Schedule) Hal checks your GitHub
  projects, folders and pages and reads again only what changed, never overwriting what you changed on a
  card. Each shows **What changed**, and **Go back to the last read** undoes a read that went wrong.
- **Split a code project into projects by folder.** A project that holds several of its own (a website
  and an app in one) can be split, and each part gets its own dates and your share of its changes.

- **Say two cards are the same.** When one project or job shows up as two cards, press **Same as...** on
  Check your record, pick the other card and the name to keep. Hal keeps one card with the files of both,
  even if you had already checked or hidden one, where hiding a duplicate used to hide its files too. Your
  merges are listed under **You merged these** with **Undo**, and a card you hid that comes from the same
  repository or project files as one you can see is offered under **Hidden cards from the same files**.
- **Drafts rest on evidence chosen first.** Before writing a letter, Hal picks up to eight facts it will
  rest on, each quoted word for word from your CV, the facts you checked or your own documents, with where
  the work was done and your part in it (a team of seven, the features you built) when your files say so.
  The letter's notes list them under **Evidence**. Each paragraph rests on one piece of work's facts and is
  checked against them (see **Each piece of work is told once** under Fixed): a detail added to fit the
  advert, a personal project placed in your degree, or a team's work written as yours alone is written
  again once, then taken out or named. **Check before sending** no longer lists a whole-pack check's
  guesses, which were mostly false alarms, and drafts are written in a steadier style that adds fewer
  details of its own. The letter's reason for
  wanting the job comes from one specific thing about the employer, in the advert's or the company's own
  words, paired with one of your facts; when nothing connects, the letter gives no reason rather than
  a generic one.
- **Read your web links into your record.** Hal can read your own site or blog (up to 30 pages under the
  link you give), your GitHub profile and repositories with their READMEs, and Google Docs and Slides
  shared with anyone. Sites such as Instagram and LinkedIn need you signed in, so Hal asks you to save the
  page and drop the file in instead.
- **Read a whole folder or zip into your record.** Add a zip with **Add files** on Your record, or put files
  in the **+Inbox** drop folder, and Hal reads every document into a note of its own, CVs, transcripts and
  letters first. Each note has a summary, tags and links to your modules and to the employers, skills and
  projects it gives facts about. Code projects become one note each, and each module one course note.
  Slides and handouts are written up as lecture notes without names, emails or admin. Files OneDrive or Dropbox
  has not downloaded, files with no text and copies are listed rather than read, settings files, hidden
  folders and bundled code are left out, and a fact whose words
  are not in its document is marked **Check this**. Hal carries on from the next file after a restart.
  Notes and slides are filed by their words unless **Read every document with the AI** is on, and
  **Read them with the AI** on the Overview reads the ones already filed, in place. Hal reads as many
  documents at once as **AI calls at once** allows.
- **Tidy my record.** One button on the Overview (or `jobhunt ingest --tidy`) backs up your record, then
  holds what an older Hal filed to today's rules: other people's and private files, notes about people and
  places, lecturers' emails and older copies of projects come out, and slides become lecture notes.
- **Whose each document is.** Hal sorts what it reads into your own documents, course material and someone
  else's. Course material only shows what you studied, your contact details come only from your CVs and
  letters, and someone else's documents (a friend's CV, a downloaded paper) are left out of your record
  when they name who made them.
  Your email and the usernames in your links in Settings name you, and you can add a student number
  under **Other names you go by** so Hal knows a file is yours.
- **Say what a folder holds.** When you add a folder or zip, choose **My own work**, **Course material I
  was given** or **A mix of both**. Your coursework, drafts and reports in a folder of your own work count
  as yours even when they do not name you, and a folder of course material only shows what you studied.
  The drop folder has the same choice in Settings, and `jobhunt ingest` takes `--mine` and `--course`.
- **Who saved a file.** Hal reads the author a Word, PowerPoint, Excel, OpenDocument or PDF file keeps in
  its properties, so coursework saved under your name or username counts as yours even when its words do
  not name you. Names such as "Microsoft Office User" or a computer's name are ignored.
- **Coursework in a module's folder.** Coursework, reports, projects and essays kept beside a module's
  lectures are read by the AI like your other work, and stay linked from the module's note. **Tidy my
  record** reads the ones an older Hal filed by their words alone.
- **Data files and photos stay with their work.** Data exports, figures, logs, diffs, 3D and circuit files
  and camera photos no longer become notes of their own. Each is listed in the note of the project, report
  or module it belongs to, and a loose one is skipped. **Tidy my record** does the same for notes an older
  Hal made of them.
- **Empty notes and notes made from slides.** Notes with no words of their own, such as the stubs a
  note-taking tool makes, are skipped and counted on the Overview. A line a tool put in many notes (ten
  or more of those read at once) is not counted as your words, and a note marked `status: stub` is
  empty, so thousands of stubs no longer go to the AI one by one. A note that says which slides or handout
  it was made from is filed as course material without the AI. **Tidy my record** applies both to notes
  an older Hal filed.
- **The newest copy of each project.** When a folder holds several copies of one code project ("Robot",
  "Robot (2)", "robot-v3-final", the same project in two folders or in a zip), Hal reads only the newest
  and lists the others under **Earlier versions** in its note. A newer copy added later updates the same
  note, and a fact you checked that it changes goes back to unchecked for you to look at again.
- **One project per project folder.** A folder of your own work with a README at its top is one project,
  even with no code in it: its plans, tickets and write-ups each get a note, but they add their tools and
  quotes to one project card instead of each becoming a project of its own. A CV kept in the folder, and a
  note about another of your projects, still count as they are. **Tidy my record** folds the cards an
  older Hal made of such a folder's notes into its one card, unless you checked them.
- **One card for an experience found under several names.** After reading your files, Hal merges the
  projects, courses and achievements it found more than once under different names (a working title and a
  final title, a folder's name and the name on your CV) into one card with all their files, keeping your
  CV's name. Check your record lists them under **Hal merged these** with **Undo**, which shows them apart
  again for good. Cards you confirmed or edited are never merged.
- **Private files stay out of your record.** When Hal reads a folder or zip, files that look like bank
  details, a passport or ID card, a payslip or tax form, medical information, a date of birth with a
  passport or licence number, or passwords and keys are not filed, not sent to the AI and give no facts.
  Any other date of birth, such as on a transcript, is taken out of the words Hal files. CVs with your
  contact details are always read. The Overview lists them by name with the reason, and **Add it anyway**
  reads one in. **Keep private documents out of your
  record** in Settings, CV and documents turns the check off.
- **Your profile.** Hal keeps the facts it finds in your record as a profile: jobs, education with its
  modules and marks, skills with the words that show them, projects, certificates, achievements and links.
  Each fact names the files it came from. The same job in two CVs is one entry, written the same way in
  both or not ("Sept 2023 - present", "09/2023"), and "MS Excel" and "Excel" are one skill. A project is one
  entry under several names ("Hatch" and "Hatch - AI desktop companion") or with one link, and a line such
  as "GitHub" or a single file such as "solver.c" is not a project. A degree's
  modules come from your transcript (or your CV when there is no transcript), one per module code with its
  mark, never lecture titles from your notes or slides. A module code on its own is not a course, a
  training course is a certificate rather than education, and "supervisor at a bar" with nothing more is
  not a job. A course or job with an end date in the past is shown as ended, even when an older CV says
  "present".
- **Your corrections stay.** Confirm, edit or hide each fact in your profile. When Hal reads your files
  again it never changes a fact you checked or edited, and a hidden fact stays hidden. The one exception is
  a newer copy of a project that says something else: then a fact you checked goes back to unchecked.
- **What does not add up.** Hal lists two files that give different dates for one job, a grade on your
  transcript that is not the one on your CV (Hal uses the transcript's until you choose), an end date
  before a start date, two full-time jobs at once, and gaps of more than six months. The same fact in
  other words ("Thirty units" and "30 units", "GCSE" and "GCSEs") or with less detail ("1st" and "Year 1:
  81.5% (1st)") is not listed, nor is a project role.
- **Your record in tabs.** Your record has a tab for work history, education, skills, projects,
  certificates and achievements, and the details about you, each a card you can confirm, edit, hide or add
  by hand. Each card names the files it came from with their words, and marks words not found in the file.
  Where your files disagree, choose the right value on the card or on the Inconsistencies tab. The Overview
  adds files, zips, folders on this computer and web links, and shows a bar with the time left, the file in
  hand and the files OneDrive or Dropbox has not downloaded.
- **Hal only uses facts you checked.** Your CV, cover letters, answers and messages use only the facts you
  confirmed or edited in your profile, never one Hal found that you have not checked, and never one you hid.
  My CV and an application's documents say how many facts are waiting for you, with **Check them**. They
  lead with your best experiences, the ones closest to the job first, and leave out archived ones.
- **Check your record.** A page that shows what Hal found one card at a time: **Yes, that's right**,
  **Fix it** or **Not me**, with the words from your file and the file's name. Where your files disagree it
  asks **Which is right?** first. Skills come in groups from one file to keep or drop together, and the
  ones Hal is less sure of wait under **Show more**. A bar shows how many you have checked, and you can stop
  and carry on later. A fact you said is not you stays hidden when another file names it.
- **Hal sorts your experiences.** After reading your files, Hal sorts each job, course, project,
  achievement and certificate into what you would lead with, what is useful when a job asks for it, and
  what is not worth telling an employer, with a few words on why. Anything you hid, or that only appears in
  files that are not your own, is set aside, and school qualifications count as useful rather than the lead.
  Without Hal's AI only these rules are used. A choice you make yourself is never changed.
- **Your best experiences.** Check your record opens with the experiences Hal would lead with, best first,
  about 25 of them, each with why and **Confirm**, **Edit** and **Move to archive**. Archived ones are folded
  away with **Move up a tier**, and **Sort them again** asks Hal to sort them afresh.
- **Skills from your own work.** A skill that only your course material names, such as a topic from a
  lecture, is listed apart on Check your record under **Studied in your course** and never goes on your CV
  or in a letter, unless you press **I have used this**. A skill that is not on your CV, not checked by you
  and not a tool of a project worth telling an employer about, such as a word a research note mentions, is
  folded away under **Mentioned in your files** in the same way, and the Overview says how many skills of
  each kind a read found. One skill written several ways
  ("python-scripting", "Python scripting") is one entry, shown in normal case.
- **Large records read faster.** Hal adds what it finds in each file to your profile much faster when your
  record holds thousands of facts.
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

- **Your record's tabs update once Hal has read what you added.** A card Hal found in a file you just
  added, such as the project from a piece of coursework in your showcase, appeared only after you
  reloaded the page.

- **Letters tell the work that fits the job best.** Hal chose a letter's projects by a couple of words in
  common with the advert, so a letter for a networking role led with a voice assistant while a packet
  sniffer waited in the fit table. The AI now ranks all your projects and jobs against the job once per
  pack, with a short reason for each, and the letter tells the three best that have facts to tell. The
  message and the answers take the next ones (see **Your best work leads**). If the ranking fails, Hal
  chooses by the advert's words as before.
- **Your best work leads.** The projects and jobs you would lead with (your best, at the top of **Check your
  record**) only broke a tie in the ranking, so a letter for a web role told two first-year coursework apps
  and left the person's AI assistant 13th. A letter now tells first the work you would lead with that the
  ranking places in its top half, then the next best; other work goes before it only when that work is in
  the top three and yours is not in the top half. The message and the answers follow the same order. Work
  you would lead with is always ranked, also when the AI leaves it out, its name is written a little
  differently, or its card has no summary.
- **The opening is about the employer.** A letter's opening named the project behind your reason for the
  employer with its details, and a paragraph then told the same details again, or it began with the
  advert's words as the employer's ("Your requirement to write and improve SQL queries ...", "Your work on
  ..."). The opening now gives your name and course, the role and one concrete thing about the employer,
  such as what it makes, its mission or a fact from the company brief, in your own words. It names none of
  your projects, which the paragraphs tell, and a duty of the job is never the reason. An opening that
  names a project, repeats a paragraph's details or numbers, or begins "Your requirement", "Your need" or
  "Your work on" is written once more.
- **"Why do you want to work here?" is about the employer.** Four answers said only "because I built"
  a project, and others gave the advert back ("Your focus on ownership and working with stakeholders ...")
  or an employee's quote as the reason. The answer now opens with what you see in the employer, such as its
  product or its mission, in your own words, then ties it in a sentence to one piece of your work that fits
  the job well: never one the job ranks in its bottom half, also for "Why are you a good fit?". Hal looks
  for the employer's reason in the company brief before the advert, and never takes it from a quote, a
  list of what the job asks, or a line written to you. An answer, or a letter's opening, whose reason
  repeats five words in a row of the advert or mostly one of its sentences is written once more, and an
  answer that does not speak of the employer or tie it to your work is too.
- **A letter keeps its true paragraphs.** One letter came out at 56 words: three paragraphs were left out,
  two of them over claims your record supports, because the AI check still doubted them after one rewrite.
  A paragraph that passes every other check is now kept, without the sentence the AI doubted when the rest
  still reads, or whole and listed under **Check before sending**, in the message and the answers as in the
  letter (a message once kept a doubtful claim while the pack said "Nothing to check"). A number written in
  words, such as "twenty-three" or "one hundred and thirteen", is read whole (it was read as 20, and a true
  paragraph was left out). When a paragraph is left out, the letter tells your next best piece of work
  instead, and a letter under half your length setting tries more of your work.
- **A cleaner fit table.** The fit table held the AI's own thinking ("I will mark this as a gap if strict
  ...", and once about 400 words of "Wait, re-reading the advert ..."); such sentences, and any entry that
  thinks aloud, are now taken out, and an entry longer than 40 words keeps only its first sentence. A gap such as CI/CD or graph algorithms is no longer listed when
  your record shows it or a common equivalent (GitHub Actions, GitLab CI or Jenkins for CI/CD; A*,
  Dijkstra or BFS for graph algorithms); the table cites where instead. A module code with its year, such
  as CSC2031/23, is no longer listed as a number your CV does not hold.
- **Each project is told once in a pack.** The answer to "Why do you want to work here?" told the project
  your reason rests on even when the letter had told it already and other work was left untold. It now
  takes work the letter and the message have not told while there is some, like the other answers.
- **The message asks about the employer's work.** The question at the end of the message to the person
  hiring asked about your own project's subject, such as "cross-platform window management" asked of a
  consultancy. It now asks about a task, system or product the advert or the company brief names, and may
  touch on your project's area only when the job ranks that project among the work that fits it best. A
  question that does not is written once more.
- **Answers and messages say which project.** Form answers and messages to the person hiring began "I
  wrote a 113-test suite ..." and named the project later, or never. Like a letter paragraph, each now names
  its project or job in its first sentence, and is written once more when it does not.
- **A tool is used for what your facts say.** Drafts said a project "used Docker to package the application"
  when your record used Docker as a sandbox, called frontend work backend work, and said a module gave you
  C++ knowledge when the module never names C++. A sentence that says what a tool was for now needs that
  purpose in your facts about the work, and a part of a system (backend, deployment, database) in a fact
  naming the tool; a module named with a tool needs the module's own entry to
  name it, in letters, answers and messages. Either is written once more when it does not. A sentence that
  gives one tool a job your facts give another tool ("GPT-SoVITS to transcribe" when faster-whisper did the
  transcribing) is written once more naming the right one, and a project's paragraph names only the tools
  its facts name, unless they name none (a LIDAR project was said to use Python because Python was on its
  tool list).
- **The advert's words stay the advert's.** Phrases such as "low-latency systems" or "help clients bring
  their ideas to life" went from the advert into sentences about your own work, as if you had done them. A
  sentence about your work that takes three or more of the advert's words in a row, words your facts do
  not hold, is now written once more. A sentence about the employer or the role may still use them.
- **A note about all your projects no longer mixes them up.** A note describing about ten projects tied
  each of them to the others, so a letter put one project's network sniffer in another project. Hal now
  reads such a note only for the parts that name one project, and never takes another name for a project
  from it.
- **A project's own repository speaks only for that project.** An earlier project picked up facts from the
  repository of a later project with a longer name, and letters said it used the later one's database. A
  repository or project file named after one of your projects now gives its tools and quotes to that
  project alone, also when the file's name carries what an import adds to it, such as "repository", "(2)",
  a version number or "-main". Your record still shows every file as Hal found it.
- **Paragraphs rest on sentences about your work.** The facts a paragraph about a project was written from
  held table rows, links, credit lines and ratings, and missed what the project's own README says. Hal now
  keeps only sentences about the work, adds the sentences of the project's own README, and gives the ones
  with a result or a number first, then the ones that say how the work was done. Commands, lists of
  dependencies, counts of files, lines about the README or its tour and half sentences cut from a longer
  line are left out too.
- **Letters tell your projects first.** A letter for a software role told two university modules instead of
  strong projects and came to half its length, and another spent a paragraph on the degree its opening
  already gave. A letter now tells your projects and jobs that fit the job, up to three, each long enough
  to reach your letter length. A module is a paragraph only when fewer than two projects fit; a module the
  job asks about that no paragraph shows may get one clause with its mark in the opening. What you said you
  are looking for ("seeking a role in ...") no longer reaches a letter.
- **Your reason for the employer is concrete.** Openings said the role "matches my experience" or "fits my
  recent work", for reasons such as having built features for a team project. The opening's reason now
  names one specific thing about the employer, from the advert or the company's own words, and the project
  of yours it connects to, by name; "matches my", "fits my" and the like are written again.
- **No blank answers or empty messages.** Half the packs had a form answer left empty, or a message with
  nothing after its introduction, when Hal left out a paragraph it could not check. Hal now tries another
  piece of your work, then writes the answer or message whole, and lists a question only when nothing could
  answer it. The message always tells one piece of work and asks one question.
- **One project is not told three times.** A pack told the same project in the letter's opening, again in a
  letter paragraph and again in a form answer. The answers now count the project behind the opening's
  reason as told, and tell other projects of yours that fit the job first.
- **Messages say your degree once.** A message opened with who you are, your degree included, and its next
  sentence said the degree again. That sentence is now written again as a fact of your work.
- **Fewer false items under Check before sending.** Most items were "Hal left out the paragraph about
  First-Class Honours: your facts gave nothing". Your degree is in the letter's opening and your CV, so a
  paragraph about it or a module that had nothing to say is no longer listed. A requirement to be in a
  town no longer shows as an eligibility warning when your settings say you are willing to move.
- **The fit table says where each project was done.** It called a university team project a personal one
  ("personal projects like ... and ..."). When a row's words do not fit one of the projects it names, each
  project is now followed by where it was done, as your record says.
- **Each piece of work is told once, in a paragraph of its own.** Letters retold one project in several
  paragraphs, gave one project's tools or test suite to another with a similar name, and read as lists of
  facts. Hal now writes each paragraph of a letter's middle on its own, about one project or job, from that
  work's facts alone, then checks it against those facts: a tool, number or name that belongs to another of
  your projects is caught and named, and the paragraph is written once more. One that is still wrong loses
  the sentence at fault, or is left out and named under **Check before sending**. A sentence that ties your
  work to the job with stock words ("aligns with", "meets the requirement for") is written again too. The
  letter opens with who you are (your course, grade, university and year), the role and your one reason
  for this employer, and closes by asking for the next step. The message to the person hiring tells one
  piece of work in a sentence or two and asks one question, and each form answer tells one (two when the
  question asks for more than one example), taking work the letter has not told first. A question your facts
  cannot answer is named under **Check before sending** rather than answered with a guess.
- **Every message says who is writing.** Messages to the person hiring opened with a bare question, "I also
  ...", or "I am available to start immediately", never saying who you are. Hal now writes the opening
  itself from your settings and the course you checked: "Hello <your contact>, I'm <your name>, a
  first-class <subject> graduate from <university> (2026), applying for the <role> role." The AI writes
  only what follows, and a greeting or introduction of its own is taken out.
- **Planned is not done.** A team project listed tools its design document only planned, and every letter
  said you used them. A tool or skill that only a plan gives (a design document, a specification, a
  proposal, or words such as "we will use") is now left out of your letters, messages and answers, and Your
  record marks it **planned in** and the document's name. A draft may still say it was planned.
- **Projects with similar names stay apart in the fit table too.** The fit table and the letter's
  **Evidence** note still gave a local assistant the tools of a job-search helper with a similar name. Each
  project with a similar name is now named there with a few words of its own description, and a tool only
  the other project used is taken out.
- **Each paragraph says which project.** "I wrote a 113-test suite that ..." left the reader guessing which
  project it meant. Each paragraph about your work now names it in its first sentence, as it is about that
  one project or job alone. A paragraph drawn from a project's own document that has no card, such as your
  dissertation, names it by the document's title, and a module's paragraph names the module.
- **No "The advert requires ..." openings.** Sentences still began by restating the advert: "The role asks
  for ...", "Regarding teamwork, ...", "In terms of testing, ...", "As required, ...". A paragraph with such an
  opening is now written again. Hal no longer cuts the opening out on its own, as that left broken
  sentences.
- **No "still studying" after you graduate.** "I am currently completing a degree, on track to achieve a
  First" was left in drafts written after the course ended. Hal now also finds "currently completing", "on
  track to", "expected to graduate", a predicted grade and "now in my final year" once your course has
  ended, and the rewrite is told where you are today, as Settings and Your record say it.
- **No "student" after you graduate.** Messages called people "a Computer Science student" months after their
  course ended. A sentence that calls you a student now, such as "As a Computer Science student, I am keen
  to ...", is rewritten once your course has ended; "As a student, I built ..." is about the past and stays.
- **Packs are drafted with fewer AI calls.** Hal checked every sentence about you in a call of its own, about
  55 calls for one pack. It now makes one call for each paragraph, at most one check of it (none when it
  only says its facts' own words) and one more draft when something is wrong. Our test pack, with two
  projects, a letter, a message and two answers, makes 17 calls, and each pack's log says how many it made.
- **Projects with similar names stay apart.** When two of your projects share a name, such as an
  assistant and a job-search helper both starting with the same word, letters gave one the other's tools or
  purpose. Hal now names each with a few words of its own description in the facts the writer reads, and
  rewrites a sentence that gives one of them a tool or a purpose only the other has.
- **Your newest CV speaks for your CVs.** When an older version of your CV gave a project tools your newest
  CV no longer claims, every letter said you used them. Hal now leaves out of your letters, messages and
  answers any tool, duty or detail that only an older CV gives, unless a repository or another document of
  your own gives it too, and your record marks it **only in an older CV**.
- **One module, one fact.** A CV line listing every module and its mark was one piece of evidence, so a
  letter could say "I achieved 86 in one module, where I contributed to a team project" because another
  module on the same line was called "Team Project". Hal now reads such a line as one fact per module, each
  with its heading ("Second year modules"), and the same for skills lists and lists of achievements. A
  module's mark tied to a project or a piece of work the module's own line never names is rewritten like
  any unsupported sentence. The facts you checked list each module, duty and achievement on a line of its
  own too.
- **A list of your projects is not one project.** A portfolio folder holding a plan that described all
  your projects turned every one of them into the portfolio, and cards that shared such a plan were merged.
  A document that describes three or more projects now lists them, as a CV does, and a note about another
  of your projects keeps its own name, even when it words the name differently from the project's folder.
- **Hal merges only what it has checked.** Cards found in one file were merged without asking the AI, and
  merges chained, so unrelated projects ended up in one card. Now only cards with the same project link
  are merged without the AI; every other merge needs the AI to say the two cards are one, two projects
  with their own folders or repositories are never merged, and a merged card never takes in others.
- **Tidy my record undoes those merges.** A tidy brings back the projects an older Hal folded into a
  portfolio or merged by the old rules, with all their files, and merges them again only where today's
  rules and the AI agree. Cards you checked stay as they are.
- **Your website no longer joins two projects.** A CV that ends several project lines with "Write-ups at"
  your site gave those projects your site's address as their link, so your best project was merged into
  your portfolio site's card, under the portfolio's name. A site's front page and the website in your
  settings no longer tie two projects together; a link to a page of its own still does. Tidy my record
  takes apart projects merged this way, each with its own name and tools.
- **A research folder is one project again.** In a project folder whose notes each named a few of its
  tickets and write-ups, every ticket became a project card once more, because a note naming three or more
  was taken as a list of projects. Now a name in such a folder stays a project of its own only when Hal
  knows that project from outside the folder (its own folder or repository, another file, or your CV);
  the rest are part of the folder's project, at reading and when you tidy your record.
- **A follow-up project keeps its own card.** A repository you made after your dissertation was merged
  into the dissertation's card because the AI judged them one. A project with its own folder or repository
  now joins a card without one only when their names share a telling word and the AI agrees, and Tidy my
  record takes apart merges this rule refuses.
- **A ticket id in capitals is not an acronym.** A ticket such as "LIT-SYSTEM_DESIGN-01" in a project
  folder kept a card of its own when another of your projects had "system" in its name, because every word
  in capitals counted as an acronym. Only short words in capitals (up to five letters, such as "GPU" or
  "LIDAR") count as acronyms now; the rest are ordinary words.
- **Notes deep in a project folder fold into it.** After a tidy, cards made from notes deep inside a
  project folder, such as a folder of reading notes, could stay on their own when they shared a word with a
  ticket in another project folder, or with a folder inside the project that you once gave Hal on its own.
  They now fold into the project, and that inner folder keeps its own card and files.
- **One website is one card.** Your portfolio site could stay several cards: one called "Portfolio", one
  named as its address, and the repository it is built from. Hal's AI now sees each card's web address and
  repository when it looks for cards found twice, and a repository named after a site's address, such as
  "name.github.io", can join the card that links to that site. A link to a site's front page still never
  merges two cards on its own, and two repositories stay two projects.
- **The company check says when it is unsure.** A large trading firm's pack named a small IT consultancy of
  the same name, registered at a flat, as the employer. A registered company is now named as the employer
  only when the advert's own website is on its record, or it is in the advert's town and trade at an
  address that is not a flat; a company with the same exact name is otherwise shown as a possible match to
  check, or not at all.
- **Check before sending is always there.** Some packs had no **Check before sending** at all, so nothing
  said a letter was too short or did not read well. Every pack now has the section, saying "Nothing to
  check." when that is so, and it lists a letter well under your preferred length and a paragraph that
  starts by pointing back at something the paragraph before does not say.
- **Drafts speak as you.** Letters opened sentences with the advert's wording ("For the requirement to
  ..."), wrote "My CV lists C++", called you "the applicant", or pasted a settings choice ("as a British or
  Irish citizen"). Hal now writes such a paragraph again once, gives the writer your
  right to work and nationality in plain words, and saves drafts with plain quotes, no stray space at the
  start and each project spelt as on Your record.
- **A tool is only given to the project that used it.** A letter said a project was built with pandas
  because your CV's skills line lists pandas, though the project never used it. A tool you name for a
  project now has to be in that project's own facts; your skills list can still back "I know pandas".
- **No repeats across your drafts.** A letter could say the same sentence twice in a row, and one line of
  your CV turned up in the letter, the message and both answers. A paragraph that says one thing twice is
  now written again, and the answers take work the letter has not told first.
- **No holes where a sentence was taken out.** When Hal took out a sentence your facts did not support, the
  letter could be left with "This project allowed me ..." pointing at nothing, a paragraph of one sentence,
  or only 105 words when you prefer 250. Hal now takes a sentence out only when its paragraph still reads
  without it, and otherwise leaves the paragraph out and says so; a letter well under your length is listed.
- **Letters and answers use only your own documents.** Hal searched every note in your record when it
  wrote, so lecture notes, other people's files and files it was unsure about could turn up in a letter as
  your own work. Letters, form answers, interview notes and CV suggestions now read only your own documents
  and the notes you wrote yourself. Older CVs and letters to other employers are left out too, as your
  current CV is given to the writer whole. Your record is read again once to note whose each file is.
- **Check before sending lists what needs you.** Most of its items were the employer's own words from the
  advert. Hal now checks only what a draft says about you, against your CV, the facts you checked and your
  own documents. It takes out or tones down a claim it cannot find there once, and lists only what is
  left. Form answers get the same check, and a claim or a number Hal cannot find is marked **Check this**.
  Hal no longer treats its earlier drafts for a job as facts when it fills in the form.
- **Drafts know today's date.** A letter written after a degree had ended still offered to start "upon
  completing my degree". Every letter, message, answer and form now gets today's date and a line saying
  whether your course has ended and when you can start, and a draft that talks about finishing an ended
  course is rewritten once. Each project also says whether it was part of your degree, done at work or
  your own, so a personal project is no longer called degree work.
- **Each project keeps to where it was done.** Letters called a personal project part of a degree, a
  university team project a personal project, and coursework "personal work". Each of your best
  experiences on Check your record now says where it was done (**Part of my degree**, **University team
  project**, **At work** or **In my own time**). Hal fills it in from your files, you can change it, and Hal
  never changes your choice. A letter, message or answer that says otherwise is rewritten once, and listed
  under **Check before sending** if it still does.
- **Your own reason, not the advert's words.** Letters and "Why do you want to work here?" answers often
  gave the advert back as the reason ("the role offers full in-house training"). Hal now asks for one thing
  of yours tied to one thing about the employer, rewrites a sentence that repeats the advert or the
  company's description once, and lists it under **Check before sending** if it still does. Naming the
  role or the company is fine.
- **Better guesses of where each project was done.** Hal called a team project on a CV ("7 developers")
  a personal project, gave no answer for a project named with a module code, and called a project that
  builds on a dissertation in your own time part of your degree. It now reads team, course and own-time
  words before the kind of file. A draft that calls a project by a shorter name, its folder's name or a
  hidden copy's name is now checked against where it was done too.
- **What a module covered comes from your facts.** A letter said a module "covered core statistical
  methods" when that module was about databases and SQL. A sentence about what a module covered, taught or
  introduced is now checked against your CV, the facts you checked and your own documents that name that
  module. It is rewritten once when none of them say it, and listed under **Check before sending** if it is
  still there.
- **The fit table's evidence is checked too.** A row said two of your projects used Pandas, Scikit-learn
  and Matplotlib when neither project lists them. A tool the fit table gives to a project whose own facts
  (or your CV's line about it) never name it is now taken out of the row; a row left with no evidence is
  listed as a gap, and one that lost some is no longer rated strong.
- **No future tense about a finished degree.** A letter written in November offered to start "following
  my graduation in June". Words that put your graduation still to come ("after my graduation", "once I
  graduate", "on completing my degree") are now found once your course has ended, and the sentence is
  rewritten like any other claim your facts do not support. A sentence about what you did after graduating
  is left alone.
- **ELIGIBILITY means a condition.** An advert that asked for a relevant degree and would "also
  consider" people with a Master's was flagged "ELIGIBILITY: requires a Master's". A degree or a number of
  years is now an **ELIGIBILITY** line only when the advert states it as a condition ("must", "required",
  "essential", "you will need", "minimum", or a list under such a heading), never when it says it would
  also consider, welcome or love to hear from people who have it.
- **No offer to move to where you live.** A letter offered to relocate to the London office for a person
  who lives in London. An offer to relocate or move to the town in your settings is now taken out of the
  letter, the message and the answers.
- **The company check finds the right company.** It listed any company with the employer's name, such as a
  solar firm, a fungicide and a closed company for a lender, and a Wikipedia page listing everything of
  that name. Hal now shows a Wikipedia article only when it links to the employer's website or is about
  the advert's trade (or, when it names no trade, the advert's town), never a page listing several things
  of one name. A Companies House entry must be active, carry the employer's whole name and be in the
  advert's trade, or in its town when its records name no trade. Otherwise the pack says "No reliable
  match found" and lists no sections to read.
- **No repeated answers.** Two form answers gave the same details nearly word for word, and the fit table
  listed one gap twice. A later answer that repeats an earlier one is now rewritten once to use other
  evidence, and listed under **Check before sending** if it still repeats; each gap is listed once.
- **The company check skips namesakes it still let through.** A London lender's pack still showed an
  American solar maker's Wikipedia article, with its "Environmental controversy" section to read, and a
  finance company of the same name set up ten days earlier. An article whose title adds a word to the
  employer's name now needs a link to the employer's website or the advert's town, and a company set up in
  the last year is shown only when its name is exactly the employer's and its record gives the advert's
  website.
- **The fit table sees all your projects.** It said "No evidence of AWS" although one of your projects
  lists AWS, because only the projects that fit in the letter's notes were read. The fit table now reads
  the tools of every project and job you checked, and a requirement that names one of them is never listed
  as a gap.
- **The message goes to the right person, and changes are checked.** The message's **To:** line could be
  the advert's address for questions about applying. It now names the hiring contact the advert gives, or
  suggests a LinkedIn search, never a careers or recruitment mailbox. When you ask Hal for changes, the new
  message and answers now get every check the first drafts get (claims, where each project was done, the
  greeting, a cut-off ending and repeating the advert); before, only the letter was checked.
- **Fewer false alarms under Check before sending.** It listed twice as many false alarms as real
  problems. A claim your CV states is no longer listed as unsupported, an "-ing" ending is flagged only
  when it claims what something shows (", showcasing my care"), not when it states a fact, and a letter
  sentence is flagged for repeating your CV only when it copies a whole CV line nearly word for word.
- **The fit table is complete and agrees with itself.** A requirement the advert lists as essential could
  be missing from the table; Hal now asks once more for any it left out, and lists one still missing as a
  gap. A row that says there is no evidence is now a gap, never "partial". Office days or a place of work
  you meet, because you live there, will move or are happy in an office, now read as met with the reason
  ("You live in London"), not "partial, no evidence of refusal".
- **Clearer packs.** The message to the person hiring could greet you instead of them; it is now rewritten,
  and starts "Hello," when the advert names no one. A letter that stopped mid-word is asked for again, and
  if it stops again Hal keeps it to its last full sentence and says so under **Check before sending**. An
  advert that requires a higher degree, more years of experience or the right to work without sponsorship
  you do not have now gets an **ELIGIBILITY** line at the top of that list, while office days in the town
  you live in, or a move you said you would make, are no longer listed as gaps. A tool given to the wrong
  project ("the Lua project needed C++") is now caught as a claim your facts do not support.
- **The AI's own reason when it cannot answer.** When Hal's model was switched off, commands such as
  `jobhunt pack` stopped with a long error ending in "(503)". They now say in one line what the AI said,
  such as "Hal is off; run `hal on`".
- **Opening Hal from another computer finds a free port.** The desktop launcher gave up with "Could
  not connect" when the port it picked was in use by another program's connection, even though the desktop
  was fine. It now moves on to the next port.
- **Everything Hal writes keeps to your spelling.** CVs, CV suggestions, letters, messages, form answers
  and interview notes now all follow **Spelling** in Settings (UK English unless you choose US English).
  Before, a new CV's profile was always written in British English, CV suggestions followed the CV type
  rather than your setting, and form answers, interview notes and documents you asked Hal to change were
  never checked. A word spelt the other way is now changed before you see the draft, and Hal catches many
  more of them (traveled, favorite, theater, license, fulfill, gray). Words right in UK English, such as
  "program", "disk" and "analog", are left alone, and so are names, quotations and web addresses.
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
