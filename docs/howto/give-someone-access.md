# Give someone access to Hal Employment

Hal's files are a private GitHub repository. A person you add installs Hal by cloning it and gets updates by
pulling it, with their own GitHub sign-in. This page is for the owner.

1. On GitHub, open the repository, **Settings**, **Collaborators**, **Add people**, and add their GitHub account.
   They get an invitation email; they must accept it before they install.
2. Make filled-in install files from your checkout (the copies in the repository say OWNER/REPO and refuse
   to run):

   ```bash
   python scripts/install/pins.py --check            # the install files hold the pinned downloads
   python scripts/install/pins.py --make ~/hal-out   # reads the account and repository from origin
   ```

3. Give them `~/hal-out/install-hal-windows.cmd` (or `install-hal-linux.sh`) on a USB stick or through a
   cloud-storage link, never as an email attachment.
4. Send them the link to [Install Hal Employment on Windows](install-on-windows.md) or
   [on Linux](install-on-linux.md).

Collaborators on a personal repository can push. Hal installs only commits you signed, once your signing key
is in `scripts/install/allowed_signers`; until then every update installs unchecked and Settings, Updates
says so. [Release and update](../dev/release-and-update.md) explains signing. Where your GitHub plan allows
it, also protect `main` in Settings, Branches.

To remove someone, remove them under Collaborators. Their Hal keeps working but gets no more updates, and says
"Your GitHub account no longer has access to Hal's files."

| Symptom | Fix |
| --- | --- |
| `pins.py --make` says there is no GitHub origin remote | Pass the name: `--slug account/repository`. |
| `pins.py --check` names a file | Run `python scripts/install/pins.py --write` and commit the result. |
| The install file stops with "no checksum for ..." | Run `python scripts/install/pins.py --fill` once (it downloads each pinned file and records its SHA-256), commit, then make the files again. |
| Their Hal refuses an update as not made by you | The update has a commit you did not sign, for example one merged in GitHub's web page. Merge on your own computer, sign, and push. |
