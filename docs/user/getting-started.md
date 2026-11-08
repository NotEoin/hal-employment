# Getting started with Hal Employment

Hal Employment helps you find jobs and apply for them. It looks for jobs that suit you, explains why each
one fits, writes a CV and cover letter for each job you choose, and fills in the employer's form in your
browser. You check everything, and you press Submit on the employer's site yourself. Hal never presses
Submit, and it never sends anything to an employer.

This page is for anyone using Hal. You do not need to know anything technical.

## What you need

- A Windows 10 or 11 computer, or a 64-bit Linux desktop, with Hal installed:
  [on Windows](../howto/install-on-windows.md) or [on Linux](../howto/install-on-linux.md).
- An AI for Hal to write with: a local model, if your computer has a strong graphics card, or a Claude Pro
  or Max subscription. The setup guide helps you choose.
- Your CV, as a PDF or Word file, if you have one. If not, Hal can build one with you.
- About 30 minutes for the setup guide. You can stop at any time and carry on later.

## Open Hal

Open **Hal Employment** from the Start menu, the desktop or your applications menu. Hal opens in its own
window.

Closing the window does not stop Hal: it keeps looking for jobs in the background. Open it again from the
same shortcut. To pause or quit Hal, open **Settings**, **Advanced**.

If Hal ever shows "For your safety, Hal opens from its shortcut", close that page and open Hal from its
shortcut again.

## The setup guide

The first time you open Hal, the setup guide opens by itself. Each answer is saved as you type, and
**Save and finish later** is on every step. Steps you skip stay on Home under "Finish setting up".

1. **Welcome.** What Hal does, what it never does, and what stays on your computer.
2. **Your CV.** Choose one of three ways: use your CV as it is, let Hal suggest improvements, or build a
   fresh CV. Upload a PDF or Word file, then press **Check what Hal read** to see that Hal read it correctly.
3. **About you.** Your name, where you live, and the details forms ask for. Most of it is filled in from
   your CV ("From your CV"). The private questions (equality and adjustments) are optional; see
   [Your privacy](privacy.md).
4. **What you're looking for.** Your career stage, whether you are studying now, and the kinds of role you
   want: graduate jobs, graduate schemes, paid internships, placement years, insight weeks and spring
   weeks, apprenticeships, part-time or fixed-term work. Press every career area you are interested in, such
   as **Heritage and museums** and **Creative and media**; each fills in example roles you can change. Hal
   then says "Here is what I understood"; press **Change something** if it is not right.
5. **Your record.** Hal makes a folder for your certificates, references and other evidence. Press
   **Add files** to copy documents into it.
6. **How Hal thinks.** Choose **On this computer** if your computer runs a local model through Ollama, or
   **Your Claude subscription** (Claude Pro or Max). For Claude, press **Install**, then **Sign in**, then
   **Test**. Hal never sees your Claude password.
7. **Browser helper.** The helper lets Hal fill in forms in your own browser, where you are already signed
   in. Pick your browser and follow the steps on the screen, or see
   [Load the browser helper](../howto/load-the-browser-helper.md).
8. **Email** (optional). Lets Hal read replies from employers and job alerts from a Gmail inbox. Hal never
   sends email from your account, except your own daily summary to you.
9. **Phone alerts** (optional). Hal can tell you on your phone when something needs you, through the free
   ntfy app.
10. **Updates** (optional). Hal checks GitHub for new versions and installs one only when you press
    **Update Hal**.
11. **All set.** Hal starts its first search. Home shows how far it has got.

## A normal day with Hal

1. Open **Home**. It lists only what needs you today: new matches worth a look, applications waiting for
   you, replies from employers.
2. Open **Jobs**, **Matches**. Each job says why it fits you and anything that does not. Press **Save** to
   keep a job for later, or **Not for me** and say why, so Hal learns what you want.
