# Contributing

Thank you for looking. Issues and pull requests are welcome; for anything larger than a fix, open an issue
first so we can agree the shape before you write it.

## Set up

You need git, [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer (uv can fetch it).

```sh
git clone https://github.com/NotEoin/hal-employment.git && cd hal-employment
uv sync --frozen --extra browser --extra documents   # Linux; on Windows leave out --extra documents
uv run python -m playwright install chromium         # Hal's own browser window, printing and browser tests
uv run python -m jobhunt install --dev               # optional: the menu entry and the command-line shim
```

Your data never goes inside the clone. To keep a development copy apart from a Hal you use, point it at a
folder of its own before running anything:

```sh
export HAL_EMPLOYMENT_HOME="$PWD/.tmp-home"   # this clone's own data folder
uv run python -m jobhunt serve --open         # run the server here and open a signed-in window
```

## Run the checks

```sh
scripts/dev/checkpoint.sh --quick   # before each commit: the staged lines' checks, then the tests
scripts/dev/checkpoint.sh           # before a pull request: every check, browser tests on
```

[docs/dev/testing.md](docs/dev/testing.md) explains each check, the fakes and the made-up people the tests
use. CI runs the same tests on Linux and Windows.

## What a change needs

- **Tests.** A bug fix comes with a test that fails without it. Test names are behaviour sentences, and
  every test passes on Linux and Windows.
- **Docs that match.** When behaviour changes, update its doc comment, the docs under `docs/` and the
  [changelog](CHANGELOG.md) in the same pull request.
- **Hal's promises hold.** Hal never presses Submit, private answers never reach the AI or a log, and
  nothing about a person is built into the code. The tests check each of these; never weaken one to make a
  change pass.
- **Plain text.** Code, comments, docs and commit messages are ASCII. Comments say why, not what.
- **Made-up data only.** Tests and fixtures use invented people, employers and places.

## Commit style

One logical change per commit, with a one-line subject in the form `<area>: <what it now does>`, in plain
words and at most 72 characters, for example `apply: a dropped upload is never marked done`. Reuse the areas
in `git log` (`ui`, `cv`, `apply`, `find`, `core`, `ai`, `docs`, `tests` and so on). The subjects become
the What's new list on the Updates page, so write them for the person using Hal.

## Security problems

Please do not open a public issue for a security problem; see [SECURITY.md](SECURITY.md).
