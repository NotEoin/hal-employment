# Install Hal Employment on Linux

You need a 64-bit Linux desktop with git and curl. Hal goes into `~/.local/opt/hal-employment` with its own
copies of uv and Python, each download checked against a pinned SHA-256. Nothing is added to your PATH, and
your git settings are not changed.

1. Open the repository on GitHub, then the file `scripts/install/install-hal-linux.sh`, press
   **Download raw file** and save it in Downloads.
2. Run it in a terminal:

   ```bash
   bash ~/Downloads/install-hal-linux.sh   # about 5 minutes; safe to run again
   ```

3. At "2 of 6: Signing you in to GitHub", the GitHub command line shows a one-time code and opens your
   browser. Paste the code there and sign in with any GitHub account; Hal's git uses the sign-in to
   download Hal and its updates.
4. When it says "Done", Hal's setup guide opens. Hal is in your applications menu as **Hal Employment**, and
   starts when you sign in.

Running the install file again is always safe: it repairs the install and keeps your data.

The command line is `~/.local/bin/hal-employment-cli`, for example:

```bash
~/.local/bin/hal-employment-cli list -n 10   # the ten best open jobs
```

To run Hal from your own clone instead, see [CONTRIBUTING.md](../../CONTRIBUTING.md).

| Symptom | Fix |
| --- | --- |
| "Hal needs git" or "Hal needs curl" | Install it with your distribution's package manager, then run the file again. This is the one step that needs admin rights. |
| "Hal could not sign you in to GitHub" | Run the file again and finish the sign-in in the browser. |
| "Hal could not reach GitHub" | Check the internet connection, then run the file again. |
| Printing says it uses another way | Playwright's browser could not get the system libraries it wants. Is Hal working? lists them; your distribution's packages for Chromium usually bring them. |
| "... was not made by this install, so it was left alone" | A file of yours is where Hal's command or menu entry goes. Move it, then run the file again. |
