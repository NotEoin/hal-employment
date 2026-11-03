# Hal Employment

> *Spend less time on application forms, and more on the parts only you can write.*

Applying for jobs means typing the same details into a new form every time. Hal Employment takes
care of that side of it. It runs on your own computer, looks for UK graduate jobs and internships
that fit what you want, and tells you why it picked each one. When you apply, it drafts a CV for
that job, a cover letter and answers to the form's questions, and fills them into the employer's
form in your browser.

The drafts are a starting point, not a finished application. Read them, rewrite anything that
doesn't sound like you, and press Submit yourself. Hal never presses Submit and never sends
anything to an employer; the tests fill practice forms whose servers count every post, and the
count must stay at zero.

Every draft is built from your record: an "Employment Record" folder in your Documents (it opens as
an Obsidian vault too) where you keep your CV, certificates, references and the full story of each
job. Each time Hal writes, it searches the record for the passages that fit that application
(retrieval-augmented generation, or RAG) and treats them as the source of truth. So the drafts are
only as good as what you put in. A sparse record gives Hal little to work with; the more you add,
the better each tailored draft gets.

![Home: what needs you today, new matches and your goal](docs/images/home.png)

It is Python 3.11+ with a standard-library HTTP server and SQLite, a Preact front end with no build
step, Playwright and a Manifest V3 browser extension for forms, and a local model through Ollama
with your own Claude subscription as the fallback.

## Your data stays with you

An application holds a lot about you: contact details, education, work history, sometimes health
or background. Most paid application tools and online AI services get all of that when you use them.
Hal Employment is free, and it is meant to run on a local model through Ollama, so your details
stay between you and the employer you apply to. Claude is only a fallback: turn on **Never
use online AI** in Settings and nothing is sent to an AI service at all. That is the recommended
setup. [docs/user/privacy.md](docs/user/privacy.md) lists what is stored and what leaves
your computer.

## What it does

- Finds jobs that fit: Hal searches UK job boards, employers' careers pages (Greenhouse, Lever, Ashby,
  Workable, SmartRecruiters, Recruitee, Personio) and the job alert emails you already get. It covers
  graduate jobs and schemes, paid internships, placement years, insight weeks and apprenticeships, and
  scores each job against your own goal, showing every point.
- Explains everything. Every score, hidden job, suggestion and form answer has a "Why?". Activity keeps
  everything Hal did and why, with Undo.
- Drafts the documents. A CV adjusted for each job, a cover letter and answers to the form's questions,
  in your CV type's style, for you to edit. Hal never adds a number, date or qualification it cannot find in your CV or
  record; it leaves a gap such as `[how many?]` for you to fill.
- Fills in the form, then hands over. A browser helper fills the form in your own browser, where you
  are already signed in. Each answer is marked "Fine" or "Check this", and on the last page Hal hands the
  form to you.
- Keeps your private answers private. Answers to equality and adjustment questions are used only on the
  form you approved, never sent to the AI, never logged and never shown again.

![Where Hal looks: the job sites Hal searches, with a switch for each](docs/images/jobs-where-hal-looks.png)

## How it is built

The AI is local first, with your Claude subscription as an optional fallback; "Never use online AI"
keeps everything on the computer. Never Submit is enforced in code by one guard, and private answers
stay out of every AI request. [docs/dev/architecture.md](docs/dev/architecture.md) has the details,
and [SECURITY.md](SECURITY.md) lists what is stored and what leaves the computer. CI runs the full
suite, browser tests included, on Linux (Python 3.11 and 3.13) and on Windows (Python 3.13).

## Use with Hal

