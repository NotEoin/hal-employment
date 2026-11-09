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

3. Wait while Hal sorts. If you stay on **Your record**, the list opens by itself when it is ready. If you
   come back later, **Overview** shows "Hal sorted ... files from ... Nothing is added until you check the
   list." at the top, and **Your record** in the menu shows a small number: press **Check the list**. The
   list opens on its own page, as wide as your window; on a phone, slide the table sideways to see every
   column. A long folder path is shortened in the middle: point at it to see all of it.

   The list goes in six numbered steps, with the one you are on highlighted: **Your courses**, **CVs and
   official papers**, **Coursework**, **Lecture material**, **Projects** and **Done**. Each step shows only
   its own files, 50 at a time, and says in one line what to do. Press **Next** when you are done with a
   step, or press any step to go back to it. Lecture material and coursework show one row a module, with how many files it holds: ticking it, or changing its course,
   changes them all. Press **Show the files** to see them one by one, and **Back to the list** to return.
   Code in a course, year or module folder (practicals, labs, assignments, a team project) goes to
   **Coursework** for that course, in its module's row. Numbered folders such as lab1 to lab8 are one row
   too, wherever they are.
4. In step 1, **Your courses**, under **Courses Hal found**, check each course your folders name, such as a "BSc Computing" folder with
   its year and module folders. Change its qualification, subject or where you studied if Hal got them
   wrong, then press **Add this course**. Its coursework and lecture notes find it at once. A year folder
   such as "2023-24", "Year 1" or "Semester 2" is never a course: its modules join your degree, as your
   folders, your **Settings** (**You**, **Degree** and **University or college**) or your transcript or CV name it. Folders
   that name one degree in different ways, such as "Computer Science BSc" and "Computer Science BsC
   Newcastle", are one course with all their modules, named as your settings or transcript name it.
5. Check each row. Under **Where it goes**, **What it is** and **Course or job**, change anything Hal got
   wrong. Untick a file to keep it out. For a project, tick **Showcase** if you want employers to hear about
   it. Hal ticks it for you only on a few of your own code projects (at most 8): ones outside your course
   folders, with your own commits and a README that says what they do. A folder with no git history shows
   no one's commits, so Hal ticks none of those, and the list says so once. If your GitHub account is in
   **Settings**, the list also suggests adding it on **Projects**. To show coursework, such as a
   module's code, star it on its course once it is added. Your changes are kept, so you can stop and come
   back later.
6. In step 2, **CVs and official papers**, press **Add the ticked CVs and papers**. Hal reads them and
   finds your courses and jobs, and the files still waiting for their course or job find it.
7. In steps 3 to 5, press **Add the ticked coursework**, **Add the ticked lecture material** and **Add the
   ticked projects**. In the last step, **Done**, **Add everything ticked** adds what is still ticked in
   every part. Each file is read by its part of your record, as if you had added it there yourself.

   Lecture material starts unticked. Lecture notes only teach Hal what you studied, and reading many takes
   a long time, so tick only the modules you want Hal to know about. When you add more than 50 files at
   once, Hal asks first and says how long reading them should take on your computer: the average of the
   files it read lately, or a first guess of 20 seconds a file before it has read any. Press **Add them**
   to go on, or **Not now** to change your ticks.

   From a terminal:

   ```sh
   python -m jobhunt sort --accept-all    # adds everything ticked in the latest sort
   python -m jobhunt sources list         # lists what each part of your record now holds
   ```

8. In the last step, look at **Left out** and **Copies**. Hal leaves out what it is not sure about, and older copies of a file
   or project. Copies of a project count as one even in different folders, including two folders you gave
   in one sort, with no git history and with
   different files, such as "Projects/tidewatch", "old/tidewatch-final" and "Desktop/Tidewatch-12gb": the
   names match once case, separators and endings such as "-v3", "-final", "-main" or "(2)" are ignored.
   A folder with another name, such as "AQ-Tidewatch", counts as a copy only when its README starts with
   the same heading. Common names such as "website" or "app" need the same files too, and numbered folders
   such as lab1 and lab2 are never copies. Hal keeps the one with the newest file, and never one in an old,
   archive, backup, unversioned or copies folder when there is another. The row it keeps shows **Older
   copies**: press **Keep this one instead** on another copy to keep that one. A single file that names three or
   more of your projects, such as a portfolio plan, is left out: Hal reads each project from its own files. It never looks inside a project's libraries or build output (folders such as `node_modules`,
   `build` or `dist`); the line at the bottom of the list counts those files. Hal also leaves out other
   people's work: published papers you kept as reading, and posters or reports named after someone else.
   A PDF in a folder of reading, such as one named papers, readings, references or literature, counts as
   a paper unless its name says it is your notes on it.
   What your course gave out, such as a brief, a handbook or a folder of course materials, is proposed as
   lecture material, never as your coursework. A course's downloaded materials folder (such as "Linked
   materials" or "Resources") is never a project, whatever it holds: its files are lecture material. A notes
   vault (the folder an app such as Obsidian, Logseq, Foam, Zettlr or Notion keeps your notes in) is never a
   project either: each note goes on its own, most of them to **Lecture material**. A folder of note
   folders on one subject is one row, and so is a folder of practice exercises (such as HackerRank or LeetCode), which Hal never stars. Downloads
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
| You cannot find the list Hal sorted | Open **Your record**, **Overview**, and press **Check the list** in the banner at the top. |
| "Add ... files?" says it takes hours | Press **Not now** and untick what Hal does not need, such as lecture notes for modules you do not want to mention. |
| There is no **Add everything ticked** | It is in the last step. Press **Done** at the top of the list. |
| There is no box for a folder | You are using Hal from another computer. Upload a zip of the folder instead. |
| "Hal is still sorting another folder." | Wait for the first sort to finish, then sort the next folder. |
| A file says "Choose one, or add your CV first" | Add the course under **Courses Hal found**, or your CVs first, or choose its course under **Course or job**. |
| "Looks private (medical information), so Hal kept it out." | Hal keeps medical, bank, ID and pay papers out. If it is fine to keep, give it a place under **Where it goes**. |
| "A published paper. Hal keeps your own work, not your reading." | If you wrote it, give it a place under **Where it goes**. |
| "Someone else's work: it is named after ..." | If it is yours, check your name under **Settings**, **You**, then give it a place under **Where it goes**. |
| "Describes several of your projects." | Nothing to do: each project is read from its own folder. To keep the file anyway, give it a place under **Where it goes**. |
| "Hal could not tell where it goes." | Choose a place under **Where it goes**, or leave it out. |
| "Only online: this computer has an empty copy." | Download the file first (in OneDrive, choose "Always keep on this device"), then sort the folder again. |
| A file says "Already in your record, under ..." | It is in your record already. Nothing to do. |
| "Undo the move first, or keep it" when you press **Forget** | A move can be forgotten only once it is undone, so you never lose the way back. |
| "Hal is still adding what you accepted." | Wait until Hal has read what you added, then finish or undo the move. |
| Hal kept the wrong copy of a project | Open **Older copies** on the row Hal kept, and press **Keep this one instead** on the one you want. |
