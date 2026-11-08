# Add your CVs, education, projects and documents

Each tab of **Your record** has its own place to add files. Where you put a file tells Hal what it is and
whose it is: a file in **Coursework** is your own work for that course, and a file in **Lecture material**
is what you were taught. Hal then only takes facts out of it, each with the words it came from. This works
the same for any subject: history essays, design portfolios, lab reports, business plans or code.

1. Open **Your record**. On **Overview**, under **Your CVs**, choose **What is it?** (CV, cover letter or
   personal statement) and press **Add files**. Add every CV you have. Hal reads cover letters and personal
   statements only to learn how you write, never as facts about you.
   Hal reads each CV into your jobs, education, skills and projects, and dates it by the date at its top
   (such as "CV, March 2025"), else by its latest entry.
   The newest is your **Current CV**; the others show **Older CV, fills gaps**. To lead with another, press
   **Make this my current CV** on it.
2. Open **Work history**. On a job's card, open **Add a document for this job** and add a reference, a
   contract or the job description. Hal takes the job's title, dates and duties from it, after your current
   CV.
3. Open **Education**. On a course's card, open **Coursework, lecture material and official papers**:
   - **Coursework**: your essays, reports, lab reports, portfolios, presentations, code, designs and
     dissertation. Hal writes a short summary of each, notes its mark if the work states one, and takes the
     skills and methods it shows as done in your coursework. Hal also suggests which module it was for:
     press **Yes, that is right**, or choose another under **Module**. Press **Showcase** on the pieces you
     are proudest of: each becomes a project of your degree on **Projects**, and Hal tells employers about
     it. Press **Showcase** again to take it out; its skills stay. Press **This is my dissertation** on your
     dissertation, so Hal names it and describes it as your dissertation. Each course has one.
   - **Lecture material**: slides and notes from the course, including notes an older Hal wrote. Hal counts
     these as what you studied, never as work you did, and never as a project. Hal takes out email
     addresses and phone numbers, turns slides into short study notes, and summarises a very long deck
     instead of keeping every page. Confirm or choose the module under **Module**, as for coursework.
   - **Official papers**: your transcript and certificates. These set your modules, marks and grade.
4. Open **Projects**. **Add projects** lists the ways to add one: your GitHub account, a link to one code
   project, a folder on this computer, an uploaded folder, a portfolio site, a web page or online document,
   or a project added by hand. Ways marked **Coming soon** are not ready yet. Press **Showcase** on a
   project's card to have Hal tell employers about it.
5. Open **Documents** and add certificates, awards, references, checks and anything else that helps.
   Choose what each one is under **What is it?**, and, if it is about one course, job or certificate, pick
   it under **Is it about one of these?**. A transcript sets that course's modules, marks and grade. A
   certificate gets its own card. A reference is kept on its job as the referee's words, not yours.
6. Each place lists what you added, with its state (**Waiting to be read**, **Read**, **Could not be read**
   or **Kept out: it looks private**) and when Hal last read it. Press **Read again** after you change a
   file's kind, or **Remove** to take it out with everything Hal took from it.

The older way to add many files at once is still on **Overview**, under **More ways to add**.

## Code projects

For a code project, Hal works out the facts itself: the languages, the tools named in the project's own
files (such as `requirements.txt` or `package.json`), the tests, and from the project's history when you
started and stopped and how many of the changes were yours. The AI only writes the short summary, from the
project's README. Hal never reads `.env` files, keys or anything that looks private.

For Hal to know which changes are yours, give your name and email under **Settings**, **You**. Add any
usernames you commit under to **Other names you go by**.

**Your GitHub account** takes your username. Hal lists your own projects, each as its own entry under the
account, so you can remove one or press **Showcase** on its card. Copies of other people's projects (forks)
are left out unless you changed them. **Link to one code project** takes the address of one project on
GitHub. Hal keeps its own copy of each project, so it can still read them when GitHub cannot be reached.
Private projects need a GitHub token, under **Settings**, **CV and documents**.

**A folder on this computer** takes a folder that holds your projects, one folder each. Each folder inside
it that is a code project, or that has files of its own (a design brief, drawings, a report), becomes its
own project. Folders that only hold more folders are left out. If the folder you give is one code project
itself, Hal reads it as that one project.

## Portfolios, web pages and online documents