Hal Employment is one part of [Hal](https://github.com/NotEoin/Hal), a modular AI assistant that runs on your own
machine, built privacy and security first. See
[how it fits together](https://github.com/NotEoin/Hal#how-it-fits-together).

Hal Employment works on its own. Next to [Hal](https://github.com/NotEoin/Hal), the local assistant,
it can write its pipeline note into the Hal vault, speak urgent alerts through
[hal-voice](https://github.com/NotEoin/hal-voice), offer Hal's local models as AI choices and pause
with `hal off`. These links turn on with "Use my Hal setup".

On its own, Hal Employment uses Claude Code or a model on this computer. With Hal, it uses Hal's
local model through Hal's gateway when the gateway answers (or after
`python -m jobhunt settings set ai.provider hal`), and never falls back to Claude unless you pick
Claude. Settings shows which mode is in force. See
[use the helper with Hal](docs/howto/use-the-helper-with-hal.md) and
[use the helper on its own](docs/howto/use-the-helper-on-its-own.md).

## Requirements

- Windows 10 (1809 or newer) or 11, with the native installer.
- A 64-bit Linux desktop.
- Windows through WSL2 runs the Linux install ([how-to](docs/howto/run-on-windows-with-wsl2.md)).
- No admin rights are needed.

## Install

- Windows: download `scripts/install/install-hal-windows.cmd` and double-click it
  ([steps](docs/howto/install-on-windows.md)).
- Linux: download `scripts/install/install-hal-linux.sh` and run it with bash
  ([steps](docs/howto/install-on-linux.md)).

The install file gets its own copies of uv, Python and Git (each pinned by SHA-256), clones Hal, makes the
shortcuts and opens the setup guide. Updates come from this repository and install only when you press
**Update Hal** ([how](docs/howto/update-hal.md)).

To run Hal from your own clone, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Run

Open **Hal Employment** from the Start menu or your applications menu. The setup guide takes about 30
minutes: your CV, what you are looking for, which AI to use and the browser helper.
[docs/user/getting-started.md](docs/user/getting-started.md) walks through it and a normal day.

![The setup guide: what are you looking for?](docs/images/setup-goal.png)

From a clone:

```sh
uv run python -m jobhunt serve --open   # run the server in this terminal and open a signed-in window
uv run python -m jobhunt open           # open a signed-in window, starting Hal in the background first
uv run python -m jobhunt --help         # every command
```

Hal listens only on `127.0.0.1`. To use it from a laptop while it runs on a desktop, see
[docs/howto/use-hal-from-another-computer.md](docs/howto/use-hal-from-another-computer.md).

## Configuration

Everything is in Settings, grouped as in the setup guide, with search and Reset; every change is kept in
Activity with Undo. The choices that matter most:

| Setting | Choices | Default |
| --- | --- | --- |
| How Hal thinks | Hal's local model (the gateway), This computer (Ollama), Another local server (OpenAI format), Claude subscription | Hal's local model when Hal's gateway answers; else This computer on Linux when Ollama answers, else Claude |
| Never use online AI | On or off | Off |
| What you're looking for | Career stage, kinds of role, roles, places, working pattern, lowest pay | Set in the setup guide |
| Browser Hal fills forms in | Opera, Google Chrome, Microsoft Edge, Brave or Vivaldi | The first one Hal finds |
| Press Submit for you | Never (it cannot be changed) | Never |
| Email and phone alerts | A Gmail inbox read with an app password; ntfy alerts | Off |

Your data lives in your own user folders, never in the program folder, so an update never touches it.
[docs/user/privacy.md](docs/user/privacy.md) lists the folders, what is stored and what leaves the
computer. For developers, `HAL_EMPLOYMENT_HOME` points Hal at another data folder and `HAL_EMPLOYMENT_PORT`
changes the port (8737 by default).

![My CV: eleven CV types, each with its own order, length and checks](docs/images/cv-types.png)

## Test

```sh
uv sync --frozen --extra browser --extra documents   # Linux; on Windows leave out --extra documents
scripts/dev/checkpoint.sh --quick                    # before each commit: the staged lines' checks, then the tests
scripts/dev/checkpoint.sh                            # every check, browser tests on
```

Tests never touch real data: with `HAL_TEST_MODE=1` and no `HAL_EMPLOYMENT_HOME`, every data folder
refuses to open, and everything outside Hal (job sites, email, the AI, GitHub, SSH) is a fake.
[docs/dev/testing.md](docs/dev/testing.md) explains the checks, and [CONTRIBUTING.md](CONTRIBUTING.md) what
a change needs. Every document is listed in [docs/README.md](docs/README.md).

## Licence

MIT, see [LICENSE](LICENSE). Third-party parts keep their own licences: see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Security problems: [SECURITY.md](SECURITY.md).
