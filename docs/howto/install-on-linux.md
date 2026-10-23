# Install Hal Employment on Linux

You need a 64-bit Linux desktop with git and curl, and a GitHub account that the person who set Hal up has
added to Hal's files. Hal goes into `~/.local/opt/hal-employment`; nothing is added to your PATH and your git
settings are not changed.

1. Accept the invitation email from GitHub.
2. Get `install-hal-linux.sh` from the person who set Hal up and save it in Downloads.
3. Run it. Either right-click it, choose **Run as a Program**, or run this one line in a terminal:

   ```bash
   bash ~/Downloads/install-hal-linux.sh   # takes about 5 minutes; safe to run again
   ```

4. The GitHub command line shows a one-time code and opens your browser. Paste the code there and sign in.
5. When it says "Done", Hal's setup guide opens. Hal is in your applications menu as **Hal Employment**, and
   starts when you sign in.

The command line is `~/.local/bin/hal-employment-cli`, for example:

```bash
~/.local/bin/hal-employment-cli list -n 10   # the ten best open jobs
```

A developer's own checkout uses `python -m jobhunt install --dev` instead: it makes only that command and the
menu entry, and leaves starting the server to whatever runs it now.

| Symptom | Fix |
| --- | --- |
| "Hal needs git" or "Hal needs curl" | Install it from your Software app, then run the file again. This is the one step that needs admin rights. |
| "Your GitHub account cannot see Hal Employment yet" | Accept the invitation email from GitHub, then run the file again. |
| "This install file is not filled in yet" or "... no checksum for ..." | Ask the person who set Hal up for a new file. |
| Printing says it uses another way | Playwright's browser could not get the system libraries it wants. Is Hal working? lists them; your distribution's packages for Chromium usually bring them. |
| "... was not made by this install, so it was left alone" | A file of yours is where Hal's command or menu entry goes. Move it, then run the file again. |
