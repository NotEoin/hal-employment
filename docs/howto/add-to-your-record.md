# Add your CVs, education, projects and documents

Each tab of **Your record** has its own place to add files. Where you put a file tells Hal what it is and
whose it is: a file in **Coursework** is your own work for that course, and a file in **Lecture material**
is what you were taught. Hal then only takes facts out of it, each with the words it came from. This works
the same for any subject: history essays, design portfolios, lab reports, business plans or code.

1. Open **Your record**. On **Overview**, under **Your CVs**, choose **What is it?** (CV, cover letter or
   personal statement) and press **Add files**. Add every CV you have. Hal reads cover letters and personal
   statements only to learn how you write, never as facts about you.
2. Open **Work history**. On a job's card, open **Add a document for this job** and add a reference, a
   contract or the job description.
3. Open **Education**. On a course's card, open **Coursework, lecture material and official papers**:
   - **Coursework**: your essays, reports, portfolios and dissertation. Press **Showcase** on the pieces you
     are proudest of.
   - **Lecture material**: slides and notes from the course. Hal counts these as what you studied, never as
     work you did.
   - **Official papers**: your transcript and certificates. These set your modules, marks and grade.
4. Open **Projects**. **Add projects** lists the ways to add one: your GitHub account, a link to one code
   project, a folder on this computer, an uploaded folder, a portfolio site, a web page or online document,
   or a project added by hand. Ways marked **Coming soon** are not ready yet. Press **Showcase** on a
   project's card to have Hal tell employers about it.
5. Open **Documents** and add certificates, awards, references, checks and anything else that helps.
   Choose what each one is under **What is it?**.
6. Each place lists what you added, with its state (**Waiting to be read**, **Read**, **Could not be read**
   or **Kept out: it looks private**) and when Hal last read it. Press **Read again** after you change a
   file's kind, or **Remove** to take it out with everything Hal took from it.

The older way to add many files at once is still on **Overview**, under **More ways to add**.

How it works inside is in [Architecture](../dev/architecture.md#sections-of-the-record-jobhuntsections).

| Symptom | Fix |
| --- | --- |
| "Hal cannot read these yet (the ... reader comes in phase ...)." | Nothing to do: Hal keeps the file and reads it once that part of Hal is ready. |
| "... is already in your record, under Documents." | The same file is in your record already. Remove it there first if it belongs here instead. |
| "This looks like it belongs in CVs." | Press **Move it to CVs** if Hal is right. Hal never moves a file by itself. |
| "Kept out: it looks private" | The file looks like it holds bank, ID or medical details. If it is fine to keep, press **Add it anyway**. |
| "Choose which education Coursework belongs to." | Add coursework from the course's own card on **Education**, not from another tab. |
| "Add a folder on the computer Hal runs on, or upload it from this one." | A folder's path only works on the computer Hal runs on. From another computer, upload the folder instead. |
| A card disappeared after you pressed **Remove** | It came only from that file, so Hal hid it. Add the file again and the card comes back as you left it. |
