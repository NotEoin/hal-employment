# Sort a big folder into your record

Use this when you have a folder of everything from your studies and jobs, such as an old laptop's Documents
or a university account downloaded as a zip. Hal suggests where each file goes in Your record, and adds only
what you accept. It works the same for any subject: essays and seminar notes, lab reports, design files or
code.

1. Open **Your record**. On **Overview**, find **Sort a big folder for me**.
2. Give Hal the folder:
   - On the computer Hal runs on, type or paste where the folder is under **A folder on this computer** and
     press **Sort this folder**.
   - From any computer, press **Upload a zip or files to sort** and choose a zip or some files (up to 256 MB
     at a time).

   From a terminal on the computer Hal runs on:

   ```sh
   python -m jobhunt sort ~/Documents/Uni    # prints what Hal proposes for each part of your record
   ```

3. Wait for **Ready for you to check**, then press **Check the list**. The buttons at the top show how many
   files Hal proposes for each part of your record: **CVs**, **Coursework**, **Lecture material**,
   **Projects**, **Documents** and the rest. Press one to see its files, 50 at a time. Lecture material and
   coursework show one row a module, with how many files it holds: ticking it, or changing its course,
   changes them all. Press **Show the files** to see them one by one, and **Back to the list** to return.
4. Under **Courses Hal found**, check each course your folders name, such as a "BSc Computing" folder with
   its year and module folders. Change its qualification, subject or where you studied if Hal got them
   wrong, then press **Add this course**. Its coursework and lecture notes find it at once.
5. Check each row. Under **Where it goes**, **What it is** and **Course or job**, change anything Hal got
   wrong. Untick a file to keep it out. For a project, tick **Showcase** if you want employers to hear about
   it. Hal ticks it for you only on a few of your own code projects (at most 8): ones outside your course
   folders, with your own commits and a README that says what they do. Your changes are kept, so you can
   stop and come back later.
6. Add your CVs and papers next: press **CVs**, then **Add the ticked CVs**, and the same for **Official
   papers**. Hal reads them and finds your courses and jobs, and the files still waiting for their course
   or job find it.
7. Press **Add everything ticked**, or **Add the ticked ...** one part at a time. Each file is read by its
   part of your record, as if you had added it there yourself.

   From a terminal:

   ```sh
   python -m jobhunt sort --accept-all    # adds everything ticked in the latest sort
   python -m jobhunt sources list         # lists what each part of your record now holds
   ```

8. Look at **Left out** and **Copies**. Hal leaves out what it is not sure about, and older copies of a file
   or project. It never looks inside a project's libraries or build output (folders such as `node_modules`,
   `build` or `dist`); the line at the bottom of the list counts those files. Hal also leaves out other
   people's work: published papers you kept as reading, and posters or reports named after someone else.
   A PDF in a folder of reading, such as one named papers, readings, references or literature, counts as
   a paper unless its name says it is your notes on it.
   What your course gave out, such as a brief, a handbook or a folder of course materials, is proposed as
   lecture material, never as your coursework. A folder of note folders on one subject is one row, and so
   is a folder of practice exercises (such as HackerRank or LeetCode), which Hal never stars. Downloads
   from a 3D-print sharing site and a notes app's templates are left out. A file goes to **Coursework** only
   when it looks handed in: a word such as coursework, assignment, report or essay in its name or folder,
   and more than a few lines. Lecture slides, notes named by week, and your notes on a topic go to
   **Lecture material**, as what you studied. To add one anyway, choose a place for it under **Where it goes**, then add it.

## Moving a record an older Hal read in

If an older Hal read files into your record all at once, **Overview** shows **Move my record to sections**.

1. Press **Move my record to sections**. Hal proposes a part of your record for each file the older Hal
   read, in the same list.
2. Check the rows and add what is right, as above. Cards you checked or edited keep what you said.
3. Press **Finish the move**. What the files you did not add gave is set aside, not deleted.
4. Changed your mind? Press **Undo the move**. Everything goes back as it was before the move.

How it works inside is in [Architecture](../dev/architecture.md#sorting-a-big-folder-sectionssorterpy).

| Symptom | Fix |
| --- | --- |
| There is no box for a folder | You are using Hal from another computer. Upload a zip of the folder instead. |
| "Hal is still sorting another folder." | Wait for the first sort to finish, then sort the next folder. |
| A file says "Choose one, or add your CV first" | Add the course under **Courses Hal found**, or your CVs first, or choose its course under **Course or job**. |
| "Looks private (medical information), so Hal kept it out." | Hal keeps medical, bank, ID and pay papers out. If it is fine to keep, give it a place under **Where it goes**. |
| "A published paper. Hal keeps your own work, not your reading." | If you wrote it, give it a place under **Where it goes**. |
| "Someone else's work: it is named after ..." | If it is yours, check your name under **Settings**, **You**, then give it a place under **Where it goes**. |
| "Hal could not tell where it goes." | Choose a place under **Where it goes**, or leave it out. |
| "Only online: this computer has an empty copy." | Download the file first (in OneDrive, choose "Always keep on this device"), then sort the folder again. |
| A file says "Already in your record, under ..." | It is in your record already. Nothing to do. |
| "Undo the move first, or keep it" when you press **Forget** | A move can be forgotten only once it is undone, so you never lose the way back. |
| "Hal is still adding what you accepted." | Wait until Hal has read what you added, then finish or undo the move. |
| A project you wanted is under **Copies** | Hal kept the newest copy. Give the copy you want a place under **Where it goes**, and untick the other. |
