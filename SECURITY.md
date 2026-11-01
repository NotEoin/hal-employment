# Security

Hal Employment runs on one person's own computer and keeps their job search there. This page says what it
protects against and what it cannot, what is stored where, what leaves the computer, and how to report a
problem. The plain-words version for people using Hal is [docs/user/privacy.md](docs/user/privacy.md); the
code is described in [docs/dev/architecture.md](docs/dev/architecture.md).

## What Hal protects against

Hal's server listens only on `127.0.0.1`, never on the network, and every request is checked:

- **Web pages in any browser.** A page on another site cannot drive Hal or read its replies. The `Host`
  header must name `127.0.0.1` or `localhost`, which stops DNS rebinding. Every API route needs Hal's
  session cookie, which is `HttpOnly` and `SameSite=Strict`, so another site's requests never carry it.
  Every write also needs the header `X-Hal: 1` and, when the browser sends an `Origin`, an origin equal to
  `http://` plus the request's `Host`.
- **Other user accounts on the computer.** The data and config folders are readable only by their owner
  (mode 0700 on Linux, the user's own profile on Windows); the private answers file and the local key are
  mode 0600. A session starts only from a one-time link (`jobhunt open` makes it; it works once, for 120
  seconds, and only its SHA-256 is stored), so knowing the port is not enough.
- **Anything on the network.** Nothing listens there. To use Hal from a laptop, the laptop opens an SSH
  tunnel to the desktop, so only someone who can sign in to the desktop over SSH can reach Hal, and the
  server still listens only on the desktop's `127.0.0.1`. Windows opened that way are marked as remote, and
  actions that would act on the desktop (opening a folder, Hal's own browser window) are refused for them.
- **The browser helper.** Each helper is paired once with a one-time code and gets its own key. A helper
  request needs that key and an `Origin` of the helper's fixed extension id, so another extension or a web
  page cannot pose as it. The helper acts only on tabs Hal opened or that the person handed to it.
- **Sending by mistake.** Hal never presses Submit. Every press in a form goes through one guard that
  refuses Submit, Send and Finish controls, and the tests fill practice forms whose servers count posts.
- **Updates.** Updates come from GitHub as a fast-forward of the installed clone, never as downloads. Once
  the maintainer's signing key is in `scripts/install/allowed_signers`, every new commit is checked against
  the key in the version already installed, so a commit cannot bring its own key
  ([docs/dev/release-and-update.md](docs/dev/release-and-update.md)). Every tool the install files download
  is pinned by version and SHA-256 (`scripts/install/pins.toml`).

## What Hal cannot protect against

- **Programs run by the same user.** They can read that user's files anyway, including Hal's database and
  its local key. Hal does not claim to stop them.
- **A computer that is already compromised,** or someone who can sign in as the user.
- **Unsigned updates.** While `scripts/install/allowed_signers` holds no key, updates are not checked for a
  signature, and Settings, Updates says so.
- **What the person sends.** Hal fills in forms but never presses Submit and never sends anything to an
  employer; what the person submits is theirs to check.
- **What a job site or an AI service does with what it receives.** Hal limits what it sends (below), not
  what happens to it afterwards.

## What is stored where

| What | Where | Protection |
| --- | --- | --- |
| Jobs, applications, settings, Activity | `jobs.db` in the data folder | The user's own folder (0700 on Linux) |
| Private answers (equality and adjustments) | `private.toml` in the data folder | 0600; read only by the form filler and the AI guard |
| Passwords and keys (email app password, phone alert code) | Windows Credential Manager or the Secret Service keyring; without one, `secrets.json` in the config folder | 0600, and encrypted with DPAPI on Windows |
| The local API key | `local-api-key` in the config folder | 0600; lets the command line and the updater in without a window |
| Sessions and one-time links | `jobs.db` | Only SHA-256 hashes of the links; sessions expire after 30 days unused |
| CV files | `cv/files/` in the data folder, named by SHA-256 | Read-only; checked against the hash on every read |
| Application documents | `Documents/Hal Employment/Applications` | The user's own Documents folder |
| Logs and database backups | `logs/` and `backups/` in the data folder | No private answer is written to a log, and `jobs.db` holds none |

The folders for each system are listed in [docs/user/privacy.md](docs/user/privacy.md#where-your-data-lives).
Nothing is kept inside Hal's program folder, so an update never touches the data.

## What leaves the computer

- Searches, to the job sites the person switched on.
- Update checks, to GitHub.
- With Hal's local model chosen: the same text goes to Hal's gateway on this computer (`127.0.0.1`), with
  the gateway token read from a file only you can read. It is never sent to Claude, even when the gateway
  is down.
- With the Claude subscription chosen: the advert, the CV and the evidence Hal picked, to Claude, through
  the person's own Claude Code sign-in. With a local model the AI runs on the computer, and "Never use
  online AI" keeps it so.
- With email set up: Gmail is read with an app password, read only. The only email Hal sends is the
  person's own daily summary, to themselves.
- With phone alerts on: short alert text, to ntfy.sh under a private random code.
- Form answers, to the employer, only when the person presses Submit on the employer's site.

Private answers never reach the AI: one classifier (`autofill.private_question`) keeps those fields out of
every AI request, and a guard in the AI layer refuses any prompt that holds a saved private answer. They
never appear in logs, Activity, events, exports or help files. The privacy tests check each of these with a
canary value.

## How to report a problem

Please do not open a public issue for a security problem.

- Use **Report a vulnerability** on the repository's **Security** tab (GitHub's private vulnerability
  reporting). Say what you saw, the steps that show it, and your Hal version (Settings, Updates).
- Do not attach your own data. A help file (Settings, Is Hal working?, Make a help file) has names, contact
  details and answers taken out, and you read it before it is saved.

You should hear back within a week. A confirmed problem is fixed on `main`, and installs offer the fix as
an update.
