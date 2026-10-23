# Release and update

How Hal Employment reaches the people who use it, and how it updates itself.
There are no release downloads: people the owner adds to the private GitHub repository install by cloning
it and update by pulling `main`.

## Install kinds

| Kind | Made by | `state/install.json` | Updates |
| --- | --- | --- | --- |
| user | `install-hal-windows.cmd` or `install-hal-linux.sh`, then `python -m jobhunt install --windows` or `--linux` | `{kind: "user", code, tools, git, uv, python_dir, uv_cache, extras, installed}` | Update Hal, the daily check, `update-hal.cmd` or `.sh` |
| developer | any other checkout, such as the owner's (`install --dev` makes only the shim and menu entry) | none | by hand with git; the app says "You run Hal from your own folder. Update it with git." |

`state/install.json` is in the data folder, next to `update-status.json` (the last result), `update.json`
(`from`, `to`, the backup, for Go back), `update-check.json` (the last check, for Settings and Home),
`update.lock` and `server.pid`. It is not `config/install.json`, which holds only the install id for cookie
names (`web/auth.py`). On Windows `git` is always the bundled PortableGit's `cmd\git.exe`, even when another
git is on PATH, so the version and Git Credential Manager are known.

## The updater (`scripts/updater.py`, `jobhunt/core/updates.py`)

`scripts/updater.py` runs `jobhunt.core.updates.main()` under uv's managed Python, outside `.venv`, so `uv
sync` can replace `.venv` while it runs:

```bash
<tools>/uv run --no-project --python 3.13 scripts/updater.py              # update
<tools>/uv run --no-project --python 3.13 scripts/updater.py --check      # steps 1 to 3 only
<tools>/uv run --no-project --python 3.13 scripts/updater.py --rollback   # Go back to the previous version
```

`core/updates.py` uses the standard library and `core.paths` only (`tests/test_updater.py` holds it to
that). Its steps, each written to `logs/update-<UTC>Z.log`:

1. Refuse in plain words: developer mode, tracked files changed by hand, an `apply.*` action running (then
   `state/update-waiting` is set and the server starts the update when the form is done). Take the lock.
2. `git fetch --prune origin main` (120 s). Background runs set `GIT_TERMINAL_PROMPT=0` and
   `GCM_INTERACTIVE=never`; only a person's press in a local session may open a sign-in window.
3. Nothing new, or only the held commit: "Hal is up to date." A commit that is not a fast-forward stops here.
   Every commit in `HEAD..origin/main` is checked for the owner's signature (below).
4. Record `from`, ask the server to stop (`POST /api/v2/system/shutdown` with the local key), wait 30 s for
   the pid in `state/server.pid`, else end it.
5. Back up `jobs.db` to `backups/jobs-<UTC>Z-before-update-<sha7>.db`.
6. `git merge --ff-only origin/main`, `uv sync --frozen --no-dev` plus the recorded extras,
   `python -m jobhunt migrate`, start the server (the Linux app unit, else a detached `pythonw`), and wait
   90 s for `GET /api/v2/version` to report the new commit.
7. Any failure in step 6 rolls back: `git reset --keep <from>`, sync again, restore the backup when the
   migration had started, start the old version, and hold the commit so it is not offered again.

The result goes to `state/update-status.json` with `recorded: false`. At its next start the server
(`core/update_routes.py`) turns it into an Activity row ("Updated Hal from 2.0.1 to 2.0.2" with What's new,
the commit subjects) and copies a held commit into `updates.held_commit`.

Update Hal and Go back spawn the updater with `platform.spawn_detached(..., unit="hal-employment-update")`:
on Linux a transient `systemd-run --user` unit, because a child in the server's own unit would be killed with
it; on Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`. `systemd-run`
passes no environment, so the uv folders go in front of the command through `env`.

## Signing your commits

Only the owner's commits should reach friends, and collaborators on a personal repository can push. So the
updater runs, for every new commit,

```bash
git -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=<file> verify-commit <commit>   # <file>: see below
```

where `<file>` is `scripts/install/allowed_signers` as the installed commit has it (`git show
HEAD:scripts/install/allowed_signers`), so a push can never bring its own key. A failure says "This update
was not made by the person who set Hal up, so Hal did not install it. Ask them." and nothing changes.

**Not set up yet.** The shipped `allowed_signers` holds comments only, because the owner has no signing key
yet. While it holds no key line, updates install without the check and Settings, Updates says "Updates are
not yet checked for the owner's signature: they have not set up signing." From the first installed commit
whose `allowed_signers` holds a key, every later commit must verify. To switch it on:

1. Make a signing key and sign every commit in your clone (its worktrees share the setting):

   ```bash
   ssh-keygen -t ed25519 -C "hal-signing" -f ~/.ssh/hal_signing          # a key just for signing
   git config gpg.format ssh
   git config user.signingkey ~/.ssh/hal_signing.pub
   git config commit.gpgsign true
   ```

2. Add one line to `scripts/install/allowed_signers`, `<your commit email> namespaces="git" <the .pub
   contents>`, commit it (signed), and push.
3. From then on, merge on your own computer and push. A merge made in GitHub's web page is signed by GitHub,
   not by you, so friends' Hal refuses it. A key change is itself a signed commit by the old key.

## Install files and pins

`scripts/install/pins.toml` pins uv, PortableGit and the GitHub CLI by version, URL and SHA-256.
`scripts/install/pins.py --write` copies the pins into the two install files (the Windows file is sent on its
own, so it carries them); `--check` fails when a copy differs (a test runs it); `--fill` downloads each file
once and records its SHA-256; `--make DIR` writes filled-in copies with the account and repository from the
`origin` remote. The copies in the repository say OWNER/REPO, because that name is a personal string that is
never committed.

Every `.cmd` and `.ps1` is ASCII with CRLF (`.gitattributes`). cmd.exe re-reads a batch file from disk after
each command, so `update-hal.cmd`, which runs while git changes the clone, first copies itself to `%TEMP%`,
and every other `.cmd` keeps its one command line, its `pause` and its `exit /b` together and last
(`tests/test_install_scripts.py`).

## The USB stick install (`core/importer_usb.py`)

`python -m jobhunt import --from-usb-install <folder>`, the Windows install file's step 6, or the setup
action `setup.import_usb` copies the old stick install: the database through SQLite's backup API (then
migrated), `private.toml` byte for byte, `apply/`, the CV files, `packs/`, the settings of `config.toml`
through `settings.check` (so "22,000" and "15 miles" are read), the saved keys into the safe store (each old
file overwritten with zeros and deleted once the stored value reads back), and `schedule.json`. Then it
retires the old Hal: its Startup shortcut, its desktop shortcut when that points into the old folder, its
`bin` on the user PATH, the old `/jobs` command when it is the stick's own, and the folder, renamed
`HalEmployment.old-<date>`. A Hal that already holds jobs is never written over.
