# Hal Employment

Hal Employment is a job search assistant that runs on your own computer. It looks for UK graduate jobs,
graduate schemes, internships and placements that suit you, explains why each one fits, writes a CV and
cover letter for each job you choose, and fills in the employer's application form in your browser.
Hal never presses Submit: you check every application and send it yourself on the employer's site.

Everything Hal keeps stays on your computer. It sends only what each task needs: searches to job sites,
and, if you use a Claude subscription, the advert, your CV and the evidence it picked when it writes.
[docs/user/privacy.md](docs/user/privacy.md) lists it all.

New to Hal? Start with [docs/user/getting-started.md](docs/user/getting-started.md). Every other
document is listed in [docs/README.md](docs/README.md).

## Install

Hal's files are a private GitHub repository. The person who set Hal up adds your GitHub account to it and
gives you an install file. You need no admin rights, and on Windows you never type a command.

- **Windows 10 or 11:** double-click `install-hal-windows.cmd` (from a USB stick or a cloud-storage link,
  never an email attachment). Steps and pictures: [docs/howto/install-on-windows.md](docs/howto/install-on-windows.md).
- **Linux:** run `install-hal-linux.sh`. Steps: [docs/howto/install-on-linux.md](docs/howto/install-on-linux.md).

The install file signs you in to GitHub in your browser, downloads Hal, makes the shortcuts and opens the
setup guide. Updates come from GitHub too, and install only when you press **Update Hal** in Settings,
Updates ([docs/howto/update-hal.md](docs/howto/update-hal.md)).

To hand Hal to someone else, see [docs/howto/give-someone-access.md](docs/howto/give-someone-access.md).
The blank install files are `scripts/install/install-hal-windows.cmd` and
`scripts/install/install-hal-linux.sh`; the how-to explains how to fill them in.

### For developers

```sh
git clone <this repository> && cd hal-employment   # any folder; your data never goes inside it
uv sync --frozen --extra browser --extra documents  # Linux; on Windows leave out --extra documents
uv run python -m playwright install chromium        # Hal's own browser window and printing on Linux
uv run python -m jobhunt install --dev              # optional: the menu entry and the command-line shim
```

## Run

Open **Hal Employment** from the Start menu, the desktop or your applications menu. Hal opens in its own
window. Closing the window does not stop Hal; open it again from the same shortcut.

From a checkout:

```sh
uv run python -m jobhunt serve --open   # run the server in this terminal and open the app, signed in
uv run python -m jobhunt open           # open a signed-in window, starting Hal in the background first
uv run python -m jobhunt --help         # every command; the old commands print what they always did
```

Hal listens only on `127.0.0.1`. To use it from a laptop while it runs on your desktop, see
[docs/howto/use-hal-from-another-computer.md](docs/howto/use-hal-from-another-computer.md).

## Test

Tests never touch your real data: with `HAL_TEST_MODE=1` and no `HAL_EMPLOYMENT_HOME`, every data folder
refuses to open. Everything outside Hal (job sites, email, the AI, GitHub, SSH) is a fake.

```sh
scripts/dev/checkpoint.sh --quick                           # before each commit: ruff, ASCII, the tests
scripts/dev/checkpoint.sh                                   # every gate, browser tests on
HAL_TEST_MODE=1 uv run python -m unittest discover tests    # the tests alone
```

On Windows, in Command Prompt:

```bat
set "HAL_TEST_MODE=1"                                 & rem first, in the same window
uv run python -m unittest discover tests              & rem the tests alone
```

[docs/dev/testing.md](docs/dev/testing.md) explains the gates, the fakes and the made-up people the tests
use.

## Where your data lives

Your data lives outside Hal's program folder, so an update never touches it.

| What | Windows | Linux |
| --- | --- | --- |
| Program | `%LOCALAPPDATA%\Programs\Hal Employment\app` | `~/.local/opt/hal-employment/app` |
| Your data (jobs, applications, CV, private answers, backups, logs) | `%APPDATA%\Hal Employment\data` | `~/.local/share/hal-employment` |
| Hal's own settings files and keys | `%APPDATA%\Hal Employment\config` | `~/.config/hal-employment` |
| Things Hal can make again | `%LOCALAPPDATA%\Hal Employment\cache` | `~/.cache/hal-employment` |
| Your applications' files | `Documents\Hal Employment\Applications` | `~/Documents/Hal Employment/Applications` |
| Your record (certificates, references) | `Documents\Employment Record` | `~/Documents/Employment Record` |

Settings, **Privacy and data** shows the data folder, makes a zip of everything (**Export everything**) and
can delete it all. A developer can point Hal at another folder with `HAL_EMPLOYMENT_HOME`.

## More

- [CHANGELOG.md](CHANGELOG.md): what changed in each version.
- [SECURITY.md](SECURITY.md): what Hal protects against, what is stored where, and how to report a problem.
- [docs/dev/architecture.md](docs/dev/architecture.md): how the code fits together.
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [LICENSE](LICENSE).
