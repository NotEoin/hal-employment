# How updates work

How Hal Employment installs and updates itself. There are no release downloads: an install is a git clone
of `main`, and an update is a fast-forward of that clone. The steps a person follows are in
[Update Hal Employment](../howto/update-hal.md).

## Install kinds

| Kind | Made by | `state/install.json` | Updates |
| --- | --- | --- | --- |
| user | `install-hal-windows.cmd` or `install-hal-linux.sh`, then `python -m jobhunt install --windows` or `--linux` | `{kind: "user", code, tools, git, uv, python_dir, uv_cache, extras, installed}` | Update Hal, the daily check, `update-hal.cmd` or `.sh` |
| developer | any other clone (`install --dev` makes only the command-line shim and the menu entry) | none | by hand with git; the app says "You run Hal from your own folder. Update it with git." |

`state/install.json` is in the data folder, next to `update-status.json` (the last result), `update.json`
(`from`, `to`, the backup, for Go back), `update-check.json` (the last check, for Settings and Home),
`update.lock` and `server.pid`. It is not `config/install.json`, which holds only the install id for cookie
names (`web/auth.py`). On Windows `git` is always the bundled PortableGit's `cmd\git.exe`, even when another
git is on PATH, so its version and Git Credential Manager are known.

## The updater (`scripts/updater.py`, `jobhunt/core/updates.py`)

`scripts/updater.py` runs `jobhunt.core.updates.main()` under uv's managed Python, outside `.venv`, so
`uv sync` can replace `.venv` while it runs:

```bash
<tools>/uv run --no-project --python 3.13 scripts/updater.py              # update
<tools>/uv run --no-project --python 3.13 scripts/updater.py --check      # steps 1 to 3 only
<tools>/uv run --no-project --python 3.13 scripts/updater.py --rollback   # go back to the previous version
```

`core/updates.py` uses the standard library and `core.paths` only (`tests/test_updater.py` holds it to
that), and reaches git, uv and the server through one `World` object, so tests run the real steps against
local bare repositories. Its steps, each written to `logs/update-<UTC>Z.log`:

1. Refuse in plain words: a developer clone, tracked files changed by hand, an `apply.*` action running
   (then `state/update-waiting` is set and the server starts the update when the form is done). Take the
   lock.
2. `git fetch --prune origin main` (120 s). Background runs set `GIT_TERMINAL_PROMPT=0` and
   `GCM_INTERACTIVE=never`, so only a person's press in a local window may open a sign-in.
3. Nothing new, or only a held commit: "Hal is up to date." A commit that is not a fast-forward stops here.
   Every commit in `HEAD..origin/main` is checked for a trusted signature (below).
4. Record `from`, ask the server to stop (`POST /api/v2/system/shutdown` with the local key), wait 30 s for
   the pid in `state/server.pid`, else end it.
5. Back up `jobs.db` to `backups/jobs-<UTC>Z-before-update-<sha7>.db`.
6. `git merge --ff-only origin/main`, `uv sync --frozen --no-dev` plus the recorded extras,
   `python -m jobhunt migrate`, start the server (the Linux app unit, else a detached `pythonw`), and wait
   90 s for `GET /api/v2/version` to report the new commit.
7. Any failure in step 6 rolls back: `git reset --keep <from>`, sync again, restore the backup when the
   migration had started, start the old version, and hold the commit so it is not offered again.

The result goes to `state/update-status.json` with `recorded: false`. At its next start the server
(`core/update_routes.py`) turns it into an Activity row ("Updated Hal from 2.0.1 to 2.0.2") whose What's new
is the commit subjects, newest first. Commit subjects are written in plain words for that reason.

Update Hal and Go back start the updater with `platform.spawn_detached(..., unit="hal-employment-update")`:
on Linux a transient `systemd-run --user` unit, because a child in the server's own unit would be stopped
with it; on Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`.
`systemd-run` passes no environment, so the uv folders go in front of the command through `env`.

## Signing your commits

An install can trust only the maintainer's signed commits. For every new commit, oldest first, the updater
runs

```bash
git -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=<file> verify-commit <commit>   # <file>: see below
```

where `<file>` is `scripts/install/allowed_signers` as the commit before it has it: the installed commit's
copy for the first new commit, and the previous new commit's copy after that, which was itself checked
first. So a commit can never bring its own key, a key change must be signed by the old key, and a key added
inside one update already applies to the commits after it. The daily check runs the same check, so it never
offers such a commit.

While `allowed_signers` holds no key line, updates install without the check and Settings, Updates says
"Updates are not yet checked for the owner's signature". From the first commit whose `allowed_signers` holds
a key, every later commit must verify, even when both arrive in one update. To switch it on:

1. Make a signing key and sign every commit in your clone:

   ```bash
   ssh-keygen -t ed25519 -C "hal-signing" -f ~/.ssh/hal_signing          # a key just for signing
   git config gpg.format ssh
   git config user.signingkey ~/.ssh/hal_signing.pub
   git config commit.gpgsign true
   ```

2. Add one line to `scripts/install/allowed_signers`, `<your commit email> namespaces="git" <the .pub
   contents>`, commit it (signed), and push.
3. From then on, merge on your own computer and push. A merge made in GitHub's web page is signed by GitHub,
   not by you, so installs refuse it.

## Install files and pins

`scripts/install/pins.toml` pins uv, PortableGit and the GitHub CLI by version, URL and SHA-256.
`scripts/install/pins.py --write` copies the pins into the two install files, because the Windows file is
downloaded on its own and must carry them; `--check` fails when a copy differs (a test runs it); `--fill`
downloads each file once and records its SHA-256.

Every `.cmd` and `.ps1` is ASCII with CRLF (`.gitattributes`). cmd.exe re-reads a batch file from disk after
each command, so `update-hal.cmd`, which runs while git changes the clone, first copies itself to `%TEMP%`,
and every other `.cmd` keeps its one command line, its `pause` and its `exit /b` together and last
(`tests/test_install_scripts.py`).