3. On a job you like, press **Apply with Hal**. Hal writes the documents for this job: your CV adjusted
   for it, a cover letter, and answers to the form's questions. To choose several jobs first, press
   **Add to queue** on each instead (or Q): they wait under **Getting ready** on the **Applications**
   board while Hal writes, and **Approve all** there approves them together.
4. Read the documents. Change anything you like, or ask Hal for changes. When you are happy, press
   **Approve and fill in the form**.
5. Hal opens the employer's form in a new window of your browser and fills it in, and the application
   moves on to its **Form** step. Later forms open in that same window until you close it. Each answer is
   marked "Fine" or "Check this", and **Why this answer?** under each one says where it came from.
6. Check the form, then press **Submit** on the employer's site yourself. The application moves to
   **Sent** on the **Applications** board.
7. When the employer replies, Hal sorts it into **Messages** and moves the application on the board.

### How Hal writes, and a recruiter's read

Hal writes each letter and answer for the kind of job it is: a Civil Service form gets examples in the
STAR order (situation, task, action, result) against its behaviours, an NHS form follows the person
specification, an academic CV keeps its publications and grants, a creative CV puts the portfolio link at
the top. When Hal cannot tell the field from the advert or your goal, it uses general advice rather than
another field's. It uses only your facts and never invents a number: where a result has none, it says who
the work served instead. A first draft that breaks one of Hal's writing rules (a stock phrase, an invented
number, "not just X but Y") is rewritten once, and anything still wrong is listed under **Check before
sending**. Before it writes a letter, Hal picks the facts it will rest on: up to eight short quotes
from your CV, the facts you checked and your own documents, each with where the work was done and your part
in it when your files say so. The letter's notes list them under **Evidence**. The letter, the message
and the answers say which of these facts each sentence rests on (you never see these marks), and Hal
checks each sentence about you against only the facts it rests on: a number, tool or detail they do not
give (a tool you used on a project must be in that project's own facts, as a list of your skills says what
you know, not where you used it), a project placed somewhere else (your degree instead of your own time),
or a team's work written as yours alone. Such a sentence is rewritten once; if it is still wrong, Hal takes it out, or lists it under
**Check before sending** when the draft needs it or the next sentence points back at it ("This project
..."). After a sentence is rewritten or taken out, Hal rewrites its paragraph once so it reads well,
keeping the same facts and adding none. A letter left well under the length you prefer gets one more
paragraph from facts it has not used yet, checked the same way. When the first letter needed such a fix,
Hal writes a second one from scratch and keeps the one with fewer problems.
Hal also checks what each letter, message and answer says about you against your CV, the facts
you checked and your own documents. A claim it cannot find there is taken out or toned down once, and only
what is left is listed. What the draft says about the employer, taken from the advert, is not listed.
A sentence that says nearly what an earlier one already said, in the same draft or in the letter, the
message or an earlier answer, is rewritten once to use a fact the drafts have not used yet, or taken out,
and listed under **Check before sending** if it has to stay.
A draft may say what one of your modules covered only when your CV, the facts you checked or your own
documents say it too, as a module's name alone does not say what it taught.
Each module and its mark is a fact of its own, even when your CV lists them all on one line, so a draft
cannot tie one module's mark to a project or a piece of work that only another module's line names.
The reason you give for wanting the job, in the letter's first paragraph and in answers to "Why do you want
to work here?", should be your own. Before writing a letter, Hal pairs one specific thing about the employer,
in the advert's or the company's own words (a product, a project, a place; never "innovation" or "a great
culture"), with one of the facts it picked, and the letter's reason comes from that pairing. When nothing
of yours connects to it, the letter names the role and gives no reason rather than invent one. A sentence
that only repeats the advert or what the company says about itself is rewritten once to link something of
yours (a project, a module, an interest) to something about the employer, and listed if it still repeats
the advert. On a form, an answer with a claim or a number Hal cannot find in your facts is marked **Check this**. When you
ask Hal for changes, the new letter, message and answers get the same checks as the first drafts.

The message to the person hiring goes to the person the advert names as your contact for the job. An
address the advert gives only for questions about applying, or a careers or recruitment mailbox, is never
used; with no named contact, Hal suggests a LinkedIn search for the person hiring instead.

Hal tells the writer today's date and where you are: still studying or finished, from **When your course
ends or ended** and the course you checked on Your record, and when you can start, from **When you can
start** in Settings. A draft never talks about finishing a course that has already ended. Each project
says where it was done (part of your degree, a university team project, at work, or in your own time), and
a draft keeps it that way. A sentence that calls a project something else, such as "my degree included"
for a project you did in your own time, is rewritten once, and listed under **Check before sending** if it
is still there. This also works when the draft calls the project by a shorter name or its folder's name.
When two of your projects have similar names, Hal adds a few words of each one's own description to its
name in what the writer reads, and a sentence that gives one of them a tool or a purpose only the other has
is rewritten the same way.

The pack's **Company check** names a registered company as the employer only when the advert's own
website is on its record, or it is registered in the advert's town and trade at an address that is not a
flat. A company with the employer's exact name that Hal cannot tie to the advert that firmly is shown as
"Possible match: ... (check it is the same company)", and any other is left out.

Every pack has a **Check before sending** section, which says "Nothing to check." when nothing needs
you. Besides the sentences Hal could not fix, it lists a letter still well under the length you prefer, and
a paragraph that starts with "This", "It" or "That" pointing at something the paragraph before does not
say.

Drafts speak as you, in the first person. A sentence that opens with the advert's wording ("For the
requirement to ..."), points at your CV ("My CV lists ...") or calls you "the applicant" is rewritten once,
and your right to work is said in plain words rather than as the choice in Settings. Quotes and
apostrophes are kept plain, and each project is spelt as it is on Your record.

Everything Hal writes for you uses the spelling in **Settings**, **CV and documents**, **Spelling**: UK
English unless you choose US English. That covers CVs and CV suggestions, letters, the message to the
person hiring, form answers and interview notes. Hal changes a word spelt the other way (organize to
organise, color to colour, traveled to travelled) before you see the draft. In your own CV lines, it
offers the same change as a suggestion. Words that are right in both spellings are left alone, such as
"program" for software, "disk", "analog" and "dialog box". So are names (the Kennedy Center), quotations
and web addresses.

On the Documents step, press **Critique as a recruiter** for a hiring manager's read of your CV and cover
letter against the advert, saved as they are now. It shows the three changes that would help most, what a
recruiter sees in the first six seconds, each requirement of the advert with the line that shows it (or
"not shown yet"), a grade from 1 to 5 for each part of each document with why, and comments on the lines
they quote. Your name and contact details are taken out before the AI reads them, and the read never
comments on your age, gaps or background. It is Hal's opinion, and nothing is changed. Without Hal's AI,
you still get the writing rules' comments, line by line.

### When Hal gets stuck before the form

If Hal stops on the advert or a page before the form, open the form yourself in Hal's browser window: press
Apply on the site, close any pop-up, and sign in if it asks. Then press **Fill in from this page** on the
application's Form step, and Hal fills the form from the page your tab shows.

To change documents you have already approved, press **Change the documents** on the Form step. Hal stops
filling, and the application goes back to its Documents step; approve them again and Hal fills the form
with the changed documents.

### When Hal cannot fill in a form

Some sites ask for an account, a test or a form Hal cannot fill in. Open **Documents** and pick the
application: the CV, letter and answers you approved are there, with **Download**, **Copy all** and a
**Copy-paste details** tab for your name, email and similar boxes. Send the application on the employer's
site, then press **I sent it myself**, so Hal moves it to Sent and watches your email for a reply.

## Where Hal looks

Hal searches a list of UK job sites chosen to suit your goal, from universities and charities to the
public sector and technology. **Jobs**, **Where Hal looks** lists each one, with a switch.

- **Add a job site or careers page:** paste the address of an employer's "Jobs" page. Hal reads pages on
  Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee and Personio, pages that link a job feed,
  and pages that carry the job details search engines read. For any other page, add each job with
  **Add one advert**.
- **Watch a company:** type its name, and Hal looks for its job board on the systems above.
- **Free keys:** Adzuna and Reed need a free key each. Hal works without them and says what it skips; press
  **Get a free key** on the site's row to add one.
- **Email alerts:** many UK student and graduate sites send job alerts by email (RateMyPlacement, Bright
  Network, Prospects, TARGETjobs, Milkround, Gradcracker, CharityJob, Civil Service Jobs, NHS Jobs,
  jobs.ac.uk and others). Sign up with the address Hal reads, and each job in an alert becomes a match.
  This is the best way to hear about internships, placement years and insight weeks.

## Where things are

| In the menu | What it is for |
| --- | --- |
| Home | What needs you today |
| Jobs | Matches, Saved and Hidden jobs, and Where Hal looks |
| Applications | Every application, from Getting ready to Form filled, Sent, Interview and Offer |
| Messages | Replies from employers and job alerts, sorted by Hal |
| My CV | Your CV, Hal's suggestions, its type and look, checks, and every version |
| Documents | The CVs and letters you approved, by application, ready to copy or send |
| Your record | Your evidence: certificates, references, past work, and what Hal learned from them |
| Activity | Everything Hal did and why, with Undo where it can be undone |
| What Hal remembers | Suggestions from your choices, what Hal learned, saved answers, notes about job sites |
| Settings | Every choice, grouped as in the setup guide, plus Is Hal working? and Updates |

**Settings**, **Preferences** holds the choices about how Hal behaves while you work: whether forms open in
a separate window or beside Hal, whether Hal brings that window to the front, closes job-alert pop-ups,
keeps answers a site has already filled in, presses Next for you, how long it waits for you to sign in,
checks an advert is still open before writing, and reloads its page after an update. Each starts as Hal
behaved before it could be changed.

Press **Ctrl K** anywhere to search or jump to a page, and **Help and shortcuts** for the keys.

## Reading many files into your record

Hal can read a whole folder of your files at once: an old laptop's Documents, a university account
downloaded as a zip, a folder of certificates.

- **A zip.** On Your record, Overview, drop the zip on **Drop files here to add them** (or click there and
  choose it), up to 256 MB. Hal unpacks it safely, zips inside it too, and reads what is inside.
- **The drop folder.** Anything you put in the **+Inbox** folder inside your record is read the next time
  Hal reads your record, every 10 minutes unless you changed it. To use another folder, set **Drop folder
  for your record** in Settings, CV and documents.
- **A folder on the computer Hal runs on** can be read where it is, so nothing needs copying first: type or
  paste where it is under **Folders on this computer** and press **Read these folders**. This works only on
  that computer; a window opened from another one does not show the box. From a terminal on that computer,
  `python -m jobhunt ingest FOLDER...` does the same and prints how far it has got; the bar shows it too.
- **Web links**: paste your own site or blog, your GitHub page, or a Google Doc shared with anyone under
  **Web links**, one a line, and press **Read these links**.

Before you add a folder or a zip, choose what it holds under **What are you adding?**. This tells Hal whose
the files are, even when they do not have your name on them:

- **My own work**: your coursework, reports, projects and drafts count as yours. Slides and module pages in
  the folder still count as course material.
- **Course material I was given**: everything counts as course material, except a file with your name on it
  that the AI reads as your own work, such as your submission saved with a module's files.
- **A mix of both** (the default): Hal decides file by file, as described below.

For the drop folder, choose in **What you put in your drop folder** in Settings, CV and documents. From a
terminal, `python -m jobhunt ingest --mine FOLDER --course FOLDER` does the same; a folder given without
either is a mix of both.

While Hal reads, the Overview shows a bar with how many files are read, about how long is left, the file in
hand, what kinds of document it found, and any files it could not read. **Stop** keeps what was read so far.

Hal reads your CV, transcripts, letters and certificates first, so what it learns about you arrives early,
then reports, projects, writing, notes and slides. Each document becomes a note in your record's `Sources`
folder, grouped by kind (CVs and letters, Education, Certificates and so on), with a summary, tags and links
to notes about your modules, and the employers, skills and projects it gives facts about. Hal makes no notes
about the people and places a document mentions, as they do not help an application. A code project
becomes one note, and each module gets one course note linking its lectures, however many folders they
came from.

A folder of your own work with a README at its top (a project folder, even without code) is one project,
however many notes, plans and write-ups it holds. Each file still gets a note, linked from the project's
note, but what they say about the project goes on one project card, named from the README's first heading
or the folder's name, with the tools each note mentions. Skills in the notes are still found. A CV or a
letter kept in the folder still names each of your projects. A note about another of your projects counts
for that one, even when it names it in other words than its folder does, as long as Hal knows that project
from outside the folder: its own folder or repository, a file elsewhere, or your CV. Anything else a note
in the folder names, such as its tickets and write-ups, is part of the folder's project, however many of
them one note names. A folder that holds two or more such projects is a set of projects, not one, and a
folder of course material is never one project. With **Keep a copy of each file Hal reads in** on, the original is copied to `Attachments`
beside the notes.

The AI reads the rich documents in full, including coursework, reports, projects and essays kept in a
module's folder beside its lectures. Notes and slides are filed by their headings and words, which is
quick but gives them no AI summary and adds nothing to your profile. To have the AI read those too:

- **For the next files you add:** turn on **Read every document with the AI** in Settings, CV and
  documents. Reading takes much longer.
- **For files already read:** when the last read has ended, the Overview says how many notes were filed
  without the AI; press **Read them with the AI**. Each note is rewritten where it is, so links to it still
  work. From a terminal, `python -m jobhunt ingest --again` does the same. Code files are still read as part
  of their project, and pictures by **Read certificates and artwork in pictures with the AI**.

Hal reads as many documents at once as **AI calls at once** allows (Settings, AI). A model on your own
computer that can answer several requests together (Hal's managed vLLM model can) reads faster at 3 or 4.

Some files are listed rather than read:

- **Not downloaded**: OneDrive and Dropbox keep a file's name on your computer before its contents arrive.
  Open the folder in OneDrive or Dropbox, choose to keep the files on this device, and read it again.
- **No text**: a file with no words Hal can read, such as a scan read while **Read photos and scans of
  certificates** is off or tesseract is not installed. Hal never guesses what such a file says.
- **Copies** of a file Hal has already read are named in the first copy's note, not read again.
- **Empty notes**: a note with only a heading, links or a line a note-taking tool wrote, and fewer than two
  words of your own, is skipped. So is a note marked `status: stub`. A line that appears in ten or more of
  the notes you add at once, such as the same sentence a tool put in every note it made, does not count as
  your own words. The Overview counts them.
- **Notes made from slides**: when a note in your notes app says which slides, handout or past paper it was
  made from, Hal files it as course material without asking the AI.
- **Supporting files**: data files and exports, figures, logs, diffs, circuit and 3D-printing files, and
  photos from a camera or phone say little on their own. Hal lists each one under **Files** in the note of
  the project, report or module folder it sits in, and makes no note of its own for it. A picture or data
  file that is not inside such a folder is skipped. Photos and scans of certificates are still read.
- **Older copies of a project**: when you keep a code project several times ("Robot (2)", "robot-v3-final",
  a copy in a zip), Hal keeps the newest copy of each project. It reads only that one, and its note lists
  the others under **Earlier versions**. If you add a newer copy later, Hal reads it and updates the same
  note. A fact you checked that the newer copy changes goes back to **Found by Hal**, so you can look again;
  a fact you edited keeps your words.
- **Private files**, such as bank statements, passport scans, payslips, fit notes and files with
  passwords, are kept out of your record and never sent to the AI. The Overview lists them by name with
  the reason; press **Add it anyway** if one is fine to keep. [Your privacy](privacy.md#private-documents-in-your-files)
  says what is kept out and how to turn the check off.

Each fact Hal takes from a document comes with the document's own words. When those words are not in the
document, the fact is marked **Check this**. If Hal closes part way through, it carries on from the next
file when it opens.

Hal sorts every document by whose it is, and that decides what it can learn from it:

- **Yours**: your CV, letters, coursework, reports and projects. These give your jobs, education,
  projects, skills and achievements. Your email, phone, address and links come only from your CVs and
  letters, so a classmate's email in a group report never becomes yours.
- **Course material**: slides, handouts, lecture notes and module pages. These show what you studied, so
  they give skills and nothing else: never a lecturer's email, and never a lecture's example as your project.
  When the AI reads slides or a handout, it writes them up as lecture notes: the topics, ideas, definitions
  and examples, without names, emails, rooms or admin. Email addresses and phone numbers are taken out of
  all course material. Slides of a lecture you already have your own notes on are not filed again: your
  notes list them under **Also at**.
- **Someone else's**: another person's CV, a paper you downloaded, a classmate's poster. Hal leaves these out
  of your record and names them, with who made each one, when it finishes. A file has to name someone else
  as its maker, and not name you, to count: a file that names no one stays in your record, but Hal takes
  no facts from it.

When Hal writes a letter, a form answer, interview notes or a CV suggestion, it reads only your own
documents and the notes you wrote yourself. Course material, someone else's documents and files Hal is not
sure about stay in your record, and **Ask your record** still searches them, but they never become claims
about you. Your CV in CV Studio is given to the writer whole, so older CVs and your letters to other
employers are left out of what it reads: they can hold claims you have since dropped, or ones written for
another job.

Unless it came from a folder you said holds your own work, Hal only counts a document as yours when it
names you: your name, your email, or a username from your GitHub or LinkedIn link in **Settings > You**.
The name a Word, PowerPoint, Excel, OpenDocument or PDF file keeps as its author, or as the last person to
save it, counts too, so coursework saved under your name or university username is yours even when its
words never name you. If your files use another name for you, such as a student
number, add it under **Settings > You > Other names you go by**.

### Tidying your record

If you read files into your record with an older Hal, press **Tidy my record** on the Overview (or run
`jobhunt ingest --tidy`). Hal first saves a copy of your record and its database in Hal's backups folder.
Then it holds every note to the rules above: it takes out notes of other people's files and private files,
settings files, notes about people and places, lecturers' emails and older copies of your projects, and
facts that came from the wrong kind of file. Slides and documents only the AI can judge are read again,
which can take a while for a large record; a second tidy skips what the first one already read. Files
kept out as private are checked again, and any the check now lets through are read in. A project folder an
older Hal split into many projects, one for each note, becomes one project again: the other cards are
hidden with "Part of" and the project's name, unless you checked them. Cards an older Hal folded or merged
by rules it no longer follows come back on their own first, and are merged again only when today's rules
and the AI agree. Cards you checked, and cards inside a card you checked, stay as they are. Notes you wrote yourself are never
changed.

## Your record

Your record has a tab for each part of your profile: **Work history**, **Education**, **Skills**,
**Projects**, **Certificates and achievements** and **About you and links**. **Overview** shows how many
things of each kind Hal found and how many are left to check, and is where you add files, folders and links.
**Files** lists the folder's contents and has **Ask your record**.

Hal only uses the facts you have checked. A fact Hal found stays out of your CV, letters and answers until
you confirm it or edit it. While some are waiting, My CV and each application's documents say how many,
with a **Check them** button. Your CV and letters lead with your best experiences, the ones closest to the
job first, and never use an archived one.

Each job, qualification or skill is a card:

- **Found by Hal**, **Checked by you**, **Edited by you** or **Hidden** says where it stands.
- **From:** names each file it came from, with the words Hal took from it. Click the name to open the
  file. **Quote not found in the file** means those words are not in the file, so check the fact.
- **only in an older CV** beside a tool, duty or other detail of a project or job means only an older
  version of your CV gives it. Your newest CV (the one covering the latest dates, else the newest file)
  speaks for your CVs, so Hal leaves that detail out of your letters, messages and answers unless your
  newest CV, a repository or another document of your own gives it too. **Edit** the card and remove it,
  or add it to your newest CV if it is still true.
- **Confirm** says it is right. **Edit** lets you change any field, and **Save** keeps your words; Hal never
  changes them when it reads your files again. **Hide** keeps it out of your letters and CVs; hidden cards
  are folded away at the end of the tab, with **Show again**.
- **Add a job** (or a qualification, skill and so on) adds one by hand. Only those can be deleted: a fact
  Hal found would come back the next time it reads the file, so hide it instead.

When your files disagree, the card shows each value and the files that give it, for example a start date of
September 2023 in your CV and October 2023 in a letter. Hal lists dates, grades, employers, job titles and
qualifications that differ, not the same thing in other words ("Thirty units" and "30 units") or with less
detail ("1st" and "Year 1: 81.5% (1st)"). Press
**Use** on the right one, or **Keep it as it is**. Two jobs at once can be **Both are right**, or you can
keep one and Hal hides the other. The **Inconsistencies** tab lists everything still to sort out, with the
settled ones folded away below.

### Checking your record

**Check your record** shows what Hal found one card at a time, so you can go through it quickly. Open it
from **Check your record** on Your record's Overview, or from **Check them** on My CV or an application.

The page opens with **Your best experiences**: the jobs, courses, projects, achievements and certificates
Hal would tell an employer about first, with a few words on why each one is there. Hal sorts them after it
reads your files, putting what you would lead with first, then what is useful when a job asks for it. It
shows about 25, so you can check them in one sitting.

Each one also says where it was done: **Part of my degree**, **University team project**, **At work** or
**In my own time**. Hal fills this in from your files; if it is wrong, pick the right one. Hal writes about
the experience that way and never changes your choice.

Each has three buttons:

- **Confirm** says it is right, and Hal can use it.
- **Edit** lets you fix it.
- **Move to archive** keeps it out of your CV and letters. Use it for things not worth telling an employer,
  such as a hobby tool or a short online course anyone can click through.

Archived experiences are folded away under **Archived**. Hal puts there what you hid and what it found only
in files that are not your own, such as course material. **Move up a tier** brings one back. Hal never
changes a choice you made. **Sort them again** asks Hal to sort them afresh, for example after you change
the roles you want in your goal. Without Hal's AI, only those simple rules sort them.

Below, under **Check the rest**, Hal goes through everything else it found:

1. When two of your files disagree, Hal asks **Which is right?** first. Press the right value, or say it
   is right as it is.
2. Then each fact comes on its own card: your name and contact details, then jobs, education,
   certificates, achievements, projects and links. The card shows the words Hal found in your file and the
   file's name, which opens the file.
   - **Yes, that's right** means Hal can use it.
   - **Fix it** lets you change it. **Save** keeps your words, and Hal uses them as they are.
   - **Not me** hides it. Hal never uses it, and it stays hidden even when another file says the same.
3. Skills come last, in groups from one file, such as one module or one project. Every skill is ticked.
   Untick any that are not yours, then press **Keep these**, or press **None of these**.

The bar at the top shows how many you have checked, for example "23 of 80 checked". You can stop at any
time: when you come back, Hal starts where you left off. When nothing is left, Hal says **Your record is
ready**.

Skills Hal is less sure of wait under **Show more** at the end. These are most often found in only one
file that is not your own writing, such as a lecture's list of topics. Hal never uses them unless you keep
them.

Skills that only your course material names, such as the topics of a lecture, are listed apart under
**Studied in your course**. They show what you studied, not what you have done, so Hal leaves them off your
CV and out of your letters. If you have used one in your own work, press **I have used this** beside it.

Hal only asks about skills you can stand behind: skills on your CV, skills you checked, and the tools of a
project Hal would tell an employer about (one it would lead with, or one useful when a job asks). Any
other skill your files name, such as a word in your notes or plans, is folded away under **Mentioned in
your files**, with **I have used this** in the same way. Hal leaves them off your CV and out of your
letters. When a read ends, the Overview says how many of each it found.
One skill written several ways ("python-scripting", "Python scripting") is one entry, shown in normal case.

After reading your files, Hal looks for a project, course or achievement it found more than once under
different names, such as a project's working title and its final title, or the name on your CV and the
name of its folder. It keeps one card with all their files, named as your CV names it when your CV has it,
and lists what it merged under **Hal merged these**. If two of them are really different, press **Undo**:
Hal shows them apart again and never merges them on its own after that. Hal never merges a card you
confirmed or edited, and never two whose dates are years apart unless one file names both, or two projects
that each have a folder or repository of their own. A project with its own folder or repository joins a
card without one only when their names share a telling word, such as "sniffer" in "probe_sniffer" and
"Probe Request Sniffer". Without its AI, Hal merges only cards that share a
project link: two cards found in one file can still be two pieces of work. A link to the front page of a
site, or to the website in your settings, is not a project link, as many projects point to it.

## What Hal remembers

- **Waiting for you**: suggestions from your choices, for example a smaller travel distance after you hide
  several jobs as "Too far". Nothing changes until you press **Yes, do this**. **Not now** asks again in a
  week; **Never suggest this** stops that suggestion for good.
- **Learned**: what you said yes to, where it came from and when. Switch one off or delete it to put things
  back as they were.
- **Notes about job sites**: what works on each site, and where each note came from. Switch off any you do
  not want.
- **Saved answers**: answers Hal reuses on forms. Each says where Hal may use it; one marked **Choose where
  to use it** is never used until you pick.

### How Hal learns your writing

- **Your voice.** With **Match my own writing** on (**Settings**, **CV and documents**), Hal measures how
  you write from your own words: the letters, statements, essays and CVs in your record (never a private
  folder), the answers you rewrote and your edits of its drafts. It tells the writer your usual sentence
  length, whether you use contractions, your spelling and your usual words, and shows it two or three
  short passages of yours as examples of style, never of facts. A word Hal would normally avoid stays
  yours if you use it yourself.
- **Your edits.** When you change a letter, an answer, the message or a CV line Hal wrote, Hal keeps both
  versions. A word you take out of three drafts becomes the suggestion **Never use "word"?**, and letters
  you cut short become **Write letters of about 150 words?**. Like every suggestion, nothing changes until
  you press **Yes, do this**. An answer to a private question is never kept.

To stop the suggestions, open **Settings**, **Advanced**, **Suggestions from your choices**; **Your writing**
there covers the ones from your edits.

## When something goes wrong

1. Open **Settings**, **Is Hal working?**. Each line is fine or says what to do, often with a button that
   fixes it.
2. If a form went wrong on a job site, press **Something wrong here?** on the application's page and say
   what happened in your own words. Hal remembers it for that site.
3. If you are still stuck, open an issue on the project's GitHub page. **Make a help file** on the
   **Is Hal working?** page makes a file you can attach: your name, contact details and answers are taken
   out, and you read it before it is saved.

| Symptom | Fix |
| --- | --- |
| "For your safety, Hal opens from its shortcut" | Close the page and open Hal from its shortcut. |
| Hal does not open at all | Restart the computer and open Hal again. If it still does not open, update it from outside the app ([Update Hal Employment](../howto/update-hal.md)). |
| No new jobs for days | Open **Jobs**, **Where Hal looks** and check the sites are switched on, then **Settings**, **What you're looking for**. |
| "Hal cannot read this page by itself yet" | The careers page has no job details Hal can read. Use **Add one advert** for each job on it. |
| Hal cannot write documents | **Settings**, **Is Hal working?** shows the AI line. With Claude, press **Sign in to Claude again** if it asks. |
| The form does not fill in | Check the browser helper is connected (**Settings**, **Is Hal working?**), then try **Something wrong here?** on the page. |