**Portfolio site** takes the page that lists your work: a Behance, Dribbble or ArtStation profile, or your
own site's projects page. Hal lists the pieces it finds there. Open **Pick pieces to add** under the site,
tick the pieces you want, and press **Add the ticked pieces**. Each one becomes its own project.

**Web page or online document** takes one page: a published article, a blog post, a project page, or a
Google Doc or Slides shared with anyone who has the link. Hal takes the title, the date, the tools and
materials the page names (such as Figma, Excel or screen printing) and your role when the page says it in a
line like "Role: Lead designer". The AI writes the summary from the page's own words only.

## Your showcase

Press **Showcase** on the projects you want employers to hear about. Once one project is in your showcase,
Hal tells only showcase projects in letters, answers and messages. The others still count for your skills.
If nothing is in your showcase, Hal chooses from all your projects, as before.

What a project's own code, page or files say counts above what your CV says about it. A project card that
only your CV names is marked **from your CV only**: add the project itself to give Hal more to go on.

## Keeping projects up to date

Every 6 hours Hal checks your GitHub projects, folders and pages for changes and reads again only the ones
that changed. Anything you changed on a project's card stays as you left it. A project that changed shows
**What changed** (for example "Tools added: Django. 3 new commits.") on its entry and its card. If a new
read got something wrong, press **Go back to the last read** to put back what the read before found.

To check more or less often, open **Settings**, **Schedule**, **Show more**, and change **Check your
projects for changes every**. **Never** stops the checks; **Read again** still works.

## Projects with no page or code

**Add one by hand** is for anything else: an exhibition, a campaign, an event, research, writing, something
you built. Fill in its name, what it was, when, your part, the tools or methods you used and any links, and
attach files or pictures if you have them. What you type counts above your CV, since it is your own word on
your own work. Attached files help Hal search and quote, but Hal takes no facts from them.

How it works inside is in [Architecture](../dev/architecture.md#sections-of-the-record-jobhuntsections).

| Symptom | Fix |
| --- | --- |
| "Hal cannot read these yet (the ... reader comes in phase ...)." | Nothing to do: Hal keeps the file and reads it once that part of Hal is ready. |
| "... is already in your record, under Documents." | The same file is in your record already. Remove it there first if it belongs here instead. |
| "This looks like a transcript." | Press **Mark it as a transcript** if Hal is right, and Hal reads it again as one. |
| A reference added nothing to a job | Choose the job under **Is it about one of these?**, or add it from the job's own card on **Work history**. |
| "This looks like it belongs in CVs." | Press **Move it to CVs** if Hal is right. Hal never moves a file by itself. |
| "Kept out: it looks private" | The file looks like it holds bank, ID or medical details. If it is fine to keep, press **Add it anyway**. |
| "Choose which education Coursework belongs to." | Add coursework from the course's own card on **Education**, not from another tab. |
| "Add a folder on the computer Hal runs on, or upload it from this one." | A folder's path only works on the computer Hal runs on. From another computer, upload the folder instead. |
| The wrong CV is marked **Current CV** | Press **Make this my current CV** on the one you use now. Hal works out your cards again from it. |
| A fact says "from an older CV" | Only an older CV gives it. Add it to your current CV if it is still true, or hide it. |
| A module or mark is missing after you added a transcript | Hal reads table rows with a module's name and mark, and lines with a module code and a mark at the end ("HIS2040 Archival Methods 75"). Edit the course's card to add what it missed. |
| "... is already this course's dissertation." | Press **My dissertation** on the piece marked now to undo it, then mark the right one. |
| A card disappeared after you pressed **Remove** | It came only from that file, so Hal hid it. Add the file again and the card comes back as you left it. |
| A code project says "None of the changes are yours" | Hal found none of its changes under your name or email. If you made it, press **This is mine**, and add the name or email you used to **Other names you go by**. |
| "GitHub has no project at this link that Hal can see." | Check the link. For a private project, add a GitHub token under **Settings**, **CV and documents**, then press **Read again**. |
| A project you removed from your GitHub account does not come back | Hal remembers you removed it. Add its link with **Link to one code project** to bring it back. |
| A portfolio shows no **Pick pieces to add** | Hal found no links to pieces on that page. Add the page that lists your work, or add each piece with **Web page or online document**. |
| A project from a web page has no role | The page does not say your role in a line like "Role: ...". Add it on the project's card with **Edit**. |
| A code project has no "sole author" or "contributor" line | It has no history Hal can read (an uploaded copy often has none), so Hal shows its file dates instead. |
