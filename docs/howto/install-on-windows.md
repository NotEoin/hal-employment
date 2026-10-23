# Install Hal Employment on Windows

You need 64-bit Windows 10 (version 1809 or newer) or Windows 11 and 3 GB of free space. No admin rights
are needed, and you never type a command: one install file does everything in one window. It downloads
its own copies of uv, Python and Git into Hal's folder, each checked against a pinned SHA-256, and changes
nothing else on the computer.

1. Open [the repository on GitHub](https://github.com/NotEoin/hal-employment), then the file `scripts/install/install-hal-windows.cmd`, and press
   **Download raw file**. Your browser may warn about the download; choose **Keep**.
2. Double-click the file. If Windows shows "Windows protected your PC", press **More info**, then
   **Run anyway**. If it shows "Open File - Security Warning" instead, press **Run**.
3. Wait about 5 minutes. The window, called "Installing Hal Employment", counts its steps from "1 of 6"
   to "6 of 6". If you have an older Hal in a `HalEmployment` folder in your user folder, there is a seventh
   step that copies your jobs, applications and settings from it. Nothing in the old folder is deleted.
   Hal downloads itself from the public repository with no sign-in; only if GitHub refuses that, the window
   says so and opens a GitHub sign-in in your browser.
4. Hal's setup guide opens. You can close the install window.

Hal is now in the Start menu and on the desktop as **Hal Employment**, and **Update Hal Employment** is in
the Start menu. Hal starts when you sign in to Windows, and closing its window does not stop it.

Running the install file again is always safe: it repairs the install and keeps your data.

To remove Hal, open Settings, Apps, Installed apps, find **Hal Employment** and press **Uninstall**. It asks
whether to keep your data; the answer is yes unless you type n.

| Symptom | Fix |
| --- | --- |
| "Hal could not reach GitHub" | Check the internet connection, then double-click the file again. |
| "GitHub wants you to sign in" and nothing happens | Finish the sign-in in the browser. If the window shows a code, paste it into the GitHub page; it is already copied. Then double-click the file again if it stopped. |
| "Hal could not sign you in to GitHub, or your account cannot read ..." | GitHub asked for a sign-in and it did not finish, or the repository is not one your account may read. Double-click the file again; if it repeats, open an issue on the Hal Employment repository. |
| "The download of ... was not the file Hal expected" | A download was cut short or changed on the way. Double-click the file again. |
| "Hal needs 3 GB of free space" | Free some space on the drive that holds your user folder, then double-click the file again. |
| Smart App Control blocks a tool | Smart App Control blocks programs that are not signed. The only fix is switching it off in Windows Security, App and browser control. |
| "Hal did not start" | Double-click the file again. If it happens again, open an issue on GitHub with the install log the message names. |
