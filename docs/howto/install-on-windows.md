# Install Hal Employment on Windows

You need Windows 10 (version 1809 or newer) or Windows 11, 64-bit, 3 GB of free space, and a GitHub account
that the person who set Hal up has added to Hal's files. No admin rights are needed, and you never type a
command: the install file does everything in one window.

1. Open the email from GitHub that says you were invited, and press **Accept invitation**. (If you skip this,
   the install file opens the invitation page for you and stops.)
2. Get the file `install-hal-windows.cmd` from the person who set Hal up: on a USB stick, or through a
   cloud-storage link. It never comes as an email attachment, because Gmail and Outlook block `.cmd` files.
3. If it came from a link, your browser may warn about the download. Choose **Keep**.
4. Double-click the file. If Windows shows "Open File - Security Warning" ("The publisher could not be
   verified"), press **Run**. If it shows "Windows protected your PC" instead, press **More info**, then
   **Run anyway**. A file copied from a USB stick shows neither.
5. Your browser opens GitHub's sign-in page. Sign in and press **Authorize**. Hal never sees your password.
6. Wait about 5 minutes. The window, called "Installing Hal Employment", shows numbered steps, "1 of 7" to
   "7 of 7".
7. If you used the older Hal from the USB stick, the window says "Copied your jobs, applications and settings
   from your old Hal. Nothing was deleted." Your old folder is kept as `HalEmployment.old-<date>`.
8. Hal's setup guide opens. You can close the install window.

Hal is now in the Start menu and on the desktop as **Hal Employment**, and **Update Hal Employment** is in the
Start menu. Closing Hal's window does not stop Hal.

To remove Hal, open Settings, Apps, Installed apps, find **Hal Employment** and press **Uninstall**. It asks
whether to keep your data; the answer is yes unless you type n.

| Symptom | Fix |
| --- | --- |
| "Your GitHub account cannot see Hal Employment yet" | Accept the invitation email from GitHub (step 1), then double-click the file again. |
| "This install file is not filled in yet" | You have the copy from Hal's files, not a filled-in one. Ask the person who set Hal up for a new file. |
| "This install file is not finished: it has no checksum for ..." | The file was made before its downloads were checked. Ask for a new file. |
| "Hal could not reach GitHub" | Check the internet connection, then double-click the file again. Running it again is always safe. |
| GitHub's page shows a code to paste | Hal's first sign-in way did not work, so it uses the second: the code is already copied, so paste it into the page with Ctrl V. |
| Smart App Control blocks a tool | Smart App Control blocks programs that are not signed. The only fix is switching it off in Windows Security, App and browser control. |
| "Hal did not start" | Double-click the file again. If it happens again, send the person who set Hal up the log file the message names. |
